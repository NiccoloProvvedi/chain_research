"""Browser vero (Chromium del progetto) senza Internet: filtro delle reti vietate, estrattore, motori di ricerca.

127.0.0.2 fa la parte di un sito pubblico; 127.0.0.1 è la «rete di casa» che non si deve raggiungere.
"""
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ricerca_catena import configurazione
from ricerca_catena.web import rete
from ricerca_catena.web.browser import Browser
from ricerca_catena.web.proxy import ProxySicuro
from ricerca_catena.web.raccolta import ESTRATTORE, Raccolta

pytestmark = pytest.mark.browser
SEGRETO = "segreto-di-casa"


class Siti:
    def __init__(self) -> None:
        self.privati: list[str] = []
        pubblici = self

        class Gestore(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.server is pubblici.casa:
                    pubblici.privati.append(self.path)
                if self.path == "/rinvio":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{pubblici.porta_casa}/segreto")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                corpo = f"<html><body><p>{SEGRETO if self.server is pubblici.casa else 'pagina pubblica'}</p></body></html>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, *_a) -> None:
                pass

        self.pubblico = http.server.ThreadingHTTPServer(("127.0.0.2", 0), Gestore)
        self.casa = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gestore)
        self.porta_pubblica, self.porta_casa = self.pubblico.server_address[1], self.casa.server_address[1]
        for server in (self.pubblico, self.casa):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def chiudi(self) -> None:
        for server in (self.pubblico, self.casa):
            server.shutdown()
            server.server_close()


@pytest.fixture
def siti(monkeypatch: pytest.MonkeyPatch) -> Iterator[Siti]:
    monkeypatch.setattr(rete, "ip_vietato", lambda ip: ip != "127.0.0.2")
    rete.host_vietato.cache_clear()
    s = Siti()
    yield s
    s.chiudi()
    rete.host_vietato.cache_clear()


@pytest.fixture
def browser(siti: Siti, tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configurazione.cartella("cartella_motori"), tmp_path / "profilo")
    yield b
    b.close()


def test_pagina_pubblica_passa(browser: Browser, siti: Siti) -> None:
    browser.pagina.goto(f"http://127.0.0.2:{siti.porta_pubblica}/x", timeout=10_000)
    assert browser.pagina.inner_text("body") == "pagina pubblica"


def test_collegamento_diretto_bloccato(browser: Browser, siti: Siti) -> None:
    with pytest.raises(Exception, match="ERR_"):
        browser.pagina.goto(f"http://127.0.0.1:{siti.porta_casa}/x", timeout=10_000)
    assert siti.privati == []


def test_redirect_verso_casa_bloccato(browser: Browser, siti: Siti) -> None:
    try:
        browser.pagina.goto(f"http://127.0.0.2:{siti.porta_pubblica}/rinvio", timeout=10_000)
        testo = browser.pagina.inner_text("body")
    except Exception:  # noqa: BLE001 - anche un errore di navigazione va bene
        testo = ""
    assert SEGRETO not in testo and siti.privati == []


def test_richiesta_api_verso_casa_bloccata(browser: Browser, siti: Siti) -> None:
    try:
        browser.pagina.request.get(f"http://127.0.0.1:{siti.porta_casa}/api", timeout=10_000)
    except Exception:  # noqa: BLE001
        pass
    assert siti.privati == []


def test_websocket_e_fetch_verso_casa_bloccati(browser: Browser, siti: Siti) -> None:
    browser.pagina.goto(f"http://127.0.0.2:{siti.porta_pubblica}/x", timeout=10_000)
    browser.pagina.evaluate(f"new WebSocket('ws://127.0.0.1:{siti.porta_casa}/ws')")
    esito = browser.pagina.evaluate(f"fetch('http://127.0.0.1:{siti.porta_casa}/f', {{mode: 'no-cors'}})"
                                    ".then(() => 'ok').catch(e => String(e))")
    browser.pagina.wait_for_timeout(800)
    assert esito != "ok" and siti.privati == []


def test_profilo_vuoto_a_ogni_domanda(siti: Siti, tmp_path: Path) -> None:
    profilo = tmp_path / "profilo"
    profilo.mkdir()
    (profilo / "vecchio.txt").write_text("resti di una domanda precedente")
    b = Browser("about:blank", configurazione.cartella("cartella_motori"), profilo)
    try:
        assert not (profilo / "vecchio.txt").exists()
    finally:
        b.close()
    assert not profilo.exists()


# --- proxy, a mano ----------------------------------------------------------------------------------------------

def _chiedi(porta: int, richiesta: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", porta), timeout=5) as presa:
        presa.sendall(richiesta)
        ricevuto = b""
        while pezzo := presa.recv(65_536):
            ricevuto += pezzo
            if b"Established" in ricevuto:
                break
    return ricevuto


def test_proxy_rifiuta_casa_e_nomi_locali(siti: Siti) -> None:
    proxy = ProxySicuro()
    try:
        assert b"403" in _chiedi(proxy.porta, f"CONNECT 127.0.0.1:{siti.porta_casa} HTTP/1.1\r\n\r\n".encode())
        assert b"403" in _chiedi(proxy.porta, b"CONNECT localhost:80 HTTP/1.1\r\n\r\n")
        assert b"403" in _chiedi(proxy.porta, f"GET http://127.0.0.1:{siti.porta_casa}/x HTTP/1.1\r\n\r\n".encode())
        assert b"400" in _chiedi(proxy.porta, b"sbagliata\r\n\r\n")
        assert siti.privati == []
    finally:
        proxy.chiudi()


def test_proxy_inoltra_e_chiude_la_connessione(siti: Siti) -> None:
    proxy = ProxySicuro()
    try:
        risposta = _chiedi(proxy.porta, f"GET http://127.0.0.2:{siti.porta_pubblica}/x?a=1 HTTP/1.1\r\n"
                                        f"Host: 127.0.0.2\r\nProxy-Connection: keep-alive\r\n\r\n".encode())
        assert b"200" in risposta and b"pagina pubblica" in risposta and b"Connection: close" in risposta
        assert b"200 Connection Established" in _chiedi(
            proxy.porta, f"CONNECT 127.0.0.2:{siti.porta_pubblica} HTTP/1.1\r\n\r\n".encode())
    finally:
        proxy.chiudi()


# --- pagine e motori di ricerca, con il browser che non esce ------------------------------------------------------

@pytest.fixture
def locale(tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configurazione.cartella("cartella_motori"), tmp_path / "profilo", consenti_locali=True)
    yield b
    b.close()


def test_estrattore_tiene_il_contenuto(locale: Browser) -> None:
    locale.pagina.set_content("<html><head><title>Voce</title></head><body><nav>Menu inutile</nav><article><h2>Storia</h2>"
                              f"<p>{'Testo vero della voce. ' * 40}[1]</p><ul><li>Punto uno</li></ul></article>"
                              "<footer>Piè di pagina</footer></body></html>")
    e = locale.pagina.evaluate(ESTRATTORE)
    assert e["titolo"] == "Voce" and "## Storia" in e["testo"] and "- Punto uno" in e["testo"]
    assert "Menu inutile" not in e["testo"] and "[1]" not in e["testo"] and not e["verifica_antibot"]


def test_estrattore_riconosce_la_verifica_antibot(locale: Browser) -> None:
    locale.pagina.set_content("<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>")
    assert locale.pagina.evaluate(ESTRATTORE)["verifica_antibot"]


DDG = """<div class="result"><a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fesempio.org%2Fa">Titolo A</a>
<a class="result__snippet">Estratto A</a></div>
<div class="result"><a class="result__a" href="https://www.youtube.com/watch?v=abcdefghijk">Video</a></div>"""
BING = """<ol><li class="b_algo"><h2><a href="https://esempio.org/b">Titolo B</a></h2><div class="b_caption"><p>Estratto B</p></div></li></ol>"""


def _motori(locale: Browser, ddg: str) -> list[dict]:
    locale.pagina.route("https://html.duckduckgo.com/**", lambda r: r.fulfill(body=ddg, content_type="text/html"))
    locale.pagina.route("https://www.bing.com/**", lambda r: r.fulfill(body=BING, content_type="text/html"))
    r = Raccolta("domanda", consenti_locali=True)
    r.browser = locale
    return r.raccogli(["una ricerca"])


def test_risultati_di_duckduckgo(locale: Browser) -> None:
    assert _motori(locale, DDG) == [{"url": "https://esempio.org/a", "titolo": "Titolo A", "estratto": "Estratto A"}]


def test_bing_se_duckduckgo_non_da_niente(locale: Browser) -> None:
    assert _motori(locale, "<html></html>") == [{"url": "https://esempio.org/b", "titolo": "Titolo B", "estratto": "Estratto B"}]
