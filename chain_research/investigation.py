"""Investigation of a topic: Claude prepares the questions, the chain answers, the cycle repeats, finally the PDF.

1. Plan (director): title, goal, sections of the document and the first questions, each about one precise fact.
2. Answers: each question goes to the chain in its own process (`python -m chain_research.question`), some in
   parallel, each with its own browser.
3. Evaluation (director): coverage of each section, whether the knowledge is solid, new questions on the gaps and on
   the names that emerged. Back to step 2 until the director deems it solid, or budget, time or rounds run out.
4. Document (document.py): sections written from the answers with the sources, summary, appendix, PDF.

All the state is in `investigations/<folder>/status.json` (resumable after an interruption) and the events in
`events.jsonl` (the app shows them). Usage: `python -m chain_research.investigation <folder>` (resumes or starts).
"""
import fcntl
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from . import ROOT, configuration
from .document import status_of
from .model import claude_json, cleaning
from .model import query as duplicates_query
from .model.thinker import errors_schema, instructions
from .web.sources.wikipedia_graph import interval_per

MAX_ROUNDS = 12               # even without limits: beyond this, new questions add little
QUESTION_ESTIMATE_USD = 0.20        # before observed costs exist (bench: mean $0.14, wrong ones $0.34)
COSTS_PER_ESTIMATE = 5          # finished questions needed before the 75th percentile of observed costs is used
DOCUMENT_RESERVE_MINUTES = 5    # investigations of 1-2 Nov: a section takes about 15 seconds
MIN_QUESTION_MINUTES = 10      # below this, a question has no time to finish (median 5 minutes, mean 6.6)
GUARD_MARGIN_S = 600         # on top of the question minutes (to write the answer): then the process is closed
MIN_IMPROVEMENT = 0.05     # mean coverage (0-3) a round must add for another round to be worth it
LOCK_FILE = "process.lock"
RUN_SETTINGS = ("parallel_questions", "question_budget_usd", "question_pages", "question_minutes",
                            "review_model")
# Estimated cost of one document writing call per model (one per section, plus the frame).
WRITING_ESTIMATE_USD = {"haiku": 0.03, "sonnet": 0.06, "opus": 0.40}
QUESTIONS_SCHEMA = {"type": "array", "maxItems": 16, "items": {
    "type": "object", "additionalProperties": False, "required": ["section", "question", "reason"],
    "properties": {"section": {"type": "string", "maxLength": 20},
                   "question": {"type": "string", "minLength": 10, "maxLength": 400},
                   "reason": {"type": "string", "maxLength": 300}}}}
SECTIONS_SCHEMA = {"type": "array", "maxItems": 10, "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "title", "purpose"],
    "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 20},
                   "title": {"type": "string", "minLength": 2, "maxLength": 150},
                   "purpose": {"type": "string", "maxLength": 400}}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["title", "goal", "sections", "questions"],
                "properties": {"title": {"type": "string", "minLength": 3, "maxLength": 200},
                               "goal": {"type": "string", "maxLength": 1000},
                               "sections": {**SECTIONS_SCHEMA, "minItems": 2}, "questions": {**QUESTIONS_SCHEMA, "minItems": 3}}}
EVALUATION_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["sections", "solid_knowledge", "reason", "new_questions", "new_sections"],
                 "properties": {
                     "sections": {"type": "array", "items": {
                         "type": "object", "additionalProperties": False, "required": ["id", "coverage", "note"],
                         "properties": {"id": {"type": "string"}, "coverage": {"type": "integer", "minimum": 0,
                                                                                  "maximum": 3},
                                        "note": {"type": "string", "maxLength": 400}}}},
                     "solid_knowledge": {"type": "boolean"},
                     "reason": {"type": "string", "maxLength": 600},
                     "new_questions": {**QUESTIONS_SCHEMA, "maxItems": 12},
                     "new_sections": {**SECTIONS_SCHEMA, "maxItems": 4}}}


def _hour() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def folder_name(topic: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:50].strip("-") or "investigation"
    return f"{datetime.now():%Y-%m-%d-%H%M}-{base}"


def row(text: str, label: str) -> str:
    """The content of a "**Label:** …" line of the chain's answer ("" if missing)."""
    found = re.search(rf"\*\*{label}:?\*\*:?\s*(.+)", text or "")
    return found.group(1).strip() if found else ""


def acquire_lock(folder: Path) -> Any | None:
    """The investigation lock, held by its process while it lives (the system releases it even on a sudden death), or
    None if another process is already running the investigation."""
    file = (folder / LOCK_FILE).open("a+", encoding="utf-8")
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    file.seek(0)
    file.truncate()
    file.write(str(os.getpid()))
    file.flush()
    return file


def active_process(folder: Path) -> int | None:
    """The pid of the process running the investigation (0 if not written yet), None if there is none."""
    path = folder / LOCK_FILE
    if not path.is_file():
        return None
    with path.open("a+", encoding="utf-8") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.seek(0)
            text = file.read().strip()
            return int(text) if text.isdigit() else 0
        fcntl.flock(file, fcntl.LOCK_UN)
    return None


class Director:
    """The decisions of the investigation and the writing of the document, with the model chosen by the user."""

    def __init__(self, model: str, to_cost: Callable[[float], None],
                 stopped: Callable[[], bool] = lambda: False,
                 to_model: Callable[[str], None] = lambda _m: None) -> None:
        self.model = model
        self.to_cost = to_cost
        self.stopped = stopped
        self.to_model = to_model

    def ask(self, point: str, obj: Any, schema: dict | None, cap_usd: float = 2.0,
               wait: float = 900, rules: str | None = None) -> Any:
        """A call with a schema (validated, one correction) or with free text. Raises ClaudeError."""
        rules = rules or instructions(point)
        text = cleaning.data(obj)
        request = text
        for attempt in (1, 2):
            if self.stopped():
                raise claude_json.Interrupted("Investigation stopped.")
            try:
                data, usage = claude_json.ask(rules, request, schema, point, model=self.model,
                                                   max_budget_usd=cap_usd, wait=wait)
            except (claude_json.SessionLimit, claude_json.Interrupted):
                raise
            except claude_json.ClaudeError as error:
                cost = getattr(getattr(error, "usage", None), "cost_usd", 0.0)
                self.to_cost(cost)
                if attempt == 2:
                    raise
                continue
            self.to_cost(usage.cost_usd)
            self.to_model(usage.exact_model)
            problems = errors_schema(data, schema) if schema is not None else []
            if not problems:
                return data
            request = f"{text}\n\nTHE PREVIOUS ANSWER WAS NOT VALID:\n- " + "\n- ".join(problems)
        raise claude_json.ClaudeError(f"{point}: answer not valid after the correction")


class Investigation:
    def __init__(self, folder: Path, publish: Callable[[dict], None] | None = None) -> None:
        self.folder = Path(folder)
        self.status_file = self.folder / "status.json"
        self.status: dict = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.publish = publish or (lambda e: None)
        self._mutex = threading.RLock()  # the status is changed from several threads (one question per thread)
        self._stopped = threading.Event()
        self._processes: dict[str, subprocess.Popen] = {}
        self._start = time.monotonic() - float(self.status.get("seconds", 0))
        self._session_limit = ""  # the message, if the subscription hit its limit during the run
        self.director = Director(self.status["director_model"], self._add_cost_director, self._stopped.is_set,
                           self._add_model)

    # --- creation and status ------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, topic: str, *, director_model: str | None = None, budget_usd: float | None = None,
             max_minutes: float | None = None, without_limits: bool | None = None,
             base_folder: Path | None = None) -> "Investigation":
        settings = configuration.load()
        topic = " ".join(topic.split())
        if not 3 <= len(topic) <= 4000:
            raise ValueError("Write the topic of the research (3 to 4000 characters).")
        model = director_model or settings["director_model"]
        if model not in configuration.MODELS:
            raise ValueError(f"Invalid model: {model}")
        base = base_folder or configuration.folder("investigations_folder")
        folder = base / folder_name(topic)
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise ValueError("You have just started an investigation on the same topic: try again in a minute.") from None
        status = {
            "id": folder.name, "topic": topic, "created": _hour(), "status": "waiting", "phase": "",
            "director_model": model, "chain_model": settings["chain_model"],
            "settings": {k: settings[k] for k in RUN_SETTINGS},
            "limits": {"without_limits": bool(settings["without_limits"] if without_limits is None else without_limits),
                       "budget_usd": float(budget_usd if budget_usd is not None else settings["budget_usd"]),
                       "max_minutes": float(max_minutes if max_minutes is not None else settings["max_minutes"])},
            "plan": None, "questions": [], "evaluations": [], "round": 0, "director_spent_usd": 0.0, "seconds": 0,
            "end_reason": "", "document": None,
        }
        (folder / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        return cls(folder)

    def save(self) -> None:
        with self._mutex:
            self.status["seconds"] = round(time.monotonic() - self._start)
            temporary = self.status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.status, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.replace(self.status_file)

    def event(self, event: dict) -> None:
        article = {"when": _hour(), **event}
        with self._mutex:
            with (self.folder / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(article, ensure_ascii=False, default=str) + "\n")
        self.publish(article)

    def _phase(self, text: str) -> None:
        self.status["phase"] = text
        self.save()
        self.event({"kind": "phase", "text": text})

    def _add_cost_director(self, cost: float) -> None:
        with self._mutex:
            self.status["director_spent_usd"] = round(self.status["director_spent_usd"] + cost, 4)

    def _add_model(self, models: str) -> None:
        with self._mutex:
            known = set(self.status.get("director_models") or [])
            self.status["director_models"] = sorted(known | {m.strip() for m in models.split(",") if m.strip()})

    def _imp(self) -> dict[str, Any]:
        """The settings of this run: those fixed at creation, even if the user changes them later."""
        return {**configuration.load(), **(self.status.get("settings") or {})}

    def spent(self) -> float:
        return round(self.status["director_spent_usd"] + sum(d.get("cost_usd") or 0 for d in self.status["questions"]), 4)

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    # --- limits -------------------------------------------------------------------------------------------------------

    def _question_estimate(self) -> float:
        costs = sorted(d["cost_usd"] for d in self.status["questions"] if d.get("cost_usd"))
        if len(costs) >= COSTS_PER_ESTIMATE:
            return costs[math.ceil(0.75 * len(costs)) - 1]
        return max(QUESTION_ESTIMATE_USD, sum(costs) / len(costs)) if costs else QUESTION_ESTIMATE_USD

    def document_reserve(self) -> float:
        sections = len((self.status.get("plan") or {}).get("sections") or []) or 6
        return WRITING_ESTIMATE_USD.get(self.status["director_model"], 0.25) * (sections + 2)

    def can_start(self, in_progress: int) -> str:
        """"" if another question can start, otherwise the reason why not."""
        if self._stopped.is_set():
            return "investigation stopped"
        limits = self.status["limits"]
        if limits["without_limits"]:
            return ""
        busy = self.spent() + (in_progress + 1) * self._question_estimate() + self.document_reserve()
        if busy > limits["budget_usd"]:
            return f"budget of ${limits['budget_usd']:.2f} almost reached (spent ${self.spent():.2f})"
        if limits["max_minutes"] - DOCUMENT_RESERVE_MINUTES - self.elapsed() / 60 < MIN_QUESTION_MINUTES:
            return f"maximum time of {limits['max_minutes']:.0f} minutes almost reached"
        return ""

    def _question_minutes(self) -> float:
        """Maximum minutes for the next question: those of the chain, or fewer if the investigation's time runs out."""
        maximum = float(self._imp()["question_minutes"])
        limits = self.status["limits"]
        if limits["without_limits"]:
            return maximum
        rest = limits["max_minutes"] - DOCUMENT_RESERVE_MINUTES - self.elapsed() / 60
        return max(MIN_QUESTION_MINUTES, min(maximum, rest))

    def stop(self) -> None:
        """Stops the investigation: no new questions; running questions and director calls are closed."""
        self._stopped.set()
        for process in list(self._processes.values()):
            if process.poll() is None:
                process.terminate()
        claude_json.interrupt()

    # --- execution ----------------------------------------------------------------------------------------------------

    def run(self, document_only: bool = False) -> None:
        """Starts or resumes the investigation up to the document."""
        from . import document
        for d in self.status["questions"]:
            if d["status"] == "in_progress" or (d["status"] == "error" and not document_only):  # redone
                d["status"] = "waiting"
            if d["status"] == "waiting":
                d.pop("error", None)
        if not document_only:
            self.status["end_reason"] = ""
        self.status["status"] = "in_progress"
        self.save()
        self.event({"kind": "start", "topic": self.status["topic"], "director_model": self.status["director_model"],
                     "limits": self.status["limits"]})
        try:
            if not document_only:
                if self.status["plan"] is None:
                    self._make_plan()
                self._cycle()
            if self._stopped.is_set() and not document_only:
                self._conclude_stopped()
                return
            self._phase("Writing the document…")
            self.status["document"] = document.write(self)
            self.status["status"] = "finished"
            self.save()
            self.event({"kind": "end", "status": "finished", "reason": self.status["end_reason"],
                         "document": self.status["document"], "spent_usd": self.spent()})
        except claude_json.SessionLimit as error:
            self._session_limit = str(error)
            self._conclude_stopped()
        except claude_json.Interrupted:
            self._conclude_stopped()
        except Exception as error:  # noqa: BLE001 - the status stays saved: the investigation can be resumed
            self.status["status"] = "error"
            self.status["end_reason"] = f"{type(error).__name__}: {error}"[:500]
            self.save()
            self.event({"kind": "end", "status": "error", "reason": self.status["end_reason"]})
            raise

    def _conclude_stopped(self) -> None:
        self.status["status"] = "stopped"
        if self._session_limit:
            self.status["end_reason"] = f"{self._session_limit} Resume the investigation after the renewal."
        else:
            self.status["end_reason"] = self.status["end_reason"] or "stopped by the user"
        self.save()
        self.event({"kind": "end", "status": "stopped", "reason": self.status["end_reason"]})

    def _make_plan(self) -> None:
        self._phase("Claude is preparing the plan and the first questions…")
        plan = self.director.ask("plan_director", {"topic": self.status["topic"],
                                                  "today": f"{datetime.now():%Y-%m-%d}"}, PLAN_SCHEMA)
        self.status["plan"] = {k: plan[k] for k in ("title", "goal", "sections")}
        self.status["round"] = 1
        added = self._add_questions(plan["questions"], round_no=1)
        self.save()
        self.event({"kind": "plan", "plan": self.status["plan"], "questions": added,
                     "spent_usd": self.spent()})

    def _add_questions(self, questions: list[dict], round_no: int) -> list[dict]:
        """New questions, without duplicates of those already asked (same question or nearly: Jaccard ≥ 0.7)."""
        sections = {s["id"] for s in self.status["plan"]["sections"]}
        done = [d["question"] for d in self.status["questions"]]
        added = []
        for d in questions:
            text = " ".join(str(d["question"]).split())
            if duplicates_query.duplicate(text, done, 0.7):
                continue
            done.append(text)
            article = {"id": f"d{len(self.status['questions']) + 1}", "round": round_no,
                    "section": d["section"] if d["section"] in sections else self.status["plan"]["sections"][0]["id"],
                    "question": text, "reason": str(d.get("reason") or ""), "status": "waiting"}
            self.status["questions"].append(article)
            added.append(article)
        return added

    def _cycle(self) -> None:
        while not self._stopped.is_set():
            waiting = [d for d in self.status["questions"] if d["status"] == "waiting"]
            if waiting:
                self._phase(f"Round {self.status['round']}: {len(waiting)} questions to the chain…")
                reason = self._answer_questions(waiting)
                if reason:
                    self.status["end_reason"] = reason
                    break
                continue
            if self.status["round"] >= MAX_ROUNDS:
                self.status["end_reason"] = f"reached {MAX_ROUNDS} rounds of questions"
                break
            if self.can_start(0):  # no budget for another round: write with what there is
                self.status["end_reason"] = self.can_start(0)
                break
            if self._evaluate():
                break
        self.save()

    def _evaluate(self) -> bool:
        """The director evaluates the knowledge; True if it is enough (or there are no new questions)."""
        self._phase(f"Claude is evaluating what is known after round {self.status['round']}…")
        evaluation = self.director.ask("director_evaluate", {
            "plan": self.status["plan"], "today": f"{datetime.now():%Y-%m-%d}",
            "questions": [self.in_short(d) for d in self.status["questions"]]}, EVALUATION_SCHEMA)
        self.status["round"] += 1
        for s in evaluation["new_sections"]:
            if s["id"] not in {x["id"] for x in self.status["plan"]["sections"]}:
                self.status["plan"]["sections"].append(s)
        coverage = self._mean_coverage(evaluation)
        previous = self.status["evaluations"]
        stop = bool(previous) and coverage < self._mean_coverage(previous[-1]) + MIN_IMPROVEMENT
        added = [] if evaluation["solid_knowledge"] or stop else \
            self._add_questions(self._new_choices(evaluation), round_no=self.status["round"])
        self.status["evaluations"].append({"round": self.status["round"] - 1, "when": _hour(), **evaluation,
                                          "added_questions": [d["id"] for d in added]})
        self.save()
        self.event({"kind": "evaluation", "round": self.status["round"] - 1, "sections": evaluation["sections"],
                     "solid_knowledge": evaluation["solid_knowledge"], "reason": evaluation["reason"],
                     "questions": added, "spent_usd": self.spent()})
        if evaluation["solid_knowledge"] or not added:
            self.status["end_reason"] = ("Claude deems the knowledge solid: " if evaluation["solid_knowledge"]
                                         else "coverage no longer grows: " if stop
                                         else "no useful new question: ") + evaluation["reason"]
            return True
        return False

    @staticmethod
    def _mean_coverage(evaluation: dict) -> float:
        coverages = [s["coverage"] for s in evaluation.get("sections") or []]
        return sum(coverages) / len(coverages) if coverages else 0.0

    def _how_many_more(self) -> int | None:
        """How many questions can still be finished with the budget and time left (None: no limits)."""
        limits = self.status["limits"]
        if limits["without_limits"]:
            return None
        parallel = max(1, int(self._imp()["parallel_questions"]))
        per_budget = (limits["budget_usd"] - self.spent() - self.document_reserve()) / self._question_estimate()
        times = [d["seconds"] for d in self.status["questions"] if d.get("seconds")]
        question_minutes = sum(times) / len(times) / 60 if times else 7.0
        rest = limits["max_minutes"] - DOCUMENT_RESERVE_MINUTES - MIN_QUESTION_MINUTES - self.elapsed() / 60
        per_time = parallel * (max(0.0, rest) / question_minutes + 1)
        return max(1, int(min(per_budget, per_time)))

    def _new_choices(self, evaluation: dict) -> list[dict]:
        """The new questions, those of the least covered sections first, only as many as can be finished."""
        coverage = {s["id"]: s["coverage"] for s in evaluation["sections"]}
        sorted_items = sorted(evaluation["new_questions"], key=lambda d: coverage.get(d["section"], 0))
        return sorted_items[:self._how_many_more()]

    @staticmethod
    def in_short(d: dict) -> dict:
        """A question and its answer in short, for the director (the text comes from the web: cleaned)."""
        answer = d.get("answer") or ""
        outcome = {"answer": "found", "not_found": "not found"}.get(d["status"], d["status"])
        # The status is decided by the program's checks (web/validation.py), not by the "Confidence" written by the model.
        return {"id": d["id"], "section": d["section"], "question": d["question"], "outcome": outcome,
                "answer": cleaning.text(row(answer, "Answer"), origin="answer")[:400],
                "explanation": cleaning.text(row(answer, "Explanation"), origin="answer")[:400],
                "status": status_of(d) or outcome,
                "checks_limits": [cleaning.text(str(m), origin="answer")[:200]
                                         for m in (d.get("status_reasons") or [])[:4]],
                "sources": len(d.get("sources") or [])}

    # --- answers with the chain ---------------------------------------------------------------------------------------

    def _answer_questions(self, questions: list[dict]) -> str:
        """Answers the questions in parallel; "" if all done, otherwise the reason for stopping."""
        parallel = max(1, int(self._imp()["parallel_questions"]))
        profiles = [f"profile_{i + 1}" for i in range(parallel)]
        free = list(profiles)
        mutex = threading.Lock()
        reason = {"text": ""}
        queue = list(questions)
        in_progress = {"n": 0}
        fact = threading.Condition(mutex)

        def work(d: dict, profile: str) -> None:
            try:
                self._one_question(d, profile, parallel)
            except Exception as error:  # noqa: BLE001 - an error here must not leave the question "in progress"
                with self._mutex:
                    d.update(status="error", error=f"{type(error).__name__}: {error}"[:500], finished=_hour())
                self.save()
                self.event({"kind": "question", "id": d["id"], "status": "error", "question": d["question"],
                             "error": d["error"], "spent_usd": self.spent()})
            finally:
                with fact:
                    free.append(profile)
                    in_progress["n"] -= 1
                    fact.notify_all()

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            while queue:
                with fact:
                    while not free:
                        fact.wait()
                    why = self.can_start(in_progress["n"])
                    if why:
                        reason["text"] = why
                        break
                    d = queue.pop(0)
                    profile = free.pop(0)
                    in_progress["n"] += 1
                executor.submit(work, d, profile)
        return reason["text"]

    @staticmethod
    def _question_command() -> list[str]:
        python = ROOT / "venv" / "bin" / "python"
        return [str(python if python.exists() else sys.executable), "-m", "chain_research.question"]

    def _one_question(self, d: dict, profile: str, parallel: int) -> None:
        settings = self._imp()
        minutes = self._question_minutes()
        with self._mutex:
            d.update(status="in_progress", started=_hour())
        self.save()
        self.event({"kind": "question", "id": d["id"], "status": "in_progress", "question": d["question"]})
        request = {"question": d["question"], "profile": str(configuration.folder("profiles_folder") / profile),
                     "pieces_folder": str(self.folder / "pieces"),
                     "options": {"claude_model": self.status["chain_model"],
                                 "review_model": settings["review_model"],
                                 "budget_claude": float(settings["question_budget_usd"]),
                                 "chain_pages": int(settings["question_pages"]),
                                 "chain_minutes": minutes,
                                 # Wikimedia limits, for all the questions together.
                                 "wikipedia_interval": interval_per(parallel)}}
        with (self.folder / "errors.log").open("a", encoding="utf-8") as errors_log:
            process = subprocess.Popen(self._question_command(), cwd=ROOT, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=errors_log, text=True,
                                        start_new_session=True)
        self._processes[d["id"]] = process
        outcome, error, spent, limit = None, "", 0.0, None
        expired = threading.Event()

        def deadline() -> None:
            expired.set()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass

        guard = threading.Timer(minutes * 60 + GUARD_MARGIN_S, deadline)
        guard.daemon = True
        guard.start()
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "event" in message:
                    event = message["event"]
                    spent = max(spent, float(event.get("spent_usd") or 0))
                    self._question_event(d, event)
                elif "outcome" in message:
                    outcome = message["outcome"]
                elif "error" in message:
                    error = message["error"]
                    spent = max(spent, float(message.get("cost_usd") or 0))
                    limit = message.get("session_limit")
            process.wait()
        finally:
            guard.cancel()
            self._processes.pop(d["id"], None)
        if limit is not None:  # the other questions would hit the same limit: stop, "not found" would be false
            self._session_limit = error
            self.stop()
        if outcome is None and spent:  # the question cost money even if unfinished; the total counts it as a separate expense
            self._add_cost_director(spent)
        with self._mutex:
            if outcome is None:
                d["status"] = "waiting" if self._stopped.is_set() else "error"
                d["error"] = error or ("maximum time of the question exceeded" if expired.is_set() else
                                         "stopped" if self._stopped.is_set() else
                                         f"process closed ({process.returncode})")
            else:
                d.update(status="answer" if outcome.get("found") else "not_found", answer=outcome["answer"],
                         value=outcome.get("value"), sources=outcome["sources"], cost_usd=outcome["cost_usd"],
                         confirmed=bool(outcome.get("confirmed")), answer_status=outcome.get("status"),
                         status_reasons=((outcome.get("chain") or {}).get("checks") or {}).get("reasons") or [],
                         proofs=(outcome.get("chain") or {}).get("proofs") or [],
                         retry=outcome.get("retry"),
                         models=(outcome.get("thinker") or {}).get("models"),
                         seconds=outcome["seconds"], read_pages=outcome.get("read_pages"),
                         chain_reason=outcome.get("end_reason"))
            d["finished"] = _hour()
        self.save()
        self.event({"kind": "question", "id": d["id"], "status": d["status"], "question": d["question"],
                     "answer": row(d.get("answer") or "", "Answer")[:300], "error": d.get("error"),
                     "cost_usd": d.get("cost_usd"), "seconds": d.get("seconds"), "spent_usd": self.spent()})

    def _question_event(self, d: dict, event: dict) -> None:
        """Only the events useful to the app: chain phases and pages read."""
        if event.get("kind") == "phase":
            self.event({"kind": "question_phase", "id": d["id"], "text": str(event.get("text"))[:200]})
        elif event.get("kind") == "page" and event.get("status") == "read":
            self.event({"kind": "question_page", "id": d["id"], "url": event.get("url"),
                         "title": event.get("title")})


def main() -> int:
    """`python -m chain_research.investigation <folder> [--document]`: starts or resumes the investigation."""
    folder = Path(sys.argv[1])
    block = acquire_lock(folder)
    if block is None:
        print("This investigation is already running in another process.", file=sys.stderr)
        return 2
    investigation = Investigation(folder)
    signal.signal(signal.SIGTERM, lambda *_: investigation.stop())
    signal.signal(signal.SIGINT, lambda *_: investigation.stop())
    try:
        investigation.run(document_only="--document" in sys.argv[2:])
    except Exception:  # noqa: BLE001 - already recorded in the status
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
