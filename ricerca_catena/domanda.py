"""Una domanda risposta con la catena, in un processo a sé.

Uso: `python -m ricerca_catena.domanda` con una richiesta JSON sull'ingresso
({"domanda": ..., "opzioni": {...}, "profilo": ..., "cartella_pezzi": ...}). Sull'uscita una riga JSON per evento
({"evento": {...}}) e alla fine {"esito": {...}} oppure {"errore": "..."}. L'indagine ne lancia alcuni in parallelo:
ognuno ha il suo browser e si può fermare chiudendo il processo.
"""
import json
import sys
import threading
from pathlib import Path

from .web.raccolta import Raccolta

_scrittura = threading.Lock()


def _scrivi(oggetto: dict) -> None:
    with _scrittura:
        sys.stdout.write(json.dumps(oggetto, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def main() -> int:
    richiesta = json.loads(sys.stdin.readline())
    try:
        raccolta = Raccolta(richiesta["domanda"], opzioni=richiesta.get("opzioni") or {},
                            pubblica=lambda e: _scrivi({"evento": e}),
                            profilo=Path(richiesta["profilo"]) if richiesta.get("profilo") else None,
                            cartella_pezzi=Path(richiesta["cartella_pezzi"]) if richiesta.get("cartella_pezzi")
                            else None)
        _scrivi({"esito": raccolta.esegui()})
    except Exception as errore:  # noqa: BLE001 - l'indagine registra l'errore e continua con le altre domande
        _scrivi({"errore": f"{type(errore).__name__}: {errore}"[:500]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
