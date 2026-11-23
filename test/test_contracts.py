"""Controlli dal vivo dei servizi esterni: si rompono quando cambiano loro, non noi. Da lanciare ogni notte, a parte:

    venv/bin/python -m pytest -q -m "rete or claude"

Nessun contatto personale nelle richieste: la cartella di casa dei test è vuota.
"""
from collections.abc import Iterator
from pathlib import Path

import pytest

from chain_research import configuration
from chain_research.model import claude_json
from chain_research.web.browser import Browser
from chain_research.web.sources.wikipedia_graph import WikipediaGraph
from chain_research.web.collector import Collector


@pytest.fixture(autouse=True)
def _without_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if not request.node.get_closest_marker("claude"):  # `claude` ha bisogno del suo accesso
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))


@pytest.mark.network
def test_wikipedia_article_with_infobox_and_hyperlinks() -> None:
    wiki = WikipediaGraph(interval_s=6.5)
    article = wiki.article("Galileo Galilei", "it")
    assert article is not None and article.languages.get("en") == "Galileo Galilei" and article.categories
    assert any(r.hyperlinks for r in article.rows) and "1564" in article.start
    # le biografie di it.wiki non hanno il riquadro (12/11: prima corsa di questo test): lo si guarda su un comune
    municipality = wiki.article("Amsterdam", "it")
    assert municipality is not None and any(r.text.startswith("Altitudine") for r in municipality.infobox())


@pytest.mark.network
def test_wikipedia_research_and_articles_incoming() -> None:
    wiki = WikipediaGraph(interval_s=6.5)
    assert any(x["title"] == "Galileo Galilei" for x in wiki.search("Galileo Galilei astronomo", "it"))
    assert wiki.incoming("Galileo Galilei", "it", n=5)


@pytest.fixture
def browser(tmp_path: Path) -> Iterator[Browser]:
    b = Browser("about:blank", configuration.folder("engines_folder"), tmp_path / "profile")
    yield b
    b.close()


@pytest.mark.network
@pytest.mark.browser
@pytest.mark.parametrize("engine,script", [
    ("https://html.duckduckgo.com/html/?q=galileo+galilei", "[...document.querySelectorAll('.result a.result__a')].length"),
    ("https://www.bing.com/search?q=galileo+galilei", "[...document.querySelectorAll('li.b_algo h2 a')].length")],
    ids=["duckduckgo", "bing"])
def test_engines_of_research_with_i_selectors_expected(browser: Browser, engine: str, script: str) -> None:
    browser.page.goto(engine, wait_until="domcontentloaded", timeout=30_000)
    browser.page.wait_for_timeout(1500)
    assert browser.page.evaluate(script) > 0


@pytest.mark.network
@pytest.mark.browser
def test_true_page_through_proxy(browser: Browser) -> None:
    r = Collector("question")
    r.browser = browser
    snippet = r._extract("https://www.python.org/about/")
    assert len(snippet["text"]) > 300 and not snippet["antibot_verification"]


@pytest.mark.claude
def test_fields_of_answer_of_claude() -> None:
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    data, usage = claude_json.ask("Rispondi con ok = true.", "proof", schema, "contract", model="haiku", max_budget_usd=0.05)
    assert data == {"ok": True} and usage.cost_usd > 0 and usage.exact_model and usage.output_tokens > 0
