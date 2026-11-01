"""Registri JSONL (una riga per evento) nella cartella dei registri (log/ o RICERCA_LOG)."""
import json
import threading
from datetime import datetime
from typing import Any

from . import config

_serratura = threading.Lock()


def scrivi(nome: str, evento: dict[str, Any]) -> None:
    """Aggiunge un evento a <cartella_log>/<nome>.jsonl, con data e ora. Mai bloccante per chi chiama."""
    riga = {"quando": datetime.now().astimezone().isoformat(timespec="seconds"), **evento}
    try:
        percorso = config.cartella_log() / f"{nome}.jsonl"
        with _serratura, percorso.open("a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def leggi(nome: str) -> list[dict[str, Any]]:
    percorso = config.cartella_log() / f"{nome}.jsonl"
    if not percorso.is_file():
        return []
    righe = []
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        try:
            righe.append(json.loads(riga))
        except ValueError:
            continue
    return righe
