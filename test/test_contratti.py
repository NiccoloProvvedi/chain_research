"""Controlli dal vivo dei servizi esterni: si rompono quando cambiano loro, non noi. Da lanciare ogni notte, a parte:

    venv/bin/python -m pytest -q -m "rete or claude"

Nessun contatto personale nelle richieste: la cartella di casa dei test è vuota.
"""
from collections.abc import Iterator
from pathlib import Path

import pytest

from ricerca_catena import configurazione
from ricerca_catena.modello import claude_json
from ricerca_catena.web.browser import Browser
from ricerca_catena.web.fonti.wikipedia_grafo import WikipediaGrafo
from ricerca_catena.web.raccolta import Raccolta


@pytest.fixture(autouse=True)
def _senza_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if not request.node.get_closest_marker("claude"):  # `claude` ha bisogno del suo accesso
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))


@pytest.mark.rete
def test_wikipedia_voce_con_riquadro_e_collegamenti() -> None:
    wiki = WikipediaGrafo(intervallo_s=6.5)
    voce = wiki.voce("Galileo Galilei", "it")
    assert voce is not None and voce.lingue.get("en") == "Galileo Galilei" and voce.categorie
    assert any(r.collegamenti for r in voce.righe) and "1564" in voce.inizio
    # le biografie di it.wiki non hanno il riquadro (12/11: prima corsa di questo test): lo si guarda su un comune
    comune = wiki.voce("Amsterdam", "it")
    assert comune is not None and any(r.testo.startswith("Altitudine") for r in comune.riquadro())


@pytest.mark.rete
def test_wikipedia_ricerca_e_voci_entranti() -> None:
    wiki = WikipediaGrafo(intervallo_s=6.5)
    assert any(x["titolo"] == "Galileo Galilei" for x in wiki.cerca("Galileo Galilei astronomo", "it"))
    assert wiki.entranti("Galileo Galilei", "it", n=5)


@pytest.fixture
def browser(tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configurazione.cartella("cartella_motori"), tmp_path / "profilo")
    yield b
    b.close()


@pytest.mark.rete
@pytest.mark.browser
@pytest.mark.parametrize("motore,script", [
    ("https://html.duckduckgo.com/html/?q=galileo+galilei", "[...document.querySelectorAll('.result a.result__a')].length"),
    ("https://www.bing.com/search?q=galileo+galilei", "[...document.querySelectorAll('li.b_algo h2 a')].length")],
    ids=["duckduckgo", "bing"])
def test_motori_di_ricerca_con_i_selettori_attesi(browser: Browser, motore: str, script: str) -> None:
    browser.pagina.goto(motore, wait_until="domcontentloaded", timeout=30_000)
    browser.pagina.wait_for_timeout(1500)
    assert browser.pagina.evaluate(script) > 0


@pytest.mark.rete
@pytest.mark.browser
def test_pagina_vera_attraverso_il_proxy(browser: Browser) -> None:
    r = Raccolta("domanda")
    r.browser = browser
    estratto = r._estrai("https://www.python.org/about/")
    assert len(estratto["testo"]) > 300 and not estratto["verifica_antibot"]


@pytest.mark.claude
def test_campi_della_risposta_di_claude() -> None:
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    dati, consumo = claude_json.chiedi("Rispondi con ok = true.", "prova", schema, "contratto", modello="haiku", max_budget_usd=0.05)
    assert dati == {"ok": True} and consumo.costo_usd > 0 and consumo.modello_esatto and consumo.token_uscita > 0
