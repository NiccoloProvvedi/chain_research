"""Chiamate a Claude: `claude -p` isolato, senza strumenti, con risposta JSON validata.

Nessuno strumento (`--tools ""`), nessun server
MCP, nessun hook, nessun CLAUDE.md, nessuna memoria automatica, cartella di lavoro vuota, istruzioni di sistema
proprie, `--json-schema` per la risposta e `--max-budget-usd` come tetto della singola chiamata. Ogni chiamata,
riuscita o no, scrive una riga in <cartella_log>/pensatore.jsonl con punto, modello, token (anche di cache), costo
e durata. I campi della riga `result` sono quelli osservati il 13/10 (fixture test/fixtures/orchestratore).
"""
import json
import re
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import ROOT
from . import event_log, replay
from . import config as config_thinker


class ClaudeError(RuntimeError):
    """Claude non ha potuto rispondere (non collegato, tempo scaduto, budget, risposta non valida)."""


class SessionLimit(ClaudeError):
    """Il limite di sessione dell'abbonamento è finito: rispondere «non trovata» sarebbe falso, l'indagine si ferma."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"Limite di sessione di Claude raggiunto: si rinnova tra circa {round(seconds / 60)} minuti.")
        self.seconds = seconds


class Interrupted(ClaudeError):
    """L'utente ha fermato l'indagine mentre Claude stava rispondendo."""


# Le chiamate in corso in questo processo, per fermarle (`interrompi`). Dizionario senza serrature: `interrompi` gira
# dentro un gestore di segnale e una serratura presa dal thread principale lo bloccherebbe per sempre.
_in_progress: dict[int, subprocess.Popen] = {}


def interrupt() -> None:
    """Chiude le chiamate in corso: chi le aspettava riceve Interrotta."""
    for process in list(_in_progress.values()):
        if process.poll() is None:
            process.interrupted = True
            process.terminate()


@dataclass
class Usage:
    point: str
    model: str
    exact_model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    outcome: str = "ok"


def empty_folder() -> Path:
    """Cartella di lavoro vuota per Claude, dentro il progetto."""
    folder = ROOT / "browser" / "claude-empty"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def command(model: str, instructions: str, schema: dict | None, max_budget_usd: float | None = None) -> list[str]:
    settings = {"disableAllHooks": True, "claudeMdExcludes": ["/**/CLAUDE.md", "/**/CLAUDE.local.md"]}
    rows = [shutil.which("claude") or "claude", "-p", "--input-format", "stream-json", "--output-format",
             "stream-json", "--verbose", "--model", model, "--tools", "", "--strict-mcp-config",
             "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence", "--settings", json.dumps(settings),
             "--system-prompt", instructions]
    if max_budget_usd is not None:
        rows += ["--max-budget-usd", f"{max_budget_usd:.4f}"]
    return rows + (["--json-schema", json.dumps(schema)] if schema else [])


def read_result(stdout: str) -> dict[str, Any] | None:
    result = None
    for row in stdout.splitlines():
        try:
            data = json.loads(row)
        except ValueError:
            continue
        if data.get("type") == "assistant" and "Not logged in" in json.dumps(data):
            raise ClaudeError("Claude non è collegato al tuo account: esegui `claude` e fai l'accesso.")
        if data.get("type") == "result":
            result = data
    return result


def usage_from(result: dict[str, Any], point: str, model: str, seconds: float) -> Usage:
    use = result.get("usage") or {}
    return Usage(point=point, model=model, exact_model=", ".join(result.get("modelUsage") or {}),
                   input_tokens=int(use.get("input_tokens") or 0),
                   output_tokens=int(use.get("output_tokens") or 0),
                   cache_read_tokens=int(use.get("cache_read_input_tokens") or 0),
                   cache_write_tokens=int(use.get("cache_creation_input_tokens") or 0),
                   cost_usd=float(result.get("total_cost_usd") or 0), seconds=round(seconds, 2))


def _run(rows: list[str], text: str, wait: float) -> str:
    message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    try:
        process = subprocess.Popen(rows, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    cwd=empty_folder(), env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    except FileNotFoundError:
        raise ClaudeError("Claude Code non è installato.") from None
    _in_progress[id(process)] = process
    try:
        stdout, _ = process.communicate(json.dumps(message) + "\n", timeout=wait)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise ClaudeError("Claude non ha risposto in tempo.") from None
    finally:
        _in_progress.pop(id(process), None)
    if getattr(process, "interrupted", False):
        raise Interrupted("Indagine fermata.")
    return stdout


# Secondi passati ad aspettare il rinnovo del limite di sessione (in questo processo): i limiti di tempo delle
# ricerche non li contano.
LIMIT_WAIT = {"seconds": 0.0}


# «You've hit your session limit», «You've hit your 5-hour limit» (10/11), «Claude AI usage limit reached».
_LIMIT_MESSAGE = re.compile(r"hit your [\w -]*limit|usage limit", re.I)


def seconds_to_renewal(stdout: str) -> float | None:
    """Se Claude risponde che il limite di sessione è finito ("resets 12:50am"), i secondi fino al rinnovo (più un
    minuto); altrimenti None. Senza un orario leggibile, 15 minuti. Conta solo una risposta di errore: il testo di una
    risposta normale può parlare di limiti."""
    result = None
    for row in stdout.splitlines():
        try:
            data = json.loads(row)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            result = data
    if result is not None and not result.get("is_error"):
        return None
    text = stdout if result is None else str(result.get("result") or "")
    if not _LIMIT_MESSAGE.search(text):
        return None
    match = re.search(r"resets (\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.I)
    if not match:
        return 900.0
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
    now = datetime.now()
    renewal = now.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
    if renewal <= now:
        renewal += timedelta(days=1)
    return min((renewal - now).total_seconds() + 60, 6 * 3600)


def ask(instructions: str, text: str, schema: dict | None, point: str, model: str | None = None,
           max_budget_usd: float | None = None, wait: float | None = None,
           plan: str = "") -> tuple[Any, Usage]:
    """Una richiesta a Claude. Con `schema` restituisce il dict `structured_output`, senza il testo libero.
    `max_budget_usd` è il tetto della chiamata (predefinito: `pensatore.max_chiamata_usd`)."""
    cfg = config_thinker.load()
    model = model or cfg.claude_model(point)
    cap = max_budget_usd if max_budget_usd is not None else cfg.thinker.max_call_usd
    rows = command(model, instructions, schema, cap)
    key = replay.fingerprint("claude", model, instructions, text, schema)
    start = time.monotonic()
    usage = Usage(point=point, model=model)
    try:
        if replay.in_replay():
            try:
                stdout = replay.read(key)
            except replay.UnrecordedRequest:
                # Scrittura e revisione vengono dopo la catena, una volta sola, e le loro istruzioni cambiano con i
                # controlli: la risposta registrata vale anche se la richiesta è cambiata (rigioco.leggi_unica).
                if point not in ("writing", "review"):
                    raise
                stdout = replay.unique_read(point)
        else:
            stdout = _run(rows, text, wait or cfg.thinker.wait_s)
            while os.environ.get("CHAIN_WAIT_FOR_LIMIT") and (pause := seconds_to_renewal(stdout)) is not None:
                # Prove lunghe (banco): con il limite di sessione dell'abbonamento si aspetta il rinnovo invece di
                # far fallire le domande a metà (16/10, 23:45-00:50: "You've hit your session limit").
                event_log.write("thinker", {"point": point, "model": model,
                                              "outcome": f"limite di sessione: attendo {int(pause)} s"})
                time.sleep(pause)
                LIMIT_WAIT["seconds"] += pause
                stdout = _run(rows, text, wait or cfg.thinker.wait_s)
            replay.save(key, {"point": point, "model": model, "text": text[:2000]}, stdout)
        if (pause := seconds_to_renewal(stdout)) is not None:
            raise SessionLimit(pause)
        result = read_result(stdout)
        if result:
            usage = usage_from(result, point, model, time.monotonic() - start)
        if not result or result.get("is_error"):
            reason = (result or {}).get("subtype") or "nessuna risposta"
            detail = " ".join(str((result or {}).get("result") or "").split())[:200]
            raise ClaudeError(f"Claude non ha dato una risposta valida ({reason}"
                               + (f": {detail}" if detail else "") + ").")
        if schema is not None:
            data = result.get("structured_output")
            if not isinstance(data, dict):
                raise ClaudeError("Claude non ha dato una risposta strutturata.")
        else:
            data = str(result.get("result") or "").strip()
            if not data:
                raise ClaudeError("Claude non ha scritto il testo.")
        return data, usage
    except ClaudeError as error:
        usage.outcome = str(error)[:200]
        error.usage = usage  # anche una chiamata fallita può essere costata (per esempio oltre il tetto)
        raise
    finally:
        usage.seconds = round(time.monotonic() - start, 2)
        event_log.write("thinker", {"plan": plan, "instructions_chars": len(instructions),
                                      "text_chars": len(text), "cap_usd": cap, **asdict(usage)})
