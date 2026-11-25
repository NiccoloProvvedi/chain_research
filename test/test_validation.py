"""Program checks on the results (docs/validation.md §4): no network, no Claude."""
import pytest

from chain_research.web import validation as v
from chain_research.web.chain import Link, Chain, Proof


@pytest.mark.parametrize("value,row", [
    ("21 giugno 1886", "Hoshiyar died at her palace on June 21, 1886 and was buried in Cairo"),
    ("11 ottobre 1943", "[Michael Stonebraker (en)] Born: (1943-10-11) October 11, 1943"),
    ("11 ottobre 1943", "Born: 1943-10-11 (age 82)"),
    ("1º gennaio 2015", "Istituzione: 1º gennaio 2015"),
    ("1 gennaio 2015", "istituita il 1º gennaio 2015 dalla legge n. 56"),
    ("15. Dezember 1941", "geboren am 15. Dezember 1941 in Wien"),
    ("56 m s.l.m.", "Altitudine: 56 m s.l.m."),
    ("−2 m s.l.m.", "Altitudine: −2 m s.l.m."),
    ("20,65 km²", "Superficie: 20.65 km²"),
    ("1234 abitanti", "Abitanti: 1 234 (2019)"),
    ("36 metri", "Elevation: 118 ft (36 m)"),
    ("00118–00199", "CAP: 00118–00199"),
    ("43010", "Codice postale 43010"),
    ("Novara di Sicilia", "Il simulacro si trova a Novara di Sicilia, in provincia di Messina"),
    ("44 a.C.", "morì nel 44 a.C. a Roma"),
    ("26 luglio 1985", "[Υπουργείο Πολιτισμού (el)] συγκροτήθηκε στις 26 Ιουλίου 1985 από την κυβέρνηση"),
])
def test_value_written_in_row(value: str, row: str) -> None:
    assert v.value_in_row(value, row)


@pytest.mark.parametrize("value,row", [
    ("21 giugno 1885", "Hoshiyar died at her palace on June 21, 1886"),
    ("21 luglio 1886", "Hoshiyar died at her palace on June 21, 1886"),
    ("21 luglio 1886", "Morta il 21 giugno 1886; ebbe 7 figli"),
    ("56 m s.l.m.", "Altitudine: 58 m s.l.m."),
    ("5 m", "Altitudine: 56 m, superficie 15 km²"),
    # 14 Nov: a number with a decimal comma is not matched in pieces ("9,7" is not "9 … 7")
    ("9,7 miliardi di dollari", "Nel 1997 il costo previsto era di 9 miliardi per 7 strumenti"),
    ("20,65 km²", "Superficie: 20 km² (65 abitanti)"),
    ("13 dicembre 2004", "David Wheeler was a computer scientist at Cambridge"),
    ("Capri Leone", "Il simulacro si trova a Novara di Sicilia"),
    ("", "qualunque riga"),
])
def test_value_not_in_row(value: str, row: str) -> None:
    assert not v.value_in_row(value, row)


@pytest.mark.parametrize("text,url,expected", [
    ("[Amsterdam (it)] Altitudine: −2 m", "https://it.wikipedia.org/wiki/Amsterdam", "A"),
    ("Michael Stonebraker was born in Milton", "https://example.org/bio", "B"),
    ("[search result: Michael Stonebraker | Aubrey Research] born in Milton", "https://example.org/bio", "C"),
    ("[search result: Wikipedia] nato a Milton", "https://en.wikipedia.org/wiki/X", "C"),
])
def test_source_level(text: str, url: str, expected: str) -> None:
    assert v.level(Proof(text, url)) == expected


def test_route_does_not_return_to_ancestor() -> None:
    stroustrup = Link("Bjarne Stroustrup")
    wheeler = Link("David Wheeler", parent=stroustrup)
    burrows = Link("Michael Burrows (computer scientist)", parent=wheeler)
    assert v.repeats_ancestor(Link("David Wheeler"), burrows)
    assert v.repeats_ancestor(Link("David  wheeler"), burrows)
    assert not v.repeats_ancestor(Link("Maurice Wilkes"), burrows)
    assert v.repeats_ancestor(Link("Bjarne Stroustrup"), burrows)
    assert v.repeats_ancestor(Link("Burrows", title=("en", "Michael Burrows (computer scientist)")), burrows)
    novel = Link("I promessi sposi")
    assert not v.repeats_ancestor(Link("I promessi sposi (Petrella)"), novel)
    assert not v.repeats_ancestor(Link("Solférino (Landes)"), Link("Solferino"))


def _si(check: str, proof: Proof) -> dict:
    return {"check": check, "outcome": "yes", "proof": proof, "weight": 1}


def test_tie_proven_only_by_snippets() -> None:
    snippet = Proof("[search result: Aubrey Research] Stonebraker was born in Milton", "https://x.org/a")
    read = Proof("Stonebraker was born in Milton, New Hampshire", "https://x.org/a")
    assert v.tie_only_from_snippets(Link("Milton", parent=Link("S"), outcomes=[_si("is related «nato a»", snippet)]))
    assert not v.tie_only_from_snippets(Link("Milton", parent=Link("S"), outcomes=[_si("is related «nato a»", read)]))
    assert not v.tie_only_from_snippets(Link("Milton", outcomes=[]))  # starting point: no tie to prove


def test_disagreements_log() -> None:
    stonebraker = Link("Michael Stonebraker")
    infobox = Proof("[Michael Stonebraker (en)] Born: Newburyport, Massachusetts", "https://en.wikipedia.org/wiki/MS")
    discarded = Link("Newburyport, Massachusetts", parent=stonebraker, tie_proof=infobox, outcomes=[
        {"check": "town in New Hampshire", "outcome": "no", "proof": infobox, "weight": 2}])
    without_source = Link("Derry", parent=stonebraker, outcomes=[{"check": "c", "outcome": "no", "proof": infobox}])
    no = [{"check": "c", "outcome": "no", "proof": infobox}]
    article_its = Link("Hassan", parent=stonebraker, outcomes=no, tie_proof=Proof(
        "[Hassan (nome) (it)] Hassan è un nome proprio di persona", "https://it.wikipedia.org/wiki/Hassan"))
    sentence = Link("Freeduino", parent=stonebraker, outcomes=no, tie_proof=Proof(
        "[Michael Stonebraker (en) § Derivati] Quale conseguenza, un gruppo ha fatto Freeduino", "https://en.wikipedia.org/wiki/MS"))
    articles = v.disagreements([discarded, without_source, article_its, sentence], "place of birth of")
    assert len(articles) == 1 and articles[0]["name"] == "Newburyport, Massachusetts" and articles[0]["parent"] == "Michael Stonebraker"
    assert articles[0]["url"].endswith("/MS") and articles[0]["against"] == ["town in New Hampshire"]
    milton = Link("Milton", parent=stonebraker)
    dover = Link("Dover", parent=Link("Strafford County", parent=milton))
    assert v.disagreements_on_route(articles, dover) == articles
    assert v.disagreements_on_route(articles, Link("Roma", parent=Link("Italia"))) == []
    assert v.disagreements_on_route(articles, discarded) == []  # the route goes through the strong source's candidate
    brother = Link("Milton", parent=stonebraker, tie_proof=Proof("[MS (en)] Born: Milton", "https://en.wikipedia.org/wiki/MS"))
    assert v.disagreements_on_route(articles, brother) == []  # the chosen one also has a strong source: they are siblings


def test_confirmed_requires_all_checks() -> None:
    wiki = Proof("[Dover (en)] Elevation: 36 m", "https://en.wikipedia.org/wiki/Dover")
    snippet = Proof("[search result: x] Elevation 36 m", "https://x.org")
    a = Link("Dover", parent=Link("County"), outcomes=[_si("is related «capoluogo»", wiki)])
    c = Chain.__new__(Chain)
    c.conflicts = []
    c.value = {"value": "36 m", "proof": wiki, "link": a}
    assert c.checks()["confirmed"]
    c.value["proof"] = snippet
    assert not c.checks()["confirmed"] and c.checks()["value_source"] == "C"
    c.value["proof"] = wiki
    a.weak = True
    assert not c.checks()["confirmed"]
    a.weak = False
    a.outcomes = [_si("is related «capoluogo»", Proof("Dover is the county seat", "https://x.org/county"))]
    c.conflicts = [{"parent": "County", "name": "Other", "url": "u", "row": "r", "against": ["c"], "relation": "county seat"}]
    assert not c.checks()["confirmed"] and len(c.checks()["disagreements"]) == 1
    c.value = None
    assert not c.checks()["confirmed"]


# --- exhaustive tests: every date in every language is found, every slightly changed date is not ----------------

_LANGUAGES = {
    "it": ("{g} {m} {a}", "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre"),
    "en": ("{m} {g}, {a}", "January February March April May June July August September October November December"),
    "de": ("{g}. {m} {a}", "Januar Februar März April Mai Juni Juli August September Oktober November Dezember"),
    "fr": ("{g} {m} {a}", "janvier février mars avril mai juin juillet août septembre octobre novembre décembre"),
    "es": ("{g} de {m} de {a}", "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"),
    "pt": ("{g} de {m} de {a}", "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro"),
}
_ITALIAN = _LANGUAGES["it"][1].split()
_DATE = [(g, m, a) for g in (1, 9, 15, 28) for m in range(1, 13) for a in (476, 1703, 1886, 2015)]


@pytest.mark.parametrize("language", sorted(_LANGUAGES))
def test_every_translated_date_is_found(language: str) -> None:
    form, months = _LANGUAGES[language][0], _LANGUAGES[language][1].split()
    for g, m, a in _DATE:
        value = f"{g} {_ITALIAN[m - 1]} {a}"
        row = "[Voce (xx)] Evento avvenuto: " + form.format(g=g, m=months[m - 1], a=a) + ", secondo le cronache."
        assert v.value_in_row(value, row), (value, row)
        assert v.value_in_row(value, f"Born: {a:04d}-{m:02d}-{g:02d} (aged 70)"), value


@pytest.mark.parametrize("language", sorted(_LANGUAGES))
def test_every_slightly_changed_date_is_rejected(language: str) -> None:
    form, months = _LANGUAGES[language][0], _LANGUAGES[language][1].split()
    for g, m, a in _DATE:
        row = "[Voce (xx)] Evento avvenuto: " + form.format(g=g, m=months[m - 1], a=a) + ", secondo le cronache."
        for g2, m2, a2 in ((g + 1, m, a), (g, m % 12 + 1, a), (g, m, a + 1), (g, m, a - 1)):
            value = f"{g2} {_ITALIAN[m2 - 1]} {a2}"
            assert not v.value_in_row(value, row), (value, row)


def test_every_changed_number_is_rejected() -> None:
    for n in (2, 15, 56, 68, 138, 312, 4810, 43010):
        row = f"[Comune (it)] Altitudine: {n} m s.l.m.; abitanti: 7 (2019)"
        assert v.value_in_row(f"{n} m s.l.m.", row)
        for other in (n + 1, n - 1, n * 10, int(str(n)[::-1]) if str(n)[::-1] != str(n) else n + 2):
            if other != n:
                assert not v.value_in_row(f"{other} m s.l.m.", row), (other, row)


def test_month_names_map_to_one_number() -> None:
    """Every month name maps to a single number, except names shared by several languages with the same number."""
    assert set(v.MONTH.values()) == set(range(1, 13))
    for number, names in enumerate(v._MONTHS, start=1):
        assert all(v.MONTH[n] == number for n in names.split())


# --- the real cases from the control questions (11 Nov), as the chain saw them -----------------------------------

def test_case_postgresql_snippet_against_infobox() -> None:
    """The Wikipedia infobox says Newburyport; the chain kept Milton from a snippet, "strong" and "confirmed"."""
    stonebraker = Link("Michael Stonebraker")
    infobox = Proof("[Michael Stonebraker (en)] Born: (1943-10-11) October 11, 1943 Newburyport, Massachusetts, U.S.",
                     "https://en.wikipedia.org/wiki/Michael_Stonebraker")
    snippet = Proof("[search result: Michael Stonebraker | Computer scientist - Aubrey Research] Michael "
                     "Stonebraker was born in Milton, New Hampshire in 1943.", "https://aubrey.example/stonebraker")
    newburyport = Link("Newburyport, Massachusetts", parent=stonebraker, tie_proof=infobox, outcomes=[
        {"check": "town in New Hampshire", "outcome": "no", "proof": infobox, "weight": 2}])
    milton = Link("Milton", parent=stonebraker, tie_proof=snippet, quality=0.9,
                    outcomes=[_si("is related «luogo di nascita di» with Michael Stonebraker", snippet)])
    assert v.tie_only_from_snippets(milton)
    dover = Link("Dover, New Hampshire", parent=Link("Strafford County", parent=milton))
    c = Chain.__new__(Chain)
    c.conflicts = v.disagreements([newburyport], "is related «luogo di nascita di» with Michael Stonebraker")
    c.value = {"value": "36 metri", "link": dover,
                "proof": Proof("[Dover, New Hampshire (en)] Elevation: 118 ft (36 m)", "https://en.wikipedia.org/wiki/Dover")}
    outcome = c.checks()
    assert not outcome["confirmed"] and [x["name"] for x in outcome["disagreements"]] == ["Newburyport, Massachusetts"]


def test_case_aida_value_is_the_row_value() -> None:
    row = "[Hoshiyar Qadin (en)] Hoshiyar died at her palace on 21 June 1886 and was buried in the Khedival Mausoleum"
    assert v.value_in_row("21 giugno 1886", row) and not v.value_in_row("21 giugno 1885", row)


def test_case_cpp_chain_does_not_return_to_wheeler() -> None:
    stroustrup = Link("Bjarne Stroustrup")
    wheeler = Link("David Wheeler (computer scientist)", parent=stroustrup, title=("en", "David Wheeler (computer scientist)"))
    burrows = Link("Michael Burrows (computer scientist)", parent=Link("Roger Needham", parent=wheeler))
    assert v.repeats_ancestor(Link("David Wheeler", title=("en", "David Wheeler (computer scientist)")), burrows)
    assert not v.repeats_ancestor(Link("David Wheeler"), burrows)  # without the article, a namesake is still possible


# --- checks with the model: the program decides -------------------------------------------------------------------

@pytest.mark.parametrize("a,b,equal", [
    ("56 m s.l.m.", "56 metri", True), ("21 giugno 1886", "June 21, 1886", True), ("1 gennaio 2015", "1º gennaio 2015", True),
    ("20,65 km²", "20.65 km2", True), ("Novara di Sicilia", "novara di sicilia", True), ("1886", "1886", True),
    ("−2 m", "2 m", False), ("21 giugno 1886", "21 giugno 1885", False), ("21 giugno 1886", "21 luglio 1886", False),
    ("56 m", "58 m", False), ("Novara di Sicilia", "Capri Leone", False), ("", "", False),
    # 12 Nov, control questions (Fortran): the value written with a note after it is the same value
    ("10 marzo 1682 (data di istituzione); alcune fonti indicano novembre 1682 per la divisione in tre contee", "10 marzo 1682", True),
    ("138 m s.l.m. (453 ft)", "138 m", True), ("120 m s.l.m.", "138 m s.l.m.", False), ("320 m", "274 m s.l.m.", False),
    ("10 marzo 1682 (alcune fonti: novembre 1682)", "10 novembre 1683", False), ("−2 m (2 metri sotto)", "2 m", False),
])
def test_same_value(a: str, b: str, equal: bool) -> None:
    assert v.same_value(a, b) is equal and v.same_value(b, a) is equal


def test_review_counts_only_with_row() -> None:
    rows = [Proof("David Wheeler was a British computer scientist", "u1"), Proof("Maurice Wilkes supervised Wheeler", "u2")]
    answer = {"holds": "no", "reason": "not a physicist", "clues": [
        {"clue": "British physicist", "outcome": "no", "row": 1, "quote": "British computer scientist"},
        {"clue": "advisor of", "outcome": "no", "row": 2, "quote": "words that are not in the row"},
        {"clue": "made up", "outcome": "no", "row": 9, "quote": "British"},
        {"clue": "advisor", "outcome": "yes", "row": 2, "quote": "supervised Wheeler"},
        {"clue": "born in 1927", "outcome": "unproven", "row": 0, "quote": ""}]}
    outcome = v.examine_review(answer, rows)
    assert [o["clue"] for o in outcome["objections"]] == ["British physicist"] and outcome["objections"][0]["url"] == "u1"
    assert (outcome["proven"], outcome["unproven"], outcome["holds"]) == (1, 3, "no")
    assert v.examine_review({}, rows)["objections"] == []


_GOOD = {"second_reading": {"outcome": "agree"}, "corroboration": {"outcome": "agree"},
          "review": {"outcome": "done", "holds": "yes", "objections": [], "unproven": 0}}
_OBJECTION = {"outcome": "done", "holds": "no", "objections": [{"clue": "physicist", "quote": "computer scientist"}]}


@pytest.mark.parametrize("changes,expected", [
    ({}, "verified"),
    ({"value": ""}, "not found"),
    # 13 Nov, calibration on the 40 questions: weak link but review "holds: yes", 6 right and 0 wrong (on the control
    # questions 13 and 0); with "unproven" 11 right and 2 wrong. The weak link leads to "uncertain" only without a
    # review that holds; with a review that holds the answer is at most "supported".
    ({"weak_links": 1}, "supported"),
    ({"weak_links": 2, "exams": {**_GOOD, "review": {"outcome": "done", "holds": "unproven", "objections": [], "unproven": 1}}}, "uncertain"),
    ({"weak_links": 1, "exams": {**_GOOD, "review": {"outcome": "not done"}}}, "uncertain"),
    ({"weak_links": 1, "exams": {**_GOOD, "review": _OBJECTION}}, "uncertain"),
    ({"weak_links": 1, "exams": None}, "uncertain"),
    ({"weak_links": 1, "exams": {**_GOOD, "corroboration": {"outcome": "disagree", "value": "58 m"}}}, "contested"),
    ({"weak_links": 1, "value_source": "C"}, "uncertain"),
    ({"value_source": "C"}, "uncertain"),
    ({"exams": {**_GOOD, "review": _OBJECTION}}, "uncertain"),
    ({"exams": {**_GOOD, "corroboration": {"outcome": "disagree", "value": "21 giugno 1885"}}}, "contested"),
    ({"exams": {**_GOOD, "second_reading": {"outcome": "disagree", "value": "58 m"}}}, "contested"),
    ({"disagreements": [{"relation": "born in", "parent": "S", "name": "Newburyport"}]}, "contested"),
    ({"weak_links": 1, "disagreements": [{"relation": "born in", "parent": "S", "name": "N"}]}, "contested"),
    ({"weak_links": 1, "exams": None, "disagreements": [{"relation": "born in", "parent": "S", "name": "N"}]}, "uncertain"),
    ({"exams": {**_GOOD, "corroboration": {"outcome": "single source"}}}, "supported"),
    ({"exams": {**_GOOD, "second_reading": {"outcome": "not found again"}}}, "supported"),
    ({"exams": {**_GOOD, "second_reading": {"outcome": "not needed"}}}, "verified"),
    ({"exams": {**_GOOD, "review": {"outcome": "done", "holds": "unproven", "objections": [], "unproven": 2}}}, "supported"),
    ({"exams": {**_GOOD, "review": {"outcome": "not done"}}}, "supported"),
    ({"exams": None}, "supported"),
])
def test_answer_status(changes: dict, expected: str) -> None:
    base = {"value": "56 m", "weak_links": 0, "value_source": "A", "disagreements": [], "exams": _GOOD}
    status, reasons = v.status({**base, **changes})
    assert status == expected and status in v.STATUSES
    assert bool(reasons) == (status not in ("verified", "not found"))


# --- 13 Nov: the question's premise (questions without an answer: 15 invented values out of 20) ------------------

def _premise(outcome: str, row: int, quote: str) -> dict:
    return {"holds": "no", "reason": "", "clues": [],
            "premise": {"outcome": outcome, "what": "Newton had a wife", "row": row, "quote": quote}}


def test_refuted_premise_counts_only_with_row() -> None:
    rows = [Proof("Newton never married and had no children", "u1"), Proof("His mother remarried in 1646", "u2")]
    false = v.examine_review(_premise("refuted", 1, "never married"), rows)["premise"]
    assert false == {"outcome": "refuted", "what": "Newton had a wife", "quote": "never married", "url": "u1"}
    assert v.examine_review(_premise("refuted", 2, "never married"), rows)["premise"]["outcome"] == "unproven"
    assert v.examine_review(_premise("refuted", 0, ""), rows)["premise"]["outcome"] == "unproven"
    assert v.examine_review(_premise("holds", 1, "never married"), rows)["premise"]["outcome"] == "holds"
    assert v.examine_review({"holds": "yes", "clues": []}, rows)["premise"]["outcome"] == "absent"  # earlier answers
    assert v.refuted_premise({"review": v.examine_review(_premise("refuted", 1, "never married"), rows)})
    assert not v.refuted_premise({"review": v.examine_review(_premise("refuted", 2, "never married"), rows)})
    assert not v.refuted_premise({}) and not v.refuted_premise(None)


_OB = [{"clue": "British physicist", "quote": "computer scientist", "url": "u"}]


@pytest.mark.parametrize("review,weak_ones,rejected", [
    ({"outcome": "done", "objections": _OB, "premise": {"outcome": "unproven"}}, 1, True),
    ({"outcome": "done", "objections": _OB}, 2, False),                                          # earlier reviews: unknown
    ({"outcome": "done", "objections": _OB, "premise": {"outcome": "absent"}}, 2, False),
    ({"outcome": "done", "objections": _OB, "premise": {"outcome": "holds"}}, 1, False),          # the question makes sense: stays uncertain
    ({"outcome": "done", "objections": _OB, "premise": {"outcome": "unproven"}}, 0, False),    # route fully confirmed
    ({"outcome": "done", "objections": [], "holds": "no", "premise": {"outcome": "unproven"}}, 1, False),  # "no" without a row
    ({"outcome": "not done"}, 3, False),
])
def test_rejected_route(review: dict, weak_ones: int, rejected: bool) -> None:
    """13 Nov, calibration (prova_revisione.py): unproven premise, objection with a row and weak link together: 0
    right answers and 2 wrong on the 40 questions; on the questions without an answer, 3 invented values. Three things
    that do not add up: no value is given."""
    outcome = v.rejected_route({"review": review}, weak_ones)
    assert (outcome == _OB[0]) if rejected else outcome is None
    assert v.rejected_route(None, 2) is None


# --- 15 Nov: two reviews (the strictest wins) and the name the question uses for its starting point ---------------

def _rev(holds: str, objections=(), premise="holds", name="", proven=3, unproven=0, reason_="") -> dict:
    return {"outcome": "done", "holds": holds, "objections": list(objections), "proven": proven, "unproven": unproven,
            "premise": {"outcome": premise}, "starting_point_name": name, "reason": reason_ or holds}


def test_strictest_of_two_reviews_wins() -> None:
    a, b = {"clue": "a", "quote": "x", "url": "u"}, {"clue": "b", "quote": "y", "url": "u"}
    assert v.merge_reviews([_rev("yes"), _rev("yes")])["holds"] == "yes"
    assert v.merge_reviews([_rev("yes"), _rev("unproven", unproven=2)])["holds"] == "unproven"
    unit = v.merge_reviews([_rev("yes", proven=4), _rev("no", [a], premise="unproven", proven=2, unproven=1)])
    assert (unit["holds"], unit["objections"], unit["proven"], unit["unproven"]) == ("no", [a], 2, 1)
    assert unit["premise"]["outcome"] == "unproven" and unit["readings"] == 2
    assert v.merge_reviews([_rev("no", [a]), _rev("no", [a, b])])["objections"] == [a, b]       # without duplicates
    false = {"outcome": "refuted", "what": "c", "quote": "q", "url": "u"}
    assert v.merge_reviews([_rev("yes"), {**_rev("no"), "premise": false}])["premise"] == false
    assert v.merge_reviews([_rev("yes", premise="absent"), _rev("yes")])["premise"]["outcome"] == "absent"
    assert v.merge_reviews([_rev("yes", name=""), _rev("yes", name="Zorvex")])["starting_point_name"] == "Zorvex"
    # a single successful review: it counts; none: not done
    assert v.merge_reviews([{"outcome": "not done"}, _rev("yes")])["holds"] == "yes"
    assert v.merge_reviews([{"outcome": "not done"}, {"outcome": "not done"}]) == {"outcome": "not done"}


def test_merged_reviews_do_not_depend_on_their_order() -> None:
    """24 Nov, replay of TeX: the two reviews run in parallel and finish in either order; the objection cited in the
    reason changed with the order (same clue, two quotes)."""
    a = {"clue": "episode", "quote": "intitolato Fuorilegge", "url": "u"}
    b = {"clue": "episode", "quote": "nell'albo n. 3 della collana di Tex gigante, intitolato Fuorilegge", "url": "u"}
    one, two = _rev("no", [a], premise="unproven", name="Tex"), _rev("no", [b], premise="unproven", reason_="other")
    assert v.merge_reviews([one, two]) == v.merge_reviews([two, one])


@pytest.mark.parametrize("name,question,texts,missing", [
    ("Zorvex", "creatore nel 1987 del linguaggio Zorvex?", ["Lua è un linguaggio creato nel 1993"], ["Zorvex"]),
    ("sonda Aurelia 3", "della sonda Aurelia 3, lanciata dall'ESA?", ["Deep Impact fu una sonda della NASA"], ["Aurelia"]),
    ("Museo Castrovillari", "fondò a Oslo il Museo Castrovillari di arte etrusca?", ["Museo di Oslo", "arte etrusca"], ["Castrovillari"]),
    ("Battaglia di Hastings", "nella Battaglia di Hastings del 1066?", ["The Battle of Hastings was fought on 14 October 1066"], []),
    ("linguaggio Python", "il linguaggio Python?", ["[Python (it)] Python è un linguaggio"], []),
    ("Ducato di Valsenia", "capitale del Ducato di Valsenia?", ["Casola Valsenio è un comune", "Il ducato di Urbino"], ["Valsenia"]),
    ("Alan Turing", "il matematico alan turing?", ["nessuna riga utile"], []),          # not written like this in the question
    ("", "domanda senza nome?", ["x"], []),
    ("il celebre linguaggio dal nome di un serpente", "il celebre linguaggio dal nome di un serpente?", ["Python"], []),
])
def test_starting_point_name_missing_from_pages(name: str, question: str, texts: list[str], missing: list[str]) -> None:
    """The capitalised words of the name the question uses for its starting point must be in at least one page
    read: "Zorvex", "Aurelia", "Castrovillari" are not, because the thing does not exist. It applies only to a name
    copied from the question; a description without proper names cannot be checked this way."""
    assert v.missing_name(name, question, texts) == missing
