"""Program checks on the chain's results: the program decides, the model proposes.

See docs/validation.md §4. This module holds the checks without a model: the value written in the cited row (G1),
the route without repeated entities (G3), the source level of a proof (G4), the log of disagreements (G7).
The functions receive the chain's objects (`Link`, `Proof`) without importing them.
"""
import re
import unicodedata
from urllib.parse import urlsplit

# Months by number, in the languages of the articles read (it, en, de, fr, es, pt) and in English abbreviations.
_MONTHS = [
    "gennaio january jan januar janvier enero janeiro",
    "febbraio february feb februar fevrier febrero fevereiro",
    "marzo march mar marz mars marco",
    "aprile april apr avril abril",
    "maggio may mai mayo maio",
    "giugno june jun juni juin junio junho",
    "luglio july jul juli juillet julio julho",
    "agosto august aug aout",
    "settembre september sept sep septembre septiembre setembro",
    "ottobre october oct oktober octobre octubre outubro",
    "novembre november nov noviembre novembro",
    "dicembre december dec dezember decembre diciembre dezembro",
]
MONTH = {name: number for number, names in enumerate(_MONTHS, start=1) for name in names.split()}
_NUMBER = re.compile(r"\d(?:[\d.,   ]*\d)?")
_STOP_WORDS = {"the", "del", "della", "dei", "di", "von", "van", "de", "la", "le", "il", "and"}
SNIPPET = "[search result:"  # how chain.py marks search result snippets (pages not read)


def _flat(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _numbers(text: str) -> set[str]:
    """The numbers of a text, as digits only: both whole with their separators ("1 234" → 1234, "20,65" → 2065) and
    their groups ("1", "234"), without leading zeros."""
    numbers = set()
    for piece in _NUMBER.findall(text):
        numbers.add(re.sub(r"\D", "", piece).lstrip("0") or "0")
        numbers.update(g.lstrip("0") or "0" for g in re.findall(r"\d+", piece))
    return numbers


def _months(text: str) -> set[int]:
    return {MONTH[p] for p in re.findall(r"[a-z]+", _flat(text)) if p in MONTH}


def value_in_row(value: str, row: str) -> bool:
    """G1. The value given by the model is written in the row it cites: each of its numbers (even with other
    separators), its month (in any language or as a number: "1943-10-11") and, if it has no numbers, its words. The
    model may translate a date, not change it (11 Nov, control questions: the value was never compared with the
    row)."""
    value, row = str(value or "").strip(), str(row or "")
    if not value:
        return False
    pieces = _NUMBER.findall(value)
    if pieces:
        its = _numbers(row)
        for piece in pieces:  # whole ("1234" for "1 234") or group by group ("21", "1886" for "21, 1886")
            whole = re.sub(r"\D", "", piece).lstrip("0") or "0"
            # by groups only if in the value they are separated by a space ("21, 1886", "1 234"): "9,7" is not "9 … 7"
            to_groups = bool(re.search(r"\s", piece)) and all(
                (g.lstrip("0") or "0") in its for g in re.findall(r"\d+", piece))
            if whole not in its and not to_groups:
                return False
        # The month: the same if the row names one; a row without recognised months ("1943-10-11", or a language
        # not listed here: "26 Ιουλίου 1985") cannot contradict it, day and year are enough.
        months, of_row = _months(value), _months(row)
        return not months or not of_row or bool(months & of_row)
    words = [p for p in re.findall(r"\w+", _flat(value)) if len(p) >= 3 and p not in _STOP_WORDS]
    row_in = set(re.findall(r"\w+", _flat(row)))
    return bool(words) and all(p in row_in for p in words)


def level(proof) -> str:
    """G4. A = Wikipedia article read; B = another page read; C = search result snippet (page never read): it can
    propose a candidate, not confirm one."""
    if proof is None:
        return ""
    if str(proof.text).startswith(SNIPPET):
        return "C"
    host = (urlsplit(str(proof.url)).hostname or "").lower()
    return "A" if host == "wikipedia.org" or host.endswith(".wikipedia.org") else "B"


def _key(name: str) -> str:
    return re.sub(r"[\W_]+", "", _flat(str(name)))


def _identity(a) -> set:
    """How to recognise the same entity: the full name (with parentheses: "I promessi sposi (Petrella)" is not "I
    promessi sposi", "Solférino (Landes)" is not "Solferino"), the title and address of its Wikipedia articles."""
    marks = {_key(a.name)}
    if getattr(a, "title", None):
        marks.add(_key(a.title[1]))
    marks.update(_key(article.title) for article in (getattr(a, "articles", None) or {}).values())
    return marks - {""}


def repeats_ancestor(a, node) -> bool:
    """G3. The candidate is already on the route leading to `node` (inclusive): a chain does not return to an entity
    it already crossed (11 Nov, C++: Stroustrup → Wheeler → … → Burrows → Wheeler, "advisor of" read backwards)."""
    my = _identity(a)
    while node is not None:
        if my & _identity(node):
            return True
        node = node.parent
    return False


def _ties(a) -> list[dict]:
    return [e for e in a.outcomes if e.get("outcome") == "yes" and e.get("proof") is not None
            and str(e.get("check", "")).startswith("is related")]


def tie_only_from_snippets(a) -> bool:
    """G4. The tie to the previous link is proven only by search snippets (11 Nov, PostgreSQL: "born in Milton, New
    Hampshire" in the snippet of a page never read, a "strong" link at 0.9 against the Wikipedia infobox)."""
    ties = _ties(a)
    return a.parent is not None and bool(ties) and all(level(e["proof"]) == "C" for e in ties)


_INFOBOX_ROW = re.compile(r"\[(?P<article>[^\]]+)\]\s*[^.:\[\]]{1,40}:\s")


def _from_infobox(a) -> bool:
    """The tie row is a "Label: value" row of the previous link's article (its infobox): the source says exactly
    "the place of birth is this". A sentence naming several candidates (derivatives, students) or the candidate's own
    article does not say that."""
    found = _INFOBOX_ROW.match(str(a.tie_proof.text))
    return found is not None and _key(re.sub(r"\([^)]*\)", " ", a.parent.name)) in _key(found.group("article"))


def disagreements(discarded: list, relation: str) -> list[dict]:
    """G7. The candidates discarded by the checks that the previous article's infobox (level A) pointed to: the
    source says one thing, the question's clues another. They are logged instead of lost."""
    articles = []
    for a in discarded:
        if a.parent is None or a.tie_proof is None or level(a.tie_proof) != "A" or not _from_infobox(a):
            continue
        against = [str(e.get("check", "")) for e in a.outcomes if e.get("outcome") == "no" and e.get("proof") is not None]
        if against:
            articles.append({"relation": relation, "parent": a.parent.name, "name": a.name, "url": a.tie_proof.url,
                         "row": " ".join(str(a.tie_proof.text).split())[:300], "against": against[:4]})
    return articles


def _strong_tie(a) -> bool:
    return level(a.tie_proof) == "A" or any(level(e["proof"]) == "A" for e in _ties(a))


def disagreements_on_route(articles: list[dict], link) -> list[dict]:
    """The disagreements that touch the solution's route: from the same starting link a level-A source pointed to
    one candidate, and the chain chose another with a weaker source. If the chosen one also has its tie in a level-A
    source they are just siblings (Galileo's students), not a disagreement: on the replay of the 40 questions,
    without this condition, 33 chains out of 39 had "disagreements"."""
    route, a = [], link
    while a is not None:
        route.append(a)
        a = a.parent
    weak_ones = {_key(a.parent.name): _key(a.name) for a in route if a.parent is not None and not _strong_tie(a)}
    return [x for x in articles if _key(x["parent"]) in weak_ones and weak_ones[_key(x["parent"])] != _key(x["name"])]


# --- checks with the model: the model proposes, these functions decide -------------------------------------------

_LESS = re.compile(r"(?<![\w.,])(?<!\d\s)[−–-]\s?\d|sotto il livello del mare|below sea level", re.I)


def _whole(text: str) -> set[str]:
    """The numbers of a value, without those attached to a letter ("km2") and without joining "21, 1886"."""
    return {re.sub(r"\D", "", p).lstrip("0") or "0"
            for p in re.findall(r"(?<![^\W\d_])\d+(?:[.,]\d+)*", str(text or ""))}


def same_value(a: str, b: str) -> bool:
    """Two values say the same thing: same numbers, same sign and same month (in any language); without numbers,
    same text. "56 m s.l.m." = "56 metri"; "21 giugno 1886" = "June 21, 1886"; "−2 m" ≠ "2 m"."""
    # The value is what is written before a note ("10 marzo 1682 (data di istituzione); alcune fonti…").
    short_to, short_b = (re.split(r"\s*[(;]|\s+[—–-]\s+", str(x or "").strip(), maxsplit=1)[0] for x in (a, b))
    numbers_to, numbers_b = _whole(short_to), _whole(short_b)
    if numbers_to or numbers_b:
        months_to, months_b = _months(short_to), _months(short_b)
        return (numbers_to == numbers_b and bool(_LESS.search(str(a))) == bool(_LESS.search(str(b)))
                and (not months_to or not months_b or months_to == months_b))
    return bool(_key(short_to)) and _key(short_to) == _key(short_b)


def examine_review(answer: dict, rows: list) -> dict:
    """G6. What is left of the review after the program's check: a "no" or a "yes" counts only with the number of a
    given row and words that really are in that row; otherwise the clue is "unproven"."""
    from ..proofs.triples import present_quote
    objections, proven, unproven = [], 0, 0
    for clue in answer.get("clues") or []:
        number, outcome = clue.get("row"), clue.get("outcome")
        row = rows[number - 1] if isinstance(number, int) and 1 <= number <= len(rows) else None
        is_valid = row is not None and present_quote(str(clue.get("quote") or ""), row.text)
        if outcome == "no" and is_valid:
            objections.append({"clue": str(clue.get("clue", ""))[:200], "url": row.url,
                              "quote": str(clue.get("quote", ""))[:200]})
        elif outcome == "yes" and is_valid:
            proven += 1
        else:
            unproven += 1
    # The question's premise: "refuted" only with the words of a given row, like the clues.
    said = answer.get("premise") or {}
    number = said.get("row")
    row = rows[number - 1] if isinstance(number, int) and 1 <= number <= len(rows) else None
    # "absent": the review did not address the premise (reviews recorded before 13 Nov): unknown.
    premise = {"outcome": said.get("outcome") if said.get("outcome") in ("holds", "refuted", "unproven") else "absent"}
    if premise["outcome"] == "refuted":
        if row is not None and present_quote(str(said.get("quote") or ""), row.text):
            premise.update(what=str(said.get("what", ""))[:200], quote=str(said.get("quote", ""))[:200],
                            url=row.url)
        else:
            premise["outcome"] = "unproven"
    return {"outcome": "done", "holds": answer.get("holds", "unproven"), "objections": objections,
            "proven": proven, "unproven": unproven, "premise": premise,
            "starting_point_name": " ".join(str(answer.get("starting_point_name") or "").split())[:120],
            "reason": str(answer.get("reason", ""))[:300]}


def refuted_premise(exams: dict | None) -> dict | None:
    """The question's premise refuted by the review with a row (13 Nov, questions without an answer: the chain gave a
    value in 15 cases out of 20, following a "similar enough" route): then no value is given."""
    premise = ((exams or {}).get("review") or {}).get("premise") or {}
    return premise if premise.get("outcome") == "refuted" else None


STATUSES = ("verified", "supported", "contested", "uncertain", "not found")
CONFIDENCE = {"verified": "high", "supported": "medium", "contested": "low", "uncertain": "low"}


def status(c: dict) -> tuple[str, list[str]]:
    """The answer's status from the outcomes of the checks (docs/validation.md §4) and the reasons, in words."""
    if not c.get("value"):
        return "not found", []
    exams = c.get("exams") or {}
    reading, corroboration, review = (exams.get(k) or {} for k in ("second_reading", "corroboration", "review"))
    uncertain, contested, limits = [], [], []
    # A weak link (checks not all proven) leads to "uncertain", unless the review of the whole chain holds with no
    # objections: then it stays a limitation and the answer is at most "supported" (calibration of 13 Nov on the 40
    # questions: weak with a review that holds, 6 right and 0 wrong; weak without, 16 and 6).
    holds = review.get("outcome") == "done" and review.get("holds") == "yes" and not review.get("objections")
    if c.get("weak_links") and holds:
        limits.append(f"{c['weak_links']} links with checks not all proven, but the whole chain holds up under review")
    elif c.get("weak_links"):
        uncertain.append(f"{c['weak_links']} chain links not confirmed")
    if c.get("value_source") == "C":
        uncertain.append("the value comes from a search snippet, from a page not read")
    for o in review.get("objections") or []:
        uncertain.append(f"the review refutes \"{o['clue']}\" (\"{o['quote']}\")")
    for d in c.get("disagreements") or []:
        contested.append(f"for \"{d['relation']}\" the article on {d['parent']} gives {d['name']}")
    if reading.get("outcome") == "disagree":
        contested.append(f"a second reading of the same rows gives {reading.get('value')}")
    if corroboration.get("outcome") == "disagree":
        contested.append(f"another source gives {corroboration.get('value')}")
    if uncertain:
        return "uncertain", uncertain + contested
    if contested:
        return "contested", contested
    if not exams:
        return "supported", limits + ["model checks not done"]
    if reading.get("outcome") not in ("agree", "not needed"):
        limits.append("the second reading did not find the value again")
    if corroboration.get("outcome") != "agree":
        limits.append("the value comes from a single source")
    if review.get("outcome") != "done":
        limits.append("review not done")
    elif review.get("holds") != "yes":
        limits.append(f"the review does not find {review.get('unproven', 0)} clues of the question proven")
    return ("supported", limits) if limits else ("verified", [])


def rejected_route(exams: dict | None, weak_links: int) -> dict | None:
    """Three things that do not add up together: the review has an objection with a row, does not find the
    question's premise proven, and the route has an unconfirmed link. Then no value is given (returns the objection).
    With a premise that holds the question makes sense and the answer stays "uncertain" (calibration of 13 Nov: 0
    right answers lost)."""
    review = (exams or {}).get("review") or {}
    objections = review.get("objections") or []
    if not weak_links or not objections or (review.get("premise") or {}).get("outcome") != "unproven":
        return None
    return objections[0]


_SEVERITY = {"yes": 0, "unproven": 1, "no": 2}


def merge_reviews(reviews: list[dict]) -> dict:
    """Of several reviews of the same chain the strictest counts (15 Nov: repeating the questions the status changed
    in 17 cases out of 36, because a single review swings between "holds" and "unproven"): the worst verdict, all the
    objections with a row, the premise "holds" only if it holds for all."""
    done = [r for r in reviews if r.get("outcome") == "done"]
    if not done:
        return {"outcome": "not done"}
    worst = max(done, key=lambda r: _SEVERITY.get(r.get("holds"), 1))
    objections: list[dict] = []
    for r in done:
        objections += [o for o in r.get("objections") or [] if o not in objections]
    premises = [r.get("premise") or {"outcome": "absent"} for r in done]
    premise = next((p for p in premises if p.get("outcome") == "refuted"), None) \
        or next((p for p in premises if p.get("outcome") == "absent"), None) \
        or ({"outcome": "holds"} if all(p.get("outcome") == "holds" for p in premises) else {"outcome": "unproven"})
    return {**worst, "objections": objections, "premise": premise, "readings": len(done),
            "proven": min(r.get("proven", 0) for r in done), "unproven": max(r.get("unproven", 0) for r in done),
            "starting_point_name": next((r["starting_point_name"] for r in done if r.get("starting_point_name")), "")}


def missing_name(name: str, question: str, texts: list[str]) -> list[str]:
    """The capitalised words of the name the question uses for its starting point that are in no page read. Only if
    the name is copied from the question. With several words the first does not count ("Battaglia di Hastings" read
    in English: "Battle of Hastings"). 13 Nov, questions without an answer: for 8 invented entities out of 8 the
    chain had given a value, starting from a nearby entity; "Zorvex" or "Aurelia" are in no page."""
    name = " ".join(str(name or "").split())
    if not name or name not in " ".join(str(question or "").split()):
        return []
    owned = [p for p in re.findall(r"[^\W\d_]{4,}", name) if p[0].isupper()]
    if len(owned) > 1:
        owned = owned[1:] if name.startswith(owned[0]) else owned
    read = set(re.findall(r"\w+", _flat(" ".join(texts))))
    return [p for p in owned if _flat(p) not in read]


def with_confidence(text: str, status: str, reasons: list[str]) -> str:
    """The answer text with the **Confidence:** line written by the program from the status of the checks. An old
    **Fiducia:** line (answers saved in Italian) is replaced too."""
    if status not in CONFIDENCE:
        return text
    row = (f"**Confidence:** {CONFIDENCE[status]} — {status} answer"
            + (": " + "; ".join(reasons[:4]) if reasons else
               ": value read twice, found again in another source, chain reread with no objections") + ".")
    found = re.search(r"\*\*(?:Confidence|Fiducia):?\*\*:?[^\n]*", text)
    return text[:found.start()] + row + text[found.end():] if found else f"{text}\n\n{row}"
