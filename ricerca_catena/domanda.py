"""Una domanda risposta con la catena, in un processo a sé.

Uso: `python -m ricerca_catena.domanda` con una richiesta JSON sull'ingresso
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

from .modello import claude_json
from .web import riprova
from .web.raccolta import Raccolta

_scrittura = threading.Lock()


def _scrivi(oggetto: dict) -> None:
    with _scrittura:
        sys.stdout.write(json.dumps(oggetto, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


def _termina(*_: Any) -> None:
    """Chiusura chiesta dall'indagine: le chiamate a Claude in corso non devono continuare a costare da sole. Il browser
    si chiude da solo quando il suo programma di controllo perde il collegamento con questo processo."""
    claude_json.interrompi()
    os._exit(143)


def main() -> int:
    signal.signal(signal.SIGTERM, _termina)
    richiesta = json.loads(sys.stdin.readline())
    raccolta = None
    speso = {"prima": 0.0}
    costo = lambda: round(speso["prima"] + (raccolta.pensatore.bilancio.speso if raccolta is not None else 0.0), 4)  # noqa: E731
    try:
        pubblica = lambda e: _scrivi({"evento": e})  # noqa: E731

        def crea(opzioni: dict) -> Raccolta:
            # Una ricerca da capo: un'altra raccolta, con il suo budget; il costo della domanda è la somma.
            nonlocal raccolta
            speso["prima"] += raccolta.pensatore.bilancio.speso if raccolta is not None else 0.0
            raccolta = Raccolta(richiesta["domanda"], opzioni=opzioni, pubblica=pubblica,
                                profilo=Path(richiesta["profilo"]) if richiesta.get("profilo") else None,
                                cartella_pezzi=Path(richiesta["cartella_pezzi"]) if richiesta.get("cartella_pezzi")
                                else None)
            return raccolta
        _scrivi({"esito": riprova.con_riprova(crea, richiesta.get("opzioni") or {}, pubblica)})
    except claude_json.LimiteSessione as errore:
        _scrivi({"errore": str(errore)[:500], "limite_sessione": errore.secondi, "costo_usd": costo()})
        return 3
    except Exception as errore:  # noqa: BLE001 - l'indagine registra l'errore e continua con le altre domande
        _scrivi({"errore": f"{type(errore).__name__}: {errore}"[:500], "costo_usd": costo()})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
