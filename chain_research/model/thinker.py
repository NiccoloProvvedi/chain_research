"""The thinker: Claude as the reader of the chain, with a budget and the program's defaults.

Claude receives short, structured data (numbered rows, never whole pages to browse) and answers with JSON validated
by a schema. If Claude does not answer, if the answer is not valid after one correction or if the question budget is
not enough, the point uses its default and the research goes on: the result says so.
"""
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from . import claude_json, cleaning, event_log

PROMPT_FOLDER = Path(__file__).resolve().parent / "prompts"

# Prices for the cautious estimate before the call (dollars per million tokens): input written to cache (the worst
# case of the first call) and output. After the first call the observed cost counts.
INPUT_PRICE = 7.5
OUTPUT_PRICE = 30.0
ESTIMATED_OUTPUT = 1500
LOG_TAIL = 256 * 1024  # enough for the last 200 calls

SCHEMAS: dict[str, dict] = {
    "reading": {
        "type": "object", "additionalProperties": False,
        "required": ["notes", "relevance", "concepts", "ties", "facts"],
        "properties": {
            "ties": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["from", "relation", "to", "sentence", "translation", "why"],
                "properties": {"from": {"type": "string", "minLength": 1, "maxLength": 120},
                               "relation": {"type": "string", "minLength": 2, "maxLength": 80},
                               "to": {"type": "string", "minLength": 1, "maxLength": 120},
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
            "starting_point_name": {"type": "string", "maxLength": 120},  # optional, since 15 Nov
            # optional: reviews recorded before 13 Nov do not have it
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
    """How much can still be spent on Claude in this plan."""

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = max(0.0, float(budget_usd))
        self.spent = 0.0
        self.calls = 0
        # Share set aside for outline, writing and final check (15 Oct: long searches used up the budget before the
        # writing, "Claude did not write the answer").
        self.reserve = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent)

    def available(self, point: str) -> float:
        """How much this point can spend: the collection points leave the reserve untouched."""
        return self.remaining if point in FINAL_POINTS else max(0.0, self.remaining - self.reserve)

    @staticmethod
    def estimate(point: str, chars: int, model: str | None = None) -> float:
        """Maximum observed cost for this point and model (last 200 successful calls), or a cautious estimate.
        Only the tail of the log is read: this function runs at every decision."""
        observed = [r.get("cost_usd", 0.0) for r in event_log.read("thinker", last_byte=LOG_TAIL)[-200:]
                     if r.get("point") == point and r.get("outcome") == "ok"
                     and (model is None or r.get("model") == model)]
        prudent = chars / 3 * INPUT_PRICE / 1e6 + ESTIMATED_OUTPUT * OUTPUT_PRICE / 1e6
        return max(observed) * 1.2 if observed else prudent

    def enough(self, point: str, chars: int, model: str | None = None) -> bool:
        return self.estimate(point, chars, model) <= self.available(point)


def errors_schema(data: Any, schema: dict) -> list[str]:
    """All the schema errors (at most 8), in a form readable by the model: "field: problem"."""
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message[:200]}"
            for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path))][:8]


def instructions(point: str) -> str:
    return (PROMPT_FOLDER / f"{point}.md").read_text(encoding="utf-8").strip()


class Thinker:
    def __init__(self, plan_id: str, budget_usd: float, publish: Callable[[dict], None] | None = None) -> None:
        self.plan_id = plan_id
        self.ledger = Ledger(budget_usd)
        self.model: str | None = None  # model for all the points of this plan (e.g. "haiku"); None = config
        self.exhausted = False  # True after the first call refused for budget
        self._mutex = threading.Lock()
        self._max_cost: dict[str, float] = {}
        self.publish = publish or (lambda e: None)
        self.decisions: list[dict] = []
        self.models: set[str] = set()  # the exact models that answered (aliases change over time)

    def _count(self, usage: claude_json.Usage) -> None:
        """A cost in the ledger; the event tells outside observers how much was spent (even if the process is then closed
        before the end)."""
        with self._mutex:
            self.ledger.spent += usage.cost_usd
            self.models |= {m.strip() for m in usage.exact_model.split(",") if m.strip()}
            spent = round(self.ledger.spent, 4)
        self.publish({"kind": "cost", "spent_usd": spent})

    def _event(self, point: str, parent: str, reason: str, data: Any = None) -> None:
        article = {"kind": "thinker", "plan": self.plan_id, "point": point, "parent": parent, "reason": reason,
                "spent_usd": round(self.ledger.spent, 4), "budget_usd": self.ledger.budget_usd}
        self.decisions.append({**article, "data": data})
        event_log.write("decisions", {**article, "data": data})
        self.publish(article)

    def decide(self, point: str, obj: Any, default: Callable[[], Any],
               is_valid: Callable[[Any], list[str]] | None = None, model: str | None = None) -> tuple[Any, str]:
        """Asks Claude for the `point` decision on the data `obj`; otherwise the default. Returns (data, parent).
        `model` only for the points that want a model different from the plan's (the review)."""
        text = cleaning.data(obj)
        rules = instructions(point)
        model = model or self.model
        if not self.ledger.enough(point, len(rules) + len(text), model):
            self.exhausted = True
            self._event(point, "local", f"Claude budget exhausted (${self.ledger.spent:.3f} of "
                                           f"${self.ledger.budget_usd:.2f})")
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
                self._event(point, "local", f"Claude not available: {error}")
                return default(), "local"
            finally:
                with self._mutex:
                    self.ledger.calls += 1
            self._count(usage)  # the chain asks in parallel (one thread per entity of the beam)
            problems = errors_schema(data, schema) + (is_valid(data) if is_valid else [])
            if not problems:
                self._event(point, "claude", data.get("reason", "") if isinstance(data, dict) else "", data)
                return data, "claude"
            if attempt == 2 or not self.ledger.enough(point, len(rules) + len(request), model):
                break
            request = f"{text}\n\nTHE PREVIOUS ANSWER WAS NOT VALID:\n- " + "\n- ".join(problems[:8])
        self._event(point, "local", "invalid Claude answer: " + "; ".join(problems[:3]))
        return default(), "local"

    def ask(self, point: str, rules: str, text: str, schema: dict | None = None) -> Any:
        """Direct call to Claude for repeated tasks (reading the blocks), thread-safe.

        Returns the data (dict with a schema, text without) or None if Claude is not available, does not answer or the
        budget is no longer enough (then `exhausted` becomes True). The estimate before the call uses the maximum cost
        observed in THIS plan for the point (+20%), or the cautious estimate: no log reading at every block."""
        chars = len(rules) + len(text)
        with self._mutex:
            estimate = self._max_cost[point] * 1.2 if point in self._max_cost else \
                chars / 3 * INPUT_PRICE / 1e6 + ESTIMATED_OUTPUT * OUTPUT_PRICE / 1e6
            if estimate > self.ledger.available(point):
                if not self.exhausted:
                    self._event(point, "local", f"Claude budget exhausted (${self.ledger.spent:.3f} of "
                                                  f"${self.ledger.budget_usd:.2f})")
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
