"""HTTP client of the sources: private networks blocked at connection time too, allowed domains,
rate limits, careful retries and maximum response size.

Private networks are blocked in two places:
1. before the request and at every redirect (followed by hand), with `network.safe_address`;
2. at connection: `SafeBackend` resolves the name, refuses the connection if ANY of the addresses is
   private and connects exactly to the checked address. So a name that changes address between the check and the
   connection (DNS rebinding) does not lead to the computer or the home network. The TLS certificate is still
   verified on the name.
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
    """The name leads to the computer itself or to the home network."""


def resolve(host: str, port_number: int) -> list[str]:
    try:
        return list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, port_number, type=socket.SOCK_STREAM)))
    except OSError as error:
        raise httpcore.ConnectError(f"name not resolved: {host}") from error


class SafeBackend(httpcore.SyncBackend):
    """httpcore network backend that connects only to checked public addresses."""

    def __init__(self, local_allow: bool = False) -> None:
        self.local_allow = local_allow

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        addresses = resolve(host, port)
        if not self.local_allow and (network.local_name(host) or any(network.forbidden_ip(ip) for ip in addresses)):
            raise ForbiddenAddress(f"address not allowed: {host}")
        last: Exception | None = None
        for ip in addresses:
            try:
                return super().connect_tcp(ip, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as error:
                last = error
        raise last or httpcore.ConnectError(f"connection failed: {host}")


class SafeTransport(httpx.HTTPTransport):
    def __init__(self, local_allow: bool = False, **options: Any) -> None:
        super().__init__(**options)
        self._pool._network_backend = SafeBackend(local_allow)  # httpx does not expose it: pinned version


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
    """Client for a source. `domains` = allowed domains (None = any public domain, for audio)."""

    def __init__(self, name: str, *, user_agent: str, domains: Iterable[str] | None,
                 limiter: RateLimiter | None = None, local_allow: bool = False,
                 timeout_reading: float = 30.0, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_redirect: int = MAX_REDIRECT) -> None:
        self.name = name
        self.domains = {d.lower() for d in domains} if domains is not None else None
        self.limiter = limiter
        self.local_allow = local_allow
        self.max_redirect = max_redirect  # podcast audio often goes through 3-4 listen counters
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport or SafeTransport(local_allow=local_allow),
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout_reading, connect=10.0), follow_redirects=False)

    def teardown(self) -> None:
        self._client.close()

    # --- checks -------------------------------------------------------------------------------------------------

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            raise UnreadableContent(f"{self.name}: invalid address")
        if self.domains is not None and not any(host == d or host.endswith("." + d) for d in self.domains):
            raise UnreadableContent(f"{self.name}: domain not allowed ({host})")
        if not self.local_allow and not network.safe_address(url):
            raise UnreadableContent(f"{self.name}: address of the computer or the local network, blocked")

    # --- requests -----------------------------------------------------------------------------------------------

    def _send(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None) -> httpx.Response:
        """A GET request with redirects followed by hand (checked at every hop). The response is streamed."""
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
        raise UnreadableContent(f"{self.name}: too many redirects")

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
                    raise SourceUnavailable(f"{self.name}: network unreachable ({type(error).__name__})") \
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
                            raise ReachedLimit(f"{self.name}: too many requests (HTTP 429)")
                        raise SourceUnavailable(f"{self.name}: the server is not responding (HTTP {code})")
                    self._sleep(after if after is not None else wait)
                    continue
                if code in (401, 403):
                    raise SourceUnavailable(f"{self.name}: access denied (HTTP {code})")
                if code == 404:
                    raise UnreadableContent(f"{self.name}: content not found (HTTP 404)")
                if code >= 400:
                    raise UnreadableContent(f"{self.name}: request refused (HTTP {code})")
                return use(answer)
            finally:
                answer.close()
        raise SourceUnavailable(f"{self.name}: request failed")

    @staticmethod
    def _body(answer: httpx.Response, maximum: int) -> bytes:
        declared = answer.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > maximum:
            raise UnreadableContent(f"response too large ({int(declared) // MB} MB)")
        parts, total = [], 0
        for piece in answer.iter_bytes():
            total += len(piece)
            if total > maximum:
                raise UnreadableContent(f"response too large (over {maximum // MB} MB)")
            parts.append(piece)
        return b"".join(parts)

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                 max_mb: float = 10) -> Any:
        def use(answer: httpx.Response) -> Any:
            body = self._body(answer, int(max_mb * MB))
            try:
                return json.loads(body)
            except ValueError as error:
                raise UnreadableContent(f"{self.name}: response not in JSON format") from error
        return self._with_retries(url, params, headers, use)

    def get_text(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  max_mb: float = 10) -> str:
        def use(answer: httpx.Response) -> str:
            body = self._body(answer, int(max_mb * MB))
            return body.decode(answer.encoding or "utf-8", errors="replace")
        return self._with_retries(url, params, headers, use)
