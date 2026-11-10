"""Registra e rigioca tutto ciò che viene da fuori: risposte dei modelli, Wikipedia, motori di ricerca, pagine (T4).

Con RICERCA_REGISTRA=<cartella> ogni risposta si salva con chiave = impronta della richiesta. Con
RICERCA_RIGIOCA=<cartella> le risposte si leggono da lì e niente esce dal programma: una richiesta mai vista è un errore
(il rigioco deve essere esatto, altrimenti non dimostra niente). La stessa richiesta fatta più volte (le due letture
della verifica) ha una risposta per ogni volta. RICERCA_OGGI fissa la data che entra nelle richieste.
"""
import gzip
import hashlib
import json
import os
import threading
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ECCEZIONE = "__eccezione__"
_serratura = threading.Lock()
_letture: Counter[str] = Counter()
_scritture: Counter[str] = Counter()


class RichiestaNonRegistrata(RuntimeError):
    pass


class FallitaAllaRegistrazione(RuntimeError):
    """Questa richiesta, quando è stata registrata, era fallita: fallisce allo stesso modo."""


def impronta(*parti: Any) -> str:
    return hashlib.sha256(json.dumps(parti, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def oggi() -> str:
    return os.environ.get("RICERCA_OGGI") or f"{datetime.now():%Y-%m-%d}"


def _cartella(variabile: str) -> Path | None:
    valore = os.environ.get(variabile)
    return Path(valore).expanduser() if valore else None


def in_rigioco() -> bool:
    return _cartella("RICERCA_RIGIOCA") is not None


def _prossimo(contatore: Counter[str], chiave: str) -> str:
    with _serratura:
        numero = contatore[chiave]
        contatore[chiave] += 1
    return f"{chiave}-{numero}.json.gz"


def leggi(chiave: str) -> Any:
    cartella = _cartella("RICERCA_RIGIOCA")
    file = cartella / _prossimo(_letture, chiave) if cartella else None
    if file is None or not file.is_file():
        raise RichiestaNonRegistrata(f"risposta non registrata ({chiave[:12]})")
    return json.loads(gzip.decompress(file.read_bytes()))["risposta"]


def salva(chiave: str, richiesta: Any, risposta: Any) -> None:
    cartella = _cartella("RICERCA_REGISTRA")
    if cartella is None:
        return
    cartella.mkdir(parents=True, exist_ok=True)
    dati = json.dumps({"richiesta": richiesta, "risposta": risposta}, ensure_ascii=False, default=str).encode()
    (cartella / _prossimo(_scritture, chiave)).write_bytes(gzip.compress(dati, mtime=0))


def attraverso(tipo: str, parti: Any, produci: Callable[[], Any]) -> Any:
    """Il risultato di `produci`, registrato; nel rigioco quello registrato, senza chiamarla."""
    chiave = impronta(tipo, parti)
    if in_rigioco():
        risposta = leggi(chiave)
        if isinstance(risposta, dict) and ECCEZIONE in risposta:
            raise FallitaAllaRegistrazione(risposta[ECCEZIONE])
        return risposta
    try:
        risposta = produci()
    except Exception as errore:
        salva(chiave, {"tipo": tipo, "parti": parti}, {ECCEZIONE: str(errore)[:500]})
        raise
    salva(chiave, {"tipo": tipo, "parti": parti}, risposta)
    return risposta
