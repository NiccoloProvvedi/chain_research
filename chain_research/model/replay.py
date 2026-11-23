"""Records and replays everything that comes from outside: model answers, Wikipedia, search engines, pages (T4).

With CHAIN_RECORD=<folder> every answer is saved with key = fingerprint of the request. With CHAIN_REPLAY=<folder>
the answers are read from there and nothing leaves the program: a request never seen is an error (the replay must be
exact, otherwise it proves nothing). The same request made several times (the two readings of the verification) has
one answer for each time. CHAIN_TODAY fixes the date that goes into the requests.
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
    """This request had failed when it was recorded: it fails the same way."""


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
        raise UnrecordedRequest(f"answer not recorded ({key[:12]})")
    return json.loads(gzip.decompress(file.read_bytes()))["answer"]


def unique_read(point: str) -> Any:
    """The only recorded Claude answer for this point, whatever the request was. Used for writing the final answer: its
    input text carries the status decided by the checks, and a changed status rule must not make the recording of the
    whole chain useless (13 Nov). With zero or more than one recording: error."""
    folder = _folder("CHAIN_REPLAY")
    found_items = []
    for file in sorted(folder.glob("*.json.gz")) if folder else []:
        data = json.loads(gzip.decompress(file.read_bytes()))
        if isinstance(data.get("request"), dict) and data["request"].get("point") == point:
            found_items.append(data["answer"])
    if len(found_items) != 1:
        raise UnrecordedRequest(f"{len(found_items)} answers recorded for \"{point}\": exactly one is needed")
    return found_items[0]


def save(key: str, request: Any, answer: Any) -> None:
    folder = _folder("CHAIN_RECORD")
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"request": request, "answer": answer}, ensure_ascii=False, default=str).encode()
    (folder / _next(_writes, key)).write_bytes(gzip.compress(data, mtime=0))


def through(kind: str, parts: Any, produce: Callable[[], Any]) -> Any:
    """The result of `produce`, recorded; in replay the recorded one, without calling it."""
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
