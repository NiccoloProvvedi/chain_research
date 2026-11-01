"""Browser separato per leggere le pagine (Chromium di Playwright, profilo dentro il progetto, senza i tuoi
account) che non può raggiungere il computer né la rete di casa.

Adattato da jev_ultrafast/browser.py (MIT, vedi LICENZA_JEV_ULTRAFAST.txt): al posto di
Browser Harness, che si collega al Chrome dell'utente, si avvia un Chromium dedicato.
"""
import os
import socket
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as ErrorePlaywright
from playwright.sync_api import sync_playwright

from .rete import host_vietato, indirizzo_sicuro  # noqa: F401 - usati anche da altri moduli

LARGHEZZA, ALTEZZA = 1120, 780


def _togli_lucchetto_abbandonato(cartella_profilo: Path) -> None:
    """Chromium rifiuta un profilo "in uso" se un browser precedente è stato chiuso di colpo (spegnimento del
    container, crash). Se il proprietario del lucchetto non esiste più, il lucchetto viene tolto."""
    lucchetto = cartella_profilo / "SingletonLock"
    try:
        proprietario = os.readlink(lucchetto)
    except OSError:
        return
    host, _, pid = proprietario.rpartition("-")
    vivo = False
    if host == socket.gethostname() and pid.isdigit():
        try:
            os.kill(int(pid), 0)
            vivo = True
        except OSError:
            vivo = False
    if not vivo:
        for nome in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (cartella_profilo / nome).unlink()
            except OSError:
                pass


class Browser:
    def __init__(self, url: str, cartella_motori: Path, cartella_profilo: Path, headless: bool = True,
                 consenti_locali: bool = False) -> None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cartella_motori)
        cartella_profilo.mkdir(parents=True, exist_ok=True)
        _togli_lucchetto_abbandonato(cartella_profilo)
        self.consenti_locali = consenti_locali
        self._pw = sync_playwright().start()
        try:
            # channel="chromium": Chromium completo in modalità headless moderna (molti siti, come DuckDuckGo,
            # bloccano la versione "headless shell").
            self._contesto = self._pw.chromium.launch_persistent_context(
                str(cartella_profilo), headless=headless, channel="chromium",
                viewport={"width": LARGHEZZA, "height": ALTEZZA}, locale="it-IT",
            )
            if not consenti_locali:
                # Il browser del navigatore non può raggiungere il computer né la rete di casa.
                self._contesto.route("**/*", self._filtra_richiesta)
            self.pagina = self._contesto.pages[0] if self._contesto.pages else self._contesto.new_page()
            self.sessione = self._contesto.new_cdp_session(self.pagina)
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            identita = self.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            self.call("Emulation.setUserAgentOverride", userAgent=identita, acceptLanguage="it-IT,it;q=0.9,en;q=0.8")
            if not consenti_locali and url != "about:blank" and not indirizzo_sicuro(url):
                raise ValueError("Indirizzo non consentito: il navigatore non può aprire il computer o la rete di casa.")
            self.pagina.goto(url, wait_until="load", timeout=30_000)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _filtra_richiesta(route: Any) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")) or indirizzo_sicuro(url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def call(self, method: str, **params: Any) -> dict:
        try:
            return self.sessione.send(method, params)
        except ErrorePlaywright as errore:
            raise RuntimeError(str(errore).splitlines()[0]) from None

    def evaluate(self, expression: str) -> Any:
        try:
            response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        except RuntimeError:
            raise StalePage("Document changed during evaluation") from None
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def close(self) -> None:
        contesto, pw = getattr(self, "_contesto", None), getattr(self, "_pw", None)
        self._contesto = self._pw = None
        for chiudi in (contesto.close if contesto else None, pw.stop if pw else None):
            try:
                if chiudi:
                    chiudi()
            except Exception:  # noqa: BLE001 - chiusura best effort
                pass
