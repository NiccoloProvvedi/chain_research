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

from . import network

HEAD_LIMIT = 65_536
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 120.0
_HEAD_END = b"\r\n\r\n"


class ForbiddenAddress(OSError):
    """Il nome porta al computer stesso o alla rete di casa."""


def connect(host: str, port_number: int, local_allow: bool = False) -> socket.socket:
    """Una connessione verso `host`, solo se tutti i suoi indirizzi sono permessi."""
    if not local_allow and network.local_name(host):
        raise ForbiddenAddress(host)
    addresses = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, port_number, type=socket.SOCK_STREAM)))
    if not local_allow and any(network.forbidden_ip(ip) for ip in addresses):
        raise ForbiddenAddress(host)
    last: OSError | None = None
    for ip in addresses:
        try:
            return socket.create_connection((ip, port_number), timeout=CONNECT_TIMEOUT)
        except OSError as error:
            last = error
    raise last or OSError(f"connessione non riuscita: {host}")


def _read_head(listener: socket.socket) -> tuple[bytes, bytes]:
    """Le intestazioni della richiesta (fino alla riga vuota esclusa) e i byte già arrivati dopo di esse."""
    data = b""
    while _HEAD_END not in data:
        piece = listener.recv(8192)
        if not piece or len(data) > HEAD_LIMIT:
            raise ConnectionError("intestazioni mancanti o troppo lunghe")
        data += piece
    head, _, rest = data.partition(_HEAD_END)
    return head, rest


def _teardown_after_answer(head: bytes) -> bytes:
    """Una connessione verso il proxy serve un solo sito: il browser non deve riusarla per un altro."""
    rows = [r for r in head.split(b"\r\n")
             if not r.lower().startswith((b"connection:", b"proxy-connection:", b"keep-alive:"))]
    return b"\r\n".join([*rows, b"Connection: close"]) + _HEAD_END


def _pour(client: socket.socket, remote: socket.socket,
             head_filter: Callable[[bytes], bytes] | None = None) -> None:
    """Copia i byte nei due sensi finché entrambi hanno finito; `filtro_testa` riscrive le intestazioni della risposta."""
    client.setblocking(True)
    remote.setblocking(True)
    in_pending = b""
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ, remote)
        selector.register(remote, selectors.EVENT_READ, client)
        pending = 2
        while pending:
            events = selector.select(IDLE_TIMEOUT)
            if not events:
                return
            for key, _ in events:
                piece = key.fileobj.recv(65_536)
                destination = key.date
                if not piece:
                    selector.unregister(key.fileobj)
                    pending -= 1
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                if head_filter is not None and key.fileobj is remote:
                    in_pending += piece
                    if _HEAD_END not in in_pending:
                        if len(in_pending) > HEAD_LIMIT:
                            return
                        continue
                    head, _, rest = in_pending.partition(_HEAD_END)
                    piece = head_filter(head) + rest
                    head_filter, in_pending = None, b""
                destination.sendall(piece)


def _port_number_host(target: str, base_port_number: int) -> tuple[str, int]:
    parts = urlsplit(f"//{target}")
    if not parts.hostname:
        raise ValueError(target)
    return parts.hostname, parts.port or base_port_number


class _Handler(socketserver.BaseRequestHandler):
    server: "_Server"

    def _answer_questions(self, code: bytes) -> None:
        self.request.sendall(b"HTTP/1.1 " + code + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    def _open(self, host: str, port_number: int) -> socket.socket | None:
        try:
            return connect(host, port_number, self.server.local_allow)
        except ForbiddenAddress:
            self._answer_questions(b"403 Forbidden")
        except OSError:
            self._answer_questions(b"502 Bad Gateway")
        return None

    def handle(self) -> None:
        self.request.settimeout(IDLE_TIMEOUT)
        head, rest = _read_head(self.request)
        rows = head.split(b"\r\n")
        try:
            method, target, version = rows[0].split(b" ", 2)
            text = target.decode("latin-1")
            if method == b"CONNECT":
                host, port_number = _port_number_host(text, 443)
            else:
                parts = urlsplit(text)
                if parts.scheme != "http":
                    raise ValueError(text)
                host, port_number = _port_number_host(parts.netloc, 80)
        except ValueError:
            self._answer_questions(b"400 Bad Request")
            return
        remote = self._open(host, port_number)
        if remote is None:
            return
        with remote:
            if method == b"CONNECT":
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    remote.sendall(rest)
                _pour(self.request, remote)
                return
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            headers = [r for r in rows[1:] if not r.lower().startswith((b"proxy-", b"connection:", b"keep-alive:"))]
            first = b" ".join((method, path.encode("latin-1"), version))
            remote.sendall(b"\r\n".join([first, *headers, b"Connection: close"]) + _HEAD_END + rest)
            _pour(self.request, remote, _teardown_after_answer)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, local_allow: bool) -> None:
        self.local_allow = local_allow
        super().__init__(("127.0.0.1", 0), _Handler)

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001 - una connessione caduta non è un errore
        pass


class SafeProxy:
    """Il proxy in un thread a sé, su una porta libera di 127.0.0.1."""

    def __init__(self, local_allow: bool = False) -> None:
        self._server = _Server(local_allow)
        self.port_number: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port_number}"

    def teardown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
