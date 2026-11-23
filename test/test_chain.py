"""Guided chain (web/chain.py) with a fake model and fake pages: route, discards, profiles, value."""
import re

import pytest

from chain_research.web import collector as research
from chain_research.web.chain import Chain
from chain_research.proofs.concepts import Concepts

PAGES = {
    "Galileo Galilei allievi": [
        ("https://it.wikipedia.org/wiki/Galileo_Galilei", "Tra gli allievi di Galileo figurano Benedetto Castelli, "
         "Evangelista Torricelli e il gesuita Giuseppe Biancani.")],
    "Giuseppe Biancani allievi": [
        ("https://it.wikipedia.org/wiki/Giuseppe_Biancani", "Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un "
         "gesuita. Tra suoi discepoli figurano Giovanni Riccioli, Mario Bettini, Niccolò Zucchi e Niccolò Cabeo.")],
    "Niccolò Zucchi": [
        ("https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", "Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) "
         "è stato un gesuita, rettore del Collegio Romano.")],
    "Giovanni Riccioli": [
        ("https://it.wikipedia.org/wiki/Giovanni_Riccioli", "Giovanni Riccioli (Ferrara, 17 aprile 1598) fu "
         "gesuita e astronomo a Bologna; non fu mai rettore del Collegio Romano.")],
}
FACTS = {  # check -> candidates that satisfy / deny it (according to the fake pages)
    "gesuita": ({"Giuseppe Biancani", "Niccolò Zucchi", "Giovanni Riccioli"}, {"Benedetto Castelli"}),
    "bolognese": ({"Giuseppe Biancani"}, {"Evangelista Torricelli"}),
    "emiliano": ({"Niccolò Zucchi", "Giovanni Riccioli"}, set()),
    "rettore": ({"Niccolò Zucchi"}, {"Giovanni Riccioli"}),
}


def _row(proofs: str, text: str) -> int:
    for row in proofs.splitlines():
        m = re.match(r"\[(\d+)\] (.*)", row)
        if m and text.lower() in m.group(2).lower():
            return int(m.group(1))
    return 0


def _decide(point: str, data: dict, default):
    if point == "interpret":
        return {"rephrasings": ["x"], "clues": [], "sub_questions": ["who?"]}, "claude"
    if point == "chain":
        return {"starting_point": {"clue": "scienziato pisano", "entity": "Galileo Galilei"},
                "steps": [{"relation": "allievi", "kind": "person",
                           "checks": [{"description": "gesuita", "readings": []},
                                         {"description": "bolognese", "readings": []}]},
                          {"relation": "allievi", "kind": "person",
                           "checks": [{"description": "emiliano", "readings": []},
                                         {"description": "rettore", "readings": ["Collegio Romano"]}]}],
                "answer": {"what": "data di nascita", "checks": []}, "research_language": "it",
                "reason": "m"}, "claude"
    if point == "step":
        return {"searches": [f"{data.get('entity', data.get('description'))} allievi"]}, "claude"
    if point == "candidates" and "description" in data:
        return {"candidates": [{"name": "Giuseppe Biancani", "proof": _row(data["proofs"], "Biancani")}]
                if "Biancani" in data["proofs"] else []}, "claude"
    if point == "candidates":
        names = ["Benedetto Castelli", "Evangelista Torricelli", "Giuseppe Biancani", "Giovanni Riccioli",
                "Mario Bettini", "Niccolò Zucchi", "Niccolò Cabeo"]
        mark = {"Galileo Galilei": "allievi di Galileo", "Giuseppe Biancani": "suoi discepoli"}[data["entity"]]
        rows = [r for r in data["proofs"].splitlines() if mark in r]
        return {"candidates": [{"name": n, "proof": _row("\n".join(rows), n.split()[-1])}
                              for n in names if any(n.split()[-1] in r for r in rows)]}, "claude"
    if point == "verification":
        answer = []
        for block in data["candidates"].split("\n\n"):
            name = block.splitlines()[0].removeprefix("CANDIDATE: ")
            checks = []
            for c in data["checks"]:
                key = re.sub(r"^C\d+: ", "", c.split(" (")[0])
                if key.startswith("is related"):  # tie to the previous link
                    origins = [n.split()[-1] for n in c.split("» with ", 1)[1].split(" (")[0].split(" or ")]
                    found = next((o for o in origins if o in block.split("\n", 1)[-1]), None)
                    checks.append({"check": key, "outcome": "yes" if found else "unknown",
                                      "proof": _row(block, found) if found else 0,
                                      "quote": found or ""})
                    continue
                if key.startswith("is of this kind"):  # all fake candidates are people
                    checks.append({"check": key, "outcome": "yes", "proof": _row(block, name.split()[-1]),
                                      "quote": name.split()[-1]})
                    continue
                si, no = FACTS[key]
                # Zucchi: emiliano and rettore show up only after reading his profile
                needed = {"emiliano": "Parma", "rettore": "Collegio Romano"}.get(key) if "Zucchi" in name else None
                outcome = "yes" if name in si and (needed is None or needed in block) else \
                    ("no" if name in no else "unknown")
                checks.append({"check": key, "outcome": outcome,
                                  "proof": _row(block, name.split()[-1]) if outcome != "unknown" else 0,
                                  "quote": name.split()[-1]})
            answer.append({"name": name, "checks": checks})
        return {"candidates": answer}, "claude"
    if point == "value":
        return ({"value": "6 dicembre 1586", "proof": _row(data["proofs"], "6 dicembre 1586"), "reason": ""}
                if "6 dicembre 1586" in data["proofs"] else {"value": "", "proof": 0, "reason": ""}), "claude"
    return default(), "local"


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch):
    r = research.Collector("question about Galileo", options={"wikipedia_graph": False})
    r.concepts = Concepts()
    monkeypatch.setattr(r.thinker, "decide", _decide)
    monkeypatch.setattr(r, "_reached_limit", lambda: "")
    c = Chain(r)
    r.chain = c
    searched = []

    def search_and_read(searches, pages, purpose=""):
        searched.extend(searches)
        last = c._last_segment()
        for q in searches:
            key = next((k for k in PAGES if q == k or q.startswith(k + " ")), None)
            for url, text in PAGES.get(key, []):
                r.concepts.record(url, url, 1, text, [], [], [])
        return c._after_segments(last)

    monkeypatch.setattr(c, "_search_and_read", search_and_read)
    c.searched = searched
    return c


def test_galileo_chain_reaches_zucchi(chain) -> None:
    chain.walk()
    assert [a.name for a in chain.links[0]] == ["Giuseppe Biancani"]  # Castelli and Torricelli discarded
    assert chain.links[1][0].name == "Niccolò Zucchi"
    assert "Giovanni Riccioli" not in [a.name for a in chain.links[1]]  # "no" with a proof: he was never rector
    assert chain.value["value"] == "6 dicembre 1586" and "Zucchi" in chain.value["proof"].url
    assert "Niccolò Zucchi giuseppe biancani" in chain.searched  # profile searched, with the context
    text = chain.material()
    assert "Galileo Galilei → Giuseppe Biancani" in text and "Giuseppe Biancani → Niccolò Zucchi" in text
    assert "VALUE SOUGHT (data di nascita): 6 dicembre 1586" in text
    assert chain.summary()["value"]["of"] == "Niccolò Zucchi"


def test_chain_broken_without_candidates(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    EMPTY_PAGES: dict = {}
    monkeypatch.setattr(chain, "_search_and_read", lambda searches, pages, purpose="": [])
    assert EMPTY_PAGES == {}
    chain.walk()
    assert chain.links == [] and chain.reason.startswith("chain broken at step 1")
    assert chain.r.write().startswith("**Answer:** Not found")


def test_step_found_by_description(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = dict(PAGES)
    pages["Galileo Galilei allievi"] = [("https://it.wikipedia.org/wiki/Galileo_Galilei",
                                          "Tra gli allievi di Galileo figurano Benedetto Castelli ed Evangelista "
                                          "Torricelli.")]
    pages["gesuita; bolognese allievi"] = [("https://it.wikipedia.org/wiki/Gesuiti_bolognesi",
                                             "Giuseppe Biancani, gesuita nato a Bologna, astronomo e matematico, "
                                             "indicato come allievo di Galileo.")]
    c = chain
    r = c.r

    def search_and_read(searches, max_pages, purpose=""):
        c.searched.extend(searches)
        last = c._last_segment()
        for q in searches:
            key = next((k for k in pages if q == k or q.startswith(k + " ")), None)
            for url, text in pages.get(key, []):
                r.concepts.record(url, url, 1, text, [], [], [])
        return c._after_segments(last)

    monkeypatch.setattr(c, "_search_and_read", search_and_read)
    c.walk()
    assert c.links[0][0].name == "Giuseppe Biancani"
    assert c.history[0]["kept"] == [] and c.history[1]["attempt"] == 4
    assert c.value and c.value["value"] == "6 dicembre 1586"


def test_candidates_from_graph_when_relation_gives_none(chain) -> None:
    c = chain
    c.r.concepts.record("https://keyboardio.com/about", "Keyboardio", 1,
                          "Keyboardio was founded by Kaia Dekker and Jesse Vincent in 2014.",
                          [{"name": "Kaia Dekker", "sentence": "Kaia Dekker", "translation": ""},
                           {"name": "Jesse Vincent", "sentence": "Jesse Vincent", "translation": ""},
                           {"name": "Keyboardio", "sentence": "Keyboardio was founded", "translation": ""}],
                          [{"from": "Kaia Dekker", "relation": "co-fondatrice di", "to": "Keyboardio",
                            "sentence": "Keyboardio was founded by Kaia Dekker", "translation": "", "why": "fondatrice"},
                           {"from": "Jesse Vincent", "relation": "co-fondatore con", "to": "Kaia Dekker",
                            "sentence": "founded by Kaia Dekker and Jesse Vincent", "translation": "", "why": "soci"}])
    from chain_research.web.chain import Link
    hits: dict = {}
    c._candidates_from_graph([Link("Kaia Dekker")], hits)
    assert sorted(a.name for a in hits.values()) == ["Jesse Vincent", "Keyboardio"]
    assert "founded by Kaia Dekker and Jesse Vincent" in hits["jessevincent"].tie_proof.text


# --- Wikipedia as a graph (16 Oct) ------------------------------------------------------------------------------

from chain_research.web.sources.wikipedia_graph import Row, Article, analyze  # noqa: E402


class FakeWiki:
    """Fake Wikipedia articles: Galileo (students with links), Biancani (disciples), Zucchi (birth)."""

    def __init__(self) -> None:
        self.articles = {
            "Galileo Galilei": Article("Galileo Galilei", "it", "https://it.wikipedia.org/wiki/Galileo_Galilei", [
                Row("Galileo Galilei è stato un fisico pisano.", []),
                Row("Tra gli allievi di Galileo figurano Benedetto Castelli e il gesuita Giuseppe Biancani.",
                     ["Benedetto Castelli", "Giuseppe Biancani"], section="Allievi")]),
            "Giuseppe Biancani": Article("Giuseppe Biancani", "it", "https://it.wikipedia.org/wiki/Giuseppe_Biancani", [
                Row("Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un gesuita, matematico e astronomo.", []),
                Row("Tra suoi discepoli figurano Giovanni Riccioli e Niccolò Zucchi.",
                     ["Giovanni Riccioli", "Niccolò Zucchi"], section="Biografia")]),
            "Niccolò Zucchi": Article("Niccolò Zucchi", "it", "https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", [
                Row("Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) è stato un gesuita, rettore del "
                     "Collegio Romano.", [])]),
            "Giovanni Riccioli": Article("Giovanni Riccioli", "it", "https://it.wikipedia.org/wiki/Giovanni_Riccioli", [
                Row("Giovanni Riccioli (Ferrara, 17 aprile 1598) fu gesuita; non fu mai rettore del Collegio "
                     "Romano.", [])]),
            "Benedetto Castelli": Article("Benedetto Castelli", "it", "https://it.wikipedia.org/wiki/B_Castelli", [
                Row("Benedetto Castelli (Brescia, 1578) è stato un monaco benedettino.", [])]),
        }
        self.read_count: list[str] = []

    def article(self, title, language):
        self.read_count.append(title)
        return self.articles.get(title) if language == "it" else None

    def find(self, name, language, context="", accept=None):
        article = self.article(name, language)
        return article if article is not None and (accept is None or accept(article)) else None

    def choose_article(self, name, languages, context="", neighbours=(), research_words="", accept=None, tiebreak=None):
        for language in languages:
            article = self.find(name, language, context, accept)
            if article is not None:
                return article
        return None

    def search(self, text, language, n=5):
        return []

    def incoming(self, title, language, n=60):
        return []

    def starts(self, titles, language):
        return {}

    def url(self, title, language):
        return f"https://{language}.wikipedia.org/wiki/{title}"


def test_chain_on_wikipedia_without_web(chain) -> None:
    """With the Wikipedia articles the step does not search the web: candidates from the rows with links, the
    candidate's article reached through the link (no namesakes), value from the start of the article."""
    c = chain
    c.wiki = FakeWiki()
    c.walk()
    assert c.links[0][0].name == "Giuseppe Biancani" and c.links[0][0].title == ("it", "Giuseppe Biancani")
    assert c.links[1][0].name == "Niccolò Zucchi"
    assert c.value["value"] == "6 dicembre 1586" and "wikipedia.org" in c.value["proof"].url
    assert c.searched == []  # no web search
    assert c.history[0]["attempt"] == 1


def test_analyze_infobox_sentences_and_hyperlinks() -> None:
    html = ('<table class="sinottico"><tr><th>Nascita</th><td><a href="/wiki/Parma">Parma</a>, '
            '<a href="/wiki/6_dicembre">6 dicembre</a> 1586</td></tr></table>'
            '<p>Niccolò Zucchi è stato un gesuita. Fu allievo di <a href="/wiki/Giuseppe_Biancani">Giuseppe '
            'Biancani</a><sup class="reference">[1]</sup> a Parma.</p>'
            '<h2>Note</h2><ol><li>Una nota da saltare</li></ol>')
    rows = analyze(html, "Niccolò Zucchi", "it")
    assert rows[0].kind == "infobox" and rows[0].text == "Nascita: Parma, 6 dicembre 1586"
    assert rows[0].hyperlinks == ["Parma"]  # the day is not an entity
    sentences = [r for r in rows if r.kind == "sentence"]
    assert [f.text for f in sentences] == ["Niccolò Zucchi è stato un gesuita.",
                                        "Fu allievo di Giuseppe Biancani a Parma."]
    assert sentences[1].hyperlinks == ["Giuseppe Biancani"]
    assert not any("note" in r.text for r in rows)


def test_analyze_list_in_infobox_keeps_names_apart() -> None:
    """23 Oct, Galileo: «Notable students» with a list in the infobox became «Benedetto CastelliMario Guiducci»."""
    html = ('<table class="infobox"><tr><th>Notable students</th><td><div class="plainlist"><ul>'
            '<li><a href="/wiki/Benedetto_Castelli">Benedetto Castelli</a></li>'
            '<li><a href="/wiki/Mario_Guiducci">Mario Guiducci</a><br/>(dal 1620)</li></ul></div></td></tr></table>'
            '<p>Uno <!-- commento --> due <b>tre</b>.</p>')
    rows = analyze(html, "Galileo Galilei", "en")
    assert rows[0].text == "Notable students: Benedetto Castelli · Mario Guiducci · (dal 1620)"
    assert rows[0].hyperlinks == ["Benedetto Castelli", "Mario Guiducci"]
    assert rows[1].text == "Uno due tre."


def test_article_with_categories_disambiguation_and_redirect() -> None:
    """Visible categories as a citable row, disambiguation from the page property, redirect remembered."""
    from chain_research.web.sources.wikipedia_graph import WikipediaGraph, _disambiguation

    def fetch(url, params):
        if params.get("page") == "Mario":
            return {"parse": {"title": "Mario", "text": "<p>Mario è un nome proprio.</p>",
                              "categories": [{"category": "Pagine_di_disambiguazione"}],
                              "properties": {"disambiguation": "", "wikibase_item": "Q15888821"}}}, None
        return {"parse": {"title": "Mario Bettini", "text": "<p>Mario Bettini è stato un gesuita.</p>",
                          "redirects": [{"from": "Mario Bettinus", "to": "Mario Bettini"}],
                          "categories": [{"category": "Gesuiti_italiani"}, {"category": "Nati_a_Bologna"},
                                         {"category": "BioBot", "hidden": True}],
                          "properties": {"wikibase_item": "Q1898676"}}}, None

    wiki = WikipediaGraph(fetch=fetch, interval_s=0.0)
    v = wiki.article("Mario Bettinus", "it")
    assert v.title == "Mario Bettini" and v.redirect_from == "Mario Bettinus" and v.qid == "Q1898676"
    assert v.categories == ["Gesuiti italiani", "Nati a Bologna"] and not v.disambiguation
    assert v.rows[-1].kind == "categories" and v.rows[-1].text == "Categorie: Gesuiti italiani; Nati a Bologna"
    m = wiki.article("Mario", "it")
    assert m.disambiguation and _disambiguation(m)


def test_proof_of_another_candidate_does_not_count(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """A "yes" with the number of another candidate's row becomes "don't know" (program check)."""
    from chain_research.web.chain import Link, Proof
    c = chain
    a, b = Link("Mario Rossi"), Link("Luigi Bianchi")
    a.tie_proof = Proof("Mario Rossi è un gesuita.", "u1")
    b.tie_proof = Proof("Luigi Bianchi è un medico.", "u2")

    def decide(point, data, default):
        return {"candidates": [{"name": "Luigi Bianchi", "checks": [
            {"check": "gesuita", "outcome": "yes", "proof": 1, "quote": "gesuita"}]}]}, "claude"  # Rossi's row
    monkeypatch.setattr(c.r.thinker, "decide", decide)
    c._outcomes([a, b], [{"description": "gesuita", "readings": []}])
    assert b.outcomes[0]["outcome"] == "unknown" and b.outcomes[0]["proof"] is None and b.points == 0


def test_second_plan_when_first_fails(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    plans = []

    def decide(point, data, default):
        if point == "chain":
            plans.append(data.get("previous_chain"))
            if len(plans) == 1:  # first plan: wrong starting point, no candidates
                return {"starting_point": {"clue": "x", "entity": "Nessuno"},
                        "steps": [{"relation": "allievi", "kind": "person", "checks": []}],
                        "answer": {"what": "data di nascita", "checks": []}, "reason": "m"}, "claude"
        if point == "candidates" and data.get("entity") == "Nessuno":
            return {"candidates": []}, "claude"
        return _decide(point, data, default)
    monkeypatch.setattr(chain.r.thinker, "decide", decide)
    chain.o["parallel_plans"] = 1
    chain.walk()
    assert len(plans) == 2 and plans[1]["starting_point"] == "Nessuno"
    assert plans[1]["where_it_stopped"].startswith("chain broken at step 1")
    assert chain.value and chain.value["value"] == "6 dicembre 1586"


def test_value_at_head_of_answer(chain) -> None:
    from chain_research.web.chain import Link, Proof
    c = chain
    c.value = {"value": "Mitchell Baker", "proof": Proof("x", "u"), "link": Link("MPL"), "reason": ""}
    text = "**Answer:** The person indicated, with doubts about version 1.1, is perhaps her [3].\n\n**Limitations:** x"
    assert c._value_in_head(text).startswith("**Answer:** Mitchell Baker.\n\n**Explanation:** The person")
    already = "**Answer:** **Mitchell Baker** [3].\n"
    assert c._value_in_head(already) == already


def test_invented_quote_does_not_count(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    from chain_research.web.chain import Link, Proof
    c = chain
    a = Link("Daniel Murphy")
    a.tie_proof = Proof("Daniel Murphy worked on TENEX at BBN.", "u1")

    def decide(point, data, default):
        return {"candidates": [{"name": "Daniel Murphy", "checks": [
            {"check": "premio nel 2009", "outcome": "yes", "proof": 1, "quote": "received the award in 2009"},
            {"check": "lavorò a TENEX", "outcome": "yes", "proof": 1, "quote": "worked on TENEX"}]}]}, "claude"
    monkeypatch.setattr(c.r.thinker, "decide", decide)
    c._outcomes([a], [{"description": "premio nel 2009", "readings": []}, {"description": "TENEX", "readings": []}])
    assert [e["outcome"] for e in a.outcomes] == ["unknown", "yes"] and a.points == 1


def test_two_readings_yes_only_if_both_agree() -> None:
    from chain_research.web.chain import Chain, Proof
    p = Proof("row", "u")
    leading = [{"check": "a", "outcome": "yes", "proof": p}, {"check": "b", "outcome": "yes", "proof": p},
             {"check": "c", "outcome": "unknown", "proof": None}]
    seconds = [{"check": "a", "outcome": "yes", "proof": p}, {"check": "b", "outcome": "unknown", "proof": None},
               {"check": "c", "outcome": "no", "proof": p}]
    assert [e["outcome"] for e in Chain._merge_outcomes(leading, seconds)] == ["yes", "unknown", "no"]


def test_allowed_value_kind_and_interval():
    from chain_research.web.chain import allowed_value
    date = {"value_kind": "full date", "min": 1945, "max": 1945}
    assert allowed_value("29 novembre 1943", date) and not allowed_value("7 marzo 1945", date)
    assert allowed_value("1945", date) == "not a full date"
    inhabitants = {"value_kind": "number", "min": 300, "max": 350}
    assert allowed_value("3.274 abitanti", inhabitants) and not allowed_value("324", inhabitants)
    assert not allowed_value("20,65 km²", {"value_kind": "number"})
    assert not allowed_value("118 m s.l.m.", {"value_kind": "number", "min": 100, "max": 130})
    assert allowed_value("98 m s.l.m.", {"value_kind": "number", "min": 100, "max": 130})
    assert not allowed_value("28 ottobre", {"value_kind": "day and month"})
    assert allowed_value("Simone", {"value_kind": "day and month"})


def test_weighted_quality_and_partly(chain):
    from chain_research.web.chain import Link, quality
    outcomes = [{"check": "insegnante di Mascagni", "outcome": "yes", "weight": 3},
             {"check": "librettista", "outcome": "partly", "weight": 2},
             {"check": "is of this kind: person", "outcome": "unknown", "weight": 1}]
    assert quality(outcomes) == (3 + 1) / 6
    a = Link("Praga", outcomes=[{"check": "insegnante di Mascagni", "outcome": "no", "weight": 3, "proof": object()},
                               {"check": "librettista", "outcome": "yes", "weight": 2, "proof": object()}])
    assert chain._refuted(a)  # a heavy "no" is not offset by a light "yes"
    merged_items = chain._merge_outcomes([{"check": "x", "outcome": "yes", "proof": 1}],
                                 [{"check": "x", "outcome": "partly", "proof": 2}])
    assert merged_items[0]["outcome"] == "partly"


def test_check_from_code(chain):
    checks = [{"description": "gesuita", "weight": 3}, {"description": "bolognese", "weight": 1}]
    assert chain._resolve_check("C2", checks)["description"] == "bolognese"
    assert chain._resolve_check("gesuita (readings: )", checks)["weight"] == 3
    assert chain._resolve_check("C9", checks) is None


def test_frontier_returns_to_reserve_candidate(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """The best candidate of step 1 leads nowhere: the frontier tries the reserve one by itself, without a new plan
    (DeepSearch)."""
    from chain_research.web.chain import Link, Proof
    c = chain
    c.plan = {"starting_point": {"entity": "P", "clue": ""}, "answer": {"what": "x"},
               "steps": [{"relation": "r1", "checks": []}, {"relation": "r2", "checks": []}]}
    monkeypatch.setattr(c, "prepare", lambda: True)
    children = {"P": [("A", 0.9), ("B", 0.6)], "A": [], "B": [("C", 0.8)]}

    def attempt(t, number, step, beam, seen, all_items=False):
        origin = beam[0]
        if t not in (1, 3):
            return []
        new_items = []
        for name, q in children[origin.name]:
            if name not in seen:
                seen.add(name)
                new_items.append(Link(name, quality=q, parent=origin, tie_proof=Proof("row", "u")))
        return new_items

    def value(a, attempts):
        if a.name == "C":
            c.value = {"value": "42", "proof": Proof("42", "u"), "link": a, "reason": ""}
            return True
        return False
    monkeypatch.setattr(c, "_attempt", attempt)
    monkeypatch.setattr(c, "_entity_value", value)
    c.o["chain_plans"] = 1
    c.o["parallel_plans"] = 1
    c.walk()
    assert c.value["value"] == "42" and [x.name for x in c._path(c.value["link"])] == ["B", "C"]
    assert c.links[0][0].name == "A"  # A stays the best of step 1, but without children


def test_anchor_leads_directly_to_step(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """An entity named by the question (anchor) leads to the result of step 2 with one of its relations: the frontier
    explores it before the distant starting point."""
    from chain_research.web.chain import Link, Proof
    c = chain
    c.plan = {"starting_point": {"entity": "P", "clue": ""}, "answer": {"what": "x"},
               "anchors": [{"entity": "Mascagni", "relation": "insegnanti di", "step": 2}],
               "steps": [{"relation": "r1", "checks": []}, {"relation": "librettisti di", "checks": []}]}
    monkeypatch.setattr(c, "prepare", lambda: True)
    expanded = []

    def attempt(t, number, step, beam, seen, all_items=False):
        expanded.append((beam[0].name, number, step["relation"]))
        if beam[0].name == "Mascagni" and t == 3:
            return [Link("Ponchielli", quality=0.8, parent=beam[0], tie_proof=Proof("Ponchielli insegnò", "u"))]
        return []

    def value(a, attempts):
        c.value = {"value": "31 agosto 1834", "proof": Proof("31 agosto 1834", "u"), "link": a, "reason": ""}
        return a.name == "Ponchielli"
    monkeypatch.setattr(c, "_attempt", attempt)
    monkeypatch.setattr(c, "_entity_value", value)
    c.o["chain_plans"] = 1
    c.o["parallel_plans"] = 1
    c.walk()
    assert expanded[0] == ("Mascagni", 2, "insegnanti di")  # the anchor close to the answer goes first
    assert c.value["link"].name == "Ponchielli"
    assert "STARTING POINT: Mascagni" in c.material() and "«insegnanti di» Mascagni → Ponchielli" in c.material()


def test_alternative_plan_in_same_frontier(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first plan leads nowhere; the alternative one, asked for right away, reaches the value in the same
    frontier, without a second round."""
    asked = []

    def decide(point, data, default):
        if point == "chain":
            asked.append(data)
            if len(asked) == 1:
                return {"starting_point": {"clue": "x", "entity": "Nessuno"},
                        "steps": [{"relation": "allievi", "kind": "person", "checks": []}],
                        "answer": {"what": "data di nascita", "checks": []}, "reason": "m"}, "claude"
        if point == "candidates" and data.get("entity") == "Nessuno":
            return {"candidates": []}, "claude"
        return _decide(point, data, default)
    monkeypatch.setattr(chain.r.thinker, "decide", decide)
    chain.walk()
    assert len(asked) == 2 and asked[1]["alternative_to"]["starting_point"] == "Nessuno"
    assert chain.value["value"] == "6 dicembre 1586" and chain.plan["starting_point"]["entity"] == "Galileo Galilei"
    assert any("[plan 2]" in x for x in chain.actions)


def test_verification_sees_decisive_row_beyond_infobox(chain) -> None:
    from chain_research.web.chain import Link
    from chain_research.web.sources.wikipedia_graph import Row, Article
    rows = [Row(f"Campo {i}: valore {i}", [], "infobox", "") for i in range(25)]
    rows.append(Row("Linards (Francia)", ["Linards"], "list", "Gemellaggi"))
    a = Link("Gravellona Lomellina")
    a.articles = {"it": Article("Gravellona Lomellina", "it", "https://it.wikipedia.org/wiki/G", rows, {})}
    a.searched_articles = True
    chain.wiki = object()  # with Wikipedia on (the articles are already read)
    proofs = chain._proofs_wiki_candidate(a, [{"description": "gemellato con un paese dell'Alta Vienna",
                                              "readings": []}])
    assert any("Linards" in p.text for p in proofs)
    a.outcomes = [{"check": "gemellato", "outcome": "unknown", "weight": 3, "proof": None},
               {"check": "provincia di Pavia", "outcome": "yes", "weight": 2, "proof": object()}]
    assert chain._decisive_unknown(a)


def test_other_language_articles_added_after_verification(chain) -> None:
    from chain_research.web.chain import Link
    from chain_research.web.sources.wikipedia_graph import Row, Article

    class Wiki:
        def article(self, title, language):
            return Article(title, language, f"https://{language}.wikipedia.org/wiki/W3C",
                        [Row("Founded 1 October 1994" if language == "en" else "Fondazione ottobre 1994")],
                        {"en": "World Wide Web Consortium", "it": "World Wide Web Consortium"})
    chain.wiki = Wiki()
    a = Link("World Wide Web Consortium", title=("it", "World Wide Web Consortium"))
    assert [v.language for v in chain._articles(a, other_ones=0)] == ["it"]  # verification: one language
    assert sorted(v.language for v in chain._articles(a)) == ["en", "it"]  # value: the others are added


def test_adjacent_article_for_decisive_check(chain) -> None:
    from chain_research.web.chain import Link
    from chain_research.web.sources.wikipedia_graph import Row, Article

    class Wiki:
        def starts(self, titles, language):
            return {"Linards": "Linards è un comune francese situato nel dipartimento dell'Alta Vienna."}
    chain.wiki = Wiki()
    a = Link("Gravellona Lomellina")
    a.articles = {"it": Article("Gravellona Lomellina", "it", "https://it.wikipedia.org/wiki/G",
                         [Row("Linards (Francia)", ["Linards"], "list", "Gemellaggi")], {})}
    a.searched_articles, a.articles_other = True, 5
    proofs = chain._proofs_wiki_candidate(a, [{"description": "gemellato con un paese dell'Alta Vienna",
                                              "readings": [], "weight": 3}])
    assert "dipartimento dell'Alta Vienna" in proofs[0].text


def test_research_language(chain) -> None:
    """English by default; the country's language when the plan chooses it (22 Oct, Niccolò)."""
    chain.plan = {"languages": ["el"]}
    assert chain._languages() == ["en", "el", "it"]
    chain.plan = {"research_language": "it", "languages": ["fr"]}
    assert chain._languages() == ["it", "en", "fr"]


def test_reading_off_saves_segment_without_model(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """With chain_reading off the block read is saved without calling the model (22 Oct)."""
    r = chain.r
    r.o["chain_reading"] = False
    monkeypatch.setattr(r.thinker, "ask", lambda *a, **k: pytest.fail("the model must not be called"))
    last = chain._last_segment()
    assert r._read_block("Freedo", "https://example.org/freedo", 1, 1, "Freedo was drawn by Rubén.") == (2, "")
    assert any("Freedo was drawn" in s["text"] for s in chain._after_segments(last))


def test_namesake_starting_point_not_accepted_by_name(chain) -> None:
    from chain_research.web.chain import Link
    from chain_research.web.sources.wikipedia_graph import Row, Article
    a = Link("Freedo", context="Freedo, la mascotte derivata dal pinguino Tux")
    producer = Article("Freedo", "en", "u", [Row("Freedo is a Swedish record producer and songwriter.")], {})
    mascot = Article("Freedo", "it", "u", [Row("Freedo è la mascotte di Linux-libre, un pinguino derivato da Tux.")], {})
    assert not chain._relevant(producer, a) and chain._relevant(mascot, a)


def test_tie_won_by_co_occurrence(chain) -> None:
    """Four towns tied without a proven link: the one that appears in the segments saved on the previous entity goes
    first (graph logic, 22 Oct)."""
    from chain_research.web.chain import Link
    c = chain
    c.r.concepts.record("https://example.org/skyos", "SkyOS", 1,
                          "Robert Szeleney, born in Wels, started SkyOS in 1996.", [], [], [])
    szeleney = Link("Robert Szeleney")
    city = [Link(n, quality=0.6, parent=szeleney, weak=True) for n in ("Bad Ischl", "Gmunden", "Traun", "Wels")]
    assert sorted(city, key=c._order)[0].name == "Wels"
    c.plan = {"steps": [{}, {}]}
    assert c._priority(city[3], 2, "value") > c._priority(city[0], 2, "value")


def test_wiki_rows_merged_list_clean_infobox_few_rows() -> None:
    """Phase 4 (23 Oct): list of the same section in one row, useless infobox rows out, at most 20."""
    from chain_research.web.chain import Chain
    rows = [Row("Immagine: Debian-logo.svg", [], "infobox"), Row("Sviluppatore: Progetto Debian",
             ["Progetto Debian"], "infobox"), Row("Debian è un sistema operativo libero composto da software "
             "libero, sviluppato dalla comunità del Progetto Debian.", [], "sentence")]
    rows += [Row(f"Distro{i}", [f"Distro{i}"], "list", "Distribuzioni derivate") for i in range(40)]
    rows += [Row(f"Una frase qualunque numero {i} sulla storia.", [], "sentence", "Storia") for i in range(60)]
    article = Article("Debian", "it", "u", rows)
    c = Chain.__new__(Chain)
    proofs = c._wiki_rows([article], ["opera derivata", "derivate fork"])
    texts = [p.text for p in proofs]
    assert len(proofs) <= 20
    assert not any("Immagine" in t for t in texts)
    assert any("Distro0 · Distro1" in t and "Distro39" in " ".join(texts) for t in texts)
    assert any("sistema operativo libero" in t for t in texts)  # the definition always gets in


def test_candidate_not_in_row_discarded(chain) -> None:
    from chain_research.web.chain import Link, Proof
    c = chain
    proofs = [Proof("Tra suoi discepoli figurano Giovanni Riccioli e Niccolò Zucchi.", "u",
                   ["Giovanni Battista Riccioli", "Niccolò Zucchi"], "it")]
    candidates: dict = {}
    c._add([{"name": "Niccolò Zucchi", "proof": 1}, {"name": "Mario Bettini", "proof": 1},
                 {"name": "Giovanni Battista Riccioli", "proof": 1}, {"name": "Niccolò Cabeo", "proof": 7}],
                proofs, Link("Giuseppe Biancani"), candidates)
    assert sorted(a.name for a in candidates.values()) == ["Giovanni Battista Riccioli", "Niccolò Zucchi"]


def test_check_repeating_tie_not_counted_twice(chain) -> None:
    """Phase 5 (23 Oct, paper §9.1): «indicato come allievo di Galileo» is the tie: it becomes one of its readings."""
    from chain_research.web.chain import Link
    step = {"relation": "allievi di", "kind": "person",
             "checks": [{"description": "indicato come allievo di Galileo", "weight": 2},
                           {"description": "gesuita nato a Bologna", "weight": 3}]}
    checks = chain._step_checks(step, [Link("Galileo Galilei")])
    descriptions = [c["description"] for c in checks]
    assert "indicato come allievo di Galileo" not in descriptions and "gesuita nato a Bologna" in descriptions
    assert "indicato come allievo di Galileo" in checks[-1]["readings"]


def test_strong_only_with_proven_identifying_trait() -> None:
    from chain_research.web.chain import Link, Chain
    a = Link("Mario Guiducci")
    a.outcomes = [{"check": "is related «allievi di» with Galileo Galilei", "outcome": "yes", "proof": 3, "weight": 2},
               {"check": "gesuita nato a Bologna", "outcome": "unknown", "proof": 0, "weight": 2},
               {"check": "is of this kind: person", "outcome": "yes", "proof": 3, "weight": 1}]
    assert not Chain._identified(a)
    a.outcomes[1] = {**a.outcomes[1], "outcome": "yes", "proof": 5}
    assert Chain._identified(a)


def test_variants_of_same_name_one_candidate(chain) -> None:
    """23 Oct, Arduino: «Keyboard.io» and «Keyboardio» were two candidates (and two expansions with web pages)."""
    from chain_research.web.chain import Link, Proof
    proofs = [Proof("Keyboardio (Keyboard.io) makes the Keyboard.io Model01.", "u", [], "en")]
    candidates: dict = {}
    chain._add([{"name": "Keyboard.io", "proof": 1}, {"name": "Keyboardio", "proof": 1},
                      {"name": "Keyboard.io Model01", "proof": 1}], proofs, Link("Arduino"), candidates)
    assert [a.name for a in candidates.values()] == ["Keyboard.io", "Keyboard.io Model01"]


def test_same_article_same_entity_and_verification_summary() -> None:
    """23 Oct, Arduino: «Adafruit» and «Adafruit Industries» lead to the same article: a single key. The history
    keeps for each check the outcome given by the model, the one kept and the cited row."""
    from chain_research.web.chain import Link, Chain, Proof
    a, b = Link("Adafruit"), Link("Adafruit Industries")
    article = Article("Adafruit Industries", "en", "u")
    a.articles, b.articles = {"en": article}, {"en": article}
    assert Chain._canonical_key(a) == Chain._canonical_key(b) == "article:adafruitindustries"
    assert Chain._canonical_key(Link("Keyboard.io")) == Chain._canonical_key(Link("Keyboardio"))
    row = Proof("Adafruit sells programmable keyboard kits.", "u")
    a.outcomes = [{"check": "produce tastiere programmabili", "weight": 2, "outcome": "unknown", "proof": None,
                "said": "yes", "cited": row}]
    short = Chain._verification_in_short(a)
    assert short["article"] == "Adafruit Industries (en)"
    assert short["outcomes"][0] == {"check": "produce tastiere programmabili", "weight": 2, "said": "yes",
                                 "kept": "unknown", "row": "Adafruit sells programmable keyboard kits."}


def test_web_cheaper_for_nodes_without_wikipedia_candidates(chain) -> None:
    """24 Oct: a node that gave no candidates on Wikipedia, with quality ≥ 0.5, pays 0.1 for the web instead of 0.35
    (Keyboardio, Simone da Orsenigo); below 0.5 everything stays as before."""
    from chain_research.web.chain import Link, Proof
    c = chain
    root = Link("Arduino")
    node = Link("Keyboardio", parent=root, quality=0.6)
    node.outcomes = [{"check": "is related «ispirata da» with Arduino", "outcome": "yes", "proof": Proof("r", "u"),
                   "weight": 2}]
    wiki = c._priority(node, 3, "wiki")
    assert round(wiki - c._priority(node, 3, "web"), 2) == 0.35
    node.empty_wiki = True
    assert round(wiki - c._priority(node, 3, "web"), 2) == 0.10
    node.quality = 0.4
    assert round(c._priority(node, 3, "wiki") - c._priority(node, 3, "web"), 2) == 0.35


def test_web_expansion_same_with_differently_worded_relation(chain) -> None:
    """24 Oct: two plans expand Arduino on the web with «aziende ispirate da» and «progetti ispirati ad»: it is the
    same expansion (same pages). On Wikipedia the relation still counts."""
    from chain_research.web.chain import Link
    a = Link("Arduino")
    c = chain
    assert c._action_signature("web", a, 0, "aziende ispirate da") == c._action_signature("web", a, 0, "progetti ispirati ad")
    assert c._action_signature("wiki", a, 0, "aziende ispirate da") != c._action_signature("wiki", a, 0, "fondatori di")
    assert c._action_signature("web", a, 0, "x") != c._action_signature("web", a, 1, "x")


def test_answer_is_entity_of_last_step(chain) -> None:
    """24 Oct, Sistine Chapel: «in which municipality is the effigy» → the value is the municipality found by the last
    step, with the row that ties it, without asking Haiku for a «value» in the municipality's rows."""
    from chain_research.web.chain import Link, Proof
    chain.plan = {"steps": [{"relation": "comune in cui si trova"}],
                    "answer": {"what": "nome del comune", "value_kind": "name", "is_entity": True}}
    tie = Proof("Il simulacro dell'Assunta si trova a Novara di Sicilia.", "https://x.it/a")
    a = Link("Novara di Sicilia", tie_proof=tie, parent=Link("Madonna Assunta"),
               outcomes=[{"check": "is related «comune in cui si trova» with Madonna Assunta", "outcome": "partly",
                       "proof": tie, "weight": 2}], quality=0.6)
    without = Link("Capri Leone", tie_proof=tie, parent=Link("Assunzione di Maria"),
                   outcomes=[{"check": "is related «comune in cui si trova» with X", "outcome": "unknown",
                           "proof": None, "weight": 2}])
    assert not chain._entity_value(without, (1,)) and not chain._entity_value(without, (2, 3))
    assert chain._entity_value(a, (1,))
    assert chain.value["value"] == "Novara di Sicilia" and chain.value["proof"] is tie
    chain.value = None
    chain.plan["answer"]["is_entity"] = False
    assert not chain._value_is_entity(Link("No proofs"))


def test_final_tiebreak_rewards_chosen(chain, monkeypatch) -> None:
    """24 Oct, Mont Blanc: two municipalities tied at the last step; Haiku compares them with the question («borgo»)
    and the chosen one goes first. One far from the best does not enter; «0» leaves everything as it was."""
    from chain_research.web.chain import Link, Proof
    seen_items = []

    def decide(point, data, default):
        seen_items.append(data)
        return ({"choice": 2, "reason": "borgo"} if "Laigueglia" in data["candidates"] else {"choice": 0}), "claude"

    monkeypatch.setattr(chain.r.thinker, "decide", decide)
    alassio = Link("Alassio", start=Proof("Alassio è un comune di 9 874 abitanti.", "u1"))
    laigueglia = Link("Laigueglia", start=Proof("Laigueglia è un piccolo borgo di mare.", "u2"))
    far = Link("Andora")
    points = {"Alassio": 0.71, "Laigueglia": 0.70, "Andora": 0.4}
    assert chain._final_tiebreak([alassio, laigueglia, far], lambda a: points[a.name]) == 1
    assert laigueglia.preferred > 0 and alassio.preferred == 0
    assert "Andora" not in seen_items[0]["candidates"] and "piccolo borgo di mare" in seen_items[0]["candidates"]
    assert chain._final_tiebreak([Link("A"), Link("B")], lambda a: 0.5) == -1
    assert chain._final_tiebreak([Link("Solo")], lambda a: 0.5) == -1 and len(seen_items) == 2


def test_profile_search_from_open_check() -> None:
    """24 Oct, Arduino: the second search for the web profile tries the heaviest check still open."""
    from chain_research.web.chain import Link
    a = Link("Seeed Studio", outcomes=[
        {"check": "azienda americana", "weight": 1, "outcome": "unknown", "proof": None},
        {"check": "produce tastiere programmabili", "weight": 3, "outcome": "unknown", "proof": None},
        {"check": "is related «ispirata da» with Arduino", "weight": 3, "outcome": "unknown", "proof": None}])
    assert Chain._check_research(a) == "Seeed Studio produce tastiere programmabili"
    assert Chain._check_research(Link("Mario Rossi")) == "Mario Rossi biography"


def test_title_from_row_without_confusing_parentheses() -> None:
    """24 Oct, Mont Blanc: «La Thuile (Italia)» took «La Salle (Italia)» (key «italia»)."""
    from chain_research.web.chain import Proof, _name_key
    row = Proof("Comuni confinanti: La Salle, La Thuile", "u", ["La Salle (Italia)", "La Thuile (Italia)"], "it")
    assert Chain._title_from_proof("La Thuile (Italia)", row) == ("it", "La Thuile (Italia)")
    assert Chain._title_from_proof("Fermi", Proof("x", "u", ["Enrico Fermi"], "it")) == ("it", "Enrico Fermi")
    assert _name_key("La Thuile (Italia)") == "thuile" and _name_key("Tux (mascot)") == "tux"
    row = Proof("Paul McCartney and his wife Linda", "u", ["Linda McCartney", "Wings"], "en")
    assert Chain._title_from_proof("Paul McCartney", row) is None
    assert Chain._title_from_proof("Microsoft", Proof("x", "u", ["Microsoft ergonomic keyboards"], "en")) is None
    assert Chain._title_from_proof("Adafruit", Proof("x", "u", ["Adafruit Industries"], "en")) == ("en", "Adafruit Industries")


def test_ties_written_by_chain_are_not_proofs(chain) -> None:
    """24 Oct, Mont Blanc: a weak link recorded by the first plan must not become a proof for the second."""
    from chain_research.web.chain import Link, Proof
    a = Link("Vado Ligure", parent=Link("Saint-Gervais-les-Bains"),
               tie_proof=Proof("Vado Ligure è un comune della provincia di Savona.", "https://it.wikipedia.org/wiki/Vado"))
    chain._record(a, "gemellata con")
    assert chain.r.concepts.ties()  # the tie is in the graph (for the view and the writer)
    assert not any("→ gemellata con →" in p.text for p in chain._entity_proofs("Vado Ligure"))
    assert Chain._title_from_proof("Napoli", Proof("x", "u", ["Regno di Napoli"], "it")) is None


def test_step_clues_for_searches() -> None:
    """25 Oct, Arduino: the web searches get the checks of the step (heaviest first)."""
    step = {"checks": [{"description": "ispirata da Arduino", "readings": [], "weight": 1},
                           {"description": "produce tastiere programmabili", "readings": ["tastiere"], "weight": 3}]}
    assert Chain._clues(step) == ["produce tastiere programmabili / tastiere", "ispirata da Arduino"]


def test_whole_name_only_for_people() -> None:
    """25 Oct, Arduino: «Kaia» becomes «Kaia Dekker» (step about people); a one-word company stays as it is."""
    from chain_research.web.chain import Proof
    proofs = [Proof("Keyboardio was founded in 2012 by Jesse Vincent and Kaia Dekker.", "u")]
    assert Chain._whole_name("Kaia", proofs) == "Kaia Dekker" and Chain._whole_name("jesse", proofs) == "Jesse Vincent"
    assert Chain._whole_name("Mario Rossi", proofs) == "Mario Rossi"
    assert Chain._of_people({"kind": "person", "relation": "fondatori di"})
    assert not Chain._of_people({"kind": "organization", "relation": "aziende ispirate da"})


def test_plan_skips_nameless_object() -> None:
    """25 Oct, Sistine Chapel: «feast → effigy → municipality» becomes «feast → municipality (via the effigy)»."""
    plan = {"starting_point": {"entity": "Cappella Sistina"}, "steps": [
        {"relation": "festa a cui è dedicata", "kind": "festa", "checks": []},
        {"relation": "simulacro che raffigura", "kind": "simulacro", "words": ["simulacro"],
         "checks": [{"description": "raffigura l'Assunta", "readings": [], "weight": 2}]},
        {"relation": "comune in cui si trova", "kind": "comune", "words": ["comune"],
         "checks": [{"description": "provincia di Messina", "readings": [], "weight": 2}]}]}
    jump = Chain._jump_plan(plan)
    assert [p["relation"] for p in jump["steps"]] == ["festa a cui è dedicata",
                                                        "comune in cui si trova (via: simulacro che raffigura)"]
    assert len(jump["steps"][1]["checks"]) == 2 and "simulacro" in jump["steps"][1]["words"]
    assert Chain._jump_plan({"steps": [{"relation": "allievi di", "kind": "person"},
                                          {"relation": "luogo di nascita", "kind": "città"}]}) is None


def test_wide_tiebreak_between_linked_without_no(chain, monkeypatch) -> None:
    """25 Oct, Mont Blanc: Alassio 0.9 and Laigueglia 0.7, both tied to La Thuile and without «no»: tiebreak."""
    from chain_research.web.chain import Link, Proof
    seen_items = []
    monkeypatch.setattr(chain.r.thinker, "decide", lambda p, d, f: (seen_items.append(d) or {"choice": 2}, "claude"))
    row = Proof("La Thuile è gemellata con Alassio e Laigueglia.", "u")
    linked = lambda name, q: Link(name, quality=q, outcomes=[  # noqa: E731
        {"check": "is related «gemellato con» with La Thuile", "outcome": "yes", "proof": row, "weight": 2}])
    alassio, laigueglia = linked("Alassio", 0.9), linked("Laigueglia", 0.7)
    far = linked("Andora", 0.4)
    points = {"Alassio": 1.0, "Laigueglia": 0.8, "Andora": 0.5}
    assert chain._final_tiebreak([alassio, laigueglia, far], lambda a: points[a.name]) == 1
    assert "Andora" not in seen_items[0]["candidates"] and laigueglia.preferred > 0


def test_value_from_result_snippet(chain, monkeypatch) -> None:
    """29 Oct, Congress of Vienna: the date was only in the snippet of a page never read (Geni)."""
    from chain_research.web.chain import Link, Proof
    chain.plan = {"steps": [{"relation": "allievi"}],
                    "answer": {"what": "data di nascita", "checks": [], "value_kind": "full date"}}

    def search_and_read(searches, pages, purpose=""):
        chain._snippets = [Proof("[search result: Niccolò Zucchi] Niccolò Zucchi, gesuita, nato il "
                                  "6 dicembre 1586 a Parma.", "https://esempio.org/zucchi")]
        return []

    monkeypatch.setattr(chain, "_search_and_read", search_and_read)
    assert chain._entity_value(Link("Niccolò Zucchi", parent=Link("Giuseppe Biancani")), (2, 3))
    assert chain.value["value"] == "6 dicembre 1586" and "esempio.org" in chain.value["proof"].url



def test_same_step_with_different_checks_redoes_on_wikipedia() -> None:
    """29 Oct, Galileo: plan 2 has the same relation as plan 1 but different checks: on Wikipedia it is redone
    (Biancani discarded with the checks of plan 1, kept with those of plan 2); on the web not (it costs pages)."""
    already = {("wiki", "galileo", 0, "allievi"): ("A", []), ("web", "galileo", 0): ("A", [])}
    marks = {("wiki", "galileo", 0, "allievi"): {"A"}}
    assert Chain._already_done(("wiki", "galileo", 0, "allievi"), "wiki", "B", already, marks) == "redo"
    assert Chain._already_done(("wiki", "galileo", 0, "allievi"), "wiki", "A", already, marks) == "reuse"
    assert Chain._already_done(("web", "galileo", 0), "web", "B", already, marks) == "skip"
    assert Chain._already_done(("wiki", "other", 0, "allievi"), "wiki", "A", already, marks) == "new"


# --- review of 2026-11-11: the checks are tied to the answer (docs/validation.md, T3) -----------------

def test_value_changed_by_model_rejected(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model cites the right row but gives another year: the program rejects the value, there is no answer."""
    def liar(point: str, data: dict, default):
        if point == "value" and "6 dicembre 1586" in data["proofs"]:
            return {"value": "6 dicembre 1587", "proof": _row(data["proofs"], "6 dicembre 1586"), "reason": ""}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", liar)
    chain.walk()
    assert chain.value is None and not chain.checks()["confirmed"]
    rejections = [s for s in chain.history if s["relation"] == "value not in the row"]
    assert rejections and rejections[0]["discarded"][0][0] == "6 dicembre 1587"
    assert chain.r.write().startswith("**Answer:** Not found")


def test_right_value_carries_check_outcomes(chain) -> None:
    chain.walk()
    outcome = chain.summary()
    assert outcome["checks"]["value_source"] == "A" and outcome["checks"]["disagreements"] == []
    assert "6 dicembre 1586" in outcome["value"]["row"]
    assert any("6 dicembre 1586" in p["row"] and "Zucchi" in p["url"] for p in outcome["proofs"]) and len(outcome["proofs"]) >= 3
    assert outcome["checks"]["confirmed"] == (outcome["checks"]["weak_links"] == 0)


def test_chain_does_not_return_to_starting_point(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """Among Biancani's «disciples» the model proposes Galileo, where the chain started: discarded, with a trace."""
    def circular(point: str, data: dict, default):
        outcome, who = _decide(point, data, default)
        if point == "candidates" and data.get("entity") == "Giuseppe Biancani":
            outcome["candidates"].append({"name": "Galileo Galilei", "proof": _row(data["proofs"], "lettori di Galileo")})
        return outcome, who

    monkeypatch.setitem(PAGES, "Giuseppe Biancani allievi", [(
        "https://it.wikipedia.org/wiki/Giuseppe_Biancani", "Giuseppe Biancani (Bologna, 8 marzo 1566) è stato un "
        "gesuita. Tra suoi discepoli figurano Giovanni Riccioli, Mario Bettini, Niccolò Zucchi e Niccolò Cabeo, tutti "
        "lettori di Galileo Galilei.")])
    monkeypatch.setattr(chain.r.thinker, "decide", circular)
    chain.walk()
    returned = [s for s in chain.history if s["relation"] == "already on the route"]
    assert returned and returned[0]["candidates"] == ["Galileo Galilei"]
    assert all(a.name != "Galileo Galilei" for beam in chain.links for a in beam)
    assert chain.value["value"] == "6 dicembre 1586"


@pytest.mark.parametrize("text,strong", [
    ("[search result: Aubrey Research] Stonebraker was born in Milton, New Hampshire", False),
    ("Michael Stonebraker was born in Milton, New Hampshire in 1943", True)])
def test_snippet_does_not_make_strong_link(chain, monkeypatch: pytest.MonkeyPatch, text: str, strong: bool) -> None:
    from chain_research.web.chain import Link, Proof, quality
    proof = Proof(text, "https://esempio.org/stonebraker")
    checks = [{"description": "is related «luogo di nascita di» with Michael Stonebraker", "readings": [], "weight": 2}]
    milton = Link("Milton", parent=Link("Michael Stonebraker"))

    def outcomes(candidates, _checks) -> None:
        for a in candidates:
            a.outcomes = [{"check": checks[0]["description"], "outcome": "yes", "proof": proof, "weight": 2}]
            a.quality, a.points = quality(a.outcomes), 1

    monkeypatch.setattr(chain, "_outcomes", outcomes)
    kept = chain._verification([milton], checks)
    assert kept == [milton] and milton.weak is (not strong)


def test_discarded_infobox_candidate_stays_in_log(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    from chain_research.web.chain import Link, Proof, quality
    stonebraker = Link("Michael Stonebraker")
    infobox = Proof("[Michael Stonebraker (en)] Born: Newburyport, Massachusetts, U.S.",
                     "https://en.wikipedia.org/wiki/Michael_Stonebraker")
    tie = "is related «luogo di nascita di» with Michael Stonebraker"
    checks = [{"description": tie, "readings": [], "weight": 1},
                 {"description": "comune del New Hampshire", "readings": [], "weight": 2}]
    newburyport = Link("Newburyport, Massachusetts", parent=stonebraker, tie_proof=infobox)

    def outcomes(candidates, _checks) -> None:
        for a in candidates:
            a.outcomes = [{"check": tie, "outcome": "yes", "proof": infobox, "weight": 1},
                       {"check": "comune del New Hampshire", "outcome": "no", "proof": infobox, "weight": 2}]
            a.quality, a.points = quality(a.outcomes), 0

    monkeypatch.setattr(chain, "_outcomes", outcomes)
    assert newburyport not in [a for a in chain._verification([newburyport], checks) if not a.weak]
    assert [(x["parent"], x["name"], x["against"]) for x in chain.conflicts] == [
        ("Michael Stonebraker", "Newburyport, Massachusetts", ["comune del New Hampshire"])]
    chain._verification([newburyport], checks)
    assert len(chain.conflicts) == 1  # only once


# --- model checks on the solution (second reading, other sources, review) -----------------------------------------

def _strong_route(chain) -> None:
    """In the fake Galileo chain one link stays weak (the fake pages do not prove all checks): here the exams are
    tested, so the route is taken as confirmed."""
    a = (chain.value or {}).get("link")
    while a is not None:
        a.weak = False
        a = a.parent


def _with_review(chain, monkeypatch, review: dict, second: str = "6 dicembre 1586", budget: float = 1.0):
    """The Galileo chain with a fake model that also does the review; `second` is the value the model gives when it
    rereads (from the second «value» call on)."""
    calls = {"value": 0, "review": []}

    def model(point: str, data: dict, default, **others):
        if point == "review":
            calls["review"].append((data, others.get("model")))
            return review, "claude"
        if point == "value" and "6 dicembre 1586" in data["proofs"]:
            calls["value"] += 1
            if calls["value"] > 1:
                return {"value": second, "proof": _row(data["proofs"], second) if second else 0, "reason": ""}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = budget
    chain.walk()
    _strong_route(chain)
    chain.final_review()
    return calls


_HOLDS = {"holds": "yes", "reason": "", "clues": []}


def test_exams_without_objections_single_source(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _with_review(chain, monkeypatch, _HOLDS)
    outcome = chain.checks()
    assert outcome["exams"]["second_reading"] == {"outcome": "agree"}
    assert outcome["exams"]["corroboration"]["outcome"] == "single source" and outcome["exams"]["review"]["outcome"] == "done"
    assert outcome["status"] == "supported" and outcome["confirmed"] and "single source" in " ".join(outcome["reasons"])
    data, model = calls["review"][0]
    assert model == "sonnet" and "Galileo Galilei → [allievi] Giuseppe Biancani → [allievi] Niccolò Zucchi" in data["route"]
    assert "6 dicembre 1586" in data["answer"] and "[1] " in data["rows"]
    text = chain._confidence_from_program("**Answer:** 6 dicembre 1586.\n\n**Confidence:** high, everything fits.")
    assert "**Confidence:** medium — supported answer: " in text and "everything fits" not in text
    assert "PROGRAM CHECKS ON THE ANSWER" in chain.material()
    chain.final_review()
    assert len(calls["review"]) == 2  # two reviews, only once


def test_different_second_reading_contests_answer(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PAGES, "Niccolò Zucchi", [(
        "https://it.wikipedia.org/wiki/Niccol%C3%B2_Zucchi", "Niccolò Zucchi (Parma, 6 dicembre 1586 – Roma, 1670) "
        "è stato un gesuita, rettore del Collegio Romano. Altre fonti danno per Zucchi la nascita al 7 dicembre 1586.")])
    _with_review(chain, monkeypatch, _HOLDS, second="7 dicembre 1586")
    outcome = chain.checks()
    assert outcome["exams"]["second_reading"]["outcome"] == "disagree" and outcome["exams"]["second_reading"]["value"] == "7 dicembre 1586"
    assert outcome["status"] == "contested" and not outcome["confirmed"]
    assert "other value: 7 dicembre 1586" in chain.material()
    assert "**Confidence:** low — contested answer" in chain._confidence_from_program("**Confidence:** high")


def test_second_reading_value_not_in_row_does_not_count(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    def liar(review):
        return review
    _with_review(chain, monkeypatch, liar(_HOLDS), second="")
    assert chain.checks()["exams"]["second_reading"] == {"outcome": "not found again"}
    assert chain.checks()["status"] == "supported"


@pytest.mark.parametrize("quote,status", [("rettore del Collegio Romano", "uncertain"), ("parole inventate", "supported")])
def test_review_objection(chain, monkeypatch: pytest.MonkeyPatch, quote: str, status: str) -> None:
    """A «no» from the review counts only if it quotes words that really are in one of the rows given."""
    def review(data: dict) -> dict:
        return {"holds": "no", "reason": "m", "clues": [{"clue": "laico", "outcome": "no", "quote": quote,
                                                          "row": _row(data["rows"], "Collegio Romano")}]}

    calls = {"n": 0}

    def model(point: str, data: dict, default, **_other):
        if point == "review":
            calls["n"] += 1
            return review(data), "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    _strong_route(chain)
    chain.final_review()
    outcome = chain.checks()
    assert calls["n"] == 2 and outcome["status"] == status and outcome["confirmed"] is (status == "supported")
    assert len(outcome["exams"]["review"]["objections"]) == (1 if status == "uncertain" else 0)


def test_no_exams_without_budget(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _with_review(chain, monkeypatch, _HOLDS, budget=0.01)
    assert calls["review"] == [] and chain.exams["reason"] == "Claude budget almost used up"
    assert chain.checks()["status"] == "supported" and chain._confidence_from_program("**Confidence:** high").startswith("**Confidence:** medium")


def test_no_exams_without_value(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain, "_search_and_read", lambda searches, pages, purpose="": [])
    chain.walk()
    chain.final_review()
    assert chain.exams is None and chain.checks()["status"] == "not found"


def test_second_reading_changing_row_value_is_not_disagreement(chain, monkeypatch) -> None:
    """Rereading, the model cites the right row but writes another day: it is not another source, it is a reading
    error. The program discards it (value not in the row): «not found again», not «disagree»."""
    calls = {"value": 0}

    def model(point: str, data: dict, default, **_other):
        if point == "review":
            return _HOLDS, "claude"
        if point == "value" and "6 dicembre 1586" in data["proofs"]:
            calls["value"] += 1
            if calls["value"] > 1:
                return {"value": "9 dicembre 1586", "proof": _row(data["proofs"], "6 dicembre 1586"), "reason": ""}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    _strong_route(chain)
    chain.final_review()
    assert chain.exams["second_reading"] == {"outcome": "not found again"} and chain.checks()["status"] == "supported"


def test_review_sees_other_articles_of_every_link(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    """12 Nov, control questions (Python): the birthplace taken from one article, another language gave another one
    and the answer was «verified». The review also gets the rows of the other articles of the previous link: a «no»
    with those words makes the answer uncertain."""
    from types import SimpleNamespace

    from chain_research.web.chain import Proof
    another = Proof("[Giuseppe Biancani (en)] Notable students: Giovanni Battista Riccioli", "https://en.wikipedia.org/wiki/Biancani")
    seen_items: list[dict] = []

    def model(point: str, data: dict, default, **_other):
        if point == "review":
            seen_items.append(data)
            return {"holds": "no", "reason": "m", "clues": [{
                "clue": "allievo di Biancani", "outcome": "no", "row": _row(data["rows"], "Notable students"),
                "quote": "Notable students: Giovanni Battista Riccioli"}]}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    _strong_route(chain)
    requested: list[tuple[str, list[str]]] = []

    def articles(a, other_ones=2):
        return [SimpleNamespace(url="https://en.wikipedia.org/wiki/Biancani", title="Giuseppe Biancani", language="en",
                                languages={})] if a.name == "Giuseppe Biancani" else []

    def wiki_rows(articles, words, **_k):
        requested.append((articles[0].title, words))
        return [another]

    monkeypatch.setattr(chain, "_articles", articles)
    monkeypatch.setattr(chain, "_wiki_rows", wiki_rows)
    chain.final_review()
    assert "Notable students: Giovanni Battista Riccioli" in seen_items[0]["rows"]
    assert ("Giuseppe Biancani", ["allievi", "Niccolò Zucchi"]) in requested  # the relation and the name of the chosen link
    outcome = chain.checks()
    assert outcome["status"] == "uncertain" and outcome["exams"]["review"]["objections"][0]["url"].endswith("/Biancani")


# --- review of 2026-11-11, defects B5 and B6: outcomes go to the right candidate and check ----------------------

def _bare_chain():
    c = Chain.__new__(Chain)
    c.o = {}
    return c


def test_same_surname_candidates_do_not_swap_outcomes() -> None:
    """The model rewrites the names («G. Verdi», «Verdi Carlo»): with two candidates with the same surname the
    outcome of one must not go to the other. Without an equal name and with an ambiguous surname, no outcome («don't
    know»)."""
    from chain_research.web.chain import Link, Proof
    c = _bare_chain()
    giuseppe, carlo = Link("Giuseppe Verdi"), Link("Carlo Verdi")
    proofs = [Proof("Giuseppe Verdi fu un compositore", "u1"), Proof("Carlo Verdi fu un oste", "u2")]
    checks = [{"description": "compositore", "readings": [], "weight": 2}]
    outcome = {"candidates": [
        {"name": "Verdi Carlo", "checks": [{"check": "C1", "outcome": "no", "proof": 2, "quote": "fu un oste"}]},
        {"name": "G. Verdi", "checks": [{"check": "C1", "outcome": "yes", "proof": 1, "quote": "fu un compositore"}]}]}
    c._apply_outcomes([giuseppe, carlo], proofs, {id(giuseppe): range(1, 2), id(carlo): range(2, 3)}, outcome, checks)
    assert [e["outcome"] for e in giuseppe.outcomes] == ["unknown"] and [e["outcome"] for e in carlo.outcomes] == ["unknown"]
    # with a single candidate with that surname the match by surname stays (16 Oct: names rewritten by the model)
    only = Link("Giuseppe Verdi")
    c._apply_outcomes([only], proofs, {id(only): range(1, 2)}, {"candidates": [outcome["candidates"][1]]}, checks)
    assert [e["outcome"] for e in only.outcomes] == ["yes"]


def test_checks_with_same_start_stay_separate() -> None:
    from chain_research.web.chain import Link, Proof
    c = _bare_chain()
    start = "è stato rettore di un collegio della Compagnia di Gesù nella città di "
    checks = [{"description": start + "Roma", "readings": [], "weight": 1},
                 {"description": start + "Parma", "readings": [], "weight": 1}]
    a = Link("Niccolò Zucchi")
    proofs = [Proof("Niccolò Zucchi fu rettore del Collegio Romano a Roma", "u1")]
    outcome = {"candidates": [{"name": "Niccolò Zucchi", "checks": [
        {"check": "C1", "outcome": "yes", "proof": 1, "quote": "rettore del Collegio Romano"},
        {"check": "C2", "outcome": "no", "proof": 1, "quote": "a Roma"}]}]}
    c._apply_outcomes([a], proofs, {id(a): range(1, 2)}, outcome, checks)
    assert [(e["check"][-5:].strip(), e["outcome"]) for e in a.outcomes] == [("Roma", "yes"), ("Parma", "no")]
    merged_items = Chain._merge_outcomes(a.outcomes, a.outcomes)
    assert [e["outcome"] for e in merged_items] == ["yes", "no"]


@pytest.mark.parametrize("row,value,outcome,status", [
    ("[Niccolò Zucchi (en)] Niccolò Zucchi (December 6, 1586 – May 21, 1670) was an Italian Jesuit", "6 dicembre 1586", "agree", "verified"),
    ("[Niccolò Zucchi (en)] Niccolò Zucchi (December 16, 1586 – May 21, 1670) was an Italian Jesuit", "16 dicembre 1586", "disagree", "contested"),
    ("[Niccolò Zucchi (en)] Niccolò Zucchi was an Italian Jesuit astronomer", "", "single source", "supported"),
    # the model gives a date that is not in the row: for the program it is not another source
    ("[Niccolò Zucchi (en)] Niccolò Zucchi was an Italian Jesuit astronomer", "16 dicembre 1586", "single source", "supported"),
    # a row about someone else (neither their article nor their name) does not count
    ("[Giovanni Riccioli (en)] born December 16, 1586", "16 dicembre 1586", "single source", "supported"),
])
def test_corroboration_in_another_article(chain, monkeypatch: pytest.MonkeyPatch, row: str, value: str, outcome: str, status: str) -> None:
    """T7: the same datum searched in another article of the final entity: equal, different or absent."""
    from types import SimpleNamespace

    from chain_research.web.chain import Proof
    another = Proof(row, "https://en.wikipedia.org/wiki/Altra_voce")

    def model(point: str, data: dict, default, **_other):
        if point == "review":
            return {"holds": "yes", "reason": "", "clues": []}, "claude"
        if point == "value" and "[Article (it)]" not in data["proofs"] and "Altra" not in data["proofs"] and row in data["proofs"]:
            return {"value": value, "proof": _row(data["proofs"], row[row.index("]") + 2:][:30]) if value else 0, "reason": ""}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    _strong_route(chain)
    final = chain.value["link"]
    monkeypatch.setattr(chain, "_articles", lambda a, other_ones=2: [SimpleNamespace(
        url=another.url, title="Niccolò Zucchi", language="en", languages={})] if a is final else [])
    monkeypatch.setattr(chain, "_wiki_rows", lambda articles, words, **_k: [another])
    monkeypatch.setattr(chain, "_entity_proofs", lambda *_to, **_k: [])
    chain.final_review()
    corroboration = chain.exams["corroboration"]
    assert corroboration["outcome"] == outcome and chain.checks()["status"] == status
    if outcome == "disagree":
        assert corroboration["value"] == "16 dicembre 1586" and "other value: 16 dicembre 1586" in chain.material()


@pytest.mark.parametrize("quote,refuted", [("rettore del Collegio Romano", True), ("parole inventate", False)])
def test_refuted_premise_gives_no_value(chain, monkeypatch: pytest.MonkeyPatch, quote: str, refuted: bool) -> None:
    """13 Nov: if the review shows, with the words of a row, that the question takes for granted something the
    sources deny, the chain gives no value: «not found», with the reason. Without the row the premise stands."""
    def model(point: str, data: dict, default, **_other):
        if point == "review":
            return {"holds": "no", "reason": "m", "clues": [], "premise": {
                "outcome": "refuted", "what": "Zucchi was a layman", "quote": quote,
                "row": _row(data["rows"], "Collegio Romano")}}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    _strong_route(chain)
    chain.final_review()
    outcome = chain.checks()
    if not refuted:
        assert chain.value["value"] == "6 dicembre 1586" and outcome["status"] == "supported" and chain.discarded is None
        return
    assert chain.value is None and outcome["status"] == "not found" and not outcome["confirmed"]
    assert chain.discarded["value"] == "6 dicembre 1586" and "premise refuted" in chain.reason
    assert "Zucchi was a layman" in chain.reason and "rettore del Collegio Romano" in chain.reason
    assert outcome["premise"]["url"].endswith("Zucchi") and chain.summary()["value"] is None
    assert chain.summary()["discarded_value"] == "6 dicembre 1586"
    assert chain.r.write().startswith("**Answer:** Not found: premise refuted")


@pytest.mark.parametrize("premise,weak_ones,datum", [("unproven", True, False), ("holds", True, True), ("unproven", False, True)])
def test_rejected_route_gives_no_value(chain, monkeypatch: pytest.MonkeyPatch, premise: str, weak_ones: bool, datum: bool) -> None:
    def model(point: str, data: dict, default, **_other):
        if point == "review":
            return {"holds": "no", "reason": "m", "premise": {"outcome": premise, "what": "", "row": 0, "quote": ""},
                    "clues": [{"clue": "laico", "outcome": "no", "quote": "rettore del Collegio Romano",
                                "row": _row(data["rows"], "Collegio Romano")}]}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    if weak_ones:
        assert chain._weak_links() >= 1   # in the fake Galileo chain one link stays weak
    else:
        _strong_route(chain)
    chain.final_review()
    if datum:
        assert chain.value is not None and chain.checks()["status"] == "uncertain" and chain.discarded is None
    else:
        assert chain.value is None and chain.checks()["status"] == "not found"
        assert chain.discarded["value"] == "6 dicembre 1586" and chain.reason.startswith("chain rejected by the review")
        assert "laico" in chain.reason and "laico" in chain.checks()["reasons"][0]


# --- 15 Nov: two reviews, the stricter counts; the name the question gives the starting point -----------------

def test_of_two_disagreeing_reviews_the_stricter_counts(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([{"holds": "yes", "reason": "", "clues": []}, {"holds": "unproven", "reason": "doubt", "clues": []}])
    mutex = __import__("threading").Lock()

    def model(point: str, data: dict, default, **_other):
        if point == "review":
            with mutex:
                return next(answers), "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.walk()
    assert chain._weak_links() >= 1
    chain.final_review()
    review = chain.exams["review"]
    assert (review["holds"], review["readings"]) == ("unproven", 2)
    assert chain.checks()["status"] == "uncertain"       # weak link: both must hold


@pytest.mark.parametrize("name,found", [("Zorvex", False), ("Galileo", True), ("", True), ("Zucchi", True)])
def test_starting_point_named_by_question_must_be_in_pages(chain, monkeypatch, name: str, found: bool) -> None:
    def model(point: str, data: dict, default, **_other):
        if point == "review":
            return {"holds": "yes", "reason": "", "clues": [], "starting_point_name": name}, "claude"
        return _decide(point, data, default)

    monkeypatch.setattr(chain.r.thinker, "decide", model)
    chain.r.thinker.ledger.budget_usd = 1.0
    chain.r.topic = "Who was the student of Galileo's student according to the Zorvex treatise?"  # «Zucchi» is not in it
    chain.walk()
    _strong_route(chain)
    chain.final_review()
    if found:
        assert chain.value is not None and chain.discarded is None
    else:
        assert chain.value is None and chain.discarded["value"] == "6 dicembre 1586"
        assert "«Zorvex», named by the question, appears in none" in chain.reason
        assert chain.checks()["status"] == "not found" and "Zorvex" in chain.checks()["reasons"][0]
