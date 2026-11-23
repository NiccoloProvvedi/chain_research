"""JSONL logs (one line per event) in the log folder (log/ or CHAIN_LOG)."""
import json
import threading
from datetime import datetime
from typing import Any

from . import config

_mutex = threading.Lock()
MAX_SIZE = 5 * 1024 * 1024  # beyond this, the log becomes <name>.jsonl.1 (only one) and starts over


def write(name: str, event: dict[str, Any]) -> None:
    """Appends an event to <log_folder>/<name>.jsonl, with date and time. Never blocks the caller."""
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
    """The log rows; with `last_byte` only those at the end of the file (the first, maybe partial, is dropped)."""
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
