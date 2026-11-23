"""From the name to the Wikipedia article (phases 2-3 of the entity and rows plan): `matches`, `not_entity`,
`choose_article`."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ponte")]

from chain_research.web.sources.wikipedia_graph import Article, WikipediaGraph, matches, not_entity  # noqa: E402


@pytest.mark.parametrize("name,title,context,expected", [
    ("Mario Bettinus", "Mario Bettini", "", "variant"),
    ("Mario Bettinus", "Mario (nome)", "Giuseppe Biancani", ""),  # 23 Oct: the old «simile» merged them
    ("Silvio Piccolomini", "Papa Pio II", "", ""),
    ("Pio II", "Pio III", "", ""),  # different numbers: never the same entity
    ("Democratic Federal Yugoslavia", "Socialist Federal Republic of Yugoslavia", "", ""),
    ("Jugoslavia Democratica Federale", "Jugoslavia Federale Democratica", "", "variant"),
    ("Tux", "Tux (mascot)", "mascotte di Linux", "parentheses"),
    ("Tux", "Tux (mascot)", "Linux", "base"),  # unjustified parentheses: a link is needed
    ("Fermi", "Enrico Fermi", "", "base"),
    ("Giuseppe Biancani", "Biancani", "", ""),  # title shorter than the name: another article
    ("Freedo", "Freedom", "", ""),  # short names: only a one-letter typo
    ("Niccolò Zucchi", "Niccolo Zucchi", "", "exact"),
    ("il Regno di Francia", "Regno di Francia", "", "exact"),
    ("La Thuile", "La Thuile (Italia)", "Courmayeur, Valle d'Aosta, Italia", "parentheses"),
    ("Tex", "TeX", "", "variant"),  # different case beyond the first letter: not the same title
    ("Plutone", "Plutone (astrologia)", "l'astronomo che scoprì la luna", "base"),  # astronomo ≠ astrologia
    ("Plutone", "Plutone (astronomia)", "l'astronomo che scoprì la luna", "parentheses"),
    ("Monte Bianco", "Traforo del Monte Bianco", "", ""),  # the surname rule applies only to one-word names
])
def test_matches(name, title, context, expected) -> None:
    assert matches(name, title, context) == expected


def test_not_entity() -> None:
    assert not_entity(Article("Mario (nome)", "it", "u"))
    assert not_entity(Article("Bettini", "it", "u", categories=["Cognomi italiani"]))
    assert not_entity(Article("Lista di gesuiti", "it", "u"))
    assert not_entity(Article("Mario", "it", "u", disambiguation=True))
    assert not not_entity(Article("Mario Bettini", "it", "u", categories=["Gesuiti italiani"]))


class Fake:
    """Fake Wikipedia for `choose_article`: whole articles (parse), search, properties/categories/links, starts."""

    def __init__(self, articles: dict, research: dict, info: dict) -> None:
        self.articles, self.research, self.info = articles, research, info

    def __call__(self, url, params):
        if params.get("action") == "parse":
            v = self.articles.get(params["page"])
            if v is None:
                return {"error": {"code": "missingtitle"}}, None
            return {"parse": v}, None
        if params.get("list") == "search":
            return {"query": {"search": [{"title": t, "snippet": ""} for t in self.research.get(params["srsearch"], [])]}}, None
        if params.get("prop") == "extracts":
            return {"query": {"pages": [{"title": t, "extract": f"{t} è una voce."} for t in params["titles"].split("|")]}}, None
        pages = []
        neighbours = set((params.get("pltitles") or "").split("|"))
        for t in params["titles"].split("|"):
            x = self.info.get(t, {})
            pages.append({"title": t, "pageprops": {"disambiguation": ""} if x.get("disambiguation") else {},
                           "categories": [{"title": f"Categoria:{c}"} for c in x.get("categories", [])],
                           "links": [{"title": v} for v in x.get("towards", []) if v in neighbours]})
        return {"query": {"pages": pages}}, None


def _article(title, text, hyperlinks=()):
    html = f"<p>{text}</p>" + "".join(f'<p><a href="/wiki/{c.replace(" ", "_")}">{c}</a>.</p>' for c in hyperlinks)
    return {"title": title, "text": html}


def test_namesake_discarded_and_linked_article_chosen() -> None:
    """«Mario Bettinus»: the search gives «Mario (nome)» and «Mario Bettini»; the article linked to Biancani wins."""
    fake = Fake(
        articles={"Giuseppe Biancani": _article("Giuseppe Biancani", "Gesuita bolognese.", ["Mario Bettini"]),
              "Mario Bettini": _article("Mario Bettini", "Mario Bettini è stato un gesuita.")},
        research={"Mario Bettinus": ["Mario (nome)", "Mario Bettini"]},
        info={"Mario Bettini": {"towards": ["Giuseppe Biancani"], "categories": ["Gesuiti italiani"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    v = wiki.choose_article("Mario Bettinus", ["it"], context="gesuita", neighbours=[("it", "Giuseppe Biancani")])
    assert v is not None and v.title == "Mario Bettini"


def test_no_article_is_better_than_a_namesake() -> None:
    fake = Fake(articles={}, research={"Silvio Piccolomini": ["Papa Pio II", "Piccolomini"]}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Silvio Piccolomini", ["it", "en"], context="gesuita") is None


def test_disambiguation_page_gives_candidates_and_linked_one_wins() -> None:
    """«Tux»: the page is a disambiguation; among its articles the one linking Linux (neighbour) wins."""
    fake = Fake(
        articles={"Tux": {"title": "Tux", "text": '<ul><li><a href="/wiki/Tux_(mascotte)">Tux (mascotte)</a></li>'
                                              '<li><a href="/wiki/Tux_(abito)">Tux (abito)</a></li></ul>',
                      "properties": {"disambiguation": ""}},
              "Linux": _article("Linux", "Linux è un kernel."),
              "Tux (mascotte)": _article("Tux (mascotte)", "Tux è il pinguino mascotte di Linux.")},
        research={}, info={"Tux (mascotte)": {"towards": ["Linux"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    v = wiki.choose_article("Tux", ["it"], context="Linux", neighbours=[("it", "Linux")])
    assert v is not None and v.title == "Tux (mascotte)"


def test_tie_asks_tiebreak_and_respects_none() -> None:
    fake = Fake(
        articles={"Linux": _article("Linux", "Linux.", ["Tux (mascotte)", "Tux (gioco)"])},
        research={"Tux": ["Tux (mascotte)", "Tux (gioco)"]},
        info={"Tux (mascotte)": {"towards": ["Linux"]}, "Tux (gioco)": {"towards": ["Linux"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    requested = []

    def tiebreak(name, options):
        requested.append([t for t, _ in options])
        return None

    assert wiki.choose_article("Tux", ["it"], neighbours=[("it", "Linux")], tiebreak=tiebreak) is None
    assert requested == [["Tux (mascotte)", "Tux (gioco)"]]


def test_exact_title_without_links_goes_through_accept() -> None:
    fake = Fake(articles={"Niccolò Zucchi": _article("Niccolò Zucchi", "Niccolò Zucchi è stato un gesuita.")},
                  research={}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Niccolò Zucchi", ["it"]).title == "Niccolò Zucchi"
    assert wiki.choose_article("Niccolò Zucchi", ["it"], accept=lambda v: False) is None


def test_parentheses_justified_beats_exact_title() -> None:
    """«Haiku» in a question about an operating system: «Haiku (sistema operativo)», not the poem."""
    fake = Fake(articles={"Haiku": _article("Haiku", "Lo haiku è un componimento poetico."),
                        "Haiku (sistema operativo)": _article("Haiku (sistema operativo)", "Haiku è un sistema operativo.")},
                  research={"Haiku": ["Haiku", "Haiku (sistema operativo)"]}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    v = wiki.choose_article("Haiku", ["it"], context="un sistema operativo open source erede di BeOS")
    assert v is not None and v.title == "Haiku (sistema operativo)"


def test_generic_categories_do_not_count() -> None:
    """«Arduino»: «Pittori italiani» says nothing about a question that names Italy."""
    fake = Fake(articles={"Anna Maria Arduino": _article("Anna Maria Arduino", "Pittrice.")},
                  research={"Arduino": ["Anna Maria Arduino"]},
                  info={"Anna Maria Arduino": {"categories": ["Pittori italiani del XVII secolo"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Arduino", ["it"], context="una piattaforma hardware nata in Italia") is None


def test_redirect_to_another_entity_does_not_count() -> None:
    """23 Oct, Arduino: on en.wikipedia «Phillip Torrone» redirects to «Limor Fried» (his wife): not his article."""
    fake = Fake(articles={"Phillip Torrone": {"title": "Limor Fried", "text": "<p>Limor Fried is an engineer.</p>",
                                            "redirects": [{"from": "Phillip Torrone", "to": "Limor Fried"}]},
                        "Adafruit Industries": _article("Adafruit Industries", "Adafruit.", ["Limor Fried"]),
                        "Mozilla Firefox": {"title": "Firefox", "text": "<p>Firefox is a web browser.</p>",
                                            "redirects": [{"from": "Mozilla Firefox", "to": "Firefox"}]}},
                  research={}, info={"Limor Fried": {"towards": ["Adafruit Industries"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Phillip Torrone", ["en"], neighbours=[("en", "Adafruit Industries")]) is None
    assert wiki.choose_article("Mozilla Firefox", ["en"]).title == "Firefox"


def test_split_sentences_does_not_cut_abbreviations() -> None:
    """24 Oct, Wikivoyage: «Wikivoyage e. V., Roland Unger» and German dates «(* 15. Dezember 1941» stay whole."""
    from chain_research.web.sources.wikipedia_graph import split_sentences
    assert split_sentences("Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt. Danach kam X.") == [
        "Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt.", "Danach kam X."]
    assert len(split_sentences("Roland Unger (* 15. Dezember 1941 in Thalheim) war ein Maler. Er lebte.")) == 2
    assert split_sentences("J. R. R. Tolkien was born in 1892. He died in 1973.") == [
        "J. R. R. Tolkien was born in 1892.", "He died in 1973."]
    assert split_sentences("Nacque nel 1564. Morì nel 1642.") == ["Nacque nel 1564.", "Morì nel 1642."]


def test_name_mentioned_without_link_is_a_namesake() -> None:
    """24 Oct, Wikipedia: the German Wikivoyage article names «Roland Unger» (chairman of the association) without
    linking him; the article «Roland Unger» is about a painter born in 1941. With mention = "against" it does not pass."""
    fake = Fake(articles={"Wikivoyage": {"title": "Wikivoyage", "text": '<p>Der Vorsitzende des Wikivoyage e. V., Roland '
                                       'Unger, wurde von der <a href="/wiki/Wikimedia">Wikimedia</a> angefragt.</p>'},
                        "Roland Unger": _article("Roland Unger", "Roland Unger (* 15. Dezember 1941) war ein Maler.")},
                  research={}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    wiki.mention = "against"
    assert wiki.choose_article("Roland Unger", ["de"], neighbours=[("de", "Wikivoyage")]) is None
    wiki.mention = "for"
    assert wiki.choose_article("Roland Unger", ["de"], neighbours=[("de", "Wikivoyage")]).title == "Roland Unger"


def test_corporate_suffixes_do_not_count() -> None:
    """29 Oct, Raspberry Pi: «Premier Farnell Ltd.» is «Premier Farnell»."""
    from chain_research.web.sources.wikipedia_graph import without_corporate_suffix
    from chain_research.web.chain import _entity_key
    assert without_corporate_suffix("Premier Farnell Ltd.") == "Premier Farnell"
    assert without_corporate_suffix("Ferrari S.p.A.") == "Ferrari"
    assert without_corporate_suffix("Russell Investments") == "Russell Investments"
    assert matches("Premier Farnell Ltd.", "Premier Farnell") == "exact"
    assert _entity_key("Premier Farnell Ltd.") == _entity_key("Premier Farnell")
