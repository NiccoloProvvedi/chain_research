"""Registri JSONL (una riga per evento) nella cartella dei registri (log/ o RICERCA_LOG)."""
import json
import threading
from datetime import datetime
from typing import Any

from . import config

_serratura = threading.Lock()
DIMENSIONE_MASSIMA = 5 * 1024 * 1024  # oltre, il registro diventa <nome>.jsonl.1 (uno solo) e si riparte


def scrivi(nome: str, evento: dict[str, Any]) -> None:
    """Aggiunge un evento a <cartella_log>/<nome>.jsonl, con data e ora. Mai bloccante per chi chiama."""
    riga = {"quando": datetime.now().astimezone().isoformat(timespec="seconds"), **evento}
    try:
        percorso = config.cartella_log() / f"{nome}.jsonl"
        with _serratura:
            if percorso.is_file() and percorso.stat().st_size > DIMENSIONE_MASSIMA:
                percorso.replace(percorso.with_name(percorso.name + ".1"))
            with percorso.open("a", encoding="utf-8") as f:
                f.write(json.dumps(riga, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def leggi(nome: str, ultimi_byte: int | None = None) -> list[dict[str, Any]]:
    """Le righe del registro; con `ultimi_byte` solo quelle in coda al file (la prima, forse a metà, si scarta)."""
    percorso = config.cartella_log() / f"{nome}.jsonl"
    if not percorso.is_file():
        return []
    with percorso.open("rb") as f:
        if ultimi_byte is not None and percorso.stat().st_size > ultimi_byte:
            f.seek(-ultimi_byte, 2)
            f.readline()
        testo = f.read().decode("utf-8", errors="replace")
    righe = []
    for riga in testo.splitlines():
        try:
            righe.append(json.loads(riga))
        except ValueError:
            continue
    return righe
