"""Second independent search for uncertain answers.

The chain is not repeatable on hard questions: same question, another route, sometimes another value. On the saved
runs (16 Nov, control questions) an `uncertain` answer found again identical by another run was right 28 times out of
30; with another value 14 out of 51. So an uncertain answer is searched a second time, from scratch, with the budget
and time left for the question: same value → the answer is supported by two searches; another value → contested,
with both; no value → it stays uncertain.
"""
from collections.abc import Callable
from typing import Any

from . import validation

# Below these leftovers the second search does not start: it would stop halfway.
MIN_BUDGET_USD = 0.15
MIN_MINUTES = 8
ORDER = {"verified": 3, "supported": 2, "contested": 1, "uncertain": 0}


def _in_short(outcome: dict, how: str) -> dict:
    return {"outcome": how, "value": outcome.get("value"), "status": outcome.get("status"),
            "cost_usd": outcome.get("cost_usd", 0.0), "seconds": outcome.get("seconds", 0)}


def _with_status(outcome: dict, status: str, reasons: list[str]) -> dict:
    """The outcome with the new status and reasons, also where the investigation reads them (chain.checks) and where
    the reader of the answer sees them (the "Confidence" line, written by the program)."""
    checks = {**((outcome.get("chain") or {}).get("checks") or {}), "status": status, "reasons": reasons,
                 "confirmed": status in ("verified", "supported")}
    return {**outcome, "status": status, "confirmed": checks["confirmed"],
            "chain": {**(outcome.get("chain") or {}), "checks": checks},
            "answer": validation.with_confidence(outcome.get("answer") or "", status, reasons)}


def merge(first: dict, second: dict) -> dict:
    """The question's answer from the two searches. The main answer is the one with the better status (the first on a
    tie); the other is kept in short under `retry`. Cost and time are the sum."""
    def reasons_of(e: dict) -> list[str]:
        return list(((e.get("chain") or {}).get("checks") or {}).get("reasons") or [])

    if not second.get("value"):
        merged = _with_status(first, first["status"], reasons_of(first) + ["a second search found no value"])
        merged["retry"] = _in_short(second, "no value")
    else:
        best_second = ORDER.get(second.get("status"), 0) > ORDER.get(first.get("status"), 0)
        main, another = (second, first) if best_second else (first, second)
        if validation.same_value(str(first["value"]), str(second["value"])):
            status = main["status"] if ORDER.get(main["status"], 0) >= ORDER["supported"] else "supported"
            reasons = ["two independent searches give the same value"] + reasons_of(main)
            merged = _with_status(main, status, reasons)
            merged["retry"] = _in_short(another, "same value")
        else:
            reasons = [f"another independent search gives {another['value']}"] + reasons_of(main)
            merged = _with_status(main, "contested", reasons)
            merged["retry"] = _in_short(another, "other value")
    merged["cost_usd"] = round(float(first.get("cost_usd") or 0) + float(second.get("cost_usd") or 0), 4)
    merged["seconds"] = int(first.get("seconds") or 0) + int(second.get("seconds") or 0)
    return merged


def with_retry(create: Callable[[dict], Any], options: dict, publish: Callable[[dict], None]) -> dict:
    """The question with the chain created by `create(options)`; if the answer is uncertain and budget and time are
    left, a second search from scratch (`options["second_research"]`, on unless stated)."""
    outcome = create(options).run()
    if not options.get("second_research", True) or outcome.get("status") != "uncertain" or not outcome.get("value"):
        return outcome
    budget = float(options.get("budget_claude", 0.50)) - float(outcome.get("cost_usd") or 0)
    minutes = float(options.get("chain_minutes", 35)) - float(outcome.get("seconds") or 0) / 60
    if budget < MIN_BUDGET_USD or minutes < MIN_MINUTES:
        return outcome
    publish({"kind": "phase", "text": "Uncertain answer: searching a second time, from scratch, to see if it holds…"})
    second = create({**options, "budget_claude": budget, "chain_minutes": minutes}).run()
    return merge(outcome, second)
