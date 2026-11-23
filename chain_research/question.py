"""Una domanda risposta con la catena, in un processo a sé.

Uso: `python -m chain_research.question` con una richiesta JSON sull'ingresso
({"domanda": ..., "opzioni": {...}, "profilo": ..., "cartella_pezzi": ...}). Sull'uscita una riga JSON per evento
({"evento": {...}}) e alla fine {"esito": {...}} oppure {"errore": "...", "costo_usd": ...}, con "limite_sessione"
(secondi al rinnovo) se è finito il limite dell'abbonamento. L'indagine ne lancia alcuni in parallelo: ognuno ha il suo
browser e si può fermare chiudendo il processo.
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
    """Chiusura chiesta dall'indagine: le chiamate a Claude in corso non devono continuare a costare da sole. Il browser
    si chiude da solo quando il suo programma di controllo perde il collegamento con questo processo."""
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
            # Una ricerca da capo: un'altra raccolta, con il suo budget; il costo della domanda è la somma.
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
    except Exception as error:  # noqa: BLE001 - l'indagine registra l'errore e continua con le altre domande
        _write({"error": f"{type(error).__name__}: {error}"[:500], "cost_usd": cost()})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
