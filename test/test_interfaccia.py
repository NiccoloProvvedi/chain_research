"""Interfaccia vera nel Chromium del progetto, sul server vero (solo l'avvio del processo dell'indagine è finto)."""
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from ricerca_catena import configurazione, server
from ricerca_catena.indagine import Indagine

pytestmark = pytest.mark.browser
TOKEN = "t" * 64


class App:
    def __init__(self, gestore: server.Gestore, porta: int, pagina: Page, problemi: list[str]) -> None:
        self.gestore, self.porta, self.pagina, self.problemi = gestore, porta, pagina, problemi
        self.avviate: list[str] = []

    def nuova(self, **stato) -> Indagine:
        ind = Indagine.crea("La storia di prova", cartella_base=self.gestore.cartella)
        ind.stato.update(stato)
        ind.salva()
        return ind

    def apri(self) -> None:
        self.pagina.goto(f"http://127.0.0.1:{self.porta}/")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configurazione.cartella("cartella_motori"))
    gestore = server.Gestore(tmp_path / "indagini")
    gestore.cartella.mkdir()
    presa = socket.socket()
    presa.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    presa.bind(("127.0.0.1", 0))
    porta = presa.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.crea_app(TOKEN, lambda: porta, gestore), log_level="warning"))
    filo = threading.Thread(target=srv.run, kwargs={"sockets": [presa]}, daemon=True)
    filo.start()
    while not srv.started:
        time.sleep(0.02)
    with sync_playwright() as p:
        chromium = p.chromium.launch(channel="chromium")
        contesto = chromium.new_context()
        contesto.add_cookies([{"name": "token", "value": TOKEN, "url": f"http://127.0.0.1:{porta}"}])
        pagina = contesto.new_page()
        problemi: list[str] = []
        # L'icona la chiede il browser da solo: l'app non ne ha una.
        pagina.on("console", lambda m: problemi.append(f"{m.text} ({m.location.get('url')})")
                  if m.type == "error" and not m.location.get("url", "").endswith("/favicon.ico") else None)
        pagina.on("pageerror", lambda e: problemi.append(str(e)))
        a = App(gestore, porta, pagina, problemi)
        monkeypatch.setattr(gestore, "avvia", lambda ident, solo_documento=False: a.avviate.append(ident))
        yield a
        chromium.close()
    srv.should_exit = True
    filo.join(10)


def test_pagina_senza_errori_e_nuova_indagine(app: App) -> None:
    app.apri()
    expect(app.pagina.locator("#elenco-vuoto")).to_be_visible()
    app.pagina.fill("#argomento", "Storia di Arduino")
    app.pagina.click("#avvia")
    expect(app.pagina.locator("#elenco li")).to_have_count(1)
    assert len(app.avviate) == 1 and app.problemi == []


def test_il_vecchio_fine_non_ferma_la_vista_in_diretta(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    ind = app.nuova(stato="in_corso", fase="Lavoro", piano=None)
    ind.evento({"tipo": "fine", "stato": "fermata", "motivo": "corsa passata"})
    ind.evento({"tipo": "inizio", "modello_regia": "sonnet", "argomento": "x", "limiti": {}})
    monkeypatch.setattr(app.gestore, "in_esecuzione", lambda ident: True)
    app.apri()
    attivita = app.pagina.locator("#v-attivita")
    expect(attivita).to_contain_text("corsa passata")
    ind.evento({"tipo": "fase", "testo": "evento-dopo-la-ripresa"})
    expect(attivita).to_contain_text("evento-dopo-la-ripresa")
    assert app.problemi == []


def test_pulsanti_secondo_lo_stato(app: App) -> None:
    app.nuova(stato="finita", motivo_fine="basta", documento={"pdf": "x.pdf"})
    app.apri()
    expect(app.pagina.locator("#v-stato")).to_have_text("finita")
    expect(app.pagina.locator("#b-apri")).to_be_visible()
    expect(app.pagina.locator("#b-riprendi")).to_be_hidden()
    expect(app.pagina.locator("#b-ferma")).to_be_hidden()


def test_riprendi_con_limiti_nuovi(app: App) -> None:
    ind = app.nuova(stato="fermata", motivo_fine="budget")
    app.apri()
    expect(app.pagina.locator("#b-riprendi")).to_be_visible()
    app.pagina.click("#b-riprendi")
    expect(app.pagina.locator("#r-budget")).to_have_value("5")
    app.pagina.fill("#r-budget", "11")
    app.pagina.click("#r-conferma")
    expect(app.pagina.locator("#riprendi")).to_be_hidden()
    assert app.avviate == [ind.cartella.name]
    assert json.loads((ind.cartella / "stato.json").read_text())["limiti"]["budget_usd"] == 11
    assert app.problemi == []


def test_senza_limiti_chiede_conferma(app: App) -> None:
    app.apri()
    app.pagina.fill("#argomento", "Storia di Arduino")
    app.pagina.check("#senza-limiti")
    app.pagina.once("dialog", lambda d: d.dismiss())
    app.pagina.click("#avvia")
    app.pagina.wait_for_timeout(300)
    assert app.avviate == []
    app.pagina.once("dialog", lambda d: d.accept())
    app.pagina.click("#avvia")
    expect(app.pagina.locator("#elenco li")).to_have_count(1)
    assert len(app.avviate) == 1


def test_impostazioni_avanzate_salvate(app: App) -> None:
    app.apri()
    app.pagina.click("#apri-impostazioni")
    expect(app.pagina.locator("#i-pagine-domanda")).to_have_value("30")
    app.pagina.fill("#i-pagine-domanda", "12")
    app.pagina.click("#i-salva")
    expect(app.pagina.locator("#impostazioni")).to_be_hidden()
    assert configurazione.carica()["pagine_domanda"] == 12 and app.problemi == []


def test_modello_della_revisione_nelle_impostazioni(app: App) -> None:
    app.apri()
    app.pagina.click("#apri-impostazioni")
    expect(app.pagina.locator("#i-modello-revisione")).to_have_value("sonnet")
    app.pagina.select_option("#i-modello-revisione", "opus")
    app.pagina.click("#i-salva")
    expect(app.pagina.locator("#impostazioni")).to_be_hidden()
    assert configurazione.carica()["modello_revisione"] == "opus" and app.problemi == []


def test_lo_stato_di_ogni_risposta_si_vede(app: App) -> None:
    def d(n: int, **altro) -> dict:
        return {"id": f"d{n}", "giro": 1, "sezione": "s1", "domanda": f"Domanda {n}", "stato": "risposta",
                "risposta": f"**Risposta:** valore {n} [1].\n\n**Fiducia:** alta — dal modello", "fonti": [], **altro}
    app.nuova(stato="finita", piano={"titolo": "T", "obiettivo": "O", "sezioni": [{"id": "s1", "titolo": "Uno", "scopo": "x"}]},
              domande=[d(1, stato_risposta="verificata", motivi_stato=[]),
                       d(2, stato_risposta="incerta", motivi_stato=["1 anelli della catena non confermati"]),
                       d(3)])
    app.apri()
    app.pagina.click("#elenco li")
    righe = app.pagina.locator("#v-domande .domanda")
    expect(righe).to_have_count(3)
    expect(righe.nth(0).locator(".chip.verificata")).to_have_text("verificata")
    expect(righe.nth(1).locator(".chip.incerta")).to_have_text("incerta")
    expect(righe.nth(1).locator(".info")).to_contain_text("1 anelli della catena non confermati")
    expect(righe.nth(1).locator(".info")).not_to_contain_text("fiducia")
    expect(righe.nth(2).locator(".info")).to_contain_text("fiducia alta")  # indagine di prima: resta la riga del modello
    assert app.problemi == []
