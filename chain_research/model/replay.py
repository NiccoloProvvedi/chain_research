"""Registra e rigioca tutto ciò che viene da fuori: risposte dei modelli, Wikipedia, motori di ricerca, pagine (T4).

Con CHAIN_RECORD=<cartella> ogni risposta si salva con chiave = impronta della richiesta. Con
CHAIN_REPLAY=<cartella> le risposte si leggono da lì e niente esce dal programma: una richiesta mai vista è un errore
(il rigioco deve essere esatto, altrimenti non dimostra niente). La stessa richiesta fatta più volte (le due letture
della verifica) ha una risposta per ogni volta. CHAIN_TODAY fissa la data che entra nelle richieste.
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

EXCEPTION = "__eccezione__"
_mutex = threading.Lock()
_readings: Counter[str] = Counter()
_writes: Counter[str] = Counter()


class UnrecordedRequest(RuntimeError):
    pass


class FailedWhenRecorded(RuntimeError):
    """Questa richiesta, quando è stata registrata, era fallita: fallisce allo stesso modo."""


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def today() -> str:
    return os.environ.get("CHAIN_TODAY") or f"{datetime.now():%Y-%m-%d}"


def _folder(variable: str) -> Path | None:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else None


def in_replay() -> bool:
    return _folder("CHAIN_REPLAY") is not None


def _next(counter: Counter[str], key: str) -> str:
    with _mutex:
        number = counter[key]
        counter[key] += 1
    return f"{key}-{number}.json.gz"


def read(key: str) -> Any:
    folder = _folder("CHAIN_REPLAY")
    file = folder / _next(_readings, key) if folder else None
    if file is None or not file.is_file():
        raise UnrecordedRequest(f"risposta non registrata ({key[:12]})")
    return json.loads(gzip.decompress(file.read_bytes()))["answer"]


def unique_read(point: str) -> Any:
    """La sola risposta registrata di Claude per questo punto, qualunque fosse la richiesta. Serve alla scrittura della
    risposta finale: il suo testo d'ingresso riporta lo stato deciso dai controlli, e una regola dello stato cambiata
    non deve rendere inutile la registrazione di tutta la catena (13/11). Con zero o più di una registrazione: errore."""
    folder = _folder("CHAIN_REPLAY")
    found_items = []
    for file in sorted(folder.glob("*.json.gz")) if folder else []:
        data = json.loads(gzip.decompress(file.read_bytes()))
        if isinstance(data.get("request"), dict) and data["request"].get("point") == point:
            found_items.append(data["answer"])
    if len(found_items) != 1:
        raise UnrecordedRequest(f"{len(found_items)} risposte registrate per «{point}»: ne serve una sola")
    return found_items[0]


def save(key: str, request: Any, answer: Any) -> None:
    folder = _folder("CHAIN_RECORD")
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"request": request, "answer": answer}, ensure_ascii=False, default=str).encode()
    (folder / _next(_writes, key)).write_bytes(gzip.compress(data, mtime=0))


def through(kind: str, parts: Any, produce: Callable[[], Any]) -> Any:
    """Il risultato di `produci`, registrato; nel rigioco quello registrato, senza chiamarla."""
    key = fingerprint(kind, parts)
    if in_replay():
        answer = read(key)
        if isinstance(answer, dict) and EXCEPTION in answer:
            raise FailedWhenRecorded(answer[EXCEPTION])
        return answer
    try:
        answer = produce()
    except Exception as error:
        save(key, {"kind": kind, "parts": parts}, {EXCEPTION: str(error)[:500]})
        raise
    save(key, {"kind": kind, "parts": parts}, answer)
    return answer
