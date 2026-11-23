"""Dal nome alla voce di Wikipedia (fasi 2-3 del piano entità e righe): `corrisponde`, `non_entita`, `scegli_voce`."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ponte")]

from chain_research.web.sources.wikipedia_graph import Article, WikipediaGraph, matches, not_entity  # noqa: E402


@pytest.mark.parametrize("name,title,context,expected", [
    ("Mario Bettinus", "Mario Bettini", "", "variant"),
    ("Mario Bettinus", "Mario (nome)", "Giuseppe Biancani", ""),  # 23/10: il vecchio «simile» li univa
    ("Silvio Piccolomini", "Papa Pio II", "", ""),
    ("Pio II", "Pio III", "", ""),  # numeri diversi: mai la stessa entità
    ("Democratic Federal Yugoslavia", "Socialist Federal Republic of Yugoslavia", "", ""),
    ("Jugoslavia Democratica Federale", "Jugoslavia Federale Democratica", "", "variant"),
    ("Tux", "Tux (mascot)", "mascotte di Linux", "parentheses"),
    ("Tux", "Tux (mascot)", "Linux", "base"),  # parentesi non giustificata: serve un collegamento
    ("Fermi", "Enrico Fermi", "", "base"),
    ("Giuseppe Biancani", "Biancani", "", ""),  # titolo più corto del nome: un'altra voce
    ("Freedo", "Freedom", "", ""),  # nomi corti: solo un refuso di una lettera
    ("Niccolò Zucchi", "Niccolo Zucchi", "", "exact"),
    ("il Regno di Francia", "Regno di Francia", "", "exact"),
    ("La Thuile", "La Thuile (Italia)", "Courmayeur, Valle d'Aosta, Italia", "parentheses"),
    ("Tex", "TeX", "", "variant"),  # maiuscole diverse oltre la prima: non è lo stesso titolo
    ("Plutone", "Plutone (astrologia)", "l'astronomo che scoprì la luna", "base"),  # astronomo ≠ astrologia
    ("Plutone", "Plutone (astronomia)", "l'astronomo che scoprì la luna", "parentheses"),
    ("Monte Bianco", "Traforo del Monte Bianco", "", ""),  # il cognome vale solo per nomi di una parola
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
    """Wikipedia finta per `scegli_voce`: voci intere (parse), ricerca, proprietà/categorie/collegamenti, inizi."""

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


def namesake_discarded_test_and_linked_choice_article() -> None:
    """«Mario Bettinus»: la ricerca dà «Mario (nome)» e «Mario Bettini»; vince la voce collegata a Biancani."""
    fake = Fake(
        articles={"Giuseppe Biancani": _article("Giuseppe Biancani", "Gesuita bolognese.", ["Mario Bettini"]),
              "Mario Bettini": _article("Mario Bettini", "Mario Bettini è stato un gesuita.")},
        research={"Mario Bettinus": ["Mario (nome)", "Mario Bettini"]},
        info={"Mario Bettini": {"towards": ["Giuseppe Biancani"], "categories": ["Gesuiti italiani"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    v = wiki.choose_article("Mario Bettinus", ["it"], context="gesuita", neighbours=[("it", "Giuseppe Biancani")])
    assert v is not None and v.title == "Mario Bettini"


def test_no_article_better_of_namesake() -> None:
    fake = Fake(articles={}, research={"Silvio Piccolomini": ["Papa Pio II", "Piccolomini"]}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Silvio Piccolomini", ["it", "en"], context="gesuita") is None


def test_disambiguation_becomes_candidates_and_wins_tie() -> None:
    """«Tux»: la pagina è una disambiguazione; tra le sue voci vince quella che collega Linux (vicino)."""
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


def test_tie_asks_tiebreak_and_respects_no() -> None:
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


def test_exact_title_without_tie_passes_from_accept() -> None:
    fake = Fake(articles={"Niccolò Zucchi": _article("Niccolò Zucchi", "Niccolò Zucchi è stato un gesuita.")},
                  research={}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Niccolò Zucchi", ["it"]).title == "Niccolò Zucchi"
    assert wiki.choose_article("Niccolò Zucchi", ["it"], accept=lambda v: False) is None


def test_parentheses_justified_beats_exact_title() -> None:
    """«Haiku» in una domanda su un sistema operativo: «Haiku (sistema operativo)», non la poesia."""
    fake = Fake(articles={"Haiku": _article("Haiku", "Lo haiku è un componimento poetico."),
                        "Haiku (sistema operativo)": _article("Haiku (sistema operativo)", "Haiku è un sistema operativo.")},
                  research={"Haiku": ["Haiku", "Haiku (sistema operativo)"]}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    v = wiki.choose_article("Haiku", ["it"], context="un sistema operativo open source erede di BeOS")
    assert v is not None and v.title == "Haiku (sistema operativo)"


def test_generic_categories_not_counted() -> None:
    """«Arduino»: «Pittori italiani» non dice niente su una domanda che nomina l'Italia."""
    fake = Fake(articles={"Anna Maria Arduino": _article("Anna Maria Arduino", "Pittrice.")},
                  research={"Arduino": ["Anna Maria Arduino"]},
                  info={"Anna Maria Arduino": {"categories": ["Pittori italiani del XVII secolo"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Arduino", ["it"], context="una piattaforma hardware nata in Italia") is None


def test_redirect_towards_other_entity_not_counts() -> None:
    """23/10, Arduino: su en.wikipedia «Phillip Torrone» rinvia a «Limor Fried» (la moglie): non è la sua voce."""
    fake = Fake(articles={"Phillip Torrone": {"title": "Limor Fried", "text": "<p>Limor Fried is an engineer.</p>",
                                            "redirects": [{"from": "Phillip Torrone", "to": "Limor Fried"}]},
                        "Adafruit Industries": _article("Adafruit Industries", "Adafruit.", ["Limor Fried"]),
                        "Mozilla Firefox": {"title": "Firefox", "text": "<p>Firefox is a web browser.</p>",
                                            "redirects": [{"from": "Mozilla Firefox", "to": "Firefox"}]}},
                  research={}, info={"Limor Fried": {"towards": ["Adafruit Industries"]}})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    assert wiki.choose_article("Phillip Torrone", ["en"], neighbours=[("en", "Adafruit Industries")]) is None
    assert wiki.choose_article("Mozilla Firefox", ["en"]).title == "Firefox"


def test_split_sentences_without_cut_abbreviations() -> None:
    """24/10, Wikivoyage: «Wikivoyage e. V., Roland Unger» e le date tedesche «(* 15. Dezember 1941» restano intere."""
    from chain_research.web.sources.wikipedia_graph import split_sentences
    assert split_sentences("Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt. Danach kam X.") == [
        "Der Vorsitzende des Wikivoyage e. V., Roland Unger, wurde angefragt.", "Danach kam X."]
    assert len(split_sentences("Roland Unger (* 15. Dezember 1941 in Thalheim) war ein Maler. Er lebte.")) == 2
    assert split_sentences("J. R. R. Tolkien was born in 1892. He died in 1973.") == [
        "J. R. R. Tolkien was born in 1892.", "He died in 1973."]
    assert split_sentences("Nacque nel 1564. Morì nel 1642.") == ["Nacque nel 1564.", "Morì nel 1642."]


def named_test_without_hyperlink_and_namesake() -> None:
    """24/10, Wikipedia: la voce tedesca di Wikivoyage nomina «Roland Unger» (presidente dell'associazione) senza
    collegarlo; la voce «Roland Unger» è di un pittore del 1941. Con menzione = "contro" non passa."""
    fake = Fake(articles={"Wikivoyage": {"title": "Wikivoyage", "text": '<p>Der Vorsitzende des Wikivoyage e. V., Roland '
                                       'Unger, wurde von der <a href="/wiki/Wikimedia">Wikimedia</a> angefragt.</p>'},
                        "Roland Unger": _article("Roland Unger", "Roland Unger (* 15. Dezember 1941) war ein Maler.")},
                  research={}, info={})
    wiki = WikipediaGraph(fetch=fake, interval_s=0.0)
    wiki.mention = "against"
    assert wiki.choose_article("Roland Unger", ["de"], neighbours=[("de", "Wikivoyage")]) is None
    wiki.mention = "favore"
    assert wiki.choose_article("Roland Unger", ["de"], neighbours=[("de", "Wikivoyage")]).title == "Roland Unger"


def test_corporate_suffixes_not_counted() -> None:
    """29/10, Raspberry Pi: «Premier Farnell Ltd.» è «Premier Farnell»."""
    from chain_research.web.sources.wikipedia_graph import without_corporate_suffix
    from chain_research.web.chain import _entity_key
    assert without_corporate_suffix("Premier Farnell Ltd.") == "Premier Farnell"
    assert without_corporate_suffix("Ferrari S.p.A.") == "Ferrari"
    assert without_corporate_suffix("Russell Investments") == "Russell Investments"
    assert matches("Premier Farnell Ltd.", "Premier Farnell") == "exact"
    assert _entity_key("Premier Farnell Ltd.") == _entity_key("Premier Farnell")
