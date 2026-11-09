"""Proxy locale del browser: l'unico punto da cui passa tutto il suo traffico (pagine, redirect, WebSocket).

`context.route` di Playwright non vede i redirect né i WebSocket: il controllo delle reti vietate deve stare dove nasce
la connessione. Qui il nome si risolve, si rifiuta il collegamento se UNO qualsiasi degli indirizzi è vietato e ci si
collega proprio all'indirizzo controllato, come fa `fonti/http.py` per il client HTTP (niente DNS rebinding). HTTPS e
WebSocket passano con CONNECT, cifrati: il proxy non li legge.
"""
import selectors
import socket
import socketserver
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from . import rete

LIMITE_TESTA = 65_536
ATTESA_CONNESSIONE = 10.0
ATTESA_INATTIVITA = 120.0
_FINE_TESTA = b"\r\n\r\n"


class IndirizzoVietato(OSError):
    """Il nome porta al computer stesso o alla rete di casa."""


def collega(host: str, porta: int, consenti_locali: bool = False) -> socket.socket:
    """Una connessione verso `host`, solo se tutti i suoi indirizzi sono permessi."""
    if not consenti_locali and rete.nome_locale(host):
        raise IndirizzoVietato(host)
    indirizzi = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, porta, type=socket.SOCK_STREAM)))
    if not consenti_locali and any(rete.ip_vietato(ip) for ip in indirizzi):
        raise IndirizzoVietato(host)
    ultimo: OSError | None = None
    for ip in indirizzi:
        try:
            return socket.create_connection((ip, porta), timeout=ATTESA_CONNESSIONE)
        except OSError as errore:
            ultimo = errore
    raise ultimo or OSError(f"connessione non riuscita: {host}")


def _leggi_testa(presa: socket.socket) -> tuple[bytes, bytes]:
    """Le intestazioni della richiesta (fino alla riga vuota esclusa) e i byte già arrivati dopo di esse."""
    dati = b""
    while _FINE_TESTA not in dati:
        pezzo = presa.recv(8192)
        if not pezzo or len(dati) > LIMITE_TESTA:
            raise ConnectionError("intestazioni mancanti o troppo lunghe")
        dati += pezzo
    testa, _, resto = dati.partition(_FINE_TESTA)
    return testa, resto


def _chiudi_dopo_la_risposta(testa: bytes) -> bytes:
    """Una connessione verso il proxy serve un solo sito: il browser non deve riusarla per un altro."""
    righe = [r for r in testa.split(b"\r\n")
             if not r.lower().startswith((b"connection:", b"proxy-connection:", b"keep-alive:"))]
    return b"\r\n".join([*righe, b"Connection: close"]) + _FINE_TESTA


def _travasa(cliente: socket.socket, remoto: socket.socket,
             filtro_testa: Callable[[bytes], bytes] | None = None) -> None:
    """Copia i byte nei due sensi finché entrambi hanno finito; `filtro_testa` riscrive le intestazioni della risposta."""
    cliente.setblocking(True)
    remoto.setblocking(True)
    in_sospeso = b""
    with selectors.DefaultSelector() as selettore:
        selettore.register(cliente, selectors.EVENT_READ, remoto)
        selettore.register(remoto, selectors.EVENT_READ, cliente)
        aperti = 2
        while aperti:
            eventi = selettore.select(ATTESA_INATTIVITA)
            if not eventi:
                return
            for chiave, _ in eventi:
                pezzo = chiave.fileobj.recv(65_536)
                destinazione = chiave.data
                if not pezzo:
                    selettore.unregister(chiave.fileobj)
                    aperti -= 1
                    try:
                        destinazione.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                if filtro_testa is not None and chiave.fileobj is remoto:
                    in_sospeso += pezzo
                    if _FINE_TESTA not in in_sospeso:
                        if len(in_sospeso) > LIMITE_TESTA:
                            return
                        continue
                    testa, _, resto = in_sospeso.partition(_FINE_TESTA)
                    pezzo = filtro_testa(testa) + resto
                    filtro_testa, in_sospeso = None, b""
                destinazione.sendall(pezzo)


def _host_porta(bersaglio: str, porta_base: int) -> tuple[str, int]:
    parti = urlsplit(f"//{bersaglio}")
    if not parti.hostname:
        raise ValueError(bersaglio)
    return parti.hostname, parti.port or porta_base


class _Gestore(socketserver.BaseRequestHandler):
    server: "_Servitore"

    def _rispondi(self, codice: bytes) -> None:
        self.request.sendall(b"HTTP/1.1 " + codice + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    def _apri(self, host: str, porta: int) -> socket.socket | None:
        try:
            return collega(host, porta, self.server.consenti_locali)
        except IndirizzoVietato:
            self._rispondi(b"403 Forbidden")
        except OSError:
            self._rispondi(b"502 Bad Gateway")
        return None

    def handle(self) -> None:
        self.request.settimeout(ATTESA_INATTIVITA)
        testa, resto = _leggi_testa(self.request)
        righe = testa.split(b"\r\n")
        try:
            metodo, bersaglio, versione = righe[0].split(b" ", 2)
            testo = bersaglio.decode("latin-1")
            if metodo == b"CONNECT":
                host, porta = _host_porta(testo, 443)
            else:
                parti = urlsplit(testo)
                if parti.scheme != "http":
                    raise ValueError(testo)
                host, porta = _host_porta(parti.netloc, 80)
        except ValueError:
            self._rispondi(b"400 Bad Request")
            return
        remoto = self._apri(host, porta)
        if remoto is None:
            return
        with remoto:
            if metodo == b"CONNECT":
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if resto:
                    remoto.sendall(resto)
                _travasa(self.request, remoto)
                return
            percorso = (parti.path or "/") + (f"?{parti.query}" if parti.query else "")
            intestazioni = [r for r in righe[1:] if not r.lower().startswith((b"proxy-", b"connection:", b"keep-alive:"))]
            prima = b" ".join((metodo, percorso.encode("latin-1"), versione))
            remoto.sendall(b"\r\n".join([prima, *intestazioni, b"Connection: close"]) + _FINE_TESTA + resto)
            _travasa(self.request, remoto, _chiudi_dopo_la_risposta)


class _Servitore(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, consenti_locali: bool) -> None:
        self.consenti_locali = consenti_locali
        super().__init__(("127.0.0.1", 0), _Gestore)

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 - una connessione caduta non è un errore
        pass


class ProxySicuro:
    """Il proxy in un thread a sé, su una porta libera di 127.0.0.1."""

    def __init__(self, consenti_locali: bool = False) -> None:
        self._servitore = _Servitore(consenti_locali)
        self.porta: int = self._servitore.server_address[1]
        threading.Thread(target=self._servitore.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    @property
    def indirizzo(self) -> str:
        return f"http://127.0.0.1:{self.porta}"

    def chiudi(self) -> None:
        self._servitore.shutdown()
        self._servitore.server_close()
