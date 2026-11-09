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

from .. import rete
from .base import ContenutoNonLeggibile, FonteNonDisponibile, LimiteRaggiunto
from .limiti import Limitatore

log = logging.getLogger("ricerca_catena.fonti")
MAX_REDIRECT = 5
TENTATIVI = 3
MB = 1024 * 1024


class IndirizzoVietato(httpx.ConnectError):
    """Il nome porta al computer stesso o alla rete di casa."""


def risolvi(host: str, porta: int) -> list[str]:
    try:
        return list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, porta, type=socket.SOCK_STREAM)))
    except OSError as errore:
        raise httpcore.ConnectError(f"nome non risolto: {host}") from errore


class BackendSicuro(httpcore.SyncBackend):
    """Backend di rete di httpcore che si collega solo a indirizzi pubblici controllati."""

    def __init__(self, consenti_locali: bool = False) -> None:
        self.consenti_locali = consenti_locali

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        indirizzi = risolvi(host, port)
        if not self.consenti_locali and (rete.nome_locale(host) or any(rete.ip_vietato(ip) for ip in indirizzi)):
            raise IndirizzoVietato(f"indirizzo non consentito: {host}")
        ultimo: Exception | None = None
        for ip in indirizzi:
            try:
                return super().connect_tcp(ip, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as errore:
                ultimo = errore
        raise ultimo or httpcore.ConnectError(f"connessione non riuscita: {host}")


class TrasportoSicuro(httpx.HTTPTransport):
    def __init__(self, consenti_locali: bool = False, **opzioni: Any) -> None:
        super().__init__(**opzioni)
        self._pool._network_backend = BackendSicuro(consenti_locali)  # httpx non lo espone: versione fissata


def _secondi_retry_after(valore: str | None) -> float | None:
    if not valore:
        return None
    try:
        return max(0.0, float(valore))
    except ValueError:
        pass
    try:
        return max(0.0, email.utils.parsedate_to_datetime(valore).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class ClientFonte:
    """Client per una fonte. `domini` = domini consentiti (None = qualsiasi dominio pubblico, per l'audio)."""

    def __init__(self, nome: str, *, user_agent: str, domini: Iterable[str] | None,
                 limitatore: Limitatore | None = None, consenti_locali: bool = False,
                 timeout_lettura: float = 30.0, transport: httpx.BaseTransport | None = None,
                 dormi: Callable[[float], None] = time.sleep, max_redirect: int = MAX_REDIRECT) -> None:
        self.nome = nome
        self.domini = {d.lower() for d in domini} if domini is not None else None
        self.limitatore = limitatore
        self.consenti_locali = consenti_locali
        self.max_redirect = max_redirect  # l'audio dei podcast passa spesso da 3-4 contatori di ascolti
        self._dormi = dormi
        self._client = httpx.Client(
            transport=transport or TrasportoSicuro(consenti_locali=consenti_locali),
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout_lettura, connect=10.0), follow_redirects=False)

    def chiudi(self) -> None:
        self._client.close()

    # --- controlli -----------------------------------------------------------------------------------------------

    def _controlla_url(self, url: str) -> None:
        parti = urlsplit(url)
        host = (parti.hostname or "").lower()
        if parti.scheme not in ("http", "https") or not host:
            raise ContenutoNonLeggibile(f"{self.nome}: indirizzo non valido")
        if self.domini is not None and not any(host == d or host.endswith("." + d) for d in self.domini):
            raise ContenutoNonLeggibile(f"{self.nome}: dominio non consentito ({host})")
        if not self.consenti_locali and not rete.indirizzo_sicuro(url):
            raise ContenutoNonLeggibile(f"{self.nome}: indirizzo del computer o della rete locale, bloccato")

    # --- richieste -----------------------------------------------------------------------------------------------

    def _invia(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None) -> httpx.Response:
        """Una richiesta GET con redirect seguiti a mano (controllo a ogni salto). La risposta è in streaming."""
        for _ in range(self.max_redirect + 1):
            self._controlla_url(url)
            if self.limitatore:
                self.limitatore.attendi()
            richiesta = self._client.build_request("GET", url, params=params, headers=headers)
            risposta = self._client.send(richiesta, stream=True)
            if risposta.status_code in (301, 302, 303, 307, 308) and risposta.headers.get("location"):
                risposta.close()
                url, params = urljoin(str(risposta.request.url), risposta.headers["location"]), None
                continue
            return risposta
        raise ContenutoNonLeggibile(f"{self.nome}: troppi reindirizzamenti")

    def _con_ritentativi(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None,
                         uso: Callable[[httpx.Response], Any]) -> Any:
        for tentativo in range(1, TENTATIVI + 1):
            attesa = 2.0 ** tentativo + random.uniform(0, 1)
            try:
                risposta = self._invia(url, params, headers)
            except IndirizzoVietato as errore:
                raise ContenutoNonLeggibile(f"{self.nome}: {errore}") from errore
            except httpx.TransportError as errore:
                if tentativo == TENTATIVI:
                    raise FonteNonDisponibile(f"{self.nome}: rete non raggiungibile ({type(errore).__name__})") \
                        from errore
                self._dormi(attesa)
                continue
            try:
                codice = risposta.status_code
                if codice in (429,) or codice >= 500:
                    dopo = _secondi_retry_after(risposta.headers.get("retry-after"))
                    if tentativo == TENTATIVI or (dopo is not None and dopo > 120):
                        if codice == 429:
                            if self.limitatore and dopo:
                                self.limitatore.pausa(dopo)
                            raise LimiteRaggiunto(f"{self.nome}: troppe richieste (HTTP 429)")
                        raise FonteNonDisponibile(f"{self.nome}: il server non risponde (HTTP {codice})")
                    self._dormi(dopo if dopo is not None else attesa)
                    continue
                if codice in (401, 403):
                    raise FonteNonDisponibile(f"{self.nome}: accesso negato (HTTP {codice})")
                if codice == 404:
                    raise ContenutoNonLeggibile(f"{self.nome}: contenuto non trovato (HTTP 404)")
                if codice >= 400:
                    raise ContenutoNonLeggibile(f"{self.nome}: richiesta rifiutata (HTTP {codice})")
                return uso(risposta)
            finally:
                risposta.close()
        raise FonteNonDisponibile(f"{self.nome}: richiesta non riuscita")

    @staticmethod
    def _corpo(risposta: httpx.Response, massimo: int) -> bytes:
        dichiarata = risposta.headers.get("content-length")
        if dichiarata and dichiarata.isdigit() and int(dichiarata) > massimo:
            raise ContenutoNonLeggibile(f"risposta troppo grande ({int(dichiarata) // MB} MB)")
        parti, totale = [], 0
        for pezzo in risposta.iter_bytes():
            totale += len(pezzo)
            if totale > massimo:
                raise ContenutoNonLeggibile(f"risposta troppo grande (oltre {massimo // MB} MB)")
            parti.append(pezzo)
        return b"".join(parti)

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                 max_mb: float = 10) -> Any:
        def uso(risposta: httpx.Response) -> Any:
            corpo = self._corpo(risposta, int(max_mb * MB))
            try:
                return json.loads(corpo)
            except ValueError as errore:
                raise ContenutoNonLeggibile(f"{self.nome}: risposta non in formato JSON") from errore
        return self._con_ritentativi(url, params, headers, uso)

    def get_testo(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  max_mb: float = 10) -> str:
        def uso(risposta: httpx.Response) -> str:
            corpo = self._corpo(risposta, int(max_mb * MB))
            return corpo.decode(risposta.encoding or "utf-8", errors="replace")
        return self._con_ritentativi(url, params, headers, uso)
