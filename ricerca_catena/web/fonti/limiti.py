"""Limitatore di frequenza per dominio: un intervallo minimo tra le chiamate e un tetto all'ora.

Orologio e attesa si possono sostituire nei test (nessuna attesa reale).
"""
import threading
import time
from collections import deque
from collections.abc import Callable

from .base import LimiteRaggiunto


class Limitatore:
    def __init__(self, intervallo_s: float, max_ora: int | None = None, *, nome: str = "",
                 orologio: Callable[[], float] = time.monotonic, dormi: Callable[[float], None] = time.sleep) -> None:
        self.intervallo_s = float(intervallo_s)
        self.max_ora = max_ora
        self.nome = nome
        self._orologio = orologio
        self._dormi = dormi
        self._ultima: float | None = None
        self._ora: deque[float] = deque()
        self._pausa_fino: float = 0.0
        self._lock = threading.Lock()

    def pausa(self, secondi: float) -> None:
        """Nessuna chiamata per `secondi` (backoff di Stack Exchange, blocco anti-robot di YouTube...)."""
        with self._lock:
            self._pausa_fino = max(self._pausa_fino, self._orologio() + secondi)

    def in_pausa(self) -> float:
        """Secondi di pausa rimasti (0 se nessuna)."""
        return max(0.0, self._pausa_fino - self._orologio())

    def attendi(self, attesa_massima_s: float = 60.0) -> None:
        """Aspetta il proprio turno. LimiteRaggiunto se il turno arriverebbe dopo `attesa_massima_s`."""
        with self._lock:
            adesso = self._orologio()
            while self._ora and adesso - self._ora[0] >= 3600:
                self._ora.popleft()
            if self.max_ora is not None and len(self._ora) >= self.max_ora:
                raise LimiteRaggiunto(f"{self.nome}: raggiunto il limite di {self.max_ora} richieste all'ora")
            turno = max(self._pausa_fino, (self._ultima + self.intervallo_s) if self._ultima is not None else adesso)
            attesa = turno - adesso
            if attesa > attesa_massima_s:
                raise LimiteRaggiunto(f"{self.nome}: in pausa ancora per {int(attesa)} secondi")
            if attesa > 0:
                self._dormi(attesa)
                adesso = turno
            self._ultima = adesso
            self._ora.append(adesso)


_limitatori: dict[str, Limitatore] = {}
_lock = threading.Lock()


def limitatore(chiave: str, intervallo_s: float, max_ora: int | None = None) -> Limitatore:
    """Limitatore condiviso per una chiave (dominio o programma): vale per tutte le ricerche dello stesso processo."""
    with _lock:
        esistente = _limitatori.get(chiave)
        if esistente is None:
            esistente = _limitatori[chiave] = Limitatore(intervallo_s, max_ora, nome=chiave)
        else:
            esistente.intervallo_s, esistente.max_ora = float(intervallo_s), max_ora
        return esistente
