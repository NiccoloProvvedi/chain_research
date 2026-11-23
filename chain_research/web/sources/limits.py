"""Limitatore di frequenza per dominio: un intervallo minimo tra le chiamate e un tetto all'ora.

Orologio e attesa si possono sostituire nei test (nessuna attesa reale).
"""
import threading
import time
from collections import deque
from collections.abc import Callable

from .base import ReachedLimit


class RateLimiter:
    def __init__(self, interval_s: float, max_hour: int | None = None, *, name: str = "",
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval_s = float(interval_s)
        self.max_hour = max_hour
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._hour: deque[float] = deque()
        self._until_pause: float = 0.0
        self._lock = threading.Lock()

    def pause(self, seconds: float) -> None:
        """Nessuna chiamata per `secondi` (backoff di Stack Exchange, blocco anti-robot di YouTube...)."""
        with self._lock:
            self._until_pause = max(self._until_pause, self._clock() + seconds)

    def in_pause(self) -> float:
        """Secondi di pausa rimasti (0 se nessuna)."""
        return max(0.0, self._until_pause - self._clock())

    def wait_for(self, max_wait_s: float = 60.0) -> None:
        """Aspetta il proprio turno. LimiteRaggiunto se il turno arriverebbe dopo `attesa_massima_s`."""
        with self._lock:
            now = self._clock()
            while self._hour and now - self._hour[0] >= 3600:
                self._hour.popleft()
            if self.max_hour is not None and len(self._hour) >= self.max_hour:
                raise ReachedLimit(f"{self.name}: raggiunto il limite di {self.max_hour} richieste all'ora")
            turn = max(self._until_pause, (self._last + self.interval_s) if self._last is not None else now)
            wait = turn - now
            if wait > max_wait_s:
                raise ReachedLimit(f"{self.name}: in pausa ancora per {int(wait)} secondi")
            if wait > 0:
                self._sleep(wait)
                now = turn
            self._last = now
            self._hour.append(now)


_limiters: dict[str, RateLimiter] = {}
_lock = threading.Lock()


def limiter(key: str, interval_s: float, max_hour: int | None = None) -> RateLimiter:
    """Limitatore condiviso per una chiave (dominio o programma): vale per tutte le ricerche dello stesso processo."""
    with _lock:
        existing = _limiters.get(key)
        if existing is None:
            existing = _limiters[key] = RateLimiter(interval_s, max_hour, name=key)
        else:
            existing.interval_s, existing.max_hour = float(interval_s), max_hour
        return existing
