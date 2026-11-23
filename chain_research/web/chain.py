"""Guided chain (15 Oct, Niccolò's decision): the graph and the program drive the reasoning, the model only does small,
checkable tasks. Designed so that even small models can do chain research.

1. Claude interprets the question (rephrasings, clues with their possible readings, sub-questions) and turns it into a
   chain: a certain starting point, steps (relation to follow, kind of result, checks with the readings), answer.
2. The program walks the chain one link at a time, with a beam of at most `width` entities:
   - for each entity of the beam: searches for the relation (point "step"), pages read with the normal reading
     (concepts, ties and facts enter the graph);
   - the model lists the candidates that the numbered proofs indicate for the relation (point "candidates");
   - the model says for each candidate and check "yes / no / don't know" with the proof number (point
     "verification"); a "no" with a proof discards the candidate; for the best candidates with some "don't know" the
     infobox is looked up and the verification is redone, once;
   - the candidates without a "no" remain, sorted by number of "yes"; if none remains the step is retried with
     different searches, once, then the chain stops there.
3. The requested value is taken from the proofs of the final entity (point "value"), looking up the infobox if missing.
4. The writer receives the chain found, with the proofs of each link.
Proofs are numbered rows built by the program from the graph and from the saved page segments: the model gives a
number, it does not copy sentences, and every claim stays tied to its page.
"""
import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from urllib.parse import unquote, urlsplit

from ..model.thinker import instructions
from ..proofs.triples import present_quote
from . import validation

from .sources.wikipedia_graph import (DEGREES, LANGUAGE, Row, Article, WikipediaGraph, _plan, matches,
                                    without_corporate_suffix)

MAX_PROOFS = 80
log = logging.getLogger(__name__)
STOP_WORDS = {"della", "delle", "dello", "degli", "dalla", "nella", "sulla", "come", "anche", "questo", "quella",
                "quello", "sono", "essere", "stato", "stata", "with", "from", "that", "which", "their", "about",
                "entità", "relazione", "ricerca", "anni"}
PROOF_CHARS = 45_000
# Steps towards things that usually have neither a proper name nor a page: they can be skipped (`_jump_plan`).
NAMELESS_OBJECTS = re.compile(r"\b(simulacr\w*|statu[ae]\w*|dipint\w*|scultur\w*|affresc\w*|oggett\w*|"
                                r"immagin\w*|ritratt\w*|reliqui\w*|statue|painting|sculpture|artwork|object)\b")
# Roots (5 letters) of words of kind or size, which in the checks do not tell one row from another.
GENERIC_ROOTS = {"paese", "comun", "citta", "picco", "grand", "celeb", "famos", "local", "terri", "luogo", "perso",
                    "azien", "organ", "socie", "compa", "town", "villa", "city", "small", "large", "frazi", "borgo",
                    "stato", "regio", "nazio", "itali", "franc", "ameri", "tedes", "ingle", "inter", "milan"}
# Infobox rows without useful relations (ToG `abandon_rels`: IDs, codes, sites, images).
USELESS_INFOBOX = re.compile(r"^(immagine|image|logo|firma|signature|mappa|map|coordinate|coordinates|sito( web)?|"
                              r"website|url|codice|code|id|isbn|issn|iso|didascalia|caption|stemma|bandiera|flag|"
                              r"coat of arms|cap|prefisso|fuso orario|time zone|targa|zip code|postal code|"
                              r"area code|dialing code|codice istat|codice catastale|nome abitanti)\b[^:]{0,20}:", re.I)
MAX_CANDIDATES = 8
# Four-valued weighted verification (WisPaper, 2026): each outcome counts as much as the weight of the check; the
# program does the sum. Generic models say "yes" too easily and fail precisely on the in-between cases, hence "partly",
# and hence a "yes" counts only if both readings give it.
OUTCOME_VALUE = {"yes": 1.0, "partly": 0.5, "unknown": 0.0, "no": -1.0}
MONTHS = ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|"
        "january|february|march|april|may|june|july|august|september|october|november|december|"
        "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec|"
        "januar|februar|märz|mai|juni|juli|oktober|dezember|"
        "janvier|février|mars|avril|juin|juillet|août|septembre|octobre|novembre|décembre|"
        "enero|febrero|abril|mayo|junio|julio|septiembre|octubre|noviembre|diciembre|"
        "janeiro|fevereiro|março|maio|junho|julho|setembro|outubro|novembro|dezembro")
# Years with the era written out: «44 a.C.», «800 d.C.», «44 BC», «800 AD».
ERA = re.compile(r"\b(\d{1,4})\s*(a\.?\s?c\b\.?|b\.?\s?c\b\.?(?:e\b\.?)?|d\.?\s?c\b\.?|a\.?\s?d\b\.?|c\.?\s?e\b\.?)", re.I)
_ORDINAL = r"(?:°|º|st|nd|rd|th|er|\.)?"


@dataclass
class Proof:
    text: str
    url: str
    hyperlinks: list[str] = field(default_factory=list)  # Wikipedia articles linked in the row
    language: str = ""  # language of the Wikipedia article the row comes from


@dataclass
class Link:
    name: str
    points: int = 0  # number of checks with "yes" minus those with "no"
    quality: float = 0.0  # weighted sum of the outcomes divided by the sum of the weights, from -1 to 1
    outcomes: list[dict] = field(default_factory=list)  # {check, outcome, proof: Proof | None}
    parent: "Link | None" = None
    tie_proof: Proof | None = None  # the sentence that ties it to the previous link
    weak: bool = False  # kept only because no other candidate passed the checks
    context: str = ""  # how the chain reached it, for searches without namesakes ("progettista di Freedo")
    title: tuple[str, str] | None = None  # (language, title) of the Wikipedia article, if reached through a link
    articles: dict[str, Article] = field(default_factory=dict)  # language → Wikipedia article of the entity
    searched_articles: bool = False
    articles_other: int = -1  # how many other languages were already searched besides the first article
    start: "Proof | None" = None  # first sentences of the Wikipedia article (cheap verification)
    failures: float = 0.0  # frontier: branches from here that led nowhere
    preferred: float = 0.0  # frontier: bonus from the final tiebreak between tied candidates
    empty_wiki: bool = False  # expanded on Wikipedia without any candidate (the web is the only route)
    base_step: int = 0  # anchor: how many plan steps it already covers (0 for the starting point)
    relation: str = ""  # the relation followed to get here (for the anchor, the one starting from it)
    plan: dict | None = None  # frontier roots: the plan whose steps they follow


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def _entity_key(name: str) -> str:
    """A key for the variants of the same name: lowercase, without accents, spaces or punctuation («Keyboard.io» =
    «Keyboardio»; graphify: normalisation first of all). «Keyboard.io Model01» stays another entity."""
    return re.sub(r"[\W_]+", "", _plan(without_corporate_suffix(name)))


def _name_key(name: str) -> str:
    """The last long word of the name (usually the surname), to find it in texts even when written differently.
    Without the parentheses (24 Oct, Monte Bianco: «La Thuile (Italia)» gave «italia», and the article taken was «La
    Salle (Italia)», the first link of the row with «italia»)."""
    without = _norm(re.sub(r"\([^)]*\)", " ", name)) or _norm(name)
    words = [p for p in without.split() if len(p) >= 4]
    return words[-1] if words else without


def _weight(e: dict) -> float:
    return float(e.get("weight") or 1)


def quality(outcomes: list[dict]) -> float:
    """Weighted sum of the outcomes divided by the sum of the weights: 1 = all checks proven, -1 = all refuted."""
    total = sum(_weight(e) for e in outcomes)
    return sum(_weight(e) * OUTCOME_VALUE.get(e["outcome"], 0.0) for e in outcomes) / total if total else 0.0


def _numbers(text: str) -> list[float]:
    """The numbers of a value in all possible readings (20,65 / 3.274 / 1,234.5): it is enough for one to fall within
    the interval."""
    hits = []
    for m in re.finditer(r"\d(?:[\d.,\u202f\u00a0 ]*\d)?", str(text)):
        t = re.sub(r"[\u202f\u00a0 ]", "", m.group(0))
        for x in {t.replace(".", "").replace(",", "."), t.replace(",", ""), t.replace(",", ".")}:
            try:
                hits.append(float(x))
            except ValueError:
                pass
    return hits


def _years(text: str) -> list[int]:
    """The years written in the text; BC years are negative. Without the era at least 3 digits are needed."""
    years = []
    for m in ERA.finditer(text):
        first = m.group(2).lower().replace(".", "").replace(" ", "") in ("ac", "bc", "bce")
        years.append(-int(m.group(1)) if first else int(m.group(1)))
    rest = ERA.sub(" ", text)
    return years + [int(x) for x in re.findall(r"\b\d{3,4}\b", rest)]


def allowed_value(value: str, answer: dict) -> str:
    """Mechanical check of the value (DS4A: the data kind decides which rows count): kind and interval given by the
    plan. Returns the reason for the rejection, empty if fine. (17 Oct, Two Sicilies: «29 novembre 1943» for a State
    «founded and dissolved in 1945»; Divina Commedia: 3,274 inhabitants for «between 300 and 350».)"""
    kind = str(answer.get("value_kind") or "")
    text = str(value or "").lower()
    years = _years(text)
    month_day = re.search(rf"\b\d{{1,2}}{_ORDINAL}\s+(?:(?:of|de|del)\s+)?(?:{MONTHS})\b", text) or re.search(
        rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", text) or re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if kind == "full date" and not (month_day and years):
        return "not a full date"
    if kind == "year" and not years:
        return "contains no year"
    if kind == "day and month" and not month_day:
        return "contains no day and month"
    if kind in ("number", "code") and not re.search(r"\d", text):
        return "contains no number"
    minimum, maximum = answer.get("min"), answer.get("max")
    if not isinstance(minimum, (int, float)) and not isinstance(maximum, (int, float)):
        return ""
    minimum = float(minimum) if isinstance(minimum, (int, float)) else float("-inf")
    maximum = float(maximum) if isinstance(maximum, (int, float)) else float("inf")
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    numbers = [float(x) for x in years] if kind in ("full date", "year") else _numbers(text)
    if kind in ("full date", "year", "number", "code") and numbers \
            and not any(minimum <= x <= maximum for x in numbers):
        return f"outside the interval {minimum:g}-{maximum:g}"
    return ""


class Chain:
    def __init__(self, research) -> None:
        self.r = research
        self.o = research.o
        self.interpretation: dict = {}
        self.plan: dict = {}
        self.links: list[list[Link]] = []  # beam kept at each step
        self.value: dict | None = None  # {value, proof: Proof, link: Link}
        self.reason = ""
        self.step_searches: list[str] = []
        self.history: list[dict] = []  # for the report and the diagnoses
        # Wikipedia as a graph (16 Oct): articles = nodes, links = edges; before searching the web.
        self.wiki = WikipediaGraph(local_allow=research.local_allow,
                                   max_articles=int(self.o.get("wikipedia_articles", 600)),
                                   interval_s=self.o.get("wikipedia_interval")) \
            if self.o.get("wikipedia_graph", True) else None
        self._segment_numbers: dict[str, int] = {}
        self._alive: list[Link] = []  # unrefuted candidates of the last step walked
        self.actions: list[str] = []  # frontier: actions done, in order (for the diagnoses)
        self._recorded: set[tuple[str, str, str]] = set()  # ties written by _record: never proofs
        self._snippets: list[Proof] = []  # snippets of the results of the last search (pages not read)
        self.conflicts: list[dict] = []  # discarded candidates that had a strong source (validation.disagreements)
        self.exams: dict | None = None  # checks with the model on the solution (final_review)
        self.discarded: dict | None = None  # the value found and not given: the review refuted the premise
        self._added_pages = 0  # pages granted beyond the cap to look for the value

    # --- model calls ----------------------------------------------------------------------------------------------

    def _decide(self, point: str, data: dict, default: dict) -> dict:
        outcome, parent = self.r.thinker.decide(point, data, lambda: default)
        self.r.cost = max(self.r.cost, self.r.thinker.ledger.spent)
        return outcome if parent == "claude" else default

    # --- 1. interpretation and chain -----------------------------------------------------------------------

    def prepare(self) -> bool:
        self.r._phase("Claude is interpreting the question…")
        self.interpretation = self._decide("interpret", {"question": self.r.topic[:1000], "today": self.r.today},
                                            {"rephrasings": [], "clues": [], "sub_questions": []})
        self.r._phase("Claude is preparing the chain to walk…")
        for _ in range(2):  # 25 Oct, LibreOffice: «Claude did not answer in time» on the plan, chain never started
            self.plan = self._decide("chain", {"question": self.r.topic[:1000], "today": self.r.today,
                                                 "interpretation": self.interpretation},
                                      {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
            ok = bool(self.plan.get("steps")) and bool((self.plan.get("starting_point") or {}).get("entity"))
            if ok or self.r.thinker.exhausted:
                break
        self.r.publish({"kind": "chain_search", "interpretation": self.interpretation, "plan": self.plan})
        return ok

    # --- proofs from the graph -----------------------------------------------------------------------------------

    def _after_segments(self, last: int) -> list[dict]:
        return [dict(r) for r in self.r.concepts.graph.db.execute(
            "SELECT s.id, s.text, f.url FROM segment s JOIN source f ON f.id = s.source_id WHERE s.id > ? "
            "ORDER BY s.id", (last,))]

    def _last_segment(self) -> int:
        row = self.r.concepts.graph.db.execute("SELECT MAX(id) FROM segment").fetchone()
        return int(row[0] or 0)

    def _find(self, name: str) -> int | None:
        """The graph node for a name, even written slightly differently (surname contained in the name)."""
        graph = self.r.concepts.graph
        match = graph.find(name)
        if match is not None:
            return match
        key = _name_key(name)
        if len(key) < 4:
            return None
        for row in graph.db.execute("SELECT id, name FROM entity"):
            if key in _norm(row["name"]).split():
                return int(row["id"])
        return None

    def _windows(self, text: str, key: str, radius: int = 450, maximum: int = 3) -> list[str]:
        low, windows, start = text.lower(), [], 0
        while len(windows) < maximum:
            i = low.find(key, start)
            if i < 0:
                break
            a, b = max(0, i - radius), min(len(text), i + len(key) + radius)
            windows.append(("…" if a else "") + " ".join(text[a:b].split()) + ("…" if b < len(text) else ""))
            start = b
        return windows

    def _entity_proofs(self, name: str, segments: list[dict] | None = None, maximum: int = 30) -> list[Proof]:
        """Facts, ties and mentions of the node in the graph, plus the areas of the page segments where the name appears."""
        proofs: list[Proof] = []
        c = self.r.concepts
        node = self._find(name)
        if node is not None:
            for f in c.facts():
                if f["concept_id"] == node:
                    proofs.append(Proof(f"{f['concept']} — {f['attribute']}: {f['value']} «{f['sentence']}»", f["url"]))
            url_of = {r["id"]: r["url"] for r in c.graph.db.execute(
                "SELECT s.id, f.url FROM segment s JOIN source f ON f.id = s.source_id")}
            for t in c.ties():
                # Never the ties written by the chain itself: they are conclusions, not proofs (24 Oct, Monte Bianco:
                # the first plan records «Saint-Gervais → twinned with → Vado Ligure», a weak link cited with the start
                # of the Vado Ligure article; the second plan reads it as a proof and Vado Ligure becomes «strong»).
                if (_norm(t["subject"]), _norm(t["relation"]), _norm(t["object"])) in self._recorded:
                    continue
                if node in (t["subject_id"], t["object_id"]) and t.get("quote"):
                    proofs.append(Proof(f"{t['subject']} → {t['relation']} → {t['object']} «{t['quote']}»",
                                       url_of.get(t["segment_id"], "")))
            for d in c.where(node)[:10]:
                proofs.append(Proof(f"«{d['sentence']}»", d["url"]))
        key = _name_key(name)
        all_items = segments if segments is not None else self._after_segments(0)
        for s in all_items:
            for window in self._windows(s["text"], key, maximum=2):
                proofs.append(Proof(window, s["url"]))
        seen_items, unique = set(), []
        for p in proofs:
            if p.text not in seen_items:
                seen_items.add(p.text)
                unique.append(p)
        return unique[:maximum]

    def _step_proofs(self, entity: str, segments: list[dict], already: list[dict]) -> list[Proof]:
        """To list the candidates: in full the segments of the pages found by the searches of THIS step (they were
        searched for the relation: lists of students, infoboxes), the already saved ones only if the page is about the
        entity (name in the address), the others around the name; plus facts and ties of the node.
        (15 Oct: Galileo's Mathematics Genealogy page was read but the list of students was left out.)"""
        key = _name_key(entity)
        proofs = self._entity_proofs(entity, segments=[], maximum=25)
        for s in segments:
            proofs.append(Proof(" ".join(s["text"].split())[:4000], s["url"]))
        new_items = {s["id"] for s in segments}
        for s in already:
            if s["id"] in new_items:
                continue
            if key in _norm(unquote(urlsplit(s["url"]).path)).replace("_", " "):
                proofs.append(Proof(" ".join(s["text"].split())[:4000], s["url"]))
            else:
                proofs += [Proof(f, s["url"]) for f in self._windows(s["text"], key, radius=600)]
        kept, chars = [], 0
        for p in proofs[:MAX_PROOFS]:
            if chars + len(p.text) > PROOF_CHARS:
                continue
            kept.append(p)
            chars += len(p.text)
        return kept

    @staticmethod
    def _numbered(proofs: list[Proof]) -> str:
        return "\n".join(f"[{i}] {p.text[:4000]}" for i, p in enumerate(proofs, start=1))

    @staticmethod
    def _proof(proofs: list[Proof], number: int) -> Proof | None:
        return proofs[number - 1] if isinstance(number, int) and 1 <= number <= len(proofs) else None

    # --- search and reading of a group of pages ---------------------------------------------------------------

    def _search_and_read(self, searches: list[str], pages: int, purpose: str = "") -> list[dict]:
        """Searches, reads up to `pages` pages found by THESE searches and returns the new saved segments. `purpose`:
        the step being searched for, to choose the pages (default: the whole question)."""
        self._snippets = []
        searches = [q for q in self.r._filter_query(searches) if q]
        if not searches:
            return []
        last = self._last_segment()
        self.r.searches_done += searches
        self.step_searches += searches
        self.r.publish({"kind": "search_plan", "searches": searches, "parent": "chain"})
        results = self.r.collect(searches)
        # The result snippets are page text and cost zero readings: extra proofs for the candidates (25 Oct: 3 pages
        # out of ~25 results are read; «Keyboardio was founded in 2012 by Jesse Vincent and Kaia Dekker» was in the
        # snippet of a page never read).
        self._snippets = [Proof(f"{validation.SNIPPET} {x.get('title', '')}] {x['snippet']}", x["url"])
                          for x in results if len(x.get("snippet") or "") >= 40][:15]
        self.r._add_candidates(results, purpose=purpose)
        mine = [x for x in self.r.queue if self.r.research_of.get(x.get("url")) in searches]
        self.r.queue = [x for x in self.r.queue if x not in mine]
        for source in mine[:pages]:
            if self.r._reached_limit():
                break
            self.r.read(source)
        self.r._evaluate_completed(wait_for=True)
        return self._after_segments(last)

    # --- Wikipedia as a graph -----------------------------------------------------------------------------------

    def _research_language(self) -> str:
        """The research language (22 Oct, Niccolò): English, except for questions tied to a country (the Sistine Chapel
        in Italian, a Greek village in Greek), as decided in the plan."""
        language = str(self.plan.get("research_language") or "en").lower()
        return language if LANGUAGE.match(language) else "en"

    def _languages(self) -> list[str]:
        """Research language, English, the plan's languages and always Italian, last (24 Oct, LibreOffice: with the
        research in English the Italian BeOS article, the one with «Release corrente: R5.1d0 (15 novembre 2001)», was
        never read; the questions were in Italian and Italian infoboxes often have the requested data)."""
        other_ones = [str(x).lower() for x in self.plan.get("languages") or [] if LANGUAGE.match(str(x).lower())]
        return list(dict.fromkeys([self._research_language(), "en", *other_ones, "it"]))[:5]

    @staticmethod
    def _context_words(text: str) -> list[str]:
        return [p for p in _norm(text).split() if len(p) >= 5 and p not in STOP_WORDS][:8]

    def _relevant(self, article: Article, a: Link) -> bool:
        """Against namesakes: the article of an entity reached by the chain must link the article of the previous
        entity (in that language) or name it in full; if the previous entity has no articles, the surname or a context
        word is enough. (16 Oct: «René II de Lorraine-Guise» led to the Duke of Lorraine born in Angers, not to René
        d'Elbeuf son of Claude de Guise; 15 Oct: «Freedo» to the Swiss producer, not to the mascot.)"""
        if a.title is not None:
            return True
        if a.parent is None:  # starting point: the article must share at least one clue word (Freedo: mascot)
            roots = {p[:4] for p in _norm(a.context).split() if len(p) >= 5 and p not in STOP_WORDS}
            roots |= {p[:4].lower() for p in re.findall(r"\b[A-Z][\w-]{2,}", a.context)}
            # The name itself does not count: it appears in every namesake (22 Oct, Freedo: the English article of the
            # Swedish producer passed because it contained «Freedo»).
            roots -= {p[:4] for p in _norm(a.name).split()}
            if not roots:
                return True
            text = {p[:4] for p in _norm(" ".join(r.text for r in article.rows[:60])).split()}
            return bool(roots & text)
        first = a.parent.articles
        if first:
            titles = {v.title for v in first.values() if v.language == article.language}
            titles |= {t for v in first.values() for lg, t in v.languages.items() if lg == article.language}
            if any(t in titles for r in article.rows for t in r.hyperlinks):
                return True
            text = _norm(" ".join(r.text for r in article.rows))
            return bool(_norm(a.parent.name)) and _norm(a.parent.name) in text
        text = _norm(" ".join(r.text for r in article.rows))
        keys = {_name_key(a.parent.name), *self._context_words(a.context)}
        return any(k and k in text for k in keys)

    def _articles(self, a: Link, other_ones: int = 2) -> list[Article]:
        """The entity's articles: the first one found (Italian, English, the plan's languages) and up to `other_ones`
        in other languages, the plan's first (the article in the local language has more local details). Few
        requests: Wikipedia allows 10 per minute."""
        if self.wiki is None:
            return []
        # An article read for the verification (one language) is not enough for the value: the other languages are
        # added later (18 Oct, Debian: the W3C in Italian gives only «ottobre 1994», the full date in English never
        # arrived).
        if a.searched_articles and (other_ones <= a.articles_other or not a.articles):
            return list(a.articles.values())
        if a.searched_articles:
            first = next(iter(a.articles.values()))
            a.articles_other = other_ones
            for language in self._languages()[2:] + self._languages()[:2]:
                if len(a.articles) > other_ones:
                    break
                if language not in a.articles and language in first.languages:
                    article = self.wiki.article(first.languages[language], language)
                    if article is not None:
                        a.articles[language] = article
            return list(a.articles.values())
        a.searched_articles = True
        a.articles_other = other_ones
        first = None
        if a.title:
            first = self.wiki.article(a.title[1], a.title[0])
        if first is None:
            # 23 Oct (entities and rows plan, phases 2-3): article decided by the graph, not by the «similar» title.
            # For the starting point (no previous entity) the articles without a tie still go through `_relevant`
            # (clue). An entity without an article lends its children the articles of whoever found it (24 Oct,
            # Wikipedia: «Wikivoyage e.V.» has no article; without neighbours the painter «Roland Unger» (1941) passed
            # as the article of the association's president, named without a link in the German Wikivoyage article).
            host = a.parent
            while host is not None and not host.articles and host.parent is not None and host is not a.parent.parent:
                host = host.parent
            neighbours = [(v.language, v.title) for v in host.articles.values()] if host is not None else []
            first = self.wiki.choose_article(
                a.name, self._languages(), context=a.context, neighbours=neighbours,
                research_words=" ".join(self._context_words(a.context)[:3]),
                accept=(lambda v: self._relevant(v, a)) if a.parent is None else None,
                tiebreak=lambda name, options: self._article_tiebreak(a, options))
        if first is None:
            return []
        a.articles[first.language] = first
        order = self._languages()[2:] + self._languages()[:2]
        for language in order:
            if len(a.articles) > other_ones:
                break
            if language not in a.articles and language in first.languages:
                article = self.wiki.article(first.languages[language], language)
                if article is not None:
                    a.articles[language] = article
        return list(a.articles.values())

    def _article_tiebreak(self, a: Link, options: list[tuple[str, str]]) -> int | None:
        """Two articles with the same tie and the same degree: closed numbered list (GENRE: only valid titles or
        «none»; DeepDive: answer = number). 0 = none."""
        listing = "\n".join(f"{i}. {title} — {start}" for i, (title, start) in enumerate(options, 1))
        outcome = self._decide("article", {"name": a.name, "context": a.context[:300],
                                      "first_entity": a.parent.name if a.parent is not None else "",
                                      "articles": listing}, {"choice": 1, "reason": ""})
        choice = int(outcome.get("choice") or 0)
        return None if choice <= 0 or choice > len(options) else choice - 1

    @staticmethod
    def _roots(words: list[str]) -> set[str]:
        return {p[:6] for x in words for p in _norm(x).split() if len(p) >= 3 and p not in STOP_WORDS}

    @staticmethod
    def _terms(text: str) -> list[str]:
        """Roots (5 letters: «succeduto»/«succeeded», «derivate»/«derivative») and pairs of adjacent roots: the TF-IDF
        terms of the rows."""
        roots = [p[:5] for p in _norm(text).split() if len(p) >= 3 and p not in STOP_WORDS]
        return roots + [f"{a}_{b}" for a, b in zip(roots, roots[1:])]

    @staticmethod
    def _merged_rows(v: Article, chars: int = 700) -> list[tuple[int, "Row"]]:
        """The article's rows, with the items of a list in the same section merged into one row (up to 700
        characters): «Distribuzioni derivate: Ubuntu · Knoppix · Bharat Linux …» like «Notable students» of the
        infobox; a long list does not fill by itself the rows given to the model (23 Oct, Debian → Bharat Linux)."""
        output: list[tuple[int, Row]] = []
        for i, r in enumerate(v.rows):
            if r.kind == "elenco" and output:
                j, first = output[-1]
                if first.kind == "elenco" and first.section == r.section \
                        and len(first.text) + len(r.text) + 3 <= chars:
                    output[-1] = (j, Row(f"{first.text} · {r.text}",
                                          first.hyperlinks + [c for c in r.hyperlinks if c not in first.hyperlinks],
                                          "elenco", r.section))
                    continue
            output.append((i, r))
        return output

    def _wiki_rows(self, articles: list[Article], words: list[str], key: str = "", maximum: int = 20,
                    chars: int = 6000, bonus=None, infobox: bool = True, bare: bool = False,
                    exclude: str = "") -> list[Proof]:
        """The most useful rows of the articles for the `words`. 23 Oct (entities and rows plan, phase 4; before: the
        whole infobox and 40 opening sentences always in, raw count, 70 rows: Galileo, 69 rows for 4 useful ones):
        - TF-IDF score with the IDF computed on the rows of these articles (FEVER, `OnlineTfidfDocRanker`), roots and
          pairs of roots; the section title counts as part of the row;
        - the infobox competes with the other rows (small bonus), without the useless rows (ToG `abandon_rels`:
          images, codes, sites, coordinates); the first 2 sentences of the article (the definition) always go in;
        - for the verification (`infobox=False`) the categories row always goes in (Jesuit, born in…);
        - identical rows only once. With `key` only the rows that contain it (articles about something else).
        With `bare` only the words count (no definition, categories, infobox or extra links)."""
        question = {t for x in words for t in self._terms(x)}
        if exclude:  # the entity's name is all over its article: it does not tell rows apart
            name = set(self._terms(exclude))
            question = {t for t in question if t not in name and not any(p in name for p in t.split("_"))}
        if bare:  # a row «of a check» must have its own words, not «paese» or «comune»
            question = {t for t in question if t.split("_")[0] not in GENERIC_ROOTS}
        rows = [(v, i, r) for v in articles for i, r in self._merged_rows(v)
                 if not (key and key not in _norm(r.text))
                 and not (r.kind == "riquadro" and USELESS_INFOBOX.match(r.text))]
        terms = [set(self._terms(r.text)) for _, _, r in rows]
        n = len(rows)
        occurrences: dict[str, int] = {}
        for ts in terms:
            for t in ts & question:
                occurrences[t] = occurrences.get(t, 0) + 1
        max_idf = math.log(n + 1) + 1

        def weight(t: str) -> float:  # 0..1: rare in the rows = more informative
            return (math.log((n + 1) / (occurrences.get(t, 0) + 1)) + 1) / max_idf * (1.5 if "_" in t else 1.0)

        scores, seen_items = [], set()
        start_sentences: dict[str, int] = {}
        infoboxes: dict[str, int] = {}
        for (v, i, r), ts in zip(rows, terms):
            text_key = _norm(r.text)
            if text_key in seen_items:
                continue
            seen_items.add(text_key)
            points = 10 * sum(weight(t) for t in ts & question)
            # the section title counts as part of the row (16 Oct, Galvani: the list of presidents of the Accademia
            # dei XL, rows with only names and dates under the section «Presidenti»)
            points += 20 * sum(weight(t) for t in set(self._terms(r.section)) & question)
            if bare:
                pass
            elif r.kind == "riquadro":
                # The infobox is dense with relations, but for the verification it goes first only with the words of
                # the checks (17 Oct, Duchy of Milan: «Gemellaggi: Linards» was left out behind 25 infobox rows).
                points = points * 1.5 + ((3 if infobox else 1) if points > 0 else 0)
            elif r.kind == "categories":
                points += 0 if infobox else 60
            elif not r.section and r.kind == "sentence":
                # the definition: the first 2 real sentences (not the pronunciation bits «( gal-VAH-nee;», Galvani)
                if len(r.text) >= 60:
                    start_sentences[v.title] = start_sentences.get(v.title, 0) + 1
                points += 60 if len(r.text) >= 60 and start_sentences[v.title] <= 2 else 2
            elif r.hyperlinks:
                points += 1.0  # rows with linked articles: they fill the free slots (candidates are articles)
            if bonus is not None:
                points += bonus(r.text)
            if points > 0.6:
                scores.append((points, v, i, r))
        # The 6 best infobox rows of each article go in anyway (for the candidates): relations like citizenship sit
        # in «Born: … Como, Duchy of Milan» with no words in common (23 Oct, rows oracle).
        if infobox and not bare:
            scores.sort(key=lambda x: -x[0])
            for k, (points, v, i, r) in enumerate(scores):
                if r.kind == "riquadro":
                    infoboxes[v.title] = infoboxes.get(v.title, 0) + 1
                    if infoboxes[v.title] <= 6:
                        scores[k] = (points + 40, v, i, r)
            for v in articles:
                for i, r in self._merged_rows(v):
                    if r.kind == "riquadro" and infoboxes.get(v.title, 0) < 6 and _norm(r.text) not in seen_items \
                            and not USELESS_INFOBOX.match(r.text) and not (key and key not in _norm(r.text)):
                        infoboxes[v.title] = infoboxes.get(v.title, 0) + 1
                        seen_items.add(_norm(r.text))
                        scores.append((40.0, v, i, r))
        scores.sort(key=lambda x: -x[0])
        kept, total = [], 0
        for _, v, i, r in scores:
            text = f"[{v.title} ({v.language}){' § ' + r.section if r.section else ''}] {r.text}"
            if r.hyperlinks:  # the exact titles of the linked articles: the candidate is named like its article
                text += f" ⟶ articles: {'; '.join(r.hyperlinks[:25 if r.kind == 'elenco' else 10])}"
            if len(kept) >= maximum:
                break
            if total + len(text) > chars:
                continue  # a row that is too long does not stop the others
            kept.append((v.language, v.title, i, Proof(text, v.url, list(r.hyperlinks), v.language)))
            total += len(text)
        kept.sort(key=lambda x: (x[0], x[1], x[2]))  # in page order: it reads like a text
        return [x[3] for x in kept]

    def _pages_that_mention(self, a: Link, words: list[str]) -> list[Proof]:
        """If the entity has no article: the rows of the articles that name it (HierSearch, «adjacent passages»)."""
        if self.wiki is None:
            return []
        key = _name_key(a.name)
        context = " ".join(self._context_words(a.context)[:3])
        proofs: list[Proof] = []
        for language in self._languages()[:3]:
            for x in self.wiki.search(f'"{a.name}" {context}'.strip(), language, n=3):
                article = self.wiki.article(x["title"], language)
                if article is not None:
                    proofs += self._wiki_rows([article], words, key=key, maximum=15, chars=5000)
        return proofs

    def _incoming(self, a: Link) -> list[Proof]:
        """Reverse relations: the articles that link to the entity, with their first sentences. Only for poorly linked
        entities (with hundreds of incoming articles the list says nothing)."""
        proofs: list[Proof] = []
        for article in self._articles(a)[:1]:
            titles = self.wiki.incoming(article.title, article.language, n=100)
            if not titles or len(titles) >= 100:
                continue
            starts = self.wiki.starts(titles[:40], article.language)
            for title, text in starts.items():
                proofs.append(Proof(f"[{title} ({article.language}), article linking to {article.title}] {text[:500]}",
                                   self.wiki.url(title, article.language), [title], article.language))
        return proofs

    def _wiki_candidates(self, number: int, step: dict, origin: Link, attempt: int,
                        candidates: dict[str, Link]) -> None:
        """The step on the Wikipedia graph: rows of the entity's article (or of the articles naming it) chosen by the
        program for the words of the relation; the model only points to the candidates with the row number."""
        if self.wiki is None:
            return
        words = list(step.get("words") or []) + [step["relation"], step.get("kind", "")]
        self.r._phase(f"Step {number} on Wikipedia: {step['relation']} {origin.name}…")
        articles = self._articles(origin)
        if attempt == 1:
            # 29 Oct, Sistine Chapel: «Assunta», «Assunzione» among the step's words from the article on the Assumption
            # of Mary: 20 rows on the dogma, the gallery with the towns left out («Celebrazione dell'Assunta a Novara di
            # Sicilia»).
            names = " ".join([origin.name] + [v.title for v in articles])
            proofs = self._wiki_rows(articles, words, exclude=names) if articles else []
            if not articles or len(proofs) < 8:
                proofs += self._pages_that_mention(origin, words)
            if not articles:
                # Without an article: also the already read segments that name it, at zero page cost (24 Oct, Arduino:
                # «Keyboardio is an Oakland-based company founded in 2012 by the husband-and-wife team, Jesse Vincent
                # and Kaia Dekker» was in a page read; the founders were searched only on Wikipedia and then on the
                # web, which never happened).
                proofs += self._step_proofs(origin.name, [], self._segments_on(origin.name))[:12]
        else:
            proofs = self._incoming(origin)
        if not proofs:
            return
        listing = self._decide("candidates", {"entity": origin.name, "relation": step["relation"],
                                            "kind": step.get("kind", ""), "proofs": self._numbered(proofs),
                                            "again": f"{step['relation']} — {origin.name}"},
                              {"candidates": []})["candidates"]
        self._add(listing, proofs, origin, candidates, people=self._of_people(step))

    def _candidates_wiki_description(self, step: dict, beam: list[Link], candidates: dict[str, Link]) -> None:
        """Search by description within Wikipedia (the checks as words), first sentences of the articles found."""
        if self.wiki is None:
            return
        description = "; ".join(c.get("description", "") for c in step.get("checks", []))
        searches = [str(x) for x in step.get("descriptions") or [] if str(x).strip()][:4]
        if not description and not searches:
            return
        proofs: list[Proof] = []
        seen_items: set[str] = set()
        for language in self._languages()[:4]:  # every search in every language: we do not know which language it is written in
            titles = []
            for q in searches:
                titles += [x["title"] for x in self.wiki.search(q[:200], language, 4) if x["title"] not in seen_items]
            titles = list(dict.fromkeys(titles))[:12]
            seen_items |= set(titles)
            for title, text in self.wiki.starts(titles, language).items():
                proofs.append(Proof(f"[{title} ({language})] {text[:500]}", self.wiki.url(title, language), [title],
                                   language))
        if not proofs:
            return
        listing = self._decide("candidates", {"description": description, "kind": step.get("kind", ""),
                                            "proofs": self._numbered(proofs), "again": description},
                              {"candidates": []})["candidates"]
        self._add(listing, proofs, beam[0], candidates, people=self._of_people(step))

    def _proofs_wiki_candidate(self, a: Link, checks: list[dict], maximum: int = 14) -> list[Proof]:
        """For the verification: infobox, start of the candidate's article and rows with the words of the checks or the
        name of the previous entity."""
        articles = self._articles(a, other_ones=0)
        if not articles:
            return []
        words = [c.get("description", "") for c in checks] + [x for c in checks for x in c.get("readings") or []]
        if a.parent is not None:
            words.append(a.parent.name)
        # Each check has its 2 best rows, the heaviest first; then the common choice fills the slots. With all the
        # words together a rare check lost against many common words (24 Oct, Duchy: «gemellato con un paese dell'Alta
        # Vienna» against «Lomellina», «Pavia», «paese»: «Gemellaggi: Linards» left out of the 14).
        owned: list[Proof] = []
        for c in sorted(checks, key=lambda c: -_weight(c)):
            mine = [c.get("description", "")] + list(c.get("readings") or [])
            owned += self._wiki_rows(articles, mine, maximum=2, chars=1200, infobox=False, bare=True)
        common = self._wiki_rows(articles, words, maximum=maximum, chars=5000, infobox=False)
        seen_items, proofs = set(), []
        for p in owned + common:
            if p.text not in seen_items and len(proofs) < maximum + len(checks):
                seen_items.add(p.text)
                proofs.append(p)
        return self._with_adjacent_articles(proofs, [c for c in checks if _weight(c) >= 3])

    def _with_adjacent_articles(self, proofs: list[Proof], decisive: list[dict], maximum: int = 12) -> list[Proof]:
        """Adjacent passages (HierSearch): the rows about a decisive check (weight 3) that link other articles get the
        first sentence of those articles, with one request per language (18 Oct, Duchy of Milan: «Gemellaggi: Linards
        (Francia)» does not say that Linards is in Haute-Vienne; the Linards article does)."""
        if not decisive or self.wiki is None:
            return proofs
        roots = self._roots([c.get("description", "") for c in decisive]
                              + [x for c in decisive for x in c.get("readings") or []])
        # Only the rare words among the candidate's rows: «provincia», «comune» are in half the rows and the 12 slots
        # ended up in the starts of «Provincia (Italia)» and «Comune (Italia)» (24 Oct, Duchy of Milan: «Gemellaggi:
        # Linards» was left without the start of Linards, «comune dell'Alta Vienna», and Gravellona got «unknown»).
        row_words = [{x[:6] for x in _norm(p.text.split(" ⟶ articles:")[0]).split()} for p in proofs]
        frequency = {r: sum(1 for x in row_words if r in x) for r in roots if len(r) >= 4}
        rare = {r: f for r, f in frequency.items() if 0 < f <= max(2, len(proofs) // 4)}
        # First the rows with the check's rarest words (24 Oct, Duchy: «paese», «comune» in 3 rows out of 14 went
        # ahead of «Gemellaggi: Linards»).
        points = {i: sum(1 / rare[r] for r in row_words[i] if r in rare) for i in range(len(proofs))}
        choices: dict[str, list[tuple[int, str]]] = {}
        total = 0
        for i in sorted((i for i in points if points[i] > 0), key=lambda i: -points[i]):
            p = proofs[i]
            if not p.hyperlinks or not p.language:
                continue
            for title in p.hyperlinks[:3]:
                if total < maximum:
                    choices.setdefault(p.language, []).append((i, title))
                    total += 1
        for language, pairs in choices.items():
            starts = self.wiki.starts(list(dict.fromkeys(t for _, t in pairs)), language)
            for i, title in pairs:
                text = starts.get(title) or next((x for k, x in starts.items()
                                                   if matches(title, k) in ("exact", "variant")), "")
                if text:
                    p = proofs[i]
                    proofs[i] = Proof(f"{p.text} ⟶ {title}: {text[:220]}", p.url, p.hyperlinks, p.language)
        return proofs

    def _record(self, a: Link, relation: str) -> None:
        """The accepted link enters the concept graph (for the view and for the writer): nodes, tie, sentence."""
        if a.parent is None or a.tie_proof is None or self.r.concepts is None:
            return
        g = self.r.concepts.graph
        url = a.tie_proof.url or "chain"
        try:
            with self.r.concepts._mutex:
                source = g.source(url, domain=urlsplit(url).hostname or "")
                self._segment_numbers[url] = self._segment_numbers.get(url, 1000) + 1
                segment = g.segment(source, self._segment_numbers[url], a.tie_proof.text, url)
                for name in (a.parent.name, a.name):
                    g.db.execute("INSERT OR IGNORE INTO mention (entity_id, segment_id, sentence) VALUES (?, ?, ?)",
                                 (g.entity(name), segment, a.tie_proof.text[:300]))
                g.triple(a.parent.name, relation[:80], a.name, "entity", "fact", segment,
                         quote=a.tie_proof.text[:300])
                self._recorded.add((_norm(a.parent.name), _norm(relation[:80]), _norm(a.name)))
                g.db.commit()
        except Exception:  # noqa: BLE001 - the view must not stop the research
            pass

    # --- 2. walk ---------------------------------------------------------------------------------------------

    def walk(self) -> None:
        """One or more plans (ODS: if the first reasoning does not reach an answer, another is tried): the second plan
        knows where the first stopped and what it found."""
        if not self.prepare():
            self.reason = "chain not prepared"
            return
        best = None  # (weak links, status) of the most solid complete chain
        for round_no in range(int(self.o.get("chain_plans", 2))):
            if round_no and not self._replan():
                break
            if self.o.get("frontier", True):
                plans = [self.plan]
                if not round_no and int(self.o.get("parallel_plans", 2)) > 1:
                    alternative = self._alternative_plan()
                    plans += [alternative] if alternative else []
                    jump = self._jump_plan(self.plan)
                    if jump is not None and all(self._signature(jump) != self._signature(x) for x in plans):
                        plans.append(jump)
                self._walk_frontier(plans)
            else:
                self._walk_plan()
            if self.value:
                weak_ones = self._weak_links()
                # The most solid chain: fewest weak links, then the best worst link (17 Oct, Congress of Vienna:
                # «Elena», quality -0.1, kept against more promising chains).
                route = self._path(self.value["link"])
                solidity = (-weak_ones, min((a.quality for a in route), default=0.0))
                if best is None or solidity > best[0]:
                    best = (solidity, (self.plan, self.links, self.value, self.reason))
                if weak_ones == 0 or (self.o.get("frontier", True) and self._good_route()):
                    break
                # Complete chain but with unconfirmed links: another chain is tried and the most solid is kept
                # (self-consistency à la Open Deep Search; 16 Oct, Blender: Hallstatt taken by description).
                self.reason = f"complete chain but with {weak_ones} unconfirmed links (value {self.value['value']})"
            if self.r._reached_limit() or self.r.thinker.exhausted:
                break
        if best is not None:
            self.plan, self.links, self.value, self.reason = best[1]

    def _good_route(self) -> bool:
        """With the frontier the alternative branches have already been explored: another plan is needed only if the
        solution's route has a link below half the weight of the checks (17 Oct, Debian: a 12-minute second plan to
        find the same value again)."""
        route = self._path((self.value or {}).get("link"))
        return bool(route) and min(a.quality for a in route) >= 0.5

    def checks(self) -> dict:
        """The outcome of the checks on the solution and the answer status (docs/validation.md §4).
        «Confirmed»: status `verified` or `supported`, i.e. a value, no weak link, the value taken from a page read, no
        source pointing to another route or another value, no objection from the review."""
        exams = getattr(self, "exams", None)
        if not self.value:
            discarded = getattr(self, "discarded", None)
            premise = validation.refuted_premise(exams) if discarded else None
            return {"confirmed": False, "status": "not found", "weak_links": 0, "value_source": "",
                    "reasons": [f"premise refuted: «{premise['what']}» («{premise['quote']}»)"] if premise
                    else [self.reason] if discarded else [],
                    "disagreements": [], "exams": exams, "premise": premise}
        outcome = {"value": self.value["value"], "weak_links": self._weak_links(),
                 "value_source": validation.level(self.value["proof"]),
                 "disagreements": validation.disagreements_on_route(self.conflicts, self.value["link"]), "exams": exams}
        outcome["status"], outcome["reasons"] = validation.status(outcome)
        outcome["confirmed"] = outcome["status"] in ("verified", "supported")
        return outcome

    # Below this remaining amount the checks with the model do not start: writing the answer comes first.
    MIN_BUDGET_FOR_EXAMS = 0.06

    def final_review(self) -> None:
        """The checks with the model on the solution found, before writing the answer: second reading of the value
        (G2), the same data in another source (G5), adversarial review of the chain with another model (G6). They do
        not change the answer: they decide its status. The outcomes stay in `self.exams`."""
        if not self.value or self.exams is not None:
            return
        self.exams = {k: {"outcome": "not done"} for k in ("second_reading", "corroboration", "review")}
        if self.r.thinker.ledger.available("review") < self.MIN_BUDGET_FOR_EXAMS:
            self.exams["reason"] = "Claude budget almost used up"
            return
        self.r._phase("Checking the answer: second reading, other sources, review…")
        if (self.plan.get("answer") or {}).get("is_entity") or "proofs" not in self.value:
            # the value is the entity of the last step: there is no data to reread or to find elsewhere
            self.exams["second_reading"] = self.exams["corroboration"] = {"outcome": "not needed"}
        else:
            self.exams["second_reading"] = self._second_reading()
            self.exams["corroboration"] = self._corroboration()
        self.exams["review"] = self._adversarial_review()
        premise = validation.refuted_premise(self.exams)
        if premise:
            # The question takes for granted something a row denies: the route found is «similar enough», not the
            # answer. The value is set aside (summary: discarded_value) and the question is «not found».
            self.discarded, self.value = self.value, None
            self.reason = (f"premise refuted: the question takes «{premise['what']}» for granted, the sources say "
                           f"«{premise['quote']}»")
            self.history.append({"step": -3, "relation": "premise refuted", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [premise["what"], premise["quote"]])]})
            return
        name = (self.exams["review"] or {}).get("starting_point_name", "")
        missing = validation.missing_name(name, self.r.topic, self._read_texts())
        if missing:
            # The question names its starting point and that name is in no page read: the route started from
            # something else (15 Nov: invented entities, the chain took a nearby one).
            self.discarded, self.value = self.value, None
            self.reason = (f"premise refuted: «{name}», named by the question, appears in none of the pages read "
                           f"(missing: {', '.join(missing)})")
            self.history.append({"step": -3, "relation": "starting point name missing", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [name] + missing)]})
            return
        objection = validation.rejected_route(self.exams, self._weak_links())
        if objection:
            weak_ones = self._weak_links()
            self.discarded, self.value = self.value, None
            self.reason = (f"chain rejected by the review: «{objection['clue']}» does not fit (the sources say "
                           f"«{objection['quote']}»), the question's premise is not proven and {weak_ones} links "
                           "are not confirmed")
            self.history.append({"step": -3, "relation": "chain rejected", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [objection["clue"], objection["quote"]])]})

    def _value_request(self, a: Link, proofs: list[Proof]) -> dict:
        answer = self.plan.get("answer") or {}
        return {"question": self.r.topic[:1000], "entity": a.name, "what": answer.get("what", ""),
                "checks": answer.get("checks", []), **self._value_constraints(answer),
                "proofs": self._numbered(proofs)}

    def _read_value(self, a: Link, proofs: list[Proof]) -> tuple[str, Proof | None]:
        """A reading of the value on the `proofs`, with the same program checks as the first: kind and interval, value
        written in the row, row that talks about the entity."""
        outcome = self._decide("value", self._value_request(a, proofs), {"value": "", "proof": 0})
        value, proof = str(outcome.get("value") or ""), self._proof(proofs, outcome.get("proof"))
        if not value or proof is None or allowed_value(value, self.plan.get("answer") or {}) \
                or not validation.value_in_row(value, proof.text) \
                or not (proof.url in {v.url for v in a.articles.values()} or _name_key(a.name) in _norm(proof.text)):
            return "", None
        return value, proof

    def _second_reading(self) -> dict:
        """G2. The same rows in reverse order, read once more: the value must come out the same."""
        value, proof = self._read_value(self.value["link"], self.value["proofs"][::-1])
        if not value:
            return {"outcome": "not found again"}
        if validation.same_value(value, self.value["value"]):
            return {"outcome": "agree"}
        return {"outcome": "disagree", "value": value, "url": proof.url, "row": " ".join(proof.text.split())[:300]}

    def _corroboration(self) -> dict:
        """G5. The same data looked for in the other sources already available on the final entity: its Wikipedia
        articles in other languages and the other pages read (not the search snippets). Never the value's page."""
        a, first = self.value["link"], self.value["proof"]
        answer = self.plan.get("answer") or {}
        words = list(answer.get("words") or []) + [answer.get("what", "")] + list(answer.get("checks") or [])
        other_articles = [v for v in self._articles(a, other_ones=2) if v.url != first.url]
        proofs = self._wiki_rows(other_articles, words, maximum=40, chars=9000, bonus=self._bonus_value(answer)) \
            if other_articles else []
        proofs += [p for p in self._entity_proofs(a.name, segments=self._segments_on(a.name), maximum=20)
                  if p.url != first.url and validation.level(p) != "C"]
        if not proofs:
            return {"outcome": "single source", "watched_sources": 0}
        sources = len({p.url for p in proofs})
        value, proof = self._read_value(a, proofs)
        if not value:
            return {"outcome": "single source", "watched_sources": sources}
        if validation.same_value(value, self.value["value"]):
            return {"outcome": "agree", "url": proof.url, "watched_sources": sources}
        return {"outcome": "disagree", "value": value, "url": proof.url, "watched_sources": sources,
                "row": " ".join(proof.text.split())[:300]}

    def _route_rows(self) -> list[Proof]:
        """The rows the solution rests on: ties, proven checks and value, from the starting point to the final entity."""
        if not self.value:
            return []
        rows: list[Proof] = []
        for a in self._path(self.value["link"]):
            rows += [p for p in [a.tie_proof, a.start] + [e["proof"] for e in a.outcomes] if p is not None]
        return rows + [self.value["proof"]]

    _INFOBOX_ROW_START = re.compile(r"[^.:]{1,40}:\s")

    def _rows_of_other_articles(self, route: list[Link]) -> list[Proof]:
        """For each link, the rows about its relation in the articles of the previous link, including the other
        languages and without the page the tie was taken from: if another article gives another entity (another
        birthplace, another advisor), the reviewer of the chain must be able to see it. The value corroboration (G5)
        looks only at the last data; here the steps are looked at (12 Nov, control questions, Python: different
        birthplace between two articles, answer «verified»)."""
        rows: list[Proof] = []
        for a in route:
            if a.parent is None:
                continue
            used = a.tie_proof.url if a.tie_proof is not None else ""
            articles = [v for v in self._articles(a.parent, other_ones=2) if v.url != used]
            if not articles:
                continue
            my = next(iter(a.articles.values()), None)
            names = [a.name] + [my.languages[v.language] for v in articles if my is not None and v.language in my.languages]
            rows += self._wiki_rows(articles, [a.relation] + list(dict.fromkeys(names)), maximum=8, chars=1800,
                                      bonus=lambda text: 40 if self._INFOBOX_ROW_START.match(text) else 0)
        return rows

    def _adversarial_review(self) -> dict:
        """G6. Another model rereads the question, the route and the rows used, looking for the clue that does not fit."""
        from .. import configuration
        route = self._path(self.value["link"])
        root = self._root(self.value["link"])
        rows = self._route_rows() + self._rows_of_other_articles(route)
        rows = list({(p.url, p.text): p for p in rows}.values())[:90]
        data = {"question": self.r.topic[:1500],
                "route": " → ".join([root.name] + [f"[{a.relation}] {a.name}" for a in route]),
                "answer": f"{self.value['value']} ({(self.plan.get('answer') or {}).get('what', '')} of "
                            f"{self.value['link'].name})",
                "rows": "\n".join(f"[{i}] {p.text[:700]}" for i, p in enumerate(rows, start=1))}
        model = str(self.o.get("review_model") or configuration.load()["review_model"])

        def one(_n: int) -> dict:
            outcome, parent = self.r.thinker.decide("review", data, lambda: {}, model=model)
            return validation.examine_review(outcome, rows) if parent == "claude" else {"outcome": "not done"}
        # Two independent reviews, the stricter one counts: a single one swings between «holds» and «not proven».
        readings = max(1, int(self.o.get("review_readings", 2)))
        with ThreadPoolExecutor(max_workers=readings) as pool:
            unit = validation.merge_reviews(list(pool.map(one, range(readings))))
        self.r.cost = max(self.r.cost, self.r.thinker.ledger.spent)
        return {**unit, "model": model} if unit.get("outcome") == "done" else unit

    def _read_texts(self) -> list[str]:
        """Everything the chain read for this question: saved page segments, Wikipedia articles of the links (title and
        rows), search result snippets."""
        texts = [str(s.get("text", "")) for s in self._after_segments(0)] + [p.text for p in self._snippets]
        links = {id(a): a for beam in self.links for a in beam}
        a = (self.value or self.discarded or {}).get("link")
        while a is not None:
            links[id(a)] = a
            a = a.parent
        for a in links.values():
            texts += [x.text for x in (a.tie_proof, a.start) if x is not None]
            for article in a.articles.values():
                texts += [article.title] + [r.text for r in article.rows]
        return texts

    def _weak_links(self) -> int:
        a, weak_ones = (self.value or {}).get("link"), 0
        while a is not None:
            weak_ones += 1 if a.weak else 0
            a = a.parent
        return weak_ones

    @staticmethod
    def _signature(plan: dict) -> tuple:
        return (_norm((plan.get("starting_point") or {}).get("entity")),
                tuple(_norm(p.get("relation")) for p in plan.get("steps", [])))

    @staticmethod
    def _jump_plan(plan: dict) -> dict | None:
        """An intermediate step towards a thing without a proper name or a page of its own (statue, effigy, painting,
        object): a plan with that step merged into the next one, starting from the previous entity and inheriting its
        checks, words and descriptions (25 Oct, Sistine Chapel: «feast → effigy depicting it → town» gets lost among
        statues and paintings; «feast → town where an effigy of it is» finds Novara di Sicilia in the gallery of the
        Assumption article. In the prompt the same rule was followed 1 time out of 3)."""
        steps = list(plan.get("steps") or [])
        for i, step in enumerate(steps[:-1]):
            text = _norm(f"{step.get('kind', '')} {step.get('relation', '')}")
            if not NAMELESS_OBJECTS.search(text):
                continue
            after = steps[i + 1]
            merged = {**after,
                     "relation": f"{after['relation']} (via: {step['relation']})"[:150],
                     "words": list(dict.fromkeys(list(after.get("words") or []) + list(step.get("words") or [])))[:14],
                     "descriptions": list(after.get("descriptions") or []) + list(step.get("descriptions") or []),
                     "checks": list(after.get("checks") or []) + [
                         {**c, "description": f"tied to {step.get('kind') or step['relation']}: "
                                             f"{c.get('description', '')}"[:200]} for c in step.get("checks") or []]}
            return {**plan, "steps": steps[:i] + [merged] + steps[i + 2:],
                    "reason": f"skipping «{step['relation']}» (thing without a proper name)"}
        return None

    def _alternative_plan(self) -> dict | None:
        """A second plan, different from the first, to walk together with it in the same frontier."""
        plan = self._decide("chain", {"question": self.r.topic[:1000], "today": self.r.today,
                                        "interpretation": self.interpretation,
                                        "alternative_to": {"starting_point": (self.plan.get("starting_point") or {}).get("entity"),
                                                          "steps": [p.get("relation") for p in self.plan["steps"]]}},
                             {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
        if not plan.get("steps") or not (plan.get("starting_point") or {}).get("entity") \
                or self._signature(plan) == self._signature(self.plan):
            return None
        self.r.publish({"kind": "chain_search", "interpretation": self.interpretation, "plan": plan})
        self.history.append({"step": 0, "relation": "alternative plan", "attempt": 0, "candidates": [],
                            "kept": [], "discarded": [], "alternative_plan": plan})
        return plan

    def _replan(self) -> bool:
        hits = [{"relation": step.get("relation"), "entity": [a.name + (" (weak)" if a.weak else "")
                                                                     for a in beam]}
                   for step, beam in zip(self.plan.get("steps", []), self.links, strict=False)]
        previous = {"starting_point": (self.plan.get("starting_point") or {}).get("entity"),
                      "steps": [p.get("relation") for p in self.plan.get("steps", [])],
                      "where_it_stopped": self.reason, "found_links": hits}
        self.r._phase("The chain did not reach the answer: Claude is preparing a different chain…")
        plan = self._decide("chain", {"question": self.r.topic[:1000], "interpretation": self.interpretation,
                                        "previous_chain": previous},
                             {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
        if not plan.get("steps") or not (plan.get("starting_point") or {}).get("entity"):
            return False
        signature = lambda x: (_norm((x.get("starting_point") or {}).get("entity")),  # noqa: E731
                           tuple(_norm(p.get("relation")) for p in x.get("steps", [])))
        if signature(plan) == signature(self.plan):  # 16 Oct, Linux kernel: the same chain walked again, 9 minutes lost
            self.r._phase("The new plan is the same as the first: not walking it again.")
            return False
        self.history.append({"step": 0, "relation": "new plan", "attempt": 0, "candidates": [],
                            "kept": [], "discarded": [], "previous_plan": previous})
        self.plan, self.links, self.reason, self.value = plan, [], "", None
        self.r.publish({"kind": "chain_search", "interpretation": self.interpretation, "plan": self.plan})
        return True

    def _walk_plan(self) -> None:
        starting_point = Link(self.plan["starting_point"]["entity"],
                          context=str(self.plan["starting_point"].get("clue") or "")[:150])
        beam = [starting_point]
        width = int(self.o.get("chain_width", 3))
        for number, step in enumerate(self.plan["steps"], start=1):
            new_items = self._walk_step(number, step, beam)
            if not new_items:
                self.reason = f"chain broken at step {number} ({step.get('relation')})"
                return
            beam = new_items[:width]
            self.links.append(beam)
            if self.r._reached_limit():
                self.reason = self.r._reached_limit()
                if number < len(self.plan["steps"]):
                    return
        # Behind the beam, the other unrefuted candidates of the last step, only on Wikipedia (16 Oct, Wikipedia:
        # Roland Unger, correct, was among the candidates without "yes" and outside the beam).
        others = [a for a in self._alive if a not in beam][:4]
        self._value(beam, others)

    # --- global frontier (DeepSearch, ICLR 2026) -------------------------------------------------------------------

    @staticmethod
    def _path(a: Link) -> list[Link]:
        """The links from the starting point (excluded) to `a`."""
        chain = []
        while a is not None and a.parent is not None:
            chain.append(a)
            a = a.parent
        return chain[::-1]

    def _depth(self, a: Link) -> int:
        """The plan step the node has reached: those of its root (starting point 0, anchor base_step) plus the links
        walked from there."""
        return self._root(a).base_step + len(self._path(a))

    def _priority(self, a: Link, steps: int, action: str) -> float:
        """How worthwhile the next action on a node is: quality of the route reaching it (half the worst, half the
        mean: a chain is worth as much as its weakest link), a small bonus for depth (square root of depth/steps, to
        finish the chains) and a penalty for expensive actions (the web after Wikipedia)."""
        # A link without a proven tie to the previous one is worth much less (18 Oct, Arduino: Damian Kulash, found
        # only by description «born in the mid-70s», taken as the founder's spouse).
        route_quality = [x.quality - (0.15 if x.weak else 0.0)
                          - (0.0 if self._linked(x) else 0.05 if self._co_occurs(x) else 0.25)
                          for x in self._path(a)]
        # A root is worth 0.7: until it is expanded, nobody knows what it will give (18 Oct, Wikipedia: with 1.0 the
        # web searches from the starting point always went ahead of the value of Roland Unger, correct, up to the
        # page limit).
        base = 0.5 * min(route_quality) + 0.5 * sum(route_quality) / len(route_quality) if route_quality else 0.7
        # The Wikipedia value of a final node costs one call and also acts as a check (kind and interval): it goes
        # ahead of the web (17 Oct, Promessi sposi: Ponchielli, correct, among the candidates; the frontier expanded
        # on the web up to the 30-page limit without looking for its value).
        # Final nodes are one action away from the answer: their value, even on the web (2 pages), costs less than a
        # step on the web (6 pages and the verification).
        cost = {"wiki": 0.0, "web": 0.35, "value": -0.45, "web_value": -0.1}[action]
        # A node that gave no candidate on Wikipedia (usually: it has no article) has the web as its only route, not
        # a fallback: with a good quality the penalty goes down (24 Oct, Arduino: Keyboardio never expanded on the web
        # in 3 runs out of 4; in past runs the same for «Simone da Orsenigo», Duomo, and «Assunzione di Maria
        # Vergine», Sistine Chapel. Threshold 0.5 and penalty 0.1 chosen on the 95 runs with the frontier: 6 correct
        # entities out of 10 brought forward against 7 useless expansions in the correct runs).
        if action == "web" and a.empty_wiki and (a.quality if a.parent is not None else 0.7) >= 0.5:
            cost = 0.1
        return base + 0.1 * math.sqrt(self._depth(a) / max(steps, 1)) - cost - 0.4 * a.failures + a.preferred

    def _root(self, a: Link) -> Link:
        while a.parent is not None:
            a = a.parent
        return a

    def _plan_of(self, a: Link) -> dict:
        return self._root(a).plan or self.plan

    def _plan_roots(self, plan: dict) -> list[Link]:
        """Starting point and anchors of a plan. Anchors (Think-on-Graph, HierSearch): other entities that the question
        names with certainty and from which, with a relation of theirs, the result of a step is reached; the closer to
        the answer, the earlier they are explored (17 Oct, Raspberry Pi: the London Stock Exchange, named, was two
        steps from the answer; the chain started from the Raspberry Pi used up its pages on the distributors)."""
        n = len(plan["steps"])
        starting_point = Link(plan["starting_point"]["entity"], context=str(plan["starting_point"].get("clue") or "")[:150],
                          quality=1.0, plan=plan)
        roots = [starting_point]
        for x in plan.get("anchors") or []:
            step = int(x.get("step") or 0)
            name = " ".join(str(x.get("entity") or "").split())[:120]
            if 1 <= step <= n and name and str(x.get("relation") or "").strip() \
                    and _norm(name) != _norm(starting_point.name):
                roots.append(Link(name, quality=1.0, base_step=step - 1, relation=str(x["relation"])[:150],
                                     context=str(x.get("clue") or "")[:150], plan=plan))
        return roots

    def _walk_frontier(self, plans: list[dict] | None = None) -> None:
        """Global frontier search: instead of going step by step with a fixed beam of 3 entities, the program keeps a
        single list of all the open actions, of all steps (expand a node on Wikipedia, on the web, look for its value)
        and each time does the best one. If a branch leads nowhere, it falls back by itself to the reserve candidate of
        the right step, without redoing the plan. A node whose children all fail gets a penalty (failures); one that
        already led to a value is not penalised (asymmetric rule). With several plans (self-consistency à la Open Deep
        Search, but in parallel) the routes of all plans sit in the same frontier: the one that works best goes ahead
        (17 Oct, Lepanto: a plan with «sister republic of» instead of «replaced by» did not find the Ligurian
        Republic)."""
        plans = plans or [self.plan]
        actions: list[tuple[Link, str]] = [(r, "wiki") for plan in plans for r in self._plan_roots(plan)]
        levels: dict[tuple[int, int], list[Link]] = {}  # (plan, depth) → links reached
        seen: dict[tuple[int, int], set[str]] = {}  # names already reached (an entity enters only once)
        solutions: list[tuple[float, dict, dict]] = []
        already_expanded: dict[tuple, tuple[str, list[Link]]] = {}  # signature → (step, children found)
        web_values: dict[int, int] = {}  # parent → values looked up on the web for its children
        already_marks: dict[tuple, set[str]] = {}  # signature → steps (relation + checks) already expanded
        done, maximum = 0, int(self.o.get("frontier_actions", 30))
        priority_of = lambda x: self._priority(x[0], len(self._plan_of(x[0])["steps"]), x[1])  # noqa: E731
        while actions and done < maximum:
            if self.r._reached_limit() or self.r.thinker.exhausted:
                self.reason = self.reason or self.r._reached_limit() or "Claude budget used up"
                break
            actions.sort(key=lambda x: -priority_of(x))
            node, action = actions.pop(0)
            priority = priority_of((node, action))
            # Once a value is found, only actions as promising as the solution go on (self-consistency): a second
            # route leading to the same value confirms it, a better one replaces it. The solution is worth as much as
            # its route, without the value-search bonus (25 Oct, Arduino: «Chris Martin», spouse found by
            # description, was worth 0.82 with the 0.45 bonus and Keyboardio, 0.58, was never expanded).
            if solutions and (priority <= solutions[0][0] or done >= maximum - 2):
                break
            # The cap counts only the expansions (the expensive searches): looking for a final node's value costs one
            # call and does not use up the cap (22 Oct, Linux kernel: the 30 actions were spent on value searches
            # before reaching Alexandre Oliva, a correct but weak candidate). The time and budget limit still holds.
            if action in ("wiki", "web"):
                done += 1
            plan = self._plan_of(node)
            steps, n = plan["steps"], len(plan["steps"])
            depth = self._depth(node)
            self.actions.append(f"{action} {node.name} ({priority:.2f})" + (
                f" [plan {plans.index(plan) + 1}]" if len(plans) > 1 else ""))
            if action in ("value", "web_value"):
                self.value, self.plan = None, plan
                if self._entity_value(node, (1,) if action == "value" else (2, 3)):
                    solutions.append((self._priority(node, n, "wiki"), self.value, plan))
                    solutions.sort(key=lambda x: -x[0])
                    self.history.append({"step": n + 1, "relation": "value", "attempt": 0, "candidates": [node.name],
                                        "kept": [(self.value["value"], round(priority, 2), "solution")],
                                        "discarded": []})
                    if all(not x.weak for x in self._path(node)):
                        break  # fully confirmed route: that is enough
                elif action == "value" and web_values.get(id(node.parent), 0) < 3:
                    # At most 3 web values per parent (25 Oct, Wikipedia: «board of directors of» gave ~10 members,
                    # each with 2 value pages on the web, up to the 35-minute limit).
                    web_values[id(node.parent)] = web_values.get(id(node.parent), 0) + 1
                    actions.append((node, "web_value"))
                else:
                    self._failed(node)
                continue
            step = steps[depth]
            if node.parent is None and node.base_step:
                # From an anchor its relation is followed, with the step's checks plus the one of the skipped relation
                # (weight 3): the anchor must not make the chain lose its identity (18 Oct, Council of Trent: anchor
                # «Haute-Marne», Chaumont passed all the checks without being the birthplace of the Marquis
                # d'Elbeuf).
                first = steps[depth - 1].get("checks") or [] if depth >= 1 else []
                who = "; ".join(str(c.get("description") or "") for c in first) or \
                    str((plan.get("starting_point") or {}).get("entity") or "")
                skipped = {"description": f"is «{step['relation']}» of: {who}"[:200], "weight": 3,
                           "readings": [f"{step['relation']} (of the person or thing of the previous step)"]}
                step = {**step, "relation": node.relation,
                         "checks": list(step.get("checks") or []) + [skipped]}
            # The same entity, at the same step and with the same relation, is expanded only once even if two plans
            # (or two variants of the name) reach it: web pages are not spent twice (23 Oct, Arduino: «Keyboard.io»
            # and «Keyboardio» expanded in both plans, 30-page cap used up before the spouse).
            if action == "wiki" and self.wiki is not None:
                self._articles(node)  # the article is needed for the expansion anyway; here it gives the entity key
            signature = self._action_signature(action, node, depth, step["relation"])
            step_mark = json.dumps([step.get("relation"), step.get("checks")], sort_keys=True, default=str)
            key = (id(plan), depth + 1)
            seen.setdefault(key, set())
            children = []
            reuse = False
            case = self._already_done(signature, action, step_mark, already_expanded, already_marks)
            if case in ("skip", "reuse"):
                done -= 1
                already_mark, already_children = already_expanded[signature]
                if case == "skip" or node in [x.parent for x in already_children]:
                    self.actions[-1] += " (already done)"
                    continue
                # Same step already expanded by another plan: its children (already verified) are valid here too,
                # under this node, without redoing searches (25 Oct: a plan with the same first step stayed stuck).
                children = [replace(a, parent=node, outcomes=list(a.outcomes)) for a in already_children]
                self.actions[-1] += " (children reused)"
                reuse = True
            first_history = len(self.history)
            for attempt in (() if reuse else (1, 2) if action == "wiki" else (3, 4)):
                if attempt in (1, 2) and self.wiki is None:
                    continue
                children += self._attempt(attempt, depth + 1, step, [node], seen[key], all_items=True)
                if any(not a.weak and self._complete(a) for a in children) or self.r._reached_limit():
                    break
            if not reuse:
                # Same relation with different checks (another plan): on Wikipedia it is redone, it costs only the
                # verification (29 Oct, Galileo: Biancani discarded with the checks of plan 1, kept with those of plan
                # 2, which however never started: «already done»).
                already_expanded.setdefault(signature, (step_mark, list(children)))
                already_marks.setdefault(signature, set()).add(step_mark)
            if action == "wiki" and not reuse:
                # Empty: no candidates following the relation and none kept by the search by description, which gives
                # arbitrary names (24 Oct, Arduino: for «founders of Keyboardio» Art Lazere, Julius Chein, Steve Jobs,
                # all discarded, and the Keyboardio web never started. On past runs: +2 correct nodes brought forward,
                # +4 useless expansions in the correct runs; «none kept» everywhere: +5 and +26).
                node.empty_wiki = not any(x.get("candidates") if x.get("attempt") == 1 else x.get("kept")
                                          for x in self.history[first_history:])
                actions.append((node, "web"))  # the web stays as a reserve, with its penalty
            already = {k for x in levels.get(key, []) for k in (self._canonical_key(x), _entity_key(x.name))}
            new_items = []
            returned = [a for a in children if validation.repeats_ancestor(a, node)]
            if returned:  # a chain does not go back to an entity already crossed (relation read backwards)
                self.history.append({"step": depth + 1, "relation": "already on the route", "attempt": 0,
                                    "candidates": [a.name for a in returned], "kept": [],
                                    "discarded": [(a.name, ["entity already crossed by the chain"]) for a in returned]})
            for a in children:
                if a in returned:
                    continue
                if self._canonical_key(a) not in already and _entity_key(a.name) not in already:
                    new_items.append(a)
                    already |= {self._canonical_key(a), _entity_key(a.name)}
            if not new_items and action == "web":
                self._failed(node)
            for a in new_items:
                a.relation = step["relation"]
                levels.setdefault(key, []).append(a)
                actions.append((a, "value" if depth + 1 == n else "wiki"))
            if depth + 1 == n and len(new_items) > 1:
                self._final_tiebreak(new_items, lambda x: self._priority(x, n, "value"))
        self.plan = solutions[0][2] if solutions else plans[0]
        mine = sorted(d for (p, d) in levels if p == id(self.plan))
        self.links = [sorted(levels[(id(self.plan), d)], key=self._order) for d in mine]
        steps, n = self.plan["steps"], len(self.plan["steps"])
        if solutions:
            # Two routes to the same value: the best is kept (the others confirm it).
            self.value = solutions[0][1]
            equal = [x for x in solutions if _norm(x[1]["value"]) == _norm(self.value["value"])]
            self.reason = f"complete chain: {self.value['link'].name} → {self.value['value']}" + (
                f" (confirmed by {len(equal)} routes)" if len(equal) > 1 else "")
            for a in self._path(self.value["link"]):
                self._record(a, a.relation or steps[self._depth(a) - 1]["relation"])
            last = self.links[-1] if self.links else []
            if self.value["link"] in last:  # the final beam starts from the solution's link
                last.remove(self.value["link"])
                last.insert(0, self.value["link"])
        elif not self.reason:
            reached = max(mine) if mine else 0
            self.reason = (f"chain broken at step {reached + 1} ({steps[reached].get('relation')})"
                           if reached < n else "requested value not found")

    @staticmethod
    def _already_done(signature: tuple, action: str, mark: str, already_expanded: dict, already_marks: dict) -> str:
        """An expansion already done by another plan: «new» (never done), «reuse» (same step: its children),
        «redo» (Wikipedia, same relation with different checks: it costs only the verification; 29 Oct, Galileo),
        «skip» (web with different checks: pages are not spent twice)."""
        if signature not in already_expanded:
            return "new"
        if already_expanded[signature][0] == mark:
            return "reuse"
        if action == "wiki" and mark not in already_marks.get(signature, set()):
            return "redo"
        return "skip"

    def _final_tiebreak(self, candidates: list[Link], priority, margin: float = 0.05, maximum: int = 5) -> int:
        """Tied candidates of the last step (priority within `margin` of the best): Haiku compares them with the whole
        question and some rows of their articles (start, rows of the checks, rows with the question's words) and
        chooses one or none; the chosen one goes first. The checks say yes/no one at a time, the comparison sees the
        nuanced clues (24 Oct, Monte Bianco: Alassio and Laigueglia both twinned with La Thuile, 0.71 each; only
        Laigueglia is «a small seaside village», the question says «village»; in past runs 17 solutions out of 84
        came from a tie, in 4 wrong ones the right answer was among the tied).
        Returns the chosen index (from 0) or -1."""
        points = {id(a): priority(a) for a in candidates}
        best = max(points.values())
        # Among candidates tied by a row and without any «no» the gap can be wider: the verification picks one row per
        # check and can give «partly» to the right one (25 Oct, Monte Bianco: Laigueglia 0.7 with the row «piccolo
        # borgo di mare» without Savona, Alassio 0.9; 2 runs out of 5 gave Alassio).
        clean = [a for a in candidates if self._linked(a) and not self._no(a)]
        first_one = max(candidates, key=lambda a: points[id(a)])
        threshold = first_one.quality - 0.4 if first_one in clean else 2.0  # quality: siblings have the same route
        tied = sorted((a for a in candidates if points[id(a)] >= best - margin
                       or (a in clean and a.quality >= threshold)),
                      key=lambda a: -points[id(a)])[:maximum]
        if len(tied) < 2:
            return -1
        profiles = []
        for i, a in enumerate(tied, 1):
            rows = [x.text for x in (a.tie_proof, a.start) if x is not None]
            rows += [e["proof"].text for e in a.outcomes if e.get("proof") is not None]
            rows += [x.text for x in self._wiki_rows(self._articles(a), [self.r.topic], maximum=6, chars=1500)]
            rows = list(dict.fromkeys(" ".join(r.split())[:300] for r in rows))[:10]
            profiles.append(f"{i}. {a.name}\n" + "\n".join(f"   - {r}" for r in rows))
        outcome = self._decide("final", {"question": self.r.topic[:1000], "candidates": "\n".join(profiles)},
                             {"choice": 0, "reason": ""})
        choice = int(outcome.get("choice") or 0)
        self.history.append({"step": -2, "relation": "final tiebreak", "attempt": 0,
                            "candidates": [a.name for a in tied],
                            "kept": [(tied[choice - 1].name, choice, "chosen")] if 0 < choice <= len(tied) else [],
                            "discarded": [], "reason": str(outcome.get("reason") or "")[:300]})
        if not 0 < choice <= len(tied):
            return -1
        chosen = tied[choice - 1]  # goes ahead of the best, even if it started further back (wide tiebreak)
        chosen.preferred = best - points[id(chosen)] + margin
        return choice - 1

    @staticmethod
    def _failed(a: Link) -> None:
        """Penalty to a node without outcome and, by half, to its origin (it propagates little: other children may
        still work)."""
        a.failures += 1.0
        if a.parent is not None:
            a.parent.failures += 0.25

    def _step_checks(self, step: dict, beam: list[Link]) -> list[dict]:
        """A step's checks with their weights: those of the plan, the kind (weight 1) and the tie with the previous
        entity (weight 2, a check for all: 16 Oct, Monte Bianco: Alassio, Ligurian and in the province of Savona,
        preferred to Laigueglia without any row saying it was twinned with La Thuile)."""
        checks = [{**c, "weight": c.get("weight") or 2} for c in step.get("checks", [])]
        # A plan check naming the previous entity repeats the tie («indicated as a student of Galileo»): it counted
        # twice (23 Oct, paper §9.1: Guiducci and Viviani «strong» without being Bolognese Jesuits). It becomes a
        # reading of the tie.
        keys = {p for a in beam for p in _norm(a.name).split() if len(p) >= 5 and p not in STOP_WORDS}
        repeated = [c for c in checks if any(re.search(rf"\b{re.escape(k)}\b", _norm(c.get("description", "")))
                                                for k in keys)]
        checks = [c for c in checks if c not in repeated]
        if str(step.get("kind") or "").strip():  # the kind is a check (16 Oct: BeBox, a computer, taken for BeOS)
            checks.append({"description": f"is of this kind: {step['kind']}", "weight": 1,
                              "readings": [f"{step['kind']} (also as a broader or narrower category)"]})
        checks.append({"description": f"is in relation «{step['relation']}» with {' or '.join(a.name for a in beam)}",
                          "weight": 2, "readings": ["in a broad sense: student, disciple, studied with, collaborator, "
                                                 "work of, part of, as the relation requires"]
                          + [str(c.get("description", "")) for c in repeated]})
        return checks

    def _collect(self, attempt: int, number: int, step: dict,
                  beam: list[Link]) -> tuple[dict[str, Link], dict[str, Link]]:
        """The candidates of an attempt: (found following the relation, found by description)."""
        candidates: dict[str, Link] = {}
        per_description: dict[str, Link] = {}
        if attempt in (1, 2):  # Wikipedia: one thread per beam entity (model calls are slow)
            partial = [{} for _ in beam]
            jobs = [(self._wiki_candidates, (number, step, o, attempt, partial[i]))
                      for i, o in enumerate(beam)]
            if attempt == 2:  # Wikipedia again: articles linking to the entity, search by description
                jobs.append((self._candidates_wiki_description, (step, beam, per_description)))
            with ThreadPoolExecutor(max_workers=4) as group:
                for future in [group.submit(f, *a) for f, a in jobs]:
                    future.result()
            for p in partial:
                for k, v in p.items():
                    candidates.setdefault(k, v)
        elif attempt == 3:
            for origin in beam:
                self._candidates_in_forward(number, step, origin, 1, candidates)
        else:
            for origin in beam:
                self._candidates_in_forward(number, step, origin, 2, candidates)
            self._candidates_per_description(number, step, beam, per_description)
            if not candidates:
                self._candidates_from_graph(beam, per_description)
        return candidates, per_description

    def _attempt(self, attempt: int, number: int, step: dict, beam: list[Link], seen: set[str],
                   all_items: bool = False) -> list[Link]:
        """An attempt: candidates collected, verified with the step's checks; the unrefuted ones remain."""
        checks = self._step_checks(step, beam)
        candidates, per_description = self._collect(attempt, number, step, beam)
        new_items = [a for k, a in candidates.items() if k not in seen]
        good = self._verification(new_items[:MAX_CANDIDATES], checks, all_items=all_items)
        others = [a for k, a in per_description.items() if k not in seen and k not in candidates]
        if others:
            good += self._verification(others[:MAX_CANDIDATES], checks, all_items=all_items)
        seen |= set(candidates) | set(per_description)
        all_seen = new_items + others
        self.history.append({"step": number, "relation": step["relation"], "attempt": attempt,
                            "parent": [a.name for a in beam],
                            "candidates": [a.name for a in all_seen],
                            "kept": [(a.name, round(a.quality, 2), "weak" if a.weak else "strong")
                                       for a in good],
                            "discarded": [(a.name, [e["check"] for e in a.outcomes if e["outcome"] == "no"])
                                         for a in all_seen if a not in good],
                            "verifications": [self._verification_in_short(a) for a in all_seen]})
        self.r.publish({"kind": "chain_search_step", **self.history[-1]})
        return good

    def _action_signature(self, action: str, node: Link, depth: int, relation: str) -> tuple:
        """When two expansions are the same: same entity, same step and (on Wikipedia) same relation. On the web the
        relation does not count: two plans that write the relation differently read the same pages (24 Oct: on the
        95 runs with the frontier 31 duplicate web expansions, the second never gave a link of a correct solution; 12
        wrong runs out of 32 end because of the page cap)."""
        words = frozenset() if action == "web" else frozenset(
            p[:5] for p in _norm(relation).split() if len(p) >= 4)
        return (action, self._canonical_key(node), depth, words)

    @staticmethod
    def _verification_in_short(a: Link) -> dict:
        """For the history (23 Oct, Arduino: without the cited rows it was unclear why Keyboardio was worth 0.3):
        article, tie row and, for each check, outcome said by the model, outcome kept by the program and cited row."""
        article = next(iter(a.articles.values()), None)
        cited = lambda p: p.text[:220] if p is not None else ""  # noqa: E731
        return {"name": a.name, "quality": round(a.quality, 2),
                "article": f"{article.title} ({article.language})" if article else (f"{a.title[1]} ({a.title[0]})" if a.title else ""),
                "tie": cited(a.tie_proof),
                "outcomes": [{"check": str(e.get("check", ""))[:100], "weight": e.get("weight"),
                           "said": e.get("said", e.get("outcome")), "kept": e.get("outcome"),
                           "row": cited(e.get("cited") or e.get("proof"))} for e in a.outcomes]}

    @staticmethod
    def _canonical_key(a: Link) -> str:
        """The same entity with different names: if the Wikipedia article is known, the key is the article (English
        title if there is one: «Adafruit» and «Adafruit Industries» lead to the same article, 23 Oct); otherwise the
        name variants."""
        article = next(iter(a.articles.values()), None)
        if article is not None:
            english = article.title if article.language == "en" else article.languages.get("en")
            return "article:" + _entity_key(english or article.title)
        return _entity_key(a.name)

    def _walk_step(self, number: int, step: dict, beam: list[Link]) -> list[Link]:
        """Four attempts, from the cheapest; it stops at the first that gives strong candidates.
        1. Wikipedia as a graph: rows of the entity's article chosen for the words of the relation (16 Oct).
        2. Wikipedia again: articles linking to the entity and search by description (the checks as words).
        3. Web forward (searches for the relation, pages read).
        4. Web with different searches and BY DESCRIPTION (the checks as search key), plus the neighbours in the graph;
           for the candidates found by description the tie with the previous entity becomes a check.
           (15 Oct: Biancani does not appear in the pages about Galileo's students, but does among Bolognese Jesuits.)
        A weak candidate stays in reserve."""
        reserve: list[Link] = []
        found_strong: list[Link] = []
        seen: set[str] = set()
        self._alive = []
        for attempt in (1, 2, 3, 4):
            if attempt in (1, 2) and self.wiki is None:
                continue
            good = self._attempt(attempt, number, step, beam, seen)
            self._alive = sorted(self._alive + good, key=self._order)
            strong = sorted([a for a in good if not a.weak], key=lambda a: -a.quality)
            found_strong = sorted(found_strong + strong,
                                   key=lambda a: (bool(a.parent and a.parent.weak), -a.quality, self._doubts(a)))
            complete = any(self._complete(a) for a in found_strong)
            # With Wikipedia the two cheap attempts are always made before choosing, unless a candidate passes all
            # the checks (16 Oct, Emacs: Bobrow "strong" at the first attempt, Tomlinson came only later).
            if found_strong and (complete or attempt >= 2 or self.wiki is None):
                picked = found_strong + [a for a in self._alive if a.weak and a not in found_strong]
                for a in picked:
                    self._record(a, step["relation"])
                return picked
            reserve += [a for a in good if a.weak]  # the weak ones of all attempts, then the best
            if self.r._reached_limit() or self.r.thinker.exhausted:
                break
        reserve = sorted(reserve, key=self._order)[
            :int(self.o.get("chain_width", 3))]
        for a in reserve:
            self._record(a, step["relation"])
        return reserve

    def _candidates_in_forward(self, number: int, step: dict, origin: Link, attempt: int,
                             candidates: dict[str, Link]) -> None:
        self.r._phase(f"Step {number}: {step['relation']} {origin.name}…")
        searches = self._decide("step", {"research_language": self._research_language(),
                                          "entity": origin.name, "context": origin.context,
                                          "relation": step["relation"],
                                          "kind": step.get("kind", ""), "clues": self._clues(step),
                                          "searches_already_done":
                                              self.step_searches[-12:] if attempt == 2 else []},
                                {"searches": [f"{origin.name} {step['relation']}"]})["searches"]
        clues = "; ".join(self._clues(step))
        segments = self._search_and_read(searches, int(self.o.get("pages_per_step", 3)),
                                       purpose=f"{step['relation']} {origin.name}" + (f" ({clues})" if clues else ""))
        proofs = self._step_proofs(origin.name, segments, self._segments_on(origin.name))
        read = {p.url for p in proofs}
        proofs += [p for p in self._snippets if p.url not in read]
        if not proofs:
            return
        listing = self._decide("candidates", {"entity": origin.name, "relation": step["relation"],
                                            "kind": step.get("kind", ""), "proofs": self._numbered(proofs),
                                            "again": f"{step['relation']} — {origin.name}"},
                              {"candidates": []})["candidates"]
        self._add(listing, proofs, origin, candidates, people=self._of_people(step))

    @staticmethod
    def _clues(step: dict) -> list[str]:
        """The question's clues about the step's result (the checks, the heaviest first) for the web searches:
        «companies inspired by Arduino» leads to hundreds of results, «Arduino programmable keyboard company» to the
        result (25 Oct, Arduino: the first web searches never mentioned keyboards)."""
        checks = sorted(step.get("checks") or [], key=lambda c: -_weight(c))
        return [" / ".join([str(c.get("description") or "")] + [str(x) for x in (c.get("readings") or [])[:1]])[:160]
                for c in checks[:3] if c.get("description")]

    def _candidates_per_description(self, number: int, step: dict, beam: list[Link],
                                   candidates: dict[str, Link]) -> None:
        description = "; ".join(c.get("description", "") for c in step.get("checks", []))
        if not description:
            return
        self.r._phase(f"Step {number}: searching by description ({description[:80]})…")
        searches = self._decide("step", {"research_language": self._research_language(),
                                          "description": description, "kind": step.get("kind", ""),
                                          "relation_with": f"{step['relation']} {beam[0].name}",
                                          "searches_already_done": self.step_searches[-12:]},
                                {"searches": [description[:120]]})["searches"]
        segments = self._search_and_read(searches, int(self.o.get("pages_per_step", 3)),
                                       purpose=f"{step['relation']} {beam[0].name} ({description[:200]})")
        proofs = [Proof(" ".join(s["text"].split())[:4000], s["url"]) for s in segments][:12]
        proofs += [p for p in self._snippets if p.url not in {x.url for x in proofs}]
        if not proofs:
            return
        listing = self._decide("candidates", {"description": description, "kind": step.get("kind", ""),
                                            "proofs": self._numbered(proofs), "again": description},
                              {"candidates": []})["candidates"]
        self._add(listing, proofs, beam[0], candidates, people=self._of_people(step))

    def _candidates_from_graph(self, beam: list[Link], candidates: dict[str, Link]) -> None:
        """If the relation sought gave no candidates, the entities already tied to the previous entity in the graph,
        with any tie, become candidates (the tie sought becomes a check). 15 Oct, Arduino: Jesse Vincent was in the
        graph as co-founder of Keyboardio, no page called him "spouse of Kaia Dekker"."""
        c = self.r.concepts
        url_of = {r["id"]: r["url"] for r in c.graph.db.execute(
            "SELECT s.id, f.url FROM segment s JOIN source f ON f.id = s.source_id")}
        for origin in beam:
            node = self._find(origin.name)
            if node is None:
                continue
            neighbours = 0
            for t in c.ties():
                if node not in (t["subject_id"], t["object_id"]) or neighbours >= MAX_CANDIDATES:
                    continue
                other = t["object"] if t["subject_id"] == node else t["subject"]
                key = _entity_key(other)
                if not key or key == _entity_key(origin.name) or key in candidates:
                    continue
                proof = Proof(f"{t['subject']} → {t['relation']} → {t['object']} «{t.get('quote') or ''}»",
                              url_of.get(t["segment_id"], ""))
                candidates[key] = Link(other, parent=origin, tie_proof=proof,
                                           context=f"{origin.name}: {origin.context}"[:150])
                neighbours += 1

    @staticmethod
    def _name_in_row(name: str, proof: Proof) -> bool:
        """The candidate really is in the row it cites: the name (or the surname) in the text, or a linked article of
        the row that is it (GENRE: the model chooses among names that are there, it does not invent them)."""
        text = _plan(proof.text)
        key = _plan(_name_key(name))
        if _plan(name) in text or (len(key) >= 4 and re.search(rf"\b{re.escape(key)}\b", text)):
            return True
        return any(matches(name, t) for t in proof.hyperlinks)

    @staticmethod
    def _whole_name(name: str, proofs: list[Proof]) -> str:
        """A one-word name («Kaia», «jesse») becomes the full form the proofs write most often («Kaia Dekker»): with
        only the first name the next step looks for namesakes (25 Oct, Arduino: «spouse of Kaia» → Andy Bell, Idha).
        Only First Last forms with capital initials, never another person with that surname."""
        words = name.split()
        if len(words) != 1 or len(name) < 3:
            return name
        forms: dict[str, int] = {}
        maiuscola = r"[A-ZÀ-Þ][\w'’-]+"
        for p in proofs:
            for m in re.finditer(rf"\b{re.escape(name[0].upper() + name[1:])}((?:\s+(?:van|von|de|da|di|del|der|le|la))?"
                                 rf"(?:\s+{maiuscola}){{1,2}})", p.text):
                forms[(name[0].upper() + name[1:] + m.group(1)).strip()] = forms.get(
                    (name[0].upper() + name[1:] + m.group(1)).strip(), 0) + 1
        if not forms:
            return name
        return max(forms.items(), key=lambda x: (x[1], -len(x[0])))[0]

    @staticmethod
    def _of_people(step: dict) -> bool:
        """The step looks for people (from the kind or the relation of the plan)."""
        text = _norm(f"{step.get('kind', '')} {step.get('relation', '')}")
        return bool(re.search(r"\b(person\w*|donna|uomo|coniuge|marito|moglie|fondat\w*|autor\w*|scrittor\w*|"
                              r"pittor\w*|architett\w*|scienziat\w*|presidente|sindaco|allievi|studente\w*|figli\w*|"
                              r"padre|madre|people|man|woman|founder\w*|spouse|wife|husband)\b", text))

    def _add(self, listing: list[dict], proofs: list[Proof], origin: Link,
                  candidates: dict[str, Link], people: bool = False) -> None:
        for c in listing:
            name = " ".join(str(c.get("name") or "").split())[:120]
            name = self._whole_name(name, proofs) if people else name
            key = _entity_key(name)
            if name and key and key != _entity_key(origin.name) and key not in candidates:
                proof = self._proof(proofs, c.get("proof"))
                if proof is None or not self._name_in_row(name, proof):
                    log.info("chain: candidate «%s» discarded: not in row %s", name, c.get("proof"))
                    continue
                candidates[key] = Link(name, parent=origin, tie_proof=proof,
                                                 context=f"{origin.name}: {origin.context}"[:150],
                                                 title=self._title_from_proof(name, proof))

    @staticmethod
    def _title_from_proof(name: str, proof: Proof | None) -> tuple[str, str] | None:
        """The candidate's Wikipedia article, if the row pointing to it links it (no namesakes possible)."""
        if proof is None or not proof.hyperlinks or not proof.language:
            return None
        key = _name_key(name)
        # First the best matching title (exact, variant, parentheses, base), then the surname: «La Thuile (Italia)»
        # must not take another link of the same row.
        # «base» (one-word name inside a long title) is not enough here: 25 Oct, Sistine Chapel: «Napoli» → «Regno di
        # Napoli»; the surname is handled below, with at most one extra word (Enrico Fermi).
        degrees = [(DEGREES.get(matches(name, t), 0.0), -i, t) for i, t in enumerate(proof.hyperlinks)]
        best = max(degrees, default=(0.0, 0, ""))
        if best[0] > DEGREES["base"]:
            return (proof.language, best[2])
        # The surname is enough only for a one-word name («Fermi»): with first name and surname another link with the
        # same surname is another person (24 Oct: Paul McCartney → Linda McCartney, Michael Dell → Susan Dell,
        # Guiniforte Solari → Giovanni Solari; 57 wrong articles out of 523 recorded verifications, mostly from here).
        if len([p for p in _norm(re.sub(r"\([^)]*\)", " ", name)).split() if len(p) >= 4]) != 1:
            return None
        for title in proof.hyperlinks:
            words = _norm(re.sub(r"\([^)]*\)", " ", title)).split()
            # at most one extra word (Enrico Fermi, Adafruit Industries; not «Microsoft ergonomic keyboards»)
            if len(key) >= 4 and key in words and len(words) <= 2:
                return (proof.language, title)
        return None

    def _segments_on(self, name: str) -> list[dict]:
        """Already saved segments where the name appears (read in previous steps)."""
        key = _name_key(name)
        return [s for s in self._after_segments(0) if key in s["text"].lower()][:12]

    def _verification(self, candidates: list[Link], checks: list[dict], all_items: bool = False) -> list[Link]:
        """Checks on all the candidates; a "no" with a proof discards. For the best ones with some "unknown" the
        infobox is looked up and the verification is redone once."""
        if not candidates:
            return []
        if not checks:
            return candidates
        self._wiki_starts(candidates)
        self._outcomes(candidates, checks)
        alive = [a for a in candidates if not self._refuted(a)]
        doubts = [a for a in sorted(alive, key=lambda a: -a.quality)
                 if any(e["outcome"] in ("unknown", "partly") for e in a.outcomes)]
        # With Wikipedia the infobox costs one request: all doubts are looked into (16 Oct, Galileo: Zucchi "rector of
        # the Collegio Romano" only in the body of the article, outside the top three by points).
        from_deepen = doubts[:int(self.o.get("profiles_per_step", 8 if self.wiki is not None else 3))]
        if from_deepen and not self.r._reached_limit():
            from_web = 0
            for a in from_deepen:
                # The profile is first of all the whole Wikipedia article (infobox and text), then the web (at most 3).
                if not a.searched_articles and self._articles(a, other_ones=0):
                    continue
                if a.articles or from_web >= 3:
                    continue
                from_web += 1
                self.r._phase(f"Looking up the profile of {a.name}…")
                words = " ".join(_norm(a.parent.name).split()[:3]) if a.parent else ""
                self._search_and_read([f"{a.name} {words}".strip(), self._check_research(a)],
                                    int(self.o.get("profile_pages", 2)))
            self._outcomes(from_deepen, checks)
            alive = [a for a in candidates if not self._refuted(a)]
        # After the profile an "unknown" counts against: those with more than half the weight of the checks proven
        # pass (15 Oct: Viviani, neither Jesuit nor Bolognese, passed because no page denied it). If nobody passes,
        # the best stays, marked weak. With equal quality the name decides, so two identical runs choose the same.
        ordered = sorted(alive, key=lambda a: (-a.quality, not self._co_occurs(a), -a.points, a.name))
        # Strong only with the tie proven (16 Oct, Blender: Traun, Wels and Gmunden "strong" because towns of Upper
        # Austria on the Traun, without any row saying the developer was born there).
        with_tie = any(str(c.get("description", "")).startswith("is in relation «") for c in checks)
        linked = lambda a: not with_tie or any(  # noqa: E731
            e["outcome"] == "yes" and e["proof"] and str(e["check"]).startswith("is in relation") for e in a.outcomes)
        # A tie proven only by a search snippet (page never read) does not make a strong link.
        strong = [a for a in ordered if a.quality > 0.5 and linked(a) and not self._decisive_unknown(a)
                 and self._identified(a) and not validation.tie_only_from_snippets(a)]
        relation = next((str(c.get("description", "")) for c in checks
                          if str(c.get("description", "")).startswith("is in relation «")), "")
        for article in validation.disagreements([a for a in candidates if a not in alive], relation):
            if article not in self.conflicts:
                self.conflicts.append(article)
        if strong:
            # Behind the strong ones the unrefuted ones remain, marked weak: if the strong one does not lead to the
            # value, the chain tries them (16 Oct, Linux kernel: Islene Calciolari Garcia without a birth date,
            # Alexandre Oliva behind).
            others = [a for a in ordered if a not in strong]
            # With the frontier all the unrefuted ones remain (16 Oct, Monte Bianco: La Thuile, correct, outside the
            # three kept with equal points); without it, only as many as the beam needs.
            others = others[:int(self.o.get("max_children", 6))] if all_items else \
                others[:max(0, int(self.o.get("chain_width", 3)) - 1)]
            for a in others:
                a.weak = True
            return strong + others
        # Without strong candidates up to `width` unrefuted weak ones remain, even without "yes": the next step
        # verifies them anyway (15 Oct: Joinville, correct answer, discarded because the pages did not say
        # "Haute-Marne").
        weak_ones = ordered[:int(self.o.get("max_children", 6) if all_items else self.o.get("chain_width", 3))]
        for a in weak_ones:
            a.weak = True
        return weak_ones

    @staticmethod
    def _check_research(a: Link) -> str:
        """Second search of the web profile: the name with the heaviest check not yet proven (first reading or
        description, content words), not «biography» (24 Oct, Arduino: «Seeed Studio biografia», «Cytron Technologies
        biografia» for companies that were candidates for «makes programmable keyboards»)."""
        pending = sorted((e for e in a.outcomes if e["outcome"] in ("unknown", "partly")
                         and not str(e["check"]).startswith(("is in relation", "is of this kind"))),
                        key=lambda e: -float(e.get("weight") or 1))
        if not pending:
            return f"{a.name} biography"
        text = next(iter(pending[0].get("readings") or []), "") or str(pending[0]["check"])
        words = [p for p in re.findall(r"[\w'’-]+", text) if len(p) >= 4 and _norm(p) not in STOP_WORDS]
        return f"{a.name} {Chain._short_context(a)} {' '.join(words[:5])}".replace("  ", " ").strip()

    @staticmethod
    def _short_context(a: Link) -> str:
        """One or two words of the entity from which the candidate was found, for web searches without namesakes
        («Roland Unger Wikivoyage»), without words already in the name."""
        if a.parent is None:
            return ""
        name = set(_norm(a.name).split())
        words = [p for p in re.sub(r"\([^)]*\)", " ", a.parent.name).split()
                  if len(p) >= 3 and _norm(p) not in name and _norm(p) not in STOP_WORDS]
        return " ".join(words[:2])

    def _order(self, a: Link) -> tuple:
        """First those coming from a strong link, then those with the tie proven, then more yes (16 Oct, Milan
        Cathedral: «Francesco (nome)» found from a weak architect went ahead of Simone da Orsenigo, strong)."""
        return (bool(a.parent and a.parent.weak), not self._linked(a), -a.quality, not self._co_occurs(a), a.name)

    @staticmethod
    def _linked(a: Link) -> bool:
        """The tie with the previous entity has a yes with a proof (16 Oct, Hubble: Ken Mattingly, never on STS-103,
        ahead of Grunsfeld in the reserve because he had more yes on the other checks)."""
        return any(e["outcome"] == "yes" and e["proof"] and str(e["check"]).startswith("is in relation")
                   for e in a.outcomes)

    def _co_occurs(self, a: Link) -> bool:
        """Graph logic, without the model: the candidate appears in the same saved segments (or in the same article
        rows) as the previous entity. An unproven tie with co-occurrence is worth more than one found only by
        description (22 Oct, Blender with Sonnet: Bad Ischl, Gmunden, Traun and Wels tied as Robert Szeleney's
        hometown; the first in alphabetical order won)."""
        if a.parent is None:
            return True
        key = _name_key(a.name)
        if len(key) < 4:
            return False
        if not hasattr(self, "_co_occurrences"):
            self._co_occurrences: dict[tuple[str, str], bool] = {}
        index = (_norm(a.parent.name), key)
        if index not in self._co_occurrences:
            texts = [s["text"] for s in self._segments_on(a.parent.name)]
            texts += [r.text for v in a.parent.articles.values() for r in v.rows]
            self._co_occurrences[index] = any(key in _norm(t).split() for t in texts)
        return self._co_occurrences[index]

    @staticmethod
    def _decisive_unknown(a: Link) -> bool:
        """A weight-3 check (it almost identifies it alone) without proof: the candidate is not confirmed, even if the
        other checks pass (17 Oct, Duchy of Milan: six neighbouring towns "strong" with equal points, none twinned
        with a village in Haute-Vienne)."""
        return any(_weight(e) >= 3 and e["outcome"] in ("unknown", "no") for e in a.outcomes)

    @staticmethod
    def _identified(a: Link) -> bool:
        """At least one identifying trait (not the kind, not the tie, weight ≥ 2) has a yes with a proof, if the plan
        has any (23 Oct, paper §9.1: Guiducci «strong» with only the tie and «Bolognese Jesuit» not proven)."""
        own = [e for e in a.outcomes if _weight(e) >= 2
                  and not str(e["check"]).startswith(("is of this kind", "is in relation"))]
        return not own or any(e["outcome"] == "yes" and e["proof"] for e in own)

    @staticmethod
    def _doubts(a: Link) -> int:
        return sum(1 for e in a.outcomes if e["outcome"] == "unknown")

    @staticmethod
    def _complete(a: Link) -> bool:
        """All the checks that identify it (not the kind) have a yes with a proof."""
        own = [e for e in a.outcomes if not str(e["check"]).startswith("is of this kind")]
        return bool(own) and all(e["outcome"] == "yes" and e["proof"] for e in own)

    @staticmethod
    def _no(a: Link) -> int:
        return sum(1 for e in a.outcomes if e["outcome"] == "no" and e["proof"])

    def _refuted(self, a: Link) -> bool:
        """Discarded if the "no" with a proof are at least as many as the "yes": a single "no" against several "yes" is
        often too strict a reading (16 Oct, Git: GitHub discarded for «opened to the public in spring 2008», which is
        the value sought, not a trait to recognise it). `points` = yes minus no."""
        no = sum(_weight(e) for e in a.outcomes if e["outcome"] == "no" and e["proof"])
        si = sum(_weight(e) * OUTCOME_VALUE[e["outcome"]] for e in a.outcomes if e["outcome"] in ("yes", "partly")
                 and e["proof"] and not str(e["check"]).startswith(("is of this kind", "is in relation")))
        # kind and tie do not offset a "no": they say the candidate is of the right sort, not that it is the right one
        return no > 0 and si <= no

    def _wiki_starts(self, candidates: list[Link]) -> None:
        """The first sentences of the articles of the candidates reached through a link, with one request per
        language (the whole article is read only for those still in doubt: Wikipedia allows 10 requests per
        minute)."""
        if self.wiki is None:
            return
        per_language: dict[str, list[Link]] = {}
        for a in candidates:
            if a.title and not a.articles and not a.start:
                per_language.setdefault(a.title[0], []).append(a)
        for language, group in per_language.items():
            starts = self.wiki.starts([a.title[1] for a in group], language)
            for a in group:
                text = starts.get(a.title[1]) or next((t for k, t in starts.items()
                                                        if matches(a.title[1], k) in ("exact", "variant")), "")
                if text:
                    a.start = Proof(f"[{a.title[1]} ({language}), start of the article] {text}",
                                     self.wiki.url(a.title[1], language), [], language)

    def _outcomes(self, candidates: list[Link], checks: list[dict]) -> None:
        """Verification in groups of 2 candidates, in parallel: shorter requests (less confusion between the
        candidates' rows) and faster. The proofs are prepared beforehand, in the main thread (graph and browser)."""
        groups = []
        for k in range(0, len(candidates), 2):
            group = candidates[k:k + 2]
            proofs: list[Proof] = []
            blocks, intervals = [], {}
            for a in group:
                mine = self._proofs_wiki_candidate(a, checks) if a.articles else ([a.start] if a.start else [])
                mine += self._entity_proofs(a.name, segments=self._segments_on(a.name), maximum=12 if not mine else 5)
                if a.tie_proof and a.tie_proof not in mine:
                    mine.insert(0, a.tie_proof)
                start = len(proofs) + 1
                proofs += mine
                intervals[id(a)] = range(start, start + len(mine))
                blocks.append(f"CANDIDATE: {a.name}\n" + "\n".join(
                    f"[{start + i}] {p.text[:1000]}" for i, p in enumerate(mine)))
            groups.append((group, proofs, blocks, intervals))
        checks_text = [f"C{i}: {c.get('description', '')} (readings: {'; '.join(c.get('readings') or [])})"
                           for i, c in enumerate(checks, start=1)]
        ask = lambda blocks: self._decide(  # noqa: E731
            "verification", {"checks": checks_text, "candidates": "\n\n".join(blocks)}, {"candidates": []})
        # Two independent readings per group (self-consistency, as in Open Deep Search): a "yes" counts only if both
        # give it, a "no" is enough in one (16 Oct: Bobrow "awarded in 2009", the Marseillaise "dedicated to a
        # Bourbon": invented yes that a second reading rarely repeats).
        readings = int(self.o.get("verification_readings", 2))
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(ask, [g[2] for g in groups for _ in range(readings)]))
        for n, (group, proofs, _, intervals) in enumerate(groups):
            first_items: dict[int, list[dict]] = {}
            # The readings finish in random order and the first gives the cited row: fixed order, repeatable runs.
            for outcome in sorted(outcomes[n * readings:(n + 1) * readings],
                                key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)):
                self._apply_outcomes(group, proofs, intervals, outcome, checks)
                for a in group:
                    first_items[id(a)] = self._merge_outcomes(first_items[id(a)], a.outcomes) if id(a) in first_items else a.outcomes
            for a in group:
                a.outcomes = first_items[id(a)]
                a.points = sum(1 for e in a.outcomes if e["outcome"] == "yes" and e["proof"]) - self._no(a)
                a.quality = quality(a.outcomes)

    @staticmethod
    def _merge_outcomes(leading: list[dict], seconds: list[dict]) -> list[dict]:
        key = lambda e: _norm(e["check"])  # noqa: E731
        others = {key(e): e for e in seconds}
        merged_items = []
        for e in leading:
            f = others.get(key(e), {"outcome": "unknown", "proof": None})
            positives = {"yes", "partly"}
            if e["outcome"] == "no" and e["proof"] or f["outcome"] == "no" and f["proof"]:
                merged_items.append(e if e["outcome"] == "no" and e["proof"] else {**e, "outcome": "no", "proof": f["proof"]})
            elif e["outcome"] == "yes" and e["proof"] and f["outcome"] == "yes" and f["proof"]:
                merged_items.append(e)
            elif e["outcome"] in positives and e["proof"] and f["outcome"] in positives and f["proof"]:
                merged_items.append({**e, "outcome": "partly"})  # one reading says only "partly": it counts half
            else:
                merged_items.append({**e, "outcome": "unknown", "proof": None})
        return merged_items

    @staticmethod
    def _resolve_check(text: str, checks: list[dict]) -> dict | None:
        """The check Haiku is answering: from the code (C2) or, if it rewrote the text, from the start of the text."""
        m = re.match(r"\s*C(\d+)\b", str(text or ""))
        if m and 1 <= int(m.group(1)) <= len(checks):
            return checks[int(m.group(1)) - 1]
        n = _norm(text)
        for c in checks:
            d = _norm(c.get("description"))
            if d and n and (n.startswith(d[:40]) or d.startswith(n[:40]) or (len(d) >= 4 and d in n)):
                return c
        return None

    def _apply_outcomes(self, candidates: list[Link], proofs: list[Proof], intervals: dict[int, range],
                       outcome: dict, checks: list[dict] | None = None) -> None:
        per_name = {_norm(x.get("name")): x for x in outcome.get("candidates", [])}
        checks = checks or []
        precise_years = {}
        for c in checks:
            text = str(c.get("description") or "")
            if not re.search(r"\b(anni|circa|metà|inizio|fine|primi|ultimi|secolo|tra|decennio)\b", text, re.I):
                precise_years[_norm(text)] = set(re.findall(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b", text))
        for a in candidates:
            # Il modello a volte riscrive il nome: allora vale il cognome, ma solo se in questo gruppo lo porta un solo
            # candidato e una sola risposta (due «Verdi»: nessun esito, meglio «non so» che l'esito dell'altro).
            surname = _name_key(a.name)
            same = [x for k, x in per_name.items() if surname in k.split()]
            single = len(same) == 1 and sum(_name_key(b.name) == surname for b in candidates) == 1
            answer = per_name.get(_norm(a.name)) or (same[0] if single else {"checks": []})
            # Controllo del programma: la prova di un "sì" o di un "no" deve stare nel blocco DEL candidato (16/10,
            # Galileo: Viviani e Guiducci "gesuiti di Bologna" con le righe di altri candidati).
            mine = intervals.get(id(a), range(0))
            owned = {v.url for v in a.articles.values()} | ({a.start.url} if a.start else set())
            key = _name_key(a.name)
            a.outcomes = []
            answers = []
            for e in answer.get("checks", []):
                c = self._resolve_check(e.get("check", ""), checks) if checks else None
                if checks and c is None:
                    continue
                answers.append({**e, "check": c["description"] if c else e.get("check", ""),
                                 "weight": c.get("weight") if c else None})
            # Ogni controllo ha un esito, anche quelli su cui Haiku tace: "non so" (pesano nel conto della qualità).
            data = {_norm(e["check"]): e for e in answers}
            answers = [data.get(_norm(c["description"])) or {"check": c["description"], "outcome": "unknown",
                                                                      "proof": 0, "weight": c.get("weight")}
                        for c in checks] if checks else answers
            for e in answers:
                number = e.get("proof")
                is_valid = isinstance(number, int) and number in mine
                if is_valid:  # e la riga parla del candidato: sua voce, riga del legame o riga che lo nomina
                    p = proofs[number - 1]
                    is_valid = p.url in owned or p is a.tie_proof or key in _norm(p.text)
                    # e le parole citate da Haiku sono davvero nella riga (16/10, Emacs: Daniel Murphy "premiato nel
                    # 2009" con una riga che non ne parla)
                    is_valid = is_valid and present_quote(str(e.get("quote") or ""), p.text)
                    # un anno preciso nel controllo deve stare nelle parole citate (Bobrow "premiato nel 2009")
                    years = precise_years.get(_norm(e.get("check", "")), set())
                    is_valid = is_valid and (not years or any(x in str(e.get("quote") or "") for x in years))
                a.outcomes.append({"check": e.get("check", ""), "weight": e.get("weight") or 1,
                                "outcome": e.get("outcome", "unknown") if is_valid or e.get("outcome") == "unknown" else "unknown",
                                "proof": self._proof(proofs, number) if is_valid else None,
                                # per capire dopo chi ha deciso: l'esito detto dal modello e la riga che ha citato
                                "said": e.get("outcome", "unknown"), "cited": self._proof(proofs, number)})
            a.points = sum(1 for e in a.outcomes if e["outcome"] == "yes" and e["proof"]) - self._no(a)
            a.quality = quality(a.outcomes)

    # --- 3. valore ---------------------------------------------------------------------------------------------

    def _value(self, beam: list[Link], others: list[Link] | None = None) -> None:
        """Il valore dalle voci di Wikipedia dell'entità finale (riquadro, inizio, righe con le parole della
        risposta), poi dalle pagine lette, poi da due pagine nuove."""
        for a in beam + (others or []):
            if self._entity_value(a, (1, 2, 3) if a in beam else (1,)):
                return
        self.reason = self.reason or "valore richiesto non trovato"

    def _entity_value(self, a: Link, attempts: tuple[int, ...]) -> bool:
        """Tentativi per il valore di un'entità: 1 = voci di Wikipedia, 2 = pezzi già letti (poi due pagine nuove),
        3 = pezzi letti di nuovo. Vero se il valore è stato trovato (in self.valore)."""
        answer = self.plan.get("answer") or {}
        if answer.get("is_entity"):  # niente ricerca del «valore» sul web: il valore è l'entità
            return self._value_is_entity(a) if 1 in attempts else False
        words = list(answer.get("words") or []) + [answer.get("what", "")] + list(answer.get("checks") or [])
        bonus = self._bonus_value(answer)
        for attempt in attempts:
            if attempt == 1:
                proofs = self._wiki_rows(self._articles(a), words, maximum=60, chars=14_000, bonus=bonus)
                if not proofs:
                    continue
            else:
                proofs = self._entity_proofs(a.name, segments=self._segments_on(a.name), maximum=40)
                if attempt == 3:
                    # Anche gli estratti dei risultati della ricerca del valore che nominano l'entità (29/10, Congresso
                    # di Vienna: «Sophie … Stackelberg (фон Мейендорф…) (1806 - 1891)» nell'estratto di Geni, pagina
                    # mai letta: si leggono solo 2 pagine).
                    key = _name_key(a.name)
                    proofs += [p for p in self._snippets if key in _norm(p.text)][:8]
            outcome = self._decide("value", {"question": self.r.topic[:1000], "entity": a.name,
                                            "what": answer.get("what", ""),
                                            "checks": answer.get("checks", []),
                                            **self._value_constraints(answer),
                                            "proofs": self._numbered(proofs)}, {"value": "", "proof": 0})
            proof = self._proof(proofs, outcome.get("proof"))
            rejection = allowed_value(outcome.get("value", ""), answer) if outcome.get("value") else ""
            if rejection:  # il programma, non Haiku, decide se il valore rispetta tipo e intervallo
                self.history.append({"step": -1, "relation": "valore rifiutato", "attempt": attempt,
                                    "candidates": [a.name], "kept": [],
                                    "discarded": [(f"{outcome.get('value')}", [rejection])]})
                proof = None
            # Il valore deve essere scritto nella riga citata: il modello può tradurre una data, non cambiarla.
            if proof is not None and not validation.value_in_row(outcome.get("value", ""), proof.text):
                self.history.append({"step": -1, "relation": "valore non nella riga", "attempt": attempt,
                                    "candidates": [a.name], "kept": [],
                                    "discarded": [(str(outcome.get("value")), [proof.text[:200]])]})
                proof = None
            # La riga del valore deve parlare dell'entità: sua voce o suo nome (16/10, Ducato di Milano:
            # l'altitudine di Bereguardo presa dalla voce di Vigevano).
            if proof is not None and not (proof.url in {v.url for v in a.articles.values()}
                                          or _name_key(a.name) in _norm(proof.text)):
                proof = None
            if outcome.get("value") and proof:
                self.value = {"value": outcome["value"], "proof": proof, "link": a,
                               "reason": outcome.get("reason", ""), "proofs": proofs}
                self.reason = f"catena completa: {a.name} → {outcome['value']}"
                return True
            self.history.append({"step": -1, "relation": "valore non trovato", "attempt": attempt,
                                "candidates": [a.name], "kept": [],
                                "discarded": [(str(outcome.get("value") or "(vuoto)"),
                                              [f"{len(proofs)} righe", "voci: " + ", ".join(
                                                  f"{v.title} ({v.language})" for v in a.articles.values()),
                                               str(outcome.get("reason") or "")[:150]])]})
            if attempt == 2 and not self.r.thinker.exhausted and not self.r._reached_limit():
                self._add_pages_value()
                # le parole della risposta, nella lingua della ricerca (le prime del piano), non la frase italiana
                key = " ".join(str(x) for x in (answer.get("words") or [])[:2]) or answer.get("what", "")
                # Mai il nome da solo: con una parola dell'entità di prima si evitano gli omonimi più famosi (25/10,
                # Wikipedia: «Roland Unger» da solo dava il pittore nato nel 1941, non il presidente di Wikivoyage e.V.).
                # Anche il nome da solo (pagine di profilo che non nominano il contesto), ma le 2 pagine si scelgono
                # per lo scopo, con il contesto (25/10, Wikipedia: senza il nome da solo la pagina di Commons di
                # Roland Unger non arrivava più; con il solo nome e nessuno scopo arrivava il pittore omonimo).
                context = self._short_context(a)
                self._search_and_read(list(dict.fromkeys([f"{a.name} {key}"[:120], f"{a.name} {context}".strip(),
                                                        a.name])), 2,
                                    purpose=f"{answer.get('what', '')} di {a.name}" + (f" ({a.parent.name})" if a.parent else ""))
        return False

    def _add_pages_value(self, maximum: int = 6) -> None:
        """Due pagine in più per cercare il valore, senza che il tetto della domanda cresca senza fine."""
        if self._added_pages < maximum:
            self._added_pages += 2
            self.o["max_pages"] = int(self.o["max_pages"]) + 2

    def _value_is_entity(self, a: Link) -> bool:
        """La domanda chiede il nome dell'entità dell'ultimo passo (piano: risposta.e_entita): il valore è lei, con la
        riga che la lega all'anello di prima o l'inizio della sua voce. (24/10, Cappella Sistina: «in quale comune si
        trova il simulacro» con ultimo passo «comune in cui si trova»; Haiku cercava nelle righe del comune «il nome
        del comune del simulacro» e rispondeva vuoto, la catena finiva senza risposta.) Chi è giusto lo decidono i
        controlli del passo, già fatti. Ma il legame con l'anello di prima deve avere una riga (sì o in parte): un
        valore cercato fa da filtro, il nome dell'entità no (25/10, Cappella Sistina: Capri Leone, trovato solo per
        descrizione, legame «non so», qualità 0,4, dato come risposta; giusto il 24/10 Novara di Sicilia, legame «in
        parte» con la riga sulla statua dell'Assunta)."""
        if a.quality < 0.5 and a.outcomes:  # più di metà del peso dei controlli (Napoli 0,4; Novara di Sicilia 0,6)
            return False
        if a.parent is not None and not any(e["outcome"] in ("yes", "partly") and e["proof"]
                                        and str(e["check"]).startswith("is in relation") for e in a.outcomes):
            return False
        proof = a.tie_proof or a.start
        if proof is None:
            article = next(iter(self._articles(a)), None)
            sentence = next((r for r in article.rows if r.kind == "sentence"), None) if article is not None else None
            if sentence is None:
                return False
            proof = Proof(f"[{article.title} ({article.language})] {sentence.text}", article.url, sentence.hyperlinks, article.language)
        self.value = {"value": a.name, "proof": proof, "link": a,
                       "reason": "la domanda chiede l'entità dell'ultimo passo"}
        self.reason = f"catena completa: la risposta è l'entità {a.name}"
        return True

    @staticmethod
    def _bonus_value(answer: dict):
        """Righe bersaglio (DS4A): il programma porta avanti le righe che contengono il tipo di dato chiesto, e ancora
        di più quelle con un valore nell'intervallo, prima che Haiku le veda."""
        kind = str(answer.get("value_kind") or "")
        forms = {"full date": rf"\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b|\b(?:{MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b",
                 "day and month": rf"\b\d{{1,2}}\s+(?:{MONTHS})\b|\b(?:{MONTHS})\s+\d{{1,2}}\b",
                 "year": rf"\b(?:1\d{{3}}|20\d{{2}})\b|{ERA.pattern}", "number": r"\d", "code": r"\b\d{4,6}\b"}
        form = forms.get(kind)
        interval = isinstance(answer.get("min"), (int, float)) or isinstance(answer.get("max"), (int, float))
        if form is None and not interval:
            return None

        def bonus(text: str) -> int:
            points = 0
            if form and re.search(form, text, re.I):
                points += 30
                if interval:
                    pieces = re.findall(r"[^;|·]{0,40}?\d[\d.,\u202f\u00a0 ]*", text)
                    if any(not allowed_value(x, answer) for x in pieces):
                        points += 60
            return points
        return bonus

    @staticmethod
    def _value_constraints(answer: dict) -> dict:
        constraints = {"value_kind": answer.get("value_kind", "")}
        if isinstance(answer.get("min"), (int, float)) or isinstance(answer.get("max"), (int, float)):
            constraints["interval"] = f"{answer.get('min', '…')} - {answer.get('max', '…')}"
        return constraints

    # --- 4. scrittura --------------------------------------------------------------------------------------------

    def material(self) -> str:
        """La catena trovata con le prove, citate con il numero della fonte."""
        number = lambda url: self.r._source_number(url) if url not in {f["url"] for f in self.r.sources} else \
            next(f["number"] for f in self.r.sources if f["url"] == url)  # noqa: E731
        chain = []
        a = self.value["link"] if self.value else (self.links[-1][0] if self.links else None)
        while a is not None:
            chain.append(a)
            a = a.parent
        root = chain[-1] if chain else None
        rows = [f"PARTENZA: {root.name if root else self.plan.get('starting_point', {}).get('entity', '')}"
                 + (f" (entità nominata dalla domanda, «{root.relation}» porta al passo {root.base_step + 1})"
                    if root is not None and root.base_step else "")]
        steps = self.plan.get("steps", [])
        for i, a in enumerate(reversed(chain[:-1])):
            relation = a.relation or (steps[(root.base_step if root else 0) + i]["relation"]
                                        if (root.base_step if root else 0) + i < len(steps) else "")
            rows.append(f"\nANELLO «{relation}» {a.parent.name if a.parent else ''} → {a.name}"
                         + (" (NON CONFERMATO del tutto: i controlli non sono tutti provati, ma nessuna prova lo smentisce)"
                            if a.weak else ""))
            if a.tie_proof:
                rows.append(f"- [{number(a.tie_proof.url)}] {a.tie_proof.text[:600]}")
            for e in a.outcomes:
                if e["proof"]:
                    rows.append(f"- controllo «{e['check']}»: {e['outcome']} [{number(e['proof'].url)}] "
                                 f"{e['proof'].text[:500]}")
        if self.value:
            rows.append(f"\nVALORE CERCATO ({self.plan.get('answer', {}).get('what', '')}): "
                         f"{self.value['value']} [{number(self.value['proof'].url)}] "
                         f"{self.value['proof'].text[:600]}")
        contrary = validation.disagreements_on_route(self.conflicts, self.value["link"]) if self.value else []
        if contrary:
            rows.append("\nFONTI IN DISACCORDO CON LA CATENA (scrivile nella riga **Fonti in disaccordo** e abbassa la "
                         "**Fiducia**):")
            rows += [f"- [{number(x['url'])}] per «{x['relation']}» di {x['parent']} la fonte indica {x['name']}: "
                      f"{x['row']} — scartato perché non rispetta: {'; '.join(x['against'])}" for x in contrary]
        if self.value and self.exams:
            outcome = self.checks()
            if outcome["reasons"]:
                rows.append("\nCONTROLLI DEL PROGRAMMA SULLA RISPOSTA (riportali nei **Limiti**; se una fonte dà un altro "
                             "valore, anche in **Fonti in disaccordo**): " + "; ".join(outcome["reasons"]))
            for name in ("second_reading", "corroboration"):
                exam = self.exams.get(name) or {}
                if exam.get("outcome") == "disagree":
                    rows.append(f"- [{number(exam['url'])}] altro valore: {exam['value']} — {exam['row']}")
        others = [x for beam in self.links[-1:] for x in beam if not chain or x is not chain[0]]
        if others:
            rows.append("\nALTRI CANDIDATI DELL'ULTIMO PASSO: " + "; ".join(f"{x.name} ({x.points} controlli sì)"
                                                                            for x in others))
        return "\n".join(rows)

    def write(self, task: str) -> str | None:
        if not self.links:
            return None
        self.r._phase("Claude scrive la risposta dalla catena trovata…")
        readings = [f"{i.get('clue')}: {'; '.join(i.get('readings') or [])}"
                   for i in self.interpretation.get("clues", []) if len(i.get("readings") or []) > 1]
        text = self.r.thinker.ask(
            "writing", instructions("writing"),
            f"TOPIC: {self.r.topic}\nTODAY: {self.r.today}\n"
            + ("INDIZI AMBIGUI (se la risposta dipende da una lettura, dillo): " + " | ".join(readings) + "\n"
               if readings else "")
            + f"NOTES (la catena percorsa, con le prove e la loro fonte [n]):\n<<<DATI\n{self.material()}\nDATI>>>"
            + (f"\n\nLa catena non è completa: {self.reason}. Nella riga **Risposta:** scrivi «Non trovata» e "
               "quale anello manca." if not self.value else
               "\n\nLa catena è completa: nella riga **Risposta:** dai il VALORE CERCATO con la sua fonte. Se un anello "
               "è segnato NON CONFERMATO, rispondi comunque con il valore, dillo nei **Limiti** e abbassa la "
               "**Fiducia**: non scrivere «Non trovata».")
            + f"\n\nCOMPITO:\n{task}")
        self.r.cost = max(self.r.cost, self.r.thinker.ledger.spent)
        if not isinstance(text, str) or not text.strip():
            return None
        return self._confidence_from_program(self._value_in_head(text.strip()))

    def _confidence_from_program(self, text: str) -> str:
        """La riga **Fiducia:** la scrive il programma dallo stato dei controlli, non il modello che scrive (11/11,
        domande di controllo: risposte sbagliate con fiducia «alta» e «media»). Solo se i controlli con il modello sono
        stati fatti: altrimenti resta quella del modello."""
        if not self.value or not self.exams:
            return text
        outcome = self.checks()
        return validation.with_confidence(text, outcome["status"], outcome["reasons"])

    def _value_in_head(self, text: str) -> str:
        """La riga **Risposta:** la scrive il programma con il valore trovato e la sua fonte; quello che Haiku aveva
        scritto lì (spiegazione e dubbi) va nella riga **Spiegazione:**. (16/10, Firefox e Raspberry Pi: valore giusto
        trovato, ma la riga di Haiku era piena di dubbi e non si capiva quale fosse la risposta.)"""
        if not self.value:
            return text
        value = " ".join(str(self.value["value"]).split())
        url = self.value["proof"].url
        number = next((f["number"] for f in self.r.sources if f.get("url") == url), None)
        row = f"**Risposta:** {value}" + (f" [{number}]" if number else "") + "."
        found = re.search(r"\*\*Risposta:?\*\*:?[ \t]*([^\n]*)", text)
        if found is None:
            return f"{row}\n\n{text}"
        rest = found.group(1).strip()
        if _norm(rest.replace("*", "")).startswith(_norm(value)) and len(rest) <= len(value) + 20:
            return text
        fresh = row + (f"\n\n**Spiegazione:** {rest}" if rest else "")
        return text[:found.start()] + fresh + text[found.end():]

    def summary(self) -> dict:
        return {"interpretation": self.interpretation, "plan": self.plan, "history": self.history,
                "value": {"value": self.value["value"], "of": self.value["link"].name,
                           "url": self.value["proof"].url,
                           "row": " ".join(str(self.value["proof"].text).split())[:600]} if self.value else None,
                "reason": self.reason, "actions": self.actions, "checks": self.checks(),
                "discarded_value": self.discarded["value"] if self.discarded else None,
                # le righe della soluzione con la loro pagina: per ricontrollare il documento sulle fonti (fedelta.py)
                "proofs": [{"url": p.url, "row": " ".join(p.text.split())[:500]}
                          for p in {(p.url, p.text): p for p in self._route_rows()}.values()],
                "wikipedia": {"articles": self.wiki.read_count, "requests": self.wiki.requests, "cap": self.wiki.exhausted}
                if self.wiki is not None else None}
