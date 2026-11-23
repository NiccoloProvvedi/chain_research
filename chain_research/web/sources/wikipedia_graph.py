"""Wikipedia as a graph (16 Oct, ideas from the PDFs in reference/): articles are the nodes, links between articles
the edges, the sentence holding the link (or the infobox row) tells what kind of relation it is.

- Open Deep Search: dedicated handling of the main sites (Wikipedia) instead of the generic page;
- Think-on-Graph: the neighbours of a node are explored (outgoing links and, if needed, incoming ones) and the model
  chooses only among a few numbered edges;
- HierSearch: for an entity, the pieces of text that name it are taken (like `get_adjacent_passages`).

Everything goes through the public MediaWiki API (`api.php`): article text (`action=parse`), search (`list=search`),
article starts (`prop=extracts`), linking articles (`list=backlinks`), languages (`langlinks`). No model calls:
sentences, infoboxes and links are extracted by the program. No Wikidata (bench exams).
"""
import logging
import re
import unicodedata
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

import lxml.html

from ...model import replay
from .base import SourceError
from .config import email_account_claude, secrets
from .http import SourceClient
from .limits import limiter

log = logging.getLogger("chain_research.sources")
USER_AGENT = "chain-research/0.1 (personal use)"


VALID_CONTACT = re.compile(r"[^\s;()<>\"]{3,120}")
TEMPORARY_ERROR = "_temporary_error"  # key of the answer when Wikipedia did not respond


def valid_contact(contact: str) -> bool:
    """An email or a web address, without characters that would break the User-Agent."""
    return bool(VALID_CONTACT.fullmatch(contact)) and ("@" in contact or contact.startswith(("http://", "https://")))


def wikipedia_contact() -> tuple[str, str]:
    """(contact, origin) for the Wikipedia User-Agent. Origin: "settings" (written by the user, wins),
    "account" (the Claude account email, 1 Nov) or "" (no contact: switched off or invalid). It lives in
    config/secrets.json, outside git, and goes ONLY into requests to Wikipedia (the client accepts only
    wikipedia.org)."""
    data = secrets()
    if data.get("wikipedia_contact_active") is False:
        return "", ""
    written = str(data.get("wikipedia_contact") or "").strip()
    if written:
        return (written, "settings") if valid_contact(written) else ("", "")
    email = email_account_claude()
    return (email, "account") if valid_contact(email) else ("", "")


def user_agent() -> tuple[str, bool]:
    """The User-Agent for Wikipedia and whether it contains a contact (see wikipedia_contact)."""
    contact, _ = wikipedia_contact()
    if contact:
        return f"ricerca-catena/0.1 (personal use; {contact}) httpx", True
    return USER_AGENT, False


def interval_per(parallel: int) -> float:
    """Seconds between requests of each process, with `parallel` questions at once, to stay within the Wikimedia
    limits in total: 200 per minute with a contact, 10 without."""
    return max(0.7, 0.3 * parallel) if user_agent()[1] else 6.5 * parallel


LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z]+)?$")
# Namespaces not to follow (files, categories, help, portals...) in various languages.
SERVICE = re.compile(r"^(file|image|immagine|categoria|category|aiuto|help|portale|portal|template|wikipedia|"
                      r"speciale|special|discussione|talk|progetto|modulo|module|kategorie|datei|catégorie|fichier|"
                      r"categoría|archivo|categoria|ficheiro|κατηγορία|αρχείο)\s*:", re.I)
# Parts of the page that are not article content.
NOISE = ("reference", "mw-editsection", "navbox", "metadata", "noprint", "mw-references-wrap", "reflist",
          "hatnote", "dablink", "sistersitebox", "mw-empty-elt", "shortdescription", "toc", "thumbcaption",
          "mw-cite-backlink", "printfooter", "catlinks", "ambox", "avviso", "vedi-anche", "noexcerpt")
# Sections without facts about the entity (sources, links, notes).
USELESS_SECTIONS = re.compile(r"^(note|bibliografia|collegamenti esterni|altri progetti|voci correlate|fonti|"
                             r"references|notes|external links|further reading|see also|sources|bibliography|"
                             r"einzelnachweise|literatur|weblinks|références|liens externes|notes et références|"
                             r"bibliographie|referencias|enlaces externos|referências|ligações externas|"
                             r"παραπομπές|εξωτερικοί σύνδεσμοι)\b", re.I)
# Links to articles about dates and years (it.wikipedia links "8 marzo", "1566"): they are not entities.
MONTHS = (r"(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|january|"
        r"february|march|april|may|june|july|august|september|october|november|december)")
DATE_FORMAT = re.compile(rf"^(\d{{1,4}}( a\.c\.| bc| ad)?|\d{{1,2}} {MONTHS}|{MONTHS} \d{{1,2}}|anni \d+|"
                         rf"\d+(st|nd|rd|th) century|\w+ secolo|\d{{3,4}}s)$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-Þ«\"(\[0-9])")
# A full stop after these words does not end the sentence (24 Oct, Wikivoyage: «Vorsitzende des Wikivoyage e. V.,
# Roland Unger» was split into «… Wikivoyage e.» and «V., Roland Unger …»; «Roland Unger (* 15. Dezember 1941» into
# «(* 15.» and «Dezember …»): initials and single letters, one- or two-digit ordinals (German dates), abbreviations.
NOT_SENTENCE_END = re.compile(r"(?:^|[\s(])(?:\w|\d{1,2}|dr|prof|st|sr|jr|mr|mrs|ms|mt|ca|sig|geb|bzw|vgl|nr|hl|gest|"
                      r"[a-zäöü]+geb)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    """The text split into sentences (SENTENCE_END), without cutting after initials, ordinals and abbreviations
    (NOT_SENTENCE_END)."""
    sentences: list[str] = []
    start = 0
    for m in SENTENCE_END.finditer(text):
        if NOT_SENTENCE_END.search(text[max(start, m.start() - 12):m.start()]):
            continue
        sentences.append(text[start:m.start()])
        start = m.end()
    sentences.append(text[start:])
    return [f.strip() for f in sentences if f.strip()]


@dataclass
class Row:
    """A sentence or an infobox/table row, with the linked articles it contains."""
    text: str
    hyperlinks: list[str] = field(default_factory=list)  # titles of the linked articles
    kind: str = "sentence"  # sentence | infobox | table | list | categories
    section: str = ""


@dataclass
class Article:
    title: str
    language: str
    url: str
    rows: list[Row] = field(default_factory=list)
    languages: dict[str, str] = field(default_factory=dict)  # language → title
    categories: list[str] = field(default_factory=list)  # only the visible ones («Gesuiti italiani», «Nati nel 1566»)
    disambiguation: bool = False  # page property (DBpedia: the truth is there, not in the text)
    qid: str = ""  # Wikidata code of the article, read from Wikipedia (pageprops); only for the oracles
    redirect_from: str = ""  # the requested title, if Wikipedia redirected to this article

    @property
    def start(self) -> str:
        """The first sentences of the article (the definition, with dates and places of birth, seat, foundation...)."""
        sentences = [r.text for r in self.rows if r.kind == "sentence" and not r.section][:6]
        return " ".join(sentences)

    def infobox(self) -> list[Row]:
        return [r for r in self.rows if r.kind == "infobox"]


def _title_from_href(href: str, language: str) -> str | None:
    if not href:
        return None
    parts = urlsplit(href)
    if parts.netloc and not parts.netloc.startswith(f"{language}.wikipedia.org"):
        return None
    if "/wiki/" not in parts.path or parts.query:
        return None
    title = unquote(parts.path.split("/wiki/", 1)[1]).replace("_", " ").strip()
    if not title or SERVICE.match(title) or DATE_FORMAT.match(title):
        return None
    return title


# Elements that separate different items inside a cell or a paragraph: without a separator «text_content» glues the
# items of a list together («CastelliMario», 23 Oct, Galileo: «Notable students» of the English infobox).
BLOCKS = {"li", "br", "div", "p", "dd", "dt", "ul", "ol", "tr"}
SEPARATOR = " · "


def _text(el) -> str:
    parts: list[str] = []

    def visit(e, root: bool = False) -> None:
        if isinstance(e.tag, str):
            block = e.tag in BLOCKS and not root
            if block:
                parts.append(SEPARATOR)
            if e.text:
                parts.append(e.text)
            for child in e:
                visit(child)
            if block and e.tag != "br":
                parts.append(SEPARATOR)
        if e.tail and not root:
            parts.append(e.tail)

    visit(el, root=True)
    text = " ".join("".join(parts).split())
    text = re.sub(r"(\s*·\s*)+", SEPARATOR, text)  # consecutive separators: just one
    return text.strip(" ·")


def _hyperlinks(el, language: str) -> list[str]:
    seen: list[str] = []
    for a in el.iter("a"):
        t = _title_from_href(a.get("href", ""), language)
        if t and t not in seen:
            seen.append(t)
    return seen


def _sentences_with_hyperlinks(el, language: str) -> list[tuple[str, list[str]]]:
    """The text of a paragraph split into sentences; each sentence gets the links whose text appears in it."""
    text = _text(el)
    if not text:
        return []
    anchors = []
    for a in el.iter("a"):
        t = _title_from_href(a.get("href", ""), language)
        if t:
            anchors.append((" ".join(a.text_content().split()), t))
    sentences = split_sentences(text)
    output = []
    for sentence in sentences:
        mine = []
        for anchor, title in anchors:
            if anchor and anchor in sentence and title not in mine:
                mine.append(title)
        output.append((sentence, mine))
    return output


def analyze(html: str, title: str, language: str) -> list[Row]:
    """Rows of the article: infobox (sinottico/infobox), paragraph sentences, list items, table rows."""
    root = lxml.html.fromstring(f"<div>{html}</div>")
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        classes = set((el.get("class") or "").split())
        if el.tag in ("style", "script"):
            el.drop_tree()
            continue
        if classes & set(NOISE) or el.get("role") == "navigation" or (el.get("id") or "") == "toc":
            el.drop_tree()
    rows: list[Row] = []
    section = ""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        classes = (el.get("class") or "").lower()
        if el.tag in ("h2", "h3", "h4"):
            section = _text(el)[:80]
            continue
        if USELESS_SECTIONS.match(section):
            continue
        if el.tag == "table":
            infobox = "infobox" in classes or "sinottico" in classes or "infobox" in (el.get("id") or "")
            for tr in el.iter("tr"):
                cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("th", "td")]
                if not cells:
                    continue
                if infobox and len(cells) >= 2:
                    key, value = _text(cells[0]), " · ".join(_text(c) for c in cells[1:])
                    text = f"{key}: {value}"
                else:
                    text = " | ".join(_text(c) for c in cells)
                text = " ".join(text.split())
                if len(text) > 3:
                    rows.append(Row(text[:600], _hyperlinks(tr, language),
                                      "infobox" if infobox else "table", section))
            continue
        if el.tag == "p" and not any(p.tag == "table" for p in el.iterancestors()):
            for sentence, coll in _sentences_with_hyperlinks(el, language):
                rows.append(Row(sentence[:800], coll, "sentence", section))
        elif el.tag in ("li", "dd") and not any(p.tag in ("table", "li") for p in el.iterancestors()):
            text = _text(el)
            if 3 < len(text):
                rows.append(Row(text[:600], _hyperlinks(el, language), "list", section))
    # The infobox first: it is the piece densest in facts.
    return [r for r in rows if r.kind == "infobox"] + [r for r in rows if r.kind != "infobox"]


class WikipediaGraph:
    """Wikipedia API client with the articles kept in memory for the duration of a research."""

    # A name written without a link in the text of a neighbouring article, in `choose_article`: "for" = counts as
    # a link, "against" = in a row with other links the article with that title belongs to a namesake, "none" = does
    # not count. Article oracle (24 Oct): for 125 right / 8 wrong, none 126/6, against (row with links) see
    # bench/LOG.md.
    mention = "against"

    def __init__(self, client: SourceClient | None = None, local_allow: bool = False,
                 max_articles: int = 80, fetch: Callable[[str, dict], dict] | None = None,
                 interval_s: float | None = None) -> None:
        """`fetch(url, params)` = how to get the JSON (for tests); without it, the sources' HTTP client.
        Wikimedia rules (16 Oct): with a User-Agent without a contact the limit is 10 requests per minute (beyond:
        HTTP 429 with Retry-After); with a contact (email or web address) 200 per minute. Our User-Agent has no
        contact: one request every 6.5 s; with the contact one every 0.7 s (two runs in parallel stay under 200
        per minute). Retry-After is always respected. The headless browser gets blocked: it is not used to get
        around this."""
        agent, with_contact = user_agent()
        if interval_s is None:
            interval_s = 0.7 if with_contact else 6.5
        self.limit = limiter("wikipedia", interval_s, int(3600 / max(interval_s, 0.3)))
        self.fetch = fetch
        self.client = client or SourceClient("Wikipedia", user_agent=agent, limiter=self.limit,
                                            domains={"wikipedia.org"}, local_allow=local_allow)
        self.requests = 0
        self.articles: dict[tuple[str, str], Article | None] = {}
        self.max_articles = max_articles
        self.read_count = 0
        self.exhausted = False
        self._mutex = threading.Lock()

    @staticmethod
    def _api(language: str) -> str:
        if not LANGUAGE.match(language):
            raise SourceError(f"wikipedia: invalid language ({language})")
        return f"https://{language}.wikipedia.org/w/api.php"

    def _json(self, language: str, **params) -> dict:
        params = {"format": "json", "formatversion": 2, **params}
        self.requests += 1
        try:
            if self.fetch is None:
                return replay.through("wikipedia", [language, params],
                                          lambda: self.client.get_json(self._api(language), params=params))
            for _ in range(3):
                self.limit.wait_for(max_wait_s=120)
                data, wait = self.fetch(self._api(language), params)
                if wait is None:
                    return data if isinstance(data, dict) else {}
                self.limit.pause(min(max(wait, 5.0), 90.0))  # HTTP 429: Retry-After is respected
            return {TEMPORARY_ERROR: True}
        except replay.UnrecordedRequest:
            raise
        except Exception:  # noqa: BLE001 - Wikipedia unreachable: the chain moves to the web
            return {TEMPORARY_ERROR: True}

    def url(self, title: str, language: str) -> str:
        return f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,-.:')}"

    def search(self, text: str, language: str, n: int = 5) -> list[dict]:
        """Search in the text of the articles: [{title, fragment}]."""
        data = self._json(language, action="query", list="search", srsearch=text[:250], srlimit=n,
                          srprop="snippet", srnamespace=0)
        output = []
        for x in (data.get("query") or {}).get("search", []):
            fragment = re.sub(r"<[^>]+>", "", x.get("snippet", ""))
            output.append({"title": x.get("title", ""), "fragment": " ".join(fragment.split())})
        return output

    def article(self, title: str, language: str) -> Article | None:
        key = (language, title)
        with self._mutex:
            if key in self.articles:
                return self.articles[key]
            if self.read_count >= self.max_articles:
                if not self.exhausted:  # 17 Oct, Pace di Lodi: cap reached before the value, unnoticed
                    self.exhausted = True
                    log.warning("wikipedia: reached the cap of %d articles", self.max_articles)
                return None
            self.read_count += 1
        # Categories and properties (disambiguation, code) come with the same request (23 Oct, idea from YAGO).
        data = self._json(language, action="parse", page=title,
                          prop="text|langlinks|displaytitle|categories|properties", redirects=1)
        if data.get(TEMPORARY_ERROR):  # network or 429: the article is not «missing», it can be requested again
            with self._mutex:
                self.read_count -= 1
            return None
        parse = data.get("parse") or {}
        html = parse.get("text")
        article = None
        if isinstance(html, str) and html:
            true = parse.get("title") or title
            languages = {x.get("lang"): x.get("title") for x in parse.get("langlinks", []) if x.get("lang")}
            properties = parse.get("properties") or {}
            if isinstance(properties, list):  # old format: [{name, *}]
                properties = {x.get("name"): x.get("*", "") for x in properties if isinstance(x, dict)}
            categories = [str(c.get("category", "")).replace("_", " ") for c in parse.get("categories") or []
                         if isinstance(c, dict) and not c.get("hidden") and c.get("category")]
            rows = analyze(html, true, language)
            if categories:  # a citable row: the check reads «Gesuiti italiani; Nati a Bologna»
                rows.append(Row("Categorie: " + "; ".join(categories), [], "categories", ""))
            redirect = next((r.get("from", "") for r in parse.get("redirects") or [] if isinstance(r, dict)), "")
            article = Article(true, language, self.url(true, language), rows, languages, categories,
                        "disambiguation" in properties, str(properties.get("wikibase_item") or ""), redirect)
        with self._mutex:
            self.articles[key] = article
            if article is not None:
                self.articles[(language, article.title)] = article
        return article

    def starts(self, titles: list[str], language: str) -> dict[str, str]:
        """The first sentences of several articles with a single request (at most 20)."""
        output: dict[str, str] = {}
        for i in range(0, len(titles), 20):
            group = titles[i:i + 20]
            data = self._json(language, action="query", prop="extracts", exintro=1, explaintext=1, exsentences=4,
                              exlimit=20, titles="|".join(group), redirects=1)
            for p in (data.get("query") or {}).get("pages", []):
                if p.get("extract"):
                    output[p.get("title", "")] = " ".join(p["extract"].split())[:900]
        return output

    def incoming(self, title: str, language: str, n: int = 60) -> list[str]:
        """Articles that link to this one (reverse relations: who designed, who is a member of...)."""
        data = self._json(language, action="query", list="backlinks", bltitle=title, blnamespace=0, bllimit=n,
                          blfilterredir="nonredirects")
        return [x.get("title", "") for x in (data.get("query") or {}).get("backlinks", []) if x.get("title")]

    def find(self, name: str, language: str, context: str = "",
              accept: Callable[[Article], bool] | None = None) -> Article | None:
        """The article of an entity: first the exact title (with redirects), then the search with the context; `accept`
        discards namesakes (the next result is tried)."""
        good = lambda v: v is not None and not _disambiguation(v) and (accept is None or accept(v))  # noqa: E731
        article = self.article(name, language)
        if good(article):
            return article
        proven = {article.title} if article is not None else set()
        for text in ([f"{name} {context}".strip()] if context else []) + [name]:
            for x in self.search(text, language, n=4):
                if x["title"] in proven or DEGREES[matches(name, x["title"], context)] < DEGREES["parentheses"]:
                    continue
                proven.add(x["title"])
                v = self.article(x["title"], language)
                if good(v):
                    return v
        return None

    # --- from name to article (phases 2-3 of the entity and rows plan) --------------------------------------------

    def article_candidates(self, name: str, language: str, context: str = "", research_words: str = "",
                       maximum: int = 7) -> list[tuple[str, str]]:
        """The articles that may be the name, with the degree from `matches`: (title, degree). First the exact title
        (with the Wikipedia redirect), then the articles of a disambiguation page, then the search. At most 7
        (REL: 4 by probability + 3 by context)."""
        output: list[tuple[str, str]] = []

        def add(title: str, degree: str) -> None:
            if degree and title not in {t for t, _ in output} and len(output) < maximum:
                output.append((title, degree))

        article = self.article(name, language)
        if article is not None:
            if article.disambiguation or _disambiguation(article):
                for r in article.rows:
                    for t in r.hyperlinks:
                        add(t, matches(name, t, context))
            elif not not_entity(article):
                degree = matches(name, article.title, context)
                if not degree and article.redirect_from and _content(name, article.title):
                    # Redirect with one name inside the other («Mozilla Firefox» → «Firefox», «Einstein» → «Albert
                    # Einstein»): it is the same name. A redirect to something else never counts (23 Oct, Arduino:
                    # «Phillip Torrone» → «Limor Fried», his wife; «Democratic Federal Yugoslavia» → «Socialist…»).
                    degree = "variant"
                add(article.title, degree)
        for text in dict.fromkeys(x for x in (f"{name} {research_words}".strip(), name) if x):
            for x in self.search(text, language, n=5):
                add(x["title"], matches(name, x["title"], context))
        return output

    def info_articles(self, titles: list[str], language: str, neighbours: list[str]) -> dict[str, dict]:
        """For many articles with one request: disambiguation, visible categories, links towards the `neighbours`
        (prop=links&pltitles: GLOW, «there is a link» indicator)."""
        output: dict[str, dict] = {t: {"disambiguation": False, "categories": [], "towards": set()} for t in titles}
        if not titles:
            return output
        params = dict(action="query", titles="|".join(titles[:40]), redirects=1, prop="pageprops|categories",
                      ppprop="disambiguation", clshow="!hidden", cllimit="max")
        if neighbours:
            params.update(prop="pageprops|categories|links", pltitles="|".join(neighbours[:40]), pllimit="max")
        data = self._json(language, **params)
        q = data.get("query") or {}
        redirects = {r.get("from"): r.get("to") for r in q.get("normalized", []) + q.get("redirects", [])}
        per_title = {p.get("title"): p for p in q.get("pages", []) if isinstance(p, dict)}
        for t in titles:
            true = t
            for _ in range(3):
                true = redirects.get(true, true)
            p = per_title.get(true) or {}
            output[t] = {"disambiguation": "disambiguation" in (p.get("pageprops") or {}),
                         "categories": [str(c.get("title", "")).split(":", 1)[-1] for c in p.get("categories") or []],
                         "towards": {x.get("title") for x in p.get("links") or []}, "title": true}
        return output

    def choose_article(self, name: str, languages: list[str], context: str = "", neighbours: list[tuple[str, str]] = (),
                    research_words: str = "", accept: Callable[[Article], bool] | None = None,
                    tiebreak: Callable[[str, list[tuple[str, str]]], int | None] | None = None) -> Article | None:
        """The article for the name, decided by the graph (TAGME, Milne-Witten, GLOW adapted: few context entities, no
        incoming links). For each candidate article (`article_candidates`, degree from `matches`):
        - graph: it links a neighbouring article (+1), it is linked from a neighbouring article or named in its text
          (+1);
        - categories: one of its categories has a context word (nationalities, centuries, births/deaths excluded).
        If some have graph > 0: those within 70% of the best are kept (ε = 30%), the degree priority wins
        (justified parentheses, exact, variant, base), then graph, categories, order. If none: an exact/variant
        title or justified parentheses wins, or a non-«base» article with categories; `accept` decides on
        these. A tie between weak degrees → `tiebreak` (closed list, number or none). Otherwise no article:
        better nothing than a namesake. `neighbours` = articles (language, title) of the previous entity."""
        nearby_articles = [v for lg, t in neighbours if (v := self.article(t, lg)) is not None]
        roots = {p[:5] for p in _plan(context).split() if len(p) >= 5 and p not in TIES}
        roots -= {p[:5] for p in _plan(name).split()}  # the name itself proves nothing (Freedo, 22 Oct)
        for language in languages:
            candidates = self.article_candidates(name, language, context, research_words)
            if not candidates:
                continue
            neighbours_titles = [v.title for v in nearby_articles if v.language == language]
            neighbours_titles += [v.languages[language] for v in nearby_articles
                                  if v.language != language and language in v.languages]
            neighbours_titles = list(dict.fromkeys(neighbours_titles))
            info = self.info_articles([t for t, _ in candidates], language, neighbours_titles)
            linked_from = {_plan(t) for v in nearby_articles if v.language == language for r in v.rows
                            for t in r.hyperlinks}
            neighbours_texts = " ".join(_plan(r.text) for v in nearby_articles if v.language == language
                                        for r in v.rows)
            # rows with links: there the writers linked the articles that exist
            linked_texts = " ".join(_plan(r.text) for v in nearby_articles if v.language == language for r in v.rows
                                       if r.hyperlinks)
            scores = []
            for place, (title, degree) in enumerate(candidates):
                x = info.get(title) or {}
                true = x.get("title") or title
                if x.get("disambiguation") or NON_ENTITY_TITLE.search(true) \
                        or any(NON_ENTITY_CATEGORY.match(c) for c in x.get("categories", [])):
                    continue
                graph = 1.0 if x.get("towards") else 0.0
                base = _plan(re.sub(r"\s*\([^)]*\)\s*$", "", true))
                named = len(base) >= 6 and bool(re.search(rf"\b{re.escape(base)}\b", neighbours_texts))
                unlinked = False
                if _plan(true) in linked_from or _plan(title) in linked_from:
                    graph += 1.0
                elif named and self.mention == "for":
                    graph += 1.0
                elif named and self.mention == "against":
                    # Named without a link in a row that links other articles: that article is of a namesake
                    # (Roland Unger in the German Wikivoyage article); in a row without links it says nothing
                    # (24 Oct, oracle: «per gli editori Adelphi e Sellerio» and the right article «Adelphi»).
                    unlinked = bool(re.search(rf"\b{re.escape(base)}\b", linked_texts))
                words_cat = {p[:5] for c in x.get("categories", []) for p in _plan(c).split()} - GENERIC_CATEGORIES
                categories = 1.0 if roots & words_cat else 0.0
                scores.append({"title": true, "degree": degree, "graph": graph, "categories": categories,
                                 "place": place, "unlinked": unlinked})
            if not scores:
                continue
            best = max(p["graph"] for p in scores)
            if best > 0:
                # Links in the graph (TAGME: within 30% of the most linked, then the most «common»).
                picked = [p for p in scores if p["graph"] >= 0.7 * best]
                order = lambda p: (-PRIORITY[p["degree"]], -p["graph"], -p["categories"], p["place"])  # noqa: E731
            else:
                # No links: an exact title wins (the main article of that name); otherwise the categories need a
                # context word («Tux (mascotte)» for a question about a mascot).
                # (a «base», surname or unjustified parentheses, is not enough with categories alone: 23 Oct,
                # «Arduino» → «Gino Arduino», record producer, because of «produce» in the question)
                picked = [p for p in scores if not p["unlinked"] and (
                    PRIORITY[p["degree"]] >= PRIORITY["variant"] or (p["categories"] > 0 and p["degree"] != "base"))]
                order = lambda p: (-PRIORITY[p["degree"]], -p["categories"], p["place"])  # noqa: E731
            if not picked:
                continue
            picked.sort(key=order)
            first_one = picked[0]
            tied = [p for p in picked if order(p)[:-1] == order(first_one)[:-1]]
            if len(tied) > 1 and DEGREES[first_one["degree"]] < DEGREES["variant"] and tiebreak is not None:
                choice = tiebreak(name, [(p["title"], self._short_start(p["title"], language)) for p in tied])
                if choice is None:
                    return None
                first_one = tied[min(max(choice, 0), len(tied) - 1)]
            article = self.article(first_one["title"], language)
            if article is None or not_entity(article):
                continue
            if best == 0 and accept is not None and not accept(article):
                continue
            log.info("wikipedia: «%s» → %s (%s, graph %.0f, categories %.0f)", name, article.title, first_one["degree"],
                     first_one["graph"], first_one["categories"])
            return article
        return None

    def _short_start(self, title: str, language: str, words: int = 40) -> str:
        """Candidate card for the tiebreak (BLINK: title + start of the article)."""
        text = self.starts([title], language).get(title, "")
        return " ".join(text.split()[:words])


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


EMPTY = {"del", "della", "delle", "dei", "degli", "the", "von", "van", "des", "der", "die", "and"}

# Linking words ignored when comparing name/title (not leading articles: those are removed separately).
TIES = {"di", "da", "de", "del", "della", "dello", "dei", "degli", "delle", "d", "of", "the", "von", "van", "der",
          "des", "du", "la", "le", "il", "lo", "l", "e", "and", "y", "et", "und", "a", "al", "in"}
ARTICLES = {"il", "lo", "la", "i", "gli", "le", "l", "the", "a", "an", "el", "los", "las", "les", "der", "die", "das"}
ROMAN = re.compile(r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
DEGREES = {"exact": 3.0, "variant": 2.5, "parentheses": 2.0, "base": 1.0, "": 0.0}
# To choose among the articles of a name: parentheses justified by the context are more precise than the exact title
# (which is only the main meaning: «Haiku» poetry, «Haiku (sistema operativo)» if the question is about systems).
PRIORITY = {"parentheses": 4.0, "exact": 3.0, "variant": 2.5, "base": 1.0, "": 0.0}
# Category words too generic to say anything about the entity (nationalities, centuries, births/deaths).
GENERIC_CATEGORIES = {"itali", "ameri", "stati", "uniti", "franc", "ingle", "engli", "briti", "germa", "tedes",
                       "spagn", "europ", "secol", "centu", "nati", "morti", "birth", "death", "perso", "peopl",
                       "anni", "years", "citta", "citie"}


def _plan(text: str) -> str:
    """Lowercase, without accents or punctuation (graphify: NFKC + casefold; here accents too)."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def _without_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c))


# Company forms at the end of the name: they do not distinguish the entity (29 Oct, Raspberry Pi: «Premier Farnell
# Ltd.» without an article, expanded as an entity different from «Premier Farnell»).
CORPORATE_SUFFIXES = {"ltd", "limited", "plc", "inc", "incorporated", "llc", "gmbh", "spa", "srl", "corp",
                      "corporation", "co", "ag", "sa", "nv", "bv", "sas", "sarl", "kg", "ab", "oy", "as", "asa", "pty",
                      "lp", "llp"}


def without_corporate_suffix(text: str) -> str:
    """The name without trailing company forms («Premier Farnell Ltd.» → «Premier Farnell»; «S.p.A.» included)."""
    words = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z]\.)", r"\1", str(text)).split()
    while len(words) > 1 and re.sub(r"[\W_]+", "", _plan(words[-1])) in CORPORATE_SUFFIXES:
        words = words[:-1]
    return " ".join(words).rstrip(" ,")


def _name_words(text: str) -> list[str]:
    words = _plan(without_corporate_suffix(text)).split()
    while len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return [p for p in words if p not in TIES] or words


def _numeric(word: str) -> bool:
    return word.isdigit() or bool(ROMAN.match(word))


def _same_word(a: str, b: str) -> bool:
    """Equal, or variants of the same word (Bettinus/Bettini, Federale/Federal, Guisa/Guise). Numbers and Roman
    numerals only if equal (Pio II ≠ Pio III; graphify: «numeric tokens differ» always blocks)."""
    if a == b:
        return True
    if _numeric(a) or _numeric(b):
        return False
    if min(len(a), len(b)) >= 6:
        return a[:5] == b[:5] and abs(len(a) - len(b)) <= 3
    # short words: only a one-letter typo, at equal length (graphify: no insertions on short words)
    return len(a) == len(b) >= 4 and sum(x != y for x, y in zip(a, b)) == 1


def _same_words(a: list[str], b: list[str]) -> bool:
    """Same words in a different order («Jugoslavia Democratica Federale» / «Jugoslavia Federale Democratica»)."""
    rest = list(b)
    for x in a:
        y = next((y for y in rest if _same_word(x, y)), None)
        if y is None:
            return False
        rest.remove(y)
    return True


def _content(name: str, title: str) -> bool:
    """The words of one name are all in the other (title without parentheses)."""
    a = _name_words(name)
    b = _name_words(re.sub(r"\s*\([^)]*\)\s*$", "", title))
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(_same_word(x, y) for y in long) for x in short)


def matches(name: str, title: str, context: str = "") -> str:
    """How much the title of an article is the name sought. 23 Oct, replacing the old `_simile` (60% of words in
    common: «Mario Bettinus» → «Mario (nome)», «Silvio Piccolomini» → Pope Pius II). Rules from the code of UNC-FEVER
    (title with parentheses) and graphify (never merge two different entities):
    - "exact": same name (apart from accents, case, leading article), also with the parentheses written;
    - "variant": same words, some in a variant form (Bettinus/Bettini);
    - "parentheses": the name is the base of the title and the words in parentheses are in the context («Tux
      (mascot)» with «mascotte» in the context);
    - "base": the name is the base of a title with unjustified parentheses, or the article's surname («Fermi» →
      «Enrico Fermi»): counts only with a link in the graph;
    - "": another entity (different word, different number, title shorter than the name: «Mario» for «Mario
      Bettinus»).
    """
    if not name or not title:
        return ""
    if _plan(name) == _plan(title):
        # different case beyond the first letter: «Tex» (comic) is not «TeX» (23 Oct, oracle)
        a, b = _without_accents(name).strip(), _without_accents(title).strip()
        return "exact" if a[1:] == b[1:] or a.casefold() == a or b.casefold() == b else "variant"
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", title)
    base, parentheses = (m.group(1), m.group(2)) if m else (title, "")
    a, b = _name_words(name), _name_words(base)
    if not a or not b:
        return ""
    if len(a) == len(b) and (all(_same_word(x, y) for x, y in zip(a, b)) or _same_words(a, b)):
        if a != b and len("".join(a)) < 12 and not (
                len("".join(a)) == len("".join(b)) and sum(x != y for x, y in zip("".join(a), "".join(b))) <= 1):
            return ""  # short names: only a one-letter typo (graphify; 23 Oct, «Freedo» ≠ «Freedom»)
        if not parentheses:
            return "exact" if a == b else "variant"
        # 6-letter roots: «astronomo» does not justify «(astrologia)» (Pluto)
        roots = {p[:6] for p in _plan(context).split() if len(p) >= 4}
        if roots & {p[:6] for p in _plan(parentheses).split() if len(p) >= 4 and p not in TIES}:
            return "parentheses"
        return "base"
    # The name is the surname (or the end) of the title: «Fermi» → «Enrico Fermi»; never the reverse (shorter title).
    # Only a one-word name: «Traforo del Monte Bianco» is not «Monte Bianco» (23 Oct, oracle).
    if not parentheses and len(a) == 1 < len(b) and len(a[0]) >= 4 and _same_word(a[0], b[-1]):
        return "base"
    return ""


# Articles that are not an entity: given names, surnames, lists (YAGO: titles «List of», «(disambiguation)» → NIL).
NON_ENTITY_TITLE = re.compile(r"\((nome|cognome|prenome|name|given name|surname|nome proprio|disambigua|"
                               r"disambiguation)\)$|^(lista|elenco|list|lists|index|outline|cronologia) (di|of|dei|"
                               r"delle|degli)\b", re.I)
NON_ENTITY_CATEGORY = re.compile(r"^(pagine di disambiguazione|prenomi|cognomi|nomi propri|given names|surnames|"
                                  r"disambiguation pages)\b|(given names|surnames|family names)$", re.I)


def not_entity(article: Article) -> bool:
    return (_disambiguation(article) or bool(NON_ENTITY_TITLE.search(article.title))
            or any(NON_ENTITY_CATEGORY.match(c) for c in article.categories))


def _disambiguation(article: Article) -> bool:
    if article.disambiguation:
        return True
    start = article.start.lower()
    return ("può riferirsi a" in start or "may refer to" in start or "disambiguation" in article.title.lower()
            or "può indicare" in start or "puede referirse" in start or "peut désigner" in start
            or "steht für" in start)
