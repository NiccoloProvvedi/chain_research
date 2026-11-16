"""Seconda ricerca indipendente per le risposte incerte.

La catena non è ripetibile sulle domande difficili: stessa domanda, altra strada, a volte altro valore. Sulle corse
salvate (16/11, domande di controllo) una risposta `incerta` ritrovata uguale da un'altra corsa era giusta 28 volte su
30; con un altro valore 14 su 51. Quindi una risposta incerta si cerca una seconda volta, da capo, con il budget e il
tempo rimasti alla domanda: stesso valore → la risposta è sostenuta da due ricerche; altro valore → contestata, con
tutti e due; nessun valore → resta incerta.
"""
from collections.abc import Callable
from typing import Any

from . import validazione

# Sotto questi resti la seconda ricerca non parte: finirebbe a metà.
BUDGET_MINIMO_USD = 0.15
MINUTI_MINIMI = 8
ORDINE = {"verificata": 3, "sostenuta": 2, "contestata": 1, "incerta": 0}


def _in_breve(esito: dict, come: str) -> dict:
    return {"esito": come, "valore": esito.get("valore"), "stato": esito.get("stato"),
            "costo_usd": esito.get("costo_usd", 0.0), "secondi": esito.get("secondi", 0)}


def _con_stato(esito: dict, stato: str, motivi: list[str]) -> dict:
    """L'esito con lo stato e i motivi nuovi, anche dove li leggono l'indagine (catena.controlli) e chi legge la
    risposta (la riga «Fiducia», scritta dal programma)."""
    controlli = {**((esito.get("catena") or {}).get("controlli") or {}), "stato": stato, "motivi": motivi,
                 "confermata": stato in ("verificata", "sostenuta")}
    return {**esito, "stato": stato, "confermata": controlli["confermata"],
            "catena": {**(esito.get("catena") or {}), "controlli": controlli},
            "risposta": validazione.con_fiducia(esito.get("risposta") or "", stato, motivi)}


def unisci(prima: dict, seconda: dict) -> dict:
    """La risposta della domanda dalle due ricerche. La risposta principale è quella con lo stato migliore (a parità la
    prima); l'altra resta in breve in `riprova`. Costo e tempo sono la somma."""
    def motivi_di(e: dict) -> list[str]:
        return list(((e.get("catena") or {}).get("controlli") or {}).get("motivi") or [])

    if not seconda.get("valore"):
        unito = _con_stato(prima, prima["stato"], motivi_di(prima) + ["una seconda ricerca non ha trovato un valore"])
        unito["riprova"] = _in_breve(seconda, "nessun valore")
    else:
        migliore_la_seconda = ORDINE.get(seconda.get("stato"), 0) > ORDINE.get(prima.get("stato"), 0)
        principale, altra = (seconda, prima) if migliore_la_seconda else (prima, seconda)
        if validazione.stesso_valore(str(prima["valore"]), str(seconda["valore"])):
            stato = principale["stato"] if ORDINE.get(principale["stato"], 0) >= ORDINE["sostenuta"] else "sostenuta"
            motivi = ["due ricerche indipendenti danno lo stesso valore"] + motivi_di(principale)
            unito = _con_stato(principale, stato, motivi)
            unito["riprova"] = _in_breve(altra, "stesso valore")
        else:
            motivi = [f"un'altra ricerca indipendente dà {altra['valore']}"] + motivi_di(principale)
            unito = _con_stato(principale, "contestata", motivi)
            unito["riprova"] = _in_breve(altra, "altro valore")
    unito["costo_usd"] = round(float(prima.get("costo_usd") or 0) + float(seconda.get("costo_usd") or 0), 4)
    unito["secondi"] = int(prima.get("secondi") or 0) + int(seconda.get("secondi") or 0)
    return unito


def con_riprova(crea: Callable[[dict], Any], opzioni: dict, pubblica: Callable[[dict], None]) -> dict:
    """La domanda con la catena creata da `crea(opzioni)`; se la risposta è incerta e restano budget e tempo, una
    seconda ricerca da capo (`opzioni["seconda_ricerca"]`, accesa se non detto)."""
    esito = crea(opzioni).esegui()
    if not opzioni.get("seconda_ricerca", True) or esito.get("stato") != "incerta" or not esito.get("valore"):
        return esito
    budget = float(opzioni.get("budget_claude", 0.50)) - float(esito.get("costo_usd") or 0)
    minuti = float(opzioni.get("minuti_catena", 35)) - float(esito.get("secondi") or 0) / 60
    if budget < BUDGET_MINIMO_USD or minuti < MINUTI_MINIMI:
        return esito
    pubblica({"tipo": "fase", "testo": "Risposta incerta: la cerco una seconda volta, da capo, per vedere se torna…"})
    seconda = crea({**opzioni, "budget_claude": budget, "minuti_catena": minuti}).esegui()
    return unisci(esito, seconda)
