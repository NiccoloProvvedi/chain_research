"""Local server of the app: API and page, only from 127.0.0.1, with a secret token (cookie set by Electron).

Start: `python -m chain_research.server` (the app starts it). It listens on a free port and writes it to stdout as the
first JSON line ({"port": 12345}); the token comes from CHAIN_TOKEN. An investigation runs in its own process
(`python -m chain_research.investigation`); the server reads its status and events from the files in its folder.
"""
import asyncio
import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import ROOT, configuration
from .investigation import LOCK_FILE, Investigation, active_process
from .web.sources import config as config_sources
from .web.sources.wikipedia_graph import valid_contact, wikipedia_contact

log = logging.getLogger("chain_research.server")
INTERFACE = ROOT / "app" / "ui"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
VALID_NAME = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}-[a-z0-9-]{1,50}$"


class Protection(BaseHTTPMiddleware):
    """Accepts only requests to 127.0.0.1 with the secret token."""

    def __init__(self, app: Any, token: str, port_number: Callable[[], int]) -> None:
        super().__init__(app)
        self.token = token
        self.port_number = port_number

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        host = request.headers.get("host", "")
        if host not in (f"127.0.0.1:{self.port_number()}", f"localhost:{self.port_number()}"):
            return JSONResponse({"error": "Host not allowed."}, status_code=403)
        if not hmac.compare_digest(request.cookies.get("token", ""), self.token):
            return JSONResponse({"error": "Unauthorized access."}, status_code=403)
        if request.method == "POST" and request.headers.get("origin") not in (None, f"http://{host}"):
            return JSONResponse({"error": "Origin not allowed."}, status_code=403)
        answer = await call_next(request)
        answer.headers["Content-Security-Policy"] = CSP
        answer.headers["X-Content-Type-Options"] = "nosniff"
        answer.headers["Cache-Control"] = "no-store"
        return answer


class Manager:
    """The investigations on disk and the one in progress (one at a time: the browser profiles are shared)."""

    def __init__(self, folder: Path | None = None) -> None:
        self._folder = folder
        self.process: subprocess.Popen | None = None
        self.in_progress: str | None = None

    @property
    def folder(self) -> Path:
        return self._folder or configuration.folder("investigations_folder")

    def path(self, ident: str) -> Path:
        if not re.match(VALID_NAME, ident or "") or not (self.folder / ident / "status.json").is_file():
            raise ValueError("No such investigation.")
        return self.folder / ident

    def status(self, ident: str) -> dict:
        return json.loads((self.path(ident) / "status.json").read_text(encoding="utf-8"))

    def listing(self) -> list[dict]:
        articles = []
        for file in sorted(self.folder.glob("*/status.json"), reverse=True):
            try:
                s = json.loads(file.read_text(encoding="utf-8"))
                questions = s.get("questions") or []
                articles.append({"id": s["id"], "topic": s["topic"], "title": (s.get("plan") or {}).get("title"),
                             "status": self._true_status(s), "created": s["created"], "round": s.get("round"),
                             "questions": len(questions), "found_items": sum(d["status"] == "answer" for d in questions),
                             "spent_usd": round(s.get("director_spent_usd", 0) + sum(d.get("cost_usd") or 0
                                                                               for d in questions), 2),
                             "pdf": bool((s.get("document") or {}).get("pdf"))})
            except (ValueError, KeyError, TypeError):  # a damaged file must not hide the other investigations
                continue
        return articles

    def _true_status(self, s: dict) -> str:
        """An investigation "in progress" on disk but without a process was interrupted (app closed, computer off)."""
        if s["status"] == "in_progress" and not self.in_execution(s["id"]):
            return "interrupted"
        return s["status"]

    def _active_mine(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def in_execution(self, ident: str) -> bool:
        """Also a process that outlived a previous server: the lock in the folder tells."""
        return (self._active_mine() and self.in_progress == ident) or active_process(self.folder / ident) is not None

    def active(self) -> bool:
        return self._active_mine() or any(active_process(f.parent) is not None
                                         for f in self.folder.glob(f"*/{LOCK_FILE}"))

    def launch(self, ident: str, document_only: bool = False) -> None:
        if self.active():
            raise ValueError("An investigation is already running: stop it or wait for it to finish.")
        folder = self.path(ident)
        python = ROOT / "venv" / "bin" / "python"
        with (folder / "errors.log").open("a", encoding="utf-8") as errors:
            self.process = subprocess.Popen(
                [str(python if python.exists() else sys.executable), "-m", "chain_research.investigation", str(folder)]
                + (["--document"] if document_only else []), cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
        self.in_progress = ident

    def update_limits(self, ident: str, data: dict) -> None:
        """New limits for a stopped investigation, before resuming it (only those given)."""
        if self.in_execution(ident):
            raise ValueError("This investigation is already running.")
        s = self.status(ident)
        budget = _number(data.get("budget_usd"), *configuration.INTERVALS["budget_usd"], "Budget")
        minutes = _number(data.get("max_minutes"), *configuration.INTERVALS["max_minutes"], "Maximum time")
        if budget is not None:
            s["limits"]["budget_usd"] = budget
        if minutes is not None:
            s["limits"]["max_minutes"] = minutes
        if "without_limits" in data:
            s["limits"]["without_limits"] = bool(data["without_limits"])
        file = self.path(ident) / "status.json"
        temporary = file.with_suffix(".tmp")
        temporary.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(file)

    def clean(self, ident: str) -> None:
        """Removes the page pieces read: they are only needed while the questions are running."""
        if self.in_execution(ident):
            raise ValueError("This investigation is running.")
        shutil.rmtree(self.path(ident) / "pieces", ignore_errors=True)

    def stop(self, ident: str) -> None:
        if self._active_mine() and self.in_progress == ident:
            self.process.send_signal(signal.SIGTERM)
            return
        pid = active_process(self.path(ident))
        if not pid:
            raise ValueError("This investigation is not running.")
        os.kill(pid, signal.SIGTERM)  # outlived a previous server

    def teardown(self) -> None:
        if self._active_mine():
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)


def create_app(token: str, port_number: Callable[[], int], handler: Manager | None = None) -> Starlette:
    handler = handler or Manager()

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    async def protected(function: Callable[[], Any]) -> JSONResponse:
        try:
            outcome = function()
            if asyncio.iscoroutine(outcome):
                outcome = await outcome
            return JSONResponse(outcome)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:  # noqa: BLE001
            log.exception("Server error")
            return JSONResponse({"error": f"Error: {e}"}, status_code=500)

    async def home(_request: Request) -> Response:
        return FileResponse(INTERFACE / "index.html")

    async def settings(request: Request) -> JSONResponse:
        if request.method == "POST":
            data = await body(request)
            return await protected(lambda: save_settings(data))
        return await protected(read_settings)

    async def investigations(request: Request) -> JSONResponse:
        if request.method == "POST":
            data = await body(request)

            def fresh() -> dict:
                if handler.active():
                    raise ValueError("An investigation is already running: stop it or wait for it to finish.")
                ind = Investigation.create(str(data.get("topic") or ""), director_model=data.get("director_model"),
                                    budget_usd=_number(data.get("budget_usd"), 0.5, 500, "Budget"),
                                    max_minutes=_number(data.get("max_minutes"), 10, 24 * 60, "Maximum time"),
                                    without_limits=bool(data.get("without_limits")), base_folder=handler.folder)
                handler.launch(ind.folder.name)
                return {"id": ind.folder.name}
            return await protected(fresh)
        return await protected(handler.listing)

    async def investigation(request: Request) -> JSONResponse:
        ident = request.path_params["ident"]

        def read() -> dict:
            s = handler.status(ident)
            return {**s, "status": handler._true_status(s)}
        return await protected(read)

    async def action(request: Request) -> JSONResponse:
        ident, name = request.path_params["ident"], request.path_params["action"]

        data = await body(request)

        def run() -> dict:
            if name == "stop":
                handler.stop(ident)
            elif name == "resume":
                if handler.active():
                    raise ValueError("An investigation is already running: stop it or wait for it to finish.")
                handler.update_limits(ident, data)
                handler.launch(ident)
            elif name == "clean":
                handler.clean(ident)
            elif name == "document":
                handler.launch(ident, document_only=True)
            elif name == "open":
                pdf = handler.path(ident) / "document.pdf"
                if not pdf.is_file():
                    raise ValueError("The PDF is not there yet.")
                subprocess.Popen(["xdg-open", str(pdf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif name == "folder":
                subprocess.Popen(["xdg-open", str(handler.path(ident))], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                raise ValueError("Unknown action.")
            return {"ok": True}
        return await protected(run)

    async def events(request: Request) -> Response:
        """The events of the investigation (SSE), from events.jsonl: first those already written, then the new ones."""
        ident = request.path_params["ident"]
        try:
            file = handler.path(ident) / "events.jsonl"
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        try:
            last = max(0, int(request.headers.get("last-event-id") or request.query_params.get("from") or 0))
        except ValueError:
            last = 0

        async def stream():
            sent, rounds = last, 0
            while True:
                rows = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
                for number in range(sent, len(rows)):
                    yield f"id: {number + 1}\ndata: {rows[number]}\n\n"
                sent = max(sent, len(rows))
                # Investigation over and no process at work: the stream ends (the app reopens it if it resumes).
                if rows and '"kind": "end"' in rows[-1] and not handler.in_execution(ident):
                    yield "event: closed\ndata: {}\n\n"  # only this closes the stream: an "end" may be from a past run
                    break
                rounds += 1
                if rounds % 30 == 0:
                    yield ": alive\n\n"
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    routes = [
        Route("/", home),
        Route("/api/settings", settings, methods=["GET", "POST"]),
        Route("/api/investigations", investigations, methods=["GET", "POST"]),
        Route("/api/investigations/{ident}", investigation),
        Route("/api/investigations/{ident}/events", events),
        Route("/api/investigations/{ident}/{action}", action, methods=["POST"]),
        Mount("/ui", StaticFiles(directory=INTERFACE)),
    ]
    @asynccontextmanager
    async def life_cycle(_app: Starlette):
        yield
        handler.teardown()  # the app closes: the running investigation stops (it can be resumed)

    app = Starlette(routes=routes, lifespan=life_cycle)
    app.add_middleware(Protection, token=token, port_number=port_number)
    app.state.handler = handler
    return app


def _number(value: Any, minimum: float, maximum: float, name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: must be a number.") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"{name}: must be between {minimum:g} and {maximum:g}.")
    return number


def read_settings() -> dict:
    imp = configuration.load()
    secrets = config_sources.secrets()
    contact, origin = wikipedia_contact()
    return {"director_model": imp["director_model"], "models": list(configuration.MODELS),
            "chain_model": imp["chain_model"], "review_model": imp["review_model"],
            "question_budget_usd": imp["question_budget_usd"],
            "question_pages": imp["question_pages"], "question_minutes": imp["question_minutes"],
            "budget_usd": imp["budget_usd"], "max_minutes": imp["max_minutes"], "without_limits": imp["without_limits"],
            "parallel_questions": imp["parallel_questions"],
            "wikipedia": {"active": secrets.get("wikipedia_contact_active") is not False,
                          "contact": str(secrets.get("wikipedia_contact") or ""),
                          "in_use": contact, "origin": origin}}


def save_settings(data: dict) -> dict:
    changes: dict[str, Any] = {}
    for key in ("director_model", "chain_model", "review_model"):
        if key in data:
            if data[key] not in configuration.MODELS:
                raise ValueError("Invalid model.")
            changes[key] = data[key]
    for key, name in (("budget_usd", "Budget"), ("max_minutes", "Time"), ("parallel_questions", "Parallel questions"),
                         ("question_budget_usd", "Budget per question"), ("question_pages", "Pages per question"),
                         ("question_minutes", "Minutes per question")):
        if key in data:
            value = _number(data[key], *configuration.INTERVALS[key], name)
            if value is not None:
                changes[key] = int(value) if key in ("parallel_questions", "question_pages") else value
    if "without_limits" in data:
        changes["without_limits"] = bool(data["without_limits"])
    wiki = data.get("wikipedia")
    if isinstance(wiki, dict):
        contact = " ".join(str(wiki.get("contact") or "").split())
        if contact and not valid_contact(contact):
            raise ValueError("Wikipedia contact: an email or a web address (http:// or https://), without spaces.")
        config_sources.save_secrets({"wikipedia_contact_active": bool(wiki.get("active", True)),
                                    "wikipedia_contact": contact})
    if changes:
        configuration.save(changes)
    return read_settings()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    token = os.environ.get("CHAIN_TOKEN", "")
    if len(token) < 32:
        sys.exit("CHAIN_TOKEN missing or too short: the server must be started by the app.")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port_number = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(token, lambda: port_number), log_level="warning", access_log=False))
    print(json.dumps({"port": port_number}), flush=True)
    server.run(sockets=[listener])


if __name__ == "__main__":
    main()
