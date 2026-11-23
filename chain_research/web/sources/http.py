"""Client HTTP delle fonti: blocco delle reti private anche al momento della connessione, domini consentiti,
limiti di frequenza, ritentativi prudenti e dimensione massima delle risposte.

Il blocco delle reti private avviene in due punti:
1. prima della richiesta e a ogni redirect (seguiti a mano), con `rete.indirizzo_sicuro`;
2. alla connessione: `BackendSicuro` risolve il nome, rifiuta il collegamento se UNO qualsiasi degli indirizzi è
   privato e si collega proprio all'indirizzo controllato. Così un nome che cambia indirizzo tra il controllo e la
   connessione (DNS rebinding) non porta al computer o alla rete di casa. Il certificato TLS resta verificato sul nome.
"""
import email.utils
import json
import logging
import random
import socket
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from .. import network
from .base import UnreadableContent, SourceUnavailable, ReachedLimit
from .limits import RateLimiter

log = logging.getLogger("chain_research.sources")
MAX_REDIRECT = 5
ATTEMPTS = 3
MB = 1024 * 1024


class ForbiddenAddress(httpx.ConnectError):
    """Il nome porta al computer stesso o alla rete di casa."""


def resolve(host: str, port_number: int) -> list[str]:
    try:
        return list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, port_number, type=socket.SOCK_STREAM)))
    except OSError as error:
        raise httpcore.ConnectError(f"nome non risolto: {host}") from error


class SafeBackend(httpcore.SyncBackend):
    """Backend di rete di httpcore che si collega solo a indirizzi pubblici controllati."""

    def __init__(self, local_allow: bool = False) -> None:
        self.local_allow = local_allow

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        addresses = resolve(host, port)
        if not self.local_allow and (network.local_name(host) or any(network.forbidden_ip(ip) for ip in addresses)):
            raise ForbiddenAddress(f"indirizzo non consentito: {host}")
        last: Exception | None = None
        for ip in addresses:
            try:
                return super().connect_tcp(ip, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as error:
                last = error
        raise last or httpcore.ConnectError(f"connessione non riuscita: {host}")


class SafeTransport(httpx.HTTPTransport):
    def __init__(self, local_allow: bool = False, **options: Any) -> None:
        super().__init__(**options)
        self._pool._network_backend = SafeBackend(local_allow)  # httpx non lo espone: versione fissata


def _seconds_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class SourceClient:
    """Client per una fonte. `domini` = domini consentiti (None = qualsiasi dominio pubblico, per l'audio)."""

    def __init__(self, name: str, *, user_agent: str, domains: Iterable[str] | None,
                 limiter: RateLimiter | None = None, local_allow: bool = False,
                 timeout_reading: float = 30.0, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_redirect: int = MAX_REDIRECT) -> None:
        self.name = name
        self.domains = {d.lower() for d in domains} if domains is not None else None
        self.limiter = limiter
        self.local_allow = local_allow
        self.max_redirect = max_redirect  # l'audio dei podcast passa spesso da 3-4 contatori di ascolti
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport or SafeTransport(local_allow=local_allow),
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout_reading, connect=10.0), follow_redirects=False)

    def teardown(self) -> None:
        self._client.close()

    # --- controlli -----------------------------------------------------------------------------------------------

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            raise UnreadableContent(f"{self.name}: indirizzo non valido")
        if self.domains is not None and not any(host == d or host.endswith("." + d) for d in self.domains):
            raise UnreadableContent(f"{self.name}: dominio non consentito ({host})")
        if not self.local_allow and not network.safe_address(url):
            raise UnreadableContent(f"{self.name}: indirizzo del computer o della rete locale, bloccato")

    # --- richieste -----------------------------------------------------------------------------------------------

    def _send(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None) -> httpx.Response:
        """Una richiesta GET con redirect seguiti a mano (controllo a ogni salto). La risposta è in streaming."""
        for _ in range(self.max_redirect + 1):
            self._check_url(url)
            if self.limiter:
                self.limiter.wait_for()
            request = self._client.build_request("GET", url, params=params, headers=headers)
            answer = self._client.send(request, stream=True)
            if answer.status_code in (301, 302, 303, 307, 308) and answer.headers.get("location"):
                answer.close()
                url, params = urljoin(str(answer.request.url), answer.headers["location"]), None
                continue
            return answer
        raise UnreadableContent(f"{self.name}: troppi reindirizzamenti")

    def _with_retries(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None,
                         use: Callable[[httpx.Response], Any]) -> Any:
        for attempt in range(1, ATTEMPTS + 1):
            wait = 2.0 ** attempt + random.uniform(0, 1)
            try:
                answer = self._send(url, params, headers)
            except ForbiddenAddress as error:
                raise UnreadableContent(f"{self.name}: {error}") from error
            except httpx.TransportError as error:
                if attempt == ATTEMPTS:
                    raise SourceUnavailable(f"{self.name}: rete non raggiungibile ({type(error).__name__})") \
                        from error
                self._sleep(wait)
                continue
            try:
                code = answer.status_code
                if code in (429,) or code >= 500:
                    after = _seconds_retry_after(answer.headers.get("retry-after"))
                    if attempt == ATTEMPTS or (after is not None and after > 120):
                        if code == 429:
                            if self.limiter and after:
                                self.limiter.pause(after)
                            raise ReachedLimit(f"{self.name}: troppe richieste (HTTP 429)")
                        raise SourceUnavailable(f"{self.name}: il server non risponde (HTTP {code})")
                    self._sleep(after if after is not None else wait)
                    continue
                if code in (401, 403):
                    raise SourceUnavailable(f"{self.name}: accesso negato (HTTP {code})")
                if code == 404:
                    raise UnreadableContent(f"{self.name}: contenuto non trovato (HTTP 404)")
                if code >= 400:
                    raise UnreadableContent(f"{self.name}: richiesta rifiutata (HTTP {code})")
                return use(answer)
            finally:
                answer.close()
        raise SourceUnavailable(f"{self.name}: richiesta non riuscita")

    @staticmethod
    def _body(answer: httpx.Response, maximum: int) -> bytes:
        declared = answer.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > maximum:
            raise UnreadableContent(f"risposta troppo grande ({int(declared) // MB} MB)")
        parts, total = [], 0
        for piece in answer.iter_bytes():
            total += len(piece)
            if total > maximum:
                raise UnreadableContent(f"risposta troppo grande (oltre {maximum // MB} MB)")
            parts.append(piece)
        return b"".join(parts)

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                 max_mb: float = 10) -> Any:
        def use(answer: httpx.Response) -> Any:
            body = self._body(answer, int(max_mb * MB))
            try:
                return json.loads(body)
            except ValueError as error:
                raise UnreadableContent(f"{self.name}: risposta non in formato JSON") from error
        return self._with_retries(url, params, headers, use)

    def get_text(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  max_mb: float = 10) -> str:
        def use(answer: httpx.Response) -> str:
            body = self._body(answer, int(max_mb * MB))
            return body.decode(answer.encoding or "utf-8", errors="replace")
        return self._with_retries(url, params, headers, use)
