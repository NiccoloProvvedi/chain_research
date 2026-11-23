"""Registri JSONL (una riga per evento) nella cartella dei registri (log/ o CHAIN_LOG)."""
import json
import threading
from datetime import datetime
from typing import Any

from . import config

_mutex = threading.Lock()
MAX_SIZE = 5 * 1024 * 1024  # oltre, il registro diventa <nome>.jsonl.1 (uno solo) e si riparte


def write(name: str, event: dict[str, Any]) -> None:
    """Aggiunge un evento a <cartella_log>/<nome>.jsonl, con data e ora. Mai bloccante per chi chiama."""
    row = {"when": datetime.now().astimezone().isoformat(timespec="seconds"), **event}
    try:
        path = config.folder_log() / f"{name}.jsonl"
        with _mutex:
            if path.is_file() and path.stat().st_size > MAX_SIZE:
                path.replace(path.with_name(path.name + ".1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def read(name: str, last_byte: int | None = None) -> list[dict[str, Any]]:
    """Le righe del registro; con `ultimi_byte` solo quelle in coda al file (la prima, forse a metà, si scarta)."""
    path = config.folder_log() / f"{name}.jsonl"
    if not path.is_file():
        return []
    with path.open("rb") as f:
        if last_byte is not None and path.stat().st_size > last_byte:
            f.seek(-last_byte, 2)
            f.readline()
        text = f.read().decode("utf-8", errors="replace")
    rows = []
    for row in text.splitlines():
        try:
            rows.append(json.loads(row))
        except ValueError:
            continue
    return rows
