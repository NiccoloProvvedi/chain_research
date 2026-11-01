"""Registra e rigioca le risposte dei modelli (decisione T4).

Con RICERCA_REGISTRA=<cartella> ogni risposta di Claude si salva con chiave = impronta della
richiesta. Con RICERCA_RIGIOCA=<cartella> le risposte si leggono da lì e nessun modello viene chiamato: una
richiesta mai vista è un errore (il rigioco deve essere esatto, altrimenti non dimostra niente).
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Any


class RichiestaNonRegistrata(RuntimeError):
    pass


def impronta(*parti: Any) -> str:
    return hashlib.sha256(json.dumps(parti, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _cartella(variabile: str) -> Path | None:
    valore = os.environ.get(variabile)
    return Path(valore).expanduser() if valore else None


def in_rigioco() -> bool:
    return _cartella("RICERCA_RIGIOCA") is not None


def leggi(chiave: str) -> Any:
    cartella = _cartella("RICERCA_RIGIOCA")
    file = cartella / f"{chiave}.json" if cartella else None
    if file is None or not file.is_file():
        raise RichiestaNonRegistrata(f"risposta non registrata ({chiave[:12]})")
    return json.loads(file.read_text(encoding="utf-8"))["risposta"]


def salva(chiave: str, richiesta: Any, risposta: Any) -> None:
    cartella = _cartella("RICERCA_REGISTRA")
    if cartella is None:
        return
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / f"{chiave}.json").write_text(json.dumps({"richiesta": richiesta, "risposta": risposta},
                                                        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
