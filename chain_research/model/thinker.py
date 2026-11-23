"""Il pensatore: Claude come lettore della catena, con budget e predefiniti del programma.

Claude riceve dati brevi e strutturati (righe numerate, mai pagine intere da navigare) e risponde con JSON validato
da uno schema. Se Claude non risponde, se la risposta non è valida dopo una correzione o se il budget della domanda
non basta, il punto usa il suo predefinito e la ricerca prosegue: il risultato lo dice.
"""
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from . import claude_json, cleaning, event_log

PROMPT_FOLDER = Path(__file__).resolve().parent / "prompts"

# Prezzi per la stima prudente prima della chiamata (dollari per milione di token): ingresso scritto in cache
# (il caso peggiore della prima chiamata) e uscita. Dopo la prima chiamata conta il costo osservato.
INPUT_PRICE = 7.5
OUTPUT_PRICE = 30.0
ESTIMATED_OUTPUT = 1500
LOG_TAIL = 256 * 1024  # abbastanza per le ultime 200 chiamate

SCHEMAS: dict[str, dict] = {
    "reading": {
        "type": "object", "additionalProperties": False,
        "required": ["notes", "relevance", "concepts", "ties", "facts"],
        "properties": {
            "ties": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["parent", "relation", "a", "sentence", "translation", "why"],
                "properties": {"parent": {"type": "string", "minLength": 1, "maxLength": 120},
                               "relation": {"type": "string", "minLength": 2, "maxLength": 80},
                               "a": {"type": "string", "minLength": 1, "maxLength": 120},
                               "sentence": {"type": "string", "minLength": 3, "maxLength": 300},
                               "translation": {"type": "string", "maxLength": 400},
                               "why": {"type": "string", "maxLength": 200}}}},
            "facts": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["concept", "attribute", "value", "sentence", "translation"],
                "properties": {"concept": {"type": "string", "minLength": 1, "maxLength": 120},
                               "attribute": {"type": "string", "minLength": 2, "maxLength": 60},
                               "value": {"type": "string", "minLength": 1, "maxLength": 150},
                               "sentence": {"type": "string", "minLength": 3, "maxLength": 300},
                               "translation": {"type": "string", "maxLength": 400}}}},
            "notes": {"type": "string", "maxLength": 8000},
            "relevance": {"type": "integer", "minimum": 0, "maximum": 3},
            "concepts": {"type": "array", "maxItems": 12, "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "sentence", "translation"],
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120},
                               "sentence": {"type": "string", "minLength": 3, "maxLength": 300},
                               "translation": {"type": "string", "maxLength": 400}}}},
        },
    },
    "interpret": {
        "type": "object", "additionalProperties": False, "required": ["rephrasings", "clues", "sub_questions"],
        "properties": {
            "rephrasings": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 500}},
            "clues": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False, "required": ["clue", "refers_to", "readings"],
                "properties": {"clue": {"type": "string", "maxLength": 200},
                               "refers_to": {"type": "string", "maxLength": 300},
                               "readings": {"type": "array", "maxItems": 3,
                                           "items": {"type": "string", "maxLength": 200}}}}},
            "sub_questions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
        },
    },
    "chain": {
        "type": "object", "additionalProperties": False,
        "required": ["starting_point", "steps", "anchors", "answer", "research_language", "languages", "reason"],
        "properties": {
            "starting_point": {"type": "object", "additionalProperties": False, "required": ["clue", "entity"],
                         "properties": {"clue": {"type": "string", "maxLength": 200},
                                        "entity": {"type": "string", "minLength": 2, "maxLength": 120}}},
            "steps": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["relation", "kind", "words", "descriptions", "checks"],
                "properties": {"relation": {"type": "string", "minLength": 2, "maxLength": 150},
                               "kind": {"type": "string", "maxLength": 60},
                               "words": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 40}},
                               "descriptions": {"type": "array", "maxItems": 4,
                                               "items": {"type": "string", "maxLength": 80}},
                               "checks": {"type": "array", "maxItems": 6, "items": {
                                   "type": "object", "additionalProperties": False,
                                   "required": ["description", "readings", "weight"],
                                   "properties": {"description": {"type": "string", "maxLength": 200},
                                                  "readings": {"type": "array", "maxItems": 3,
                                                              "items": {"type": "string", "maxLength": 150}},
                                                  "weight": {"type": "integer", "minimum": 1, "maximum": 3}}}}}}},
            "answer": {"type": "object", "additionalProperties": False,
                         "required": ["what", "words", "checks", "value_kind", "is_entity"],
                         "properties": {"what": {"type": "string", "minLength": 2, "maxLength": 200},
                                        "is_entity": {"type": "boolean"},
                                        "words": {"type": "array", "maxItems": 12,
                                                   "items": {"type": "string", "maxLength": 40}},
                                        "checks": {"type": "array", "maxItems": 4,
                                                      "items": {"type": "string", "maxLength": 200}},
                                        "value_kind": {"type": "string", "enum": [
                                            "full date", "year", "day and month", "number", "code", "name",
                                            "other"]},
                                        "min": {"type": "number"}, "max": {"type": "number"}}},
            "anchors": {"type": "array", "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["entity", "clue", "relation", "step"],
                "properties": {"entity": {"type": "string", "minLength": 2, "maxLength": 120},
                               "clue": {"type": "string", "maxLength": 200},
                               "relation": {"type": "string", "minLength": 2, "maxLength": 150},
                               "step": {"type": "integer", "minimum": 1, "maximum": 6}}}},
            "research_language": {"type": "string", "minLength": 2, "maxLength": 3},
            "languages": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 3}},
            "reason": {"type": "string", "maxLength": 300},
        },
    },
    "step": {
        "type": "object", "additionalProperties": False, "required": ["searches"],
        "properties": {"searches": {"type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string", "minLength": 2, "maxLength": 120}}},
    },
    "candidates": {
        "type": "object", "additionalProperties": False, "required": ["candidates"],
        "properties": {"candidates": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "proof"],
            "properties": {"name": {"type": "string", "minLength": 2, "maxLength": 120},
                           "proof": {"type": "integer", "minimum": 0}}}}},
    },
    "verification": {
        "type": "object", "additionalProperties": False, "required": ["candidates"],
        "properties": {"candidates": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "checks"],
            "properties": {"name": {"type": "string", "maxLength": 120},
                           "checks": {"type": "array", "maxItems": 6, "items": {
                               "type": "object", "additionalProperties": False,
                               "required": ["check", "outcome", "proof", "quote"],
                               "properties": {"check": {"type": "string", "maxLength": 200},
                                              "outcome": {"type": "string", "enum": ["yes", "partly", "no", "unknown"]},
                                              "proof": {"type": "integer", "minimum": 0},
                                              "quote": {"type": "string", "maxLength": 160}}}}}}}},
    },
    "value": {
        "type": "object", "additionalProperties": False, "required": ["value", "proof", "reason"],
        "properties": {"value": {"type": "string", "maxLength": 150}, "proof": {"type": "integer", "minimum": 0},
                       "reason": {"type": "string", "maxLength": 300}},
    },
    "article": {
        "type": "object", "additionalProperties": False, "required": ["choice", "reason"],
        "properties": {"choice": {"type": "integer", "minimum": 0, "maximum": 7},
                       "reason": {"type": "string", "maxLength": 200}},
    },
    "final": {
        "type": "object", "additionalProperties": False, "required": ["choice", "reason"],
        "properties": {"choice": {"type": "integer", "minimum": 0, "maximum": 6},
                       "reason": {"type": "string", "maxLength": 300}},
    },
    "review": {
        "type": "object", "additionalProperties": False, "required": ["clues", "holds", "reason"],
        "properties": {
            "clues": {"type": "array", "maxItems": 20, "items": {
                "type": "object", "additionalProperties": False, "required": ["clue", "outcome", "row", "quote"],
                "properties": {"clue": {"type": "string", "maxLength": 200},
                               "outcome": {"type": "string", "enum": ["yes", "no", "unproven"]},
                               "row": {"type": "integer", "minimum": 0},
                               "quote": {"type": "string", "maxLength": 200}}}},
            "holds": {"type": "string", "enum": ["yes", "no", "unproven"]},
            "starting_point_name": {"type": "string", "maxLength": 120},  # facoltativo, dal 15/11
            # facoltativa: le revisioni registrate prima del 13/11 non la hanno
            "premise": {"type": "object", "additionalProperties": False, "required": ["outcome", "what", "row", "quote"],
                         "properties": {"outcome": {"type": "string", "enum": ["holds", "refuted", "unproven"]},
                                        "what": {"type": "string", "maxLength": 200},
                                        "row": {"type": "integer", "minimum": 0},
                                        "quote": {"type": "string", "maxLength": 200}}},
            "reason": {"type": "string", "maxLength": 300}},
    },
}


FINAL_POINTS = ("writing", "value")


class Ledger:
    """Quanto si può ancora spendere per Claude in questo piano."""

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = max(0.0, float(budget_usd))
        self.spent = 0.0
        self.calls = 0
        # Quota tenuta da parte per scaletta, scrittura e controllo finale (15/10: ricerche lunghe finivano il budget
        # prima della scrittura, "Claude non ha scritto la risposta").
        self.reserve = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent)

    def available(self, point: str) -> float:
        """Quanto può spendere questo punto: i punti della raccolta lasciano intatta la riserva."""
        return self.remaining if point in FINAL_POINTS else max(0.0, self.remaining - self.reserve)

    @staticmethod
    def estimate(point: str, chars: int, model: str | None = None) -> float:
        """Costo massimo osservato per questo punto e modello (ultime 200 chiamate riuscite), oppure una stima prudente.
        Si legge solo la coda del registro: questa funzione gira a ogni decisione."""
        observed = [r.get("cost_usd", 0.0) for r in event_log.read("thinker", last_byte=LOG_TAIL)[-200:]
                     if r.get("point") == point and r.get("outcome") == "ok"
                     and (model is None or r.get("model") == model)]
        prudent = chars / 3 * INPUT_PRICE / 1e6 + ESTIMATED_OUTPUT * OUTPUT_PRICE / 1e6
        return max(observed) * 1.2 if observed else prudent

    def enough(self, point: str, chars: int, model: str | None = None) -> bool:
        return self.estimate(point, chars, model) <= self.available(point)


def errors_schema(data: Any, schema: dict) -> list[str]:
    """Tutti gli errori di schema (al massimo 8), in forma leggibile per il modello: «campo: problema»."""
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '(radice)'}: {e.message[:200]}"
            for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path))][:8]


def instructions(point: str) -> str:
    return (PROMPT_FOLDER / f"{point}.md").read_text(encoding="utf-8").strip()


class Thinker:
    def __init__(self, plan_id: str, budget_usd: float, publish: Callable[[dict], None] | None = None) -> None:
        self.plan_id = plan_id
        self.ledger = Ledger(budget_usd)
        self.model: str | None = None  # modello per tutti i punti di questo piano (es. "haiku"); None = config
        self.exhausted = False  # True dopo la prima chiamata rifiutata per budget
        self._mutex = threading.Lock()
        self._max_cost: dict[str, float] = {}
        self.publish = publish or (lambda e: None)
        self.decisions: list[dict] = []
        self.models: set[str] = set()  # i modelli esatti che hanno risposto (gli alias cambiano nel tempo)

    def _count(self, usage: claude_json.Usage) -> None:
        """Un consumo nel bilancio; l'evento dice a chi guarda da fuori quanto si è speso (anche se poi il processo
        viene chiuso prima della fine)."""
        with self._mutex:
            self.ledger.spent += usage.cost_usd
            self.models |= {m.strip() for m in usage.exact_model.split(",") if m.strip()}
            spent = round(self.ledger.spent, 4)
        self.publish({"kind": "cost", "spent_usd": spent})

    def _event(self, point: str, parent: str, reason: str, data: Any = None) -> None:
        article = {"kind": "thinker", "plan": self.plan_id, "point": point, "parent": parent, "reason": reason,
                "spent_usd": round(self.ledger.spent, 4), "budget_usd": self.ledger.budget_usd}
        self.decisions.append({**article, "date": data})
        event_log.write("decisions", {**article, "date": data})
        self.publish(article)

    def decide(self, point: str, obj: Any, default: Callable[[], Any],
               is_valid: Callable[[Any], list[str]] | None = None, model: str | None = None) -> tuple[Any, str]:
        """Chiede a Claude la decisione `punto` sui dati `oggetto`; altrimenti il predefinito. Restituisce (dati, da).
        `modello` solo per i punti che vogliono un modello diverso da quello del piano (la revisione)."""
        text = cleaning.data(obj)
        rules = instructions(point)
        model = model or self.model
        if not self.ledger.enough(point, len(rules) + len(text), model):
            self.exhausted = True
            self._event(point, "local", f"budget di Claude esaurito ({self.ledger.spent:.3f} $ su "
                                           f"{self.ledger.budget_usd:.2f} $)")
            return default(), "local"
        schema = SCHEMAS[point]
        request = text
        for attempt in (1, 2):
            try:
                data, usage = claude_json.ask(rules, request, schema, point, model=model,
                                                   max_budget_usd=max(0.01, self.ledger.available(point)),
                                                   plan=self.plan_id)
            except claude_json.SessionLimit:
                raise
            except claude_json.ClaudeError as error:
                error_usage = getattr(error, "usage", None)
                with self._mutex:
                    self.ledger.spent += error_usage.cost_usd if error_usage else 0.0
                self._event(point, "local", f"Claude non disponibile: {error}")
                return default(), "local"
            finally:
                with self._mutex:
                    self.ledger.calls += 1
            self._count(usage)  # la catena chiede in parallelo (un thread per entità del fascio)
            problems = errors_schema(data, schema) + (is_valid(data) if is_valid else [])
            if not problems:
                self._event(point, "claude", data.get("reason", "") if isinstance(data, dict) else "", data)
                return data, "claude"
            if attempt == 2 or not self.ledger.enough(point, len(rules) + len(request), model):
                break
            request = f"{text}\n\nLA RISPOSTA PRECEDENTE NON ERA VALIDA:\n- " + "\n- ".join(problems[:8])
        self._event(point, "local", "risposta di Claude non valida: " + "; ".join(problems[:3]))
        return default(), "local"

    def ask(self, point: str, rules: str, text: str, schema: dict | None = None) -> Any:
        """Chiamata diretta a Claude per i compiti ripetuti (lettura dei blocchi), sicura con più thread.

        Restituisce i dati (dict con schema, testo senza) oppure None se Claude non è disponibile, non risponde o il
        budget non basta più (allora `esaurito` diventa True). La stima prima della chiamata usa il costo massimo
        osservato in QUESTO piano per il punto (+20%), oppure la stima prudente: niente lettura del registro a ogni
        blocco."""
        chars = len(rules) + len(text)
        with self._mutex:
            estimate = self._max_cost[point] * 1.2 if point in self._max_cost else \
                chars / 3 * INPUT_PRICE / 1e6 + ESTIMATED_OUTPUT * OUTPUT_PRICE / 1e6
            if estimate > self.ledger.available(point):
                if not self.exhausted:
                    self._event(point, "local", f"budget di Claude esaurito ({self.ledger.spent:.3f} $ su "
                                                  f"{self.ledger.budget_usd:.2f} $)")
                self.exhausted = True
                return None
            cap = max(0.01, self.ledger.available(point))
        try:
            data, usage = claude_json.ask(rules, text, schema, point, model=self.model,
                                               max_budget_usd=cap, plan=self.plan_id)
        except claude_json.SessionLimit:
            raise
        except claude_json.ClaudeError as error:
            cost = getattr(getattr(error, "usage", None), "cost_usd", 0.0)
            with self._mutex:
                self.ledger.spent += cost
                self.ledger.calls += 1
            return None
        if schema is not None and errors_schema(data, schema):
            data = None
        self._count(usage)
        with self._mutex:
            self.ledger.calls += 1
            self._max_cost[point] = max(self._max_cost.get(point, 0.0), usage.cost_usd)
        return data

    def summary(self) -> dict:
        return {"spent_usd": round(self.ledger.spent, 4), "budget_usd": self.ledger.budget_usd,
                "calls": self.ledger.calls, "models": sorted(self.models),
                "decisions": [{k: d[k] for k in ("point", "parent", "reason")} for d in self.decisions]}
