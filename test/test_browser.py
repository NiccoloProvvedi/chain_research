"""Browser vero (Chromium del progetto) senza Internet: filtro delle reti vietate, estrattore, motori di ricerca.

127.0.0.2 fa la parte di un sito pubblico; 127.0.0.1 è la «rete di casa» che non si deve raggiungere.
"""
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from chain_research import configuration
from chain_research.web import network
from chain_research.web.browser import Browser
from chain_research.web.proxy import SafeProxy
from chain_research.web.collector import EXTRACTOR, Collector

pytestmark = pytest.mark.browser
SECRET = "segreto-di-casa"


class Sites:
    def __init__(self) -> None:
        self.private: list[str] = []
        public = self

        class Manager(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.server is public.home:
                    public.private.append(self.path)
                if self.path == "/rinvio":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{public.home_port_number}/segreto")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = f"<html><body><p>{SECRET if self.server is public.home else 'pagina pubblica'}</p></body></html>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_to) -> None:
                pass

        self.public = http.server.ThreadingHTTPServer(("127.0.0.2", 0), Manager)
        self.home = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manager)
        self.publish_port_number, self.home_port_number = self.public.server_address[1], self.home.server_address[1]
        for server in (self.public, self.home):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def teardown(self) -> None:
        for server in (self.public, self.home):
            server.shutdown()
            server.server_close()


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sites]:
    monkeypatch.setattr(network, "forbidden_ip", lambda ip: ip != "127.0.0.2")
    network.forbidden_host.cache_clear()
    s = Sites()
    yield s
    s.teardown()
    network.forbidden_host.cache_clear()


@pytest.fixture
def browser(sites: Sites, tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configuration.folder("engines_folder"), tmp_path / "profile")
    yield b
    b.close()


def test_page_publish_passes(browser: Browser, sites: Sites) -> None:
    browser.page.goto(f"http://127.0.0.2:{sites.publish_port_number}/x", timeout=10_000)
    assert browser.page.inner_text("body") == "pagina pubblica"


def test_hyperlink_direct_blocked(browser: Browser, sites: Sites) -> None:
    with pytest.raises(Exception, match="ERR_"):
        browser.page.goto(f"http://127.0.0.1:{sites.home_port_number}/x", timeout=10_000)
    assert sites.private == []


def test_redirect_towards_home_blocked(browser: Browser, sites: Sites) -> None:
    try:
        browser.page.goto(f"http://127.0.0.2:{sites.publish_port_number}/rinvio", timeout=10_000)
        text = browser.page.inner_text("body")
    except Exception:  # noqa: BLE001 - anche un errore di navigazione va bene
        text = ""
    assert SECRET not in text and sites.private == []


def test_request_api_towards_home_blocked(browser: Browser, sites: Sites) -> None:
    try:
        browser.page.request.get(f"http://127.0.0.1:{sites.home_port_number}/api", timeout=10_000)
    except Exception:  # noqa: BLE001
        pass
    assert sites.private == []


def test_websocket_and_fetch_towards_home_blocked(browser: Browser, sites: Sites) -> None:
    browser.page.goto(f"http://127.0.0.2:{sites.publish_port_number}/x", timeout=10_000)
    browser.page.evaluate(f"new WebSocket('ws://127.0.0.1:{sites.home_port_number}/ws')")
    outcome = browser.page.evaluate(f"fetch('http://127.0.0.1:{sites.home_port_number}/f', {{mode: 'no-cors'}})"
                                    ".then(() => 'ok').catch(e => String(e))")
    browser.page.wait_for_timeout(800)
    assert outcome != "ok" and sites.private == []


def test_empty_profile_to_every_question(sites: Sites, tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "old.txt").write_text("resti di una domanda precedente")
    b = Browser("about:blank", configuration.folder("engines_folder"), profile)
    try:
        assert not (profile / "old.txt").exists()
    finally:
        b.close()
    assert not profile.exists()


# --- proxy, a mano ----------------------------------------------------------------------------------------------

def _ask(port_number: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port_number), timeout=5) as listener:
        listener.sendall(request)
        received = b""
        while piece := listener.recv(65_536):
            received += piece
            if b"Established" in received:
                break
    return received


def test_proxy_rejects_home_and_local_names(sites: Sites) -> None:
    proxy = SafeProxy()
    try:
        assert b"403" in _ask(proxy.port_number, f"CONNECT 127.0.0.1:{sites.home_port_number} HTTP/1.1\r\n\r\n".encode())
        assert b"403" in _ask(proxy.port_number, b"CONNECT localhost:80 HTTP/1.1\r\n\r\n")
        assert b"403" in _ask(proxy.port_number, f"GET http://127.0.0.1:{sites.home_port_number}/x HTTP/1.1\r\n\r\n".encode())
        assert b"400" in _ask(proxy.port_number, b"sbagliata\r\n\r\n")
        assert sites.private == []
    finally:
        proxy.teardown()


def test_proxy_forward_and_closes_connection(sites: Sites) -> None:
    proxy = SafeProxy()
    try:
        answer = _ask(proxy.port_number, f"GET http://127.0.0.2:{sites.publish_port_number}/x?a=1 HTTP/1.1\r\n"
                                        f"Host: 127.0.0.2\r\nProxy-Connection: keep-alive\r\n\r\n".encode())
        assert b"200" in answer and b"pagina pubblica" in answer and b"Connection: close" in answer
        assert b"200 Connection Established" in _ask(
            proxy.port_number, f"CONNECT 127.0.0.2:{sites.publish_port_number} HTTP/1.1\r\n\r\n".encode())
    finally:
        proxy.teardown()


# --- pagine e motori di ricerca, con il browser che non esce ------------------------------------------------------

@pytest.fixture
def locale(tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configuration.folder("engines_folder"), tmp_path / "profile", local_allow=True)
    yield b
    b.close()


def test_extractor_keeps_content(locale: Browser) -> None:
    locale.page.set_content("<html><head><title>Voce</title></head><body><nav>Menu inutile</nav><article><h2>Storia</h2>"
                              f"<p>{'Testo vero della voce. ' * 40}[1]</p><ul><li>Punto uno</li></ul></article>"
                              "<footer>Piè di pagina</footer></body></html>")
    e = locale.page.evaluate(EXTRACTOR)
    assert e["title"] == "Article" and "## Storia" in e["text"] and "- Punto uno" in e["text"]
    assert "Menu inutile" not in e["text"] and "[1]" not in e["text"] and not e["antibot_verification"]


def test_extractor_recognizes_verification_antibot(locale: Browser) -> None:
    locale.page.set_content("<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>")
    assert locale.page.evaluate(EXTRACTOR)["antibot_verification"]


DDG = """<div class="result"><a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fesempio.org%2Fa">Titolo A</a>
<a class="result__snippet">Estratto A</a></div>
<div class="result"><a class="result__a" href="https://www.youtube.com/watch?v=abcdefghijk">Video</a></div>"""
BING = """<ol><li class="b_algo"><h2><a href="https://esempio.org/b">Titolo B</a></h2><div class="b_caption"><p>Estratto B</p></div></li></ol>"""


def _engines(locale: Browser, ddg: str) -> list[dict]:
    locale.page.route("https://html.duckduckgo.com/**", lambda r: r.fulfill(body=ddg, content_type="text/html"))
    locale.page.route("https://www.bing.com/**", lambda r: r.fulfill(body=BING, content_type="text/html"))
    r = Collector("question", local_allow=True)
    r.browser = locale
    return r.collect(["una ricerca"])


def test_results_of_duckduckgo(locale: Browser) -> None:
    assert _engines(locale, DDG) == [{"url": "https://esempio.org/a", "title": "Titolo A", "snippet": "Estratto A"}]


def test_bing_if_duckduckgo_not_from_nothing(locale: Browser) -> None:
    assert _engines(locale, "<html></html>") == [{"url": "https://esempio.org/b", "title": "Titolo B", "snippet": "Estratto B"}]
