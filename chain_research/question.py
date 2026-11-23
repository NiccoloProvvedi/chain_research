"""One question answered by the chain, in its own process.

Usage: `python -m chain_research.question` with a JSON request on stdin
({"question": ..., "options": {...}, "profile": ..., "pieces_folder": ...}). On stdout one JSON line per event
({"event": {...}}) and at the end {"outcome": {...}} or {"error": "...", "cost_usd": ...}, with "session_limit"
(seconds to renewal) if the subscription limit ran out. The investigation launches several in parallel: each has its
own browser and can be stopped by closing the process.
"""
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from .model import claude_json
from .web import retry
from .web.collector import Collector

_writing = threading.Lock()


def _write(obj: dict) -> None:
    with _writing:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def _terminate(*_: Any) -> None:
    """Shutdown requested by the investigation: running Claude calls must not keep costing on their own. The browser
    closes by itself when its control program loses the connection with this process."""
    claude_json.interrupt()
    os._exit(143)


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate)
    request = json.loads(sys.stdin.readline())
    collector = None
    spent = {"first": 0.0}
    cost = lambda: round(spent["first"] + (collector.thinker.ledger.spent if collector is not None else 0.0), 4)  # noqa: E731
    try:
        publish = lambda e: _write({"event": e})  # noqa: E731

        def create(options: dict) -> Collector:
            # A research from scratch: another collection, with its own budget; the question's cost is the sum.
            nonlocal collector
            spent["first"] += collector.thinker.ledger.spent if collector is not None else 0.0
            collector = Collector(request["question"], options=options, publish=publish,
                                profile=Path(request["profile"]) if request.get("profile") else None,
                                pieces_folder=Path(request["pieces_folder"]) if request.get("pieces_folder")
                                else None)
            return collector
        _write({"outcome": retry.with_retry(create, request.get("options") or {}, publish)})
    except claude_json.SessionLimit as error:
        _write({"error": str(error)[:500], "session_limit": error.seconds, "cost_usd": cost()})
        return 3
    except Exception as error:  # noqa: BLE001 - the investigation records the error and goes on with the other questions
        _write({"error": f"{type(error).__name__}: {error}"[:500], "cost_usd": cost()})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
