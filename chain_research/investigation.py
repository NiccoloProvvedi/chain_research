"""Indagine su un argomento: Claude prepara le domande, la catena risponde, il ciclo si ripete, alla fine il PDF.

1. Piano (regia): titolo, obiettivo, sezioni del documento e le prime domande, ognuna un dato preciso.
2. Risposte: ogni domanda va alla catena in un processo a sé (`python -m chain_research.question`), alcune in
   parallelo, ognuna con il suo browser.
3. Valutazione (regia): copertura di ogni sezione, se la conoscenza è solida, nuove domande sui buchi e sui nomi
   emersi. Si torna al punto 2 finché la regia non la ritiene solida, o finiscono budget, tempo o giri.
4. Documento (documento.py): sezioni scritte dalle risposte con le fonti, sintesi, appendice, PDF.

Tutto lo stato sta in `indagini/<cartella>/stato.json` (si riprende dopo un'interruzione) e gli eventi in
`eventi.jsonl` (l'app li mostra). Uso: `python -m chain_research.investigation <cartella>` (riprende o avvia).
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

MAX_ROUNDS = 12               # anche senza limiti: oltre, le domande nuove aggiungono poco
QUESTION_ESTIMATE_USD = 0.20        # prima di avere costi osservati (banco: media 0,14 $, sbagliate 0,34 $)
COSTS_PER_ESTIMATE = 5          # da quante domande finite vale il 75° percentile dei costi osservati
DOCUMENT_RESERVE_MINUTES = 5    # indagini del 1-2/11: una sezione costa circa 15 secondi
MIN_QUESTION_MINUTES = 10      # sotto, una domanda non fa in tempo (mediana 5 minuti, media 6,6)
GUARD_MARGIN_S = 600         # oltre i minuti della domanda (per scrivere la risposta): poi il processo si chiude
MIN_IMPROVEMENT = 0.05     # copertura media (0-3) che un giro deve aggiungere perché ne serva un altro
LOCK_FILE = "process.lock"
RUN_SETTINGS = ("parallel_questions", "question_budget_usd", "question_pages", "question_minutes",
                            "review_model")
# Costo stimato di una chiamata di scrittura del documento per modello (una per sezione, più la cornice).
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
    base = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:50].strip("-") or "indagine"
    return f"{datetime.now():%Y-%m-%d-%H%M}-{base}"


def row(text: str, label: str) -> str:
    """Il contenuto di una riga «**Etichetta:** …» della risposta della catena ("" se manca)."""
    found = re.search(rf"\*\*{label}:?\*\*:?\s*(.+)", text or "")
    return found.group(1).strip() if found else ""


def acquire_lock(folder: Path) -> Any | None:
    """Il blocco dell'indagine, tenuto dal suo processo finché vive (anche se muore di colpo il sistema lo rilascia), o
    None se un altro processo la sta già eseguendo."""
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
    """Il pid del processo che sta eseguendo l'indagine (0 se non ancora scritto), None se non ce n'è nessuno."""
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
    """Le decisioni dell'indagine e la scrittura del documento, con il modello scelto dall'utente."""

    def __init__(self, model: str, to_cost: Callable[[float], None],
                 stopped: Callable[[], bool] = lambda: False,
                 to_model: Callable[[str], None] = lambda _m: None) -> None:
        self.model = model
        self.to_cost = to_cost
        self.stopped = stopped
        self.to_model = to_model

    def ask(self, point: str, obj: Any, schema: dict | None, cap_usd: float = 2.0,
               wait: float = 900, rules: str | None = None) -> Any:
        """Una chiamata con schema (validato, una correzione) o di testo libero. Solleva ErroreClaude."""
        rules = rules or instructions(point)
        text = cleaning.data(obj)
        request = text
        for attempt in (1, 2):
            if self.stopped():
                raise claude_json.Interrupted("Indagine fermata.")
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
            request = f"{text}\n\nLA RISPOSTA PRECEDENTE NON ERA VALIDA:\n- " + "\n- ".join(problems)
        raise claude_json.ClaudeError(f"{point}: risposta non valida dopo la correzione")


class Investigation:
    def __init__(self, folder: Path, publish: Callable[[dict], None] | None = None) -> None:
        self.folder = Path(folder)
        self.status_file = self.folder / "status.json"
        self.status: dict = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.publish = publish or (lambda e: None)
        self._mutex = threading.RLock()  # lo stato si cambia da più thread (una domanda per thread)
        self._stopped = threading.Event()
        self._processes: dict[str, subprocess.Popen] = {}
        self._start = time.monotonic() - float(self.status.get("seconds", 0))
        self._session_limit = ""  # il messaggio, se l'abbonamento ha finito il limite durante la corsa
        self.director = Director(self.status["director_model"], self._add_cost_director, self._stopped.is_set,
                           self._add_model)

    # --- creazione e stato --------------------------------------------------------------------------------------

    @classmethod
    def create(cls, topic: str, *, director_model: str | None = None, budget_usd: float | None = None,
             max_minutes: float | None = None, without_limits: bool | None = None,
             base_folder: Path | None = None) -> "Investigation":
        settings = configuration.load()
        topic = " ".join(topic.split())
        if not 3 <= len(topic) <= 4000:
            raise ValueError("Scrivi l'argomento della ricerca (da 3 a 4000 caratteri).")
        model = director_model or settings["director_model"]
        if model not in configuration.MODELS:
            raise ValueError(f"Modello non valido: {model}")
        base = base_folder or configuration.folder("investigations_folder")
        folder = base / folder_name(topic)
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise ValueError("Hai appena avviato un'indagine sullo stesso argomento: riprova tra un minuto.") from None
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
        """Le impostazioni di questa corsa: quelle fissate alla creazione, anche se poi l'utente le cambia."""
        return {**configuration.load(), **(self.status.get("settings") or {})}

    def spent(self) -> float:
        return round(self.status["director_spent_usd"] + sum(d.get("cost_usd") or 0 for d in self.status["questions"]), 4)

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    # --- limiti -------------------------------------------------------------------------------------------------

    def _question_estimate(self) -> float:
        costs = sorted(d["cost_usd"] for d in self.status["questions"] if d.get("cost_usd"))
        if len(costs) >= COSTS_PER_ESTIMATE:
            return costs[math.ceil(0.75 * len(costs)) - 1]
        return max(QUESTION_ESTIMATE_USD, sum(costs) / len(costs)) if costs else QUESTION_ESTIMATE_USD

    def document_reserve(self) -> float:
        sections = len((self.status.get("plan") or {}).get("sections") or []) or 6
        return WRITING_ESTIMATE_USD.get(self.status["director_model"], 0.25) * (sections + 2)

    def can_start(self, in_progress: int) -> str:
        """"" se un'altra domanda può partire, altrimenti il motivo per cui no."""
        if self._stopped.is_set():
            return "indagine fermata"
        limits = self.status["limits"]
        if limits["without_limits"]:
            return ""
        busy = self.spent() + (in_progress + 1) * self._question_estimate() + self.document_reserve()
        if busy > limits["budget_usd"]:
            return f"budget di {limits['budget_usd']:.2f} $ quasi raggiunto (speso {self.spent():.2f} $)"
        if limits["max_minutes"] - DOCUMENT_RESERVE_MINUTES - self.elapsed() / 60 < MIN_QUESTION_MINUTES:
            return f"tempo massimo di {limits['max_minutes']:.0f} minuti quasi raggiunto"
        return ""

    def _question_minutes(self) -> float:
        """Minuti massimi per la prossima domanda: quelli della catena, o meno se il tempo dell'indagine finisce."""
        maximum = float(self._imp()["question_minutes"])
        limits = self.status["limits"]
        if limits["without_limits"]:
            return maximum
        rest = limits["max_minutes"] - DOCUMENT_RESERVE_MINUTES - self.elapsed() / 60
        return max(MIN_QUESTION_MINUTES, min(maximum, rest))

    def stop(self) -> None:
        """Ferma l'indagine: nessuna nuova domanda, quelle in corso si chiudono, come le chiamate della regia."""
        self._stopped.set()
        for process in list(self._processes.values()):
            if process.poll() is None:
                process.terminate()
        claude_json.interrupt()

    # --- svolgimento --------------------------------------------------------------------------------------------

    def run(self, document_only: bool = False) -> None:
        """Avvia o riprende l'indagine fino al documento."""
        from . import document
        for d in self.status["questions"]:
            if d["status"] == "in_progress" or (d["status"] == "error" and not document_only):  # si rifà
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
            self._phase("Scrivo il documento…")
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
        except Exception as error:  # noqa: BLE001 - lo stato resta salvato: l'indagine si può riprendere
            self.status["status"] = "error"
            self.status["end_reason"] = f"{type(error).__name__}: {error}"[:500]
            self.save()
            self.event({"kind": "end", "status": "error", "reason": self.status["end_reason"]})
            raise

    def _conclude_stopped(self) -> None:
        self.status["status"] = "stopped"
        if self._session_limit:
            self.status["end_reason"] = f"{self._session_limit} Riprendi l'indagine dopo il rinnovo."
        else:
            self.status["end_reason"] = self.status["end_reason"] or "fermata dall'utente"
        self.save()
        self.event({"kind": "end", "status": "stopped", "reason": self.status["end_reason"]})

    def _make_plan(self) -> None:
        self._phase("Claude prepara il piano e le prime domande…")
        plan = self.director.ask("plan_director", {"topic": self.status["topic"],
                                                  "today": f"{datetime.now():%Y-%m-%d}"}, PLAN_SCHEMA)
        self.status["plan"] = {k: plan[k] for k in ("title", "goal", "sections")}
        self.status["round"] = 1
        added = self._add_questions(plan["questions"], round_no=1)
        self.save()
        self.event({"kind": "plan", "plan": self.status["plan"], "questions": added,
                     "spent_usd": self.spent()})

    def _add_questions(self, questions: list[dict], round_no: int) -> list[dict]:
        """Nuove domande, senza i doppioni di quelle già fatte (stessa domanda o quasi: Jaccard ≥ 0,7)."""
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
                self._phase(f"Giro {self.status['round']}: {len(waiting)} domande alla catena…")
                reason = self._answer_questions(waiting)
                if reason:
                    self.status["end_reason"] = reason
                    break
                continue
            if self.status["round"] >= MAX_ROUNDS:
                self.status["end_reason"] = f"raggiunti {MAX_ROUNDS} giri di domande"
                break
            if self.can_start(0):  # niente budget per un altro giro: si scrive con quello che c'è
                self.status["end_reason"] = self.can_start(0)
                break
            if self._evaluate():
                break
        self.save()

    def _evaluate(self) -> bool:
        """La regia valuta la conoscenza; True se basta (o non ci sono domande nuove)."""
        self._phase(f"Claude valuta cosa si sa dopo il giro {self.status['round']}…")
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
            self.status["end_reason"] = ("Claude ritiene la conoscenza solida: " if evaluation["solid_knowledge"]
                                         else "la copertura non cresce più: " if stop
                                         else "nessuna domanda nuova utile: ") + evaluation["reason"]
            return True
        return False

    @staticmethod
    def _mean_coverage(evaluation: dict) -> float:
        coverages = [s["coverage"] for s in evaluation.get("sections") or []]
        return sum(coverages) / len(coverages) if coverages else 0.0

    def _how_many_more(self) -> int | None:
        """Quante domande si possono ancora finire con budget e tempo rimasti (None: nessun limite)."""
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
        """Le nuove domande, prima quelle delle sezioni meno coperte, solo quante si possono finire."""
        coverage = {s["id"]: s["coverage"] for s in evaluation["sections"]}
        sorted_items = sorted(evaluation["new_questions"], key=lambda d: coverage.get(d["section"], 0))
        return sorted_items[:self._how_many_more()]

    @staticmethod
    def in_short(d: dict) -> dict:
        """Una domanda e la sua risposta in breve, per la regia (il testo viene dal web: ripulito)."""
        answer = d.get("answer") or ""
        outcome = {"answer": "found", "not_found": "not found"}.get(d["status"], d["status"])
        # Lo stato lo decidono i controlli del programma (web/validazione.py), non la «Fiducia» scritta dal modello.
        return {"id": d["id"], "section": d["section"], "question": d["question"], "outcome": outcome,
                "answer": cleaning.text(row(answer, "Risposta"), origin="answer")[:400],
                "explanation": cleaning.text(row(answer, "Spiegazione"), origin="answer")[:400],
                "status": status_of(d) or outcome,
                "checks_limits": [cleaning.text(str(m), origin="answer")[:200]
                                         for m in (d.get("status_reasons") or [])[:4]],
                "sources": len(d.get("sources") or [])}

    # --- risposte con la catena ---------------------------------------------------------------------------------

    def _answer_questions(self, questions: list[dict]) -> str:
        """Risponde alle domande in parallelo; "" se tutte fatte, altrimenti il motivo per cui ci si è fermati."""
        parallel = max(1, int(self._imp()["parallel_questions"]))
        profiles = [f"profilo_{i + 1}" for i in range(parallel)]
        free = list(profiles)
        mutex = threading.Lock()
        reason = {"text": ""}
        queue = list(questions)
        in_progress = {"n": 0}
        fact = threading.Condition(mutex)

        def work(d: dict, profile: str) -> None:
            try:
                self._one_question(d, profile, parallel)
            except Exception as error:  # noqa: BLE001 - un errore qui non deve lasciare la domanda «in corso»
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
                                 # Limiti di Wikimedia, per tutte le domande insieme.
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
        if limit is not None:  # le altre domande troverebbero lo stesso limite: ci si ferma, «non trovata» sarebbe falso
            self._session_limit = error
            self.stop()
        if outcome is None and spent:  # la domanda è costata anche se non è finita; il totale la conta come spesa a parte
            self._add_cost_director(spent)
        with self._mutex:
            if outcome is None:
                d["status"] = "waiting" if self._stopped.is_set() else "error"
                d["error"] = error or ("tempo massimo della domanda superato" if expired.is_set() else
                                         "stopped" if self._stopped.is_set() else
                                         f"processo chiuso ({process.returncode})")
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
                     "answer": row(d.get("answer") or "", "Risposta")[:300], "error": d.get("error"),
                     "cost_usd": d.get("cost_usd"), "seconds": d.get("seconds"), "spent_usd": self.spent()})

    def _question_event(self, d: dict, event: dict) -> None:
        """Solo gli eventi utili all'app: fasi della catena e pagine lette."""
        if event.get("kind") == "phase":
            self.event({"kind": "question_phase", "id": d["id"], "text": str(event.get("text"))[:200]})
        elif event.get("kind") == "page" and event.get("status") == "read":
            self.event({"kind": "question_page", "id": d["id"], "url": event.get("url"),
                         "title": event.get("title")})


def main() -> int:
    """`python -m chain_research.investigation <cartella> [--documento]`: avvia o riprende l'indagine."""
    folder = Path(sys.argv[1])
    block = acquire_lock(folder)
    if block is None:
        print("Questa indagine è già in esecuzione in un altro processo.", file=sys.stderr)
        return 2
    investigation = Investigation(folder)
    signal.signal(signal.SIGTERM, lambda *_: investigation.stop())
    signal.signal(signal.SIGINT, lambda *_: investigation.stop())
    try:
        investigation.run(document_only="--documento" in sys.argv[2:])
    except Exception:  # noqa: BLE001 - già registrato nello stato
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
