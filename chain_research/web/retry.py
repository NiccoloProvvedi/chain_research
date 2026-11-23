"""Seconda ricerca indipendente per le risposte incerte.

La catena non è ripetibile sulle domande difficili: stessa domanda, altra strada, a volte altro valore. Sulle corse
salvate (16/11, domande di controllo) una risposta `incerta` ritrovata uguale da un'altra corsa era giusta 28 volte su
30; con un altro valore 14 su 51. Quindi una risposta incerta si cerca una seconda volta, da capo, con il budget e il
tempo rimasti alla domanda: stesso valore → la risposta è sostenuta da due ricerche; altro valore → contestata, con
tutti e due; nessun valore → resta incerta.
"""
from collections.abc import Callable
from typing import Any

from . import validation

# Sotto questi resti la seconda ricerca non parte: finirebbe a metà.
MIN_BUDGET_USD = 0.15
MIN_MINUTES = 8
ORDER = {"verified": 3, "supported": 2, "contested": 1, "uncertain": 0}


def _in_short(outcome: dict, how: str) -> dict:
    return {"outcome": how, "value": outcome.get("value"), "status": outcome.get("status"),
            "cost_usd": outcome.get("cost_usd", 0.0), "seconds": outcome.get("seconds", 0)}


def _with_status(outcome: dict, status: str, reasons: list[str]) -> dict:
    """L'esito con lo stato e i motivi nuovi, anche dove li leggono l'indagine (catena.controlli) e chi legge la
    risposta (la riga «Fiducia», scritta dal programma)."""
    checks = {**((outcome.get("chain") or {}).get("checks") or {}), "status": status, "reasons": reasons,
                 "confirmed": status in ("verified", "supported")}
    return {**outcome, "status": status, "confirmed": checks["confirmed"],
            "chain": {**(outcome.get("chain") or {}), "checks": checks},
            "answer": validation.with_confidence(outcome.get("answer") or "", status, reasons)}


def merge(first: dict, second: dict) -> dict:
    """La risposta della domanda dalle due ricerche. La risposta principale è quella con lo stato migliore (a parità la
    prima); l'altra resta in breve in `riprova`. Costo e tempo sono la somma."""
    def reasons_of(e: dict) -> list[str]:
        return list(((e.get("chain") or {}).get("checks") or {}).get("reasons") or [])

    if not second.get("value"):
        merged = _with_status(first, first["status"], reasons_of(first) + ["una seconda ricerca non ha trovato un valore"])
        merged["retry"] = _in_short(second, "no value")
    else:
        best_second = ORDER.get(second.get("status"), 0) > ORDER.get(first.get("status"), 0)
        main, another = (second, first) if best_second else (first, second)
        if validation.same_value(str(first["value"]), str(second["value"])):
            status = main["status"] if ORDER.get(main["status"], 0) >= ORDER["supported"] else "supported"
            reasons = ["due ricerche indipendenti danno lo stesso valore"] + reasons_of(main)
            merged = _with_status(main, status, reasons)
            merged["retry"] = _in_short(another, "same value")
        else:
            reasons = [f"un'altra ricerca indipendente dà {another['value']}"] + reasons_of(main)
            merged = _with_status(main, "contested", reasons)
            merged["retry"] = _in_short(another, "other value")
    merged["cost_usd"] = round(float(first.get("cost_usd") or 0) + float(second.get("cost_usd") or 0), 4)
    merged["seconds"] = int(first.get("seconds") or 0) + int(second.get("seconds") or 0)
    return merged


def with_retry(create: Callable[[dict], Any], options: dict, publish: Callable[[dict], None]) -> dict:
    """La domanda con la catena creata da `crea(opzioni)`; se la risposta è incerta e restano budget e tempo, una
    seconda ricerca da capo (`opzioni["seconda_ricerca"]`, accesa se non detto)."""
    outcome = create(options).run()
    if not options.get("second_research", True) or outcome.get("status") != "uncertain" or not outcome.get("value"):
        return outcome
    budget = float(options.get("budget_claude", 0.50)) - float(outcome.get("cost_usd") or 0)
    minutes = float(options.get("chain_minutes", 35)) - float(outcome.get("seconds") or 0) / 60
    if budget < MIN_BUDGET_USD or minutes < MIN_MINUTES:
        return outcome
    publish({"kind": "phase", "text": "Risposta incerta: la cerco una seconda volta, da capo, per vedere se torna…"})
    second = create({**options, "budget_claude": budget, "chain_minutes": minutes}).run()
    return merge(outcome, second)
