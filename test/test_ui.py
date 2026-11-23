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

from chain_research import configuration, server
from chain_research.investigation import Investigation

pytestmark = pytest.mark.browser
TOKEN = "t" * 64


class App:
    def __init__(self, handler: server.Manager, port_number: int, page: Page, problems: list[str]) -> None:
        self.handler, self.port_number, self.page, self.problems = handler, port_number, page, problems
        self.started: list[str] = []

    def fresh(self, **status) -> Investigation:
        ind = Investigation.create("La storia di prova", base_folder=self.handler.folder)
        ind.status.update(status)
        ind.save()
        return ind

    def open_page(self) -> None:
        self.page.goto(f"http://127.0.0.1:{self.port_number}/")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configuration.folder("engines_folder"))
    handler = server.Manager(tmp_path / "indagini")
    handler.folder.mkdir()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port_number = listener.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(TOKEN, lambda: port_number, handler), log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.02)
    with sync_playwright() as p:
        chromium = p.chromium.launch(channel="chromium")
        context = chromium.new_context()
        context.add_cookies([{"name": "token", "value": TOKEN, "url": f"http://127.0.0.1:{port_number}"}])
        page = context.new_page()
        problems: list[str] = []
        # L'icona la chiede il browser da solo: l'app non ne ha una.
        page.on("console", lambda m: problems.append(f"{m.text} ({m.location.get('url')})")
                  if m.type == "error" and not m.location.get("url", "").endswith("/favicon.ico") else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        a = App(handler, port_number, page, problems)
        monkeypatch.setattr(handler, "launch", lambda ident, document_only=False: a.started.append(ident))
        yield a
        chromium.close()
    srv.should_exit = True
    thread.join(10)


def test_page_without_errors_and_new_investigation(app: App) -> None:
    app.open_page()
    expect(app.page.locator("#elenco-vuoto")).to_be_visible()
    app.page.fill("#argomento", "Storia di Arduino")
    app.page.click("#avvia")
    expect(app.page.locator("#elenco li")).to_have_count(1)
    assert len(app.started) == 1 and app.problems == []


def test_old_end_not_stop_view_in_live(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    ind = app.fresh(status="in_progress", phase="Lavoro", plan=None)
    ind.event({"kind": "end", "status": "stopped", "reason": "corsa passata"})
    ind.event({"kind": "start", "director_model": "sonnet", "topic": "x", "limits": {}})
    monkeypatch.setattr(app.handler, "in_execution", lambda ident: True)
    app.open_page()
    activity = app.page.locator("#v-attivita")
    expect(activity).to_contain_text("corsa passata")
    ind.event({"kind": "phase", "text": "evento-dopo-la-ripresa"})
    expect(activity).to_contain_text("evento-dopo-la-ripresa")
    assert app.problems == []


def test_buttons_second_status(app: App) -> None:
    app.fresh(status="finished", end_reason="basta", document={"pdf": "x.pdf"})
    app.open_page()
    expect(app.page.locator("#v-stato")).to_have_text("finished")
    expect(app.page.locator("#b-apri")).to_be_visible()
    expect(app.page.locator("#b-riprendi")).to_be_hidden()
    expect(app.page.locator("#b-ferma")).to_be_hidden()


def test_resume_with_new_limits(app: App) -> None:
    ind = app.fresh(status="stopped", end_reason="budget")
    app.open_page()
    expect(app.page.locator("#b-riprendi")).to_be_visible()
    app.page.click("#b-riprendi")
    expect(app.page.locator("#r-budget")).to_have_value("5")
    app.page.fill("#r-budget", "11")
    app.page.click("#r-conferma")
    expect(app.page.locator("#riprendi")).to_be_hidden()
    assert app.started == [ind.folder.name]
    assert json.loads((ind.folder / "status.json").read_text())["limits"]["budget_usd"] == 11
    assert app.problems == []


def test_without_limits_asks_confirmation(app: App) -> None:
    app.open_page()
    app.page.fill("#argomento", "Storia di Arduino")
    app.page.check("#senza-limiti")
    app.page.once("dialog", lambda d: d.dismiss())
    app.page.click("#avvia")
    app.page.wait_for_timeout(300)
    assert app.started == []
    app.page.once("dialog", lambda d: d.accept())
    app.page.click("#avvia")
    expect(app.page.locator("#elenco li")).to_have_count(1)
    assert len(app.started) == 1


def test_settings_saved_advanced(app: App) -> None:
    app.open_page()
    app.page.click("#apri-impostazioni")
    expect(app.page.locator("#i-pagine-domanda")).to_have_value("30")
    app.page.fill("#i-pagine-domanda", "12")
    app.page.click("#i-salva")
    expect(app.page.locator("#impostazioni")).to_be_hidden()
    assert configuration.load()["question_pages"] == 12 and app.problems == []


def test_model_of_review_in_settings(app: App) -> None:
    app.open_page()
    app.page.click("#apri-impostazioni")
    expect(app.page.locator("#i-modello-revisione")).to_have_value("sonnet")
    app.page.select_option("#i-modello-revisione", "opus")
    app.page.click("#i-salva")
    expect(app.page.locator("#impostazioni")).to_be_hidden()
    assert configuration.load()["review_model"] == "opus" and app.problems == []


def test_status_of_every_answer_sees(app: App) -> None:
    def d(n: int, **other) -> dict:
        return {"id": f"d{n}", "round": 1, "section": "s1", "question": f"Domanda {n}", "status": "answer",
                "answer": f"**Risposta:** valore {n} [1].\n\n**Fiducia:** alta — dal modello", "sources": [], **other}
    app.fresh(status="finished", plan={"title": "T", "goal": "O", "sections": [{"id": "s1", "title": "Uno", "purpose": "x"}]},
              questions=[d(1, answer_status="verified", status_reasons=[]),
                       d(2, answer_status="uncertain", status_reasons=["1 anelli della catena non confermati"]),
                       d(3)])
    app.open_page()
    app.page.click("#elenco li")
    rows = app.page.locator("#v-domande .domanda")
    expect(rows).to_have_count(3)
    expect(rows.nth(0).locator(".chip.verificata")).to_have_text("verified")
    expect(rows.nth(1).locator(".chip.incerta")).to_have_text("uncertain")
    expect(rows.nth(1).locator(".info")).to_contain_text("1 anelli della catena non confermati")
    expect(rows.nth(1).locator(".info")).not_to_contain_text("fiducia")
    expect(rows.nth(2).locator(".info")).to_contain_text("fiducia alta")  # indagine di prima: resta la riga del modello
    assert app.problems == []
