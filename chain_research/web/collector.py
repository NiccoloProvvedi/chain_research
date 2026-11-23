"""Collection for one question: the chain (chain.py) walked with the browser and Claude as the reader.

The chain drives the research; this class gives it the tools: web searches (DuckDuckGo, Bing as fallback),
choice of results, page reading with the browser (wikis from the api.php archive), block reading by Claude with
the pieces saved in the concept graph, limits on pages, minutes and budget, and the written answer with its
sources. It is the same collection as the test bench (36/40 with Claude Haiku), without the local-model routes.
"""
import hashlib
import html as encode_html
import json
import re
import secrets
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from .. import configuration
from ..model import claude_json, cleaning, replay
from ..model import config as config_model
from ..model import query as duplicates_query
from ..model.thinker import SCHEMAS, Thinker, instructions
from ..proofs.concepts import Concepts
from .browser import Browser, safe_address
from .sources import canonical_key
from .sources.http import SourceClient
from .sources.limits import limiter

EXTRACTOR = Path(__file__).with_name("extractor.js").read_text(encoding="utf-8")
BLOCK = 10_000          # characters per reading block
MAX_BLOCKS = 8          # at most ~80,000 characters read per page
MIN_TEXT = 600          # below this length the page is not a useful source
# On the host name, whole words only: "x.com" inside "linux.com" does not count.
EXCLUDED = re.compile(r"(^|\.)((youtube|facebook|instagram|tiktok|pinterest|twitter|amazon|ebay|linkedin|duckduckgo|bing|"
                     r"google)\.[a-z.]+|x\.com|youtu\.be)$", re.IGNORECASE)
REDDIT = re.compile(r"(^|\.)(reddit\.com|redd\.it)$", re.IGNORECASE)
COMMON_WORDS = {"the", "this", "that", "these", "in", "on", "at", "a", "an", "it", "he", "she", "they", "his",
                 "her", "il", "la", "lo", "le", "gli", "i", "un", "una", "di", "da", "per", "con", "nel", "nella",
                 "however", "after", "before", "during", "when", "while", "also", "some", "many", "according",
                 "source", "sources", "fonte", "fonti", "part", "none", "topic", "notes"}
# Limits of one question: those of the test bench (30 pages, 35 minutes, $0.50 of Claude).
DEFAULT_OPTIONS = {"novelty_threshold": 0.25, "min_new": 10, "max_per_site": 3, "chain_pages": 30,
                       "chain_minutes": 35, "budget_claude": 0.50, "writing_reserve": 0.08}
SHORT_INSTRUCTIONS = (
    "The user needs a SHORT, precise answer, not a document. Write in ENGLISH, using ONLY the notes, exactly these "
    "lines in Markdown:\n"
    "**Answer:** the answer in one or two sentences, with the source [n] of each fact.\n"
    "**Main evidence:** 1 to 3 bullet points, each a fact with its source [n] and, if the notes give it, the date "
    "of the information.\n"
    "**Conflicting sources:** where sources disagree and how, or \"none\".\n"
    "**Limitations:** what could not be verified or may be out of date.\n"
    "**Confidence:** high, medium or low, with the reason in a few words.\n"
    "If the notes do not contain the answer, say so in the Answer line instead of guessing. "
    "Cite the source number of each fact as [n].")


class Stopped(Exception):
    """The user stopped the research."""


def _json(text: str) -> Any:
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(match.group(0)) if match else None


def entity(text: str) -> set[str]:
    """Proper names, dates and numbers cited in a text: they measure how much NEW information a page brings."""
    # Only capitalised words mid-sentence: at the start of a sentence or bullet they are often common words ("During").
    found_items = {m.group(0).lower() for m in re.finditer(r"(?<=[a-zà-ÿ0-9,;:)] )[A-ZÀ-Ý][\w'’-]{2,}", text)} \
        - COMMON_WORDS
    found_items.update(m.group(0).lower() for m in re.finditer(r"\b\d{2,4}(?:\s?(?:dr|bc|ad|d\.c\.|a\.c\.))?\b",
                                                            text, re.IGNORECASE))
    return found_items


def split_in_blocks(text: str) -> list[str]:
    """Blocks of BLOCK characters, at most MAX_BLOCKS."""
    return [text[i:i + BLOCK] for i in range(0, min(len(text), BLOCK * MAX_BLOCKS), BLOCK)]


class _NoBrowser:
    """In replay the pages come from the recording."""

    def close(self) -> None:
        pass


class Collector:
    """One question: `run()` walks the chain and returns the answer with its sources."""

    def __init__(self, question: str, *, publish: Callable[[dict], None] = lambda e: None,
                 stopped: Callable[[], bool] = lambda: False, options: dict | None = None,
                 local_allow: bool = False, profile: Path | None = None,
                 pieces_folder: Path | None = None) -> None:
        self.o = {**DEFAULT_OPTIONS, **(options or {})}
        self.o["max_pages"] = int(self.o["chain_pages"])
        self.o["max_minutes"] = float(self.o["chain_minutes"])
        self.topic = question.strip()
        self.today = replay.today()
        self.plan_id = "p-" + secrets.token_hex(4)
        self.thinker = Thinker(self.plan_id, float(self.o["budget_claude"]), publish=publish)
        self.thinker.model = str(self.o.get("claude_model") or configuration.load()["chain_model"])
        self.publish = publish
        self.stopped = stopped
        self.local_allow = local_allow
        self.profile = profile or configuration.folder("profiles_folder") / "main"
        self.pieces_folder = pieces_folder or configuration.folder("investigations_folder") / ".pieces"
        self.sources: list[dict] = []           # useful pages, numbered for citations
        self.queue: list[dict] = []            # candidate pages to read
        self.visited: set[str] = set()
        self.read_pages: set[str] = set()   # pages with useful text (they count towards the page limit)
        self.research_of: dict[str, str] = {}  # url → search that found it (the link being sought)
        self.failed: Counter[str] = Counter()  # errors and anti-bot blocks per domain
        self.fingerprints: set[str] = set()       # content already read (the same page from different addresses)
        self.searches_done: list[str] = []
        self.executed_query: list[str] = []
        self.knowledge: set[str] = set()     # entities already known (to measure novelty)
        self.quotes: Counter[str] = Counter()
        self.little_new = 0
        self.concepts: Concepts | None = None  # saved page pieces (the chain picks rows from them)
        self.chain = None
        self.cost = 0.0
        self.end_reason = ""
        self.author = ""
        self.browser: Browser | None = None
        self._in_progress: list[tuple[dict, list[Future], dict[int, tuple]]] = []
        self._workers = ThreadPoolExecutor(max_workers=3)
        self._archive: SourceClient | None = None
        self._start = time.monotonic()
        self._initial_wait = claude_json.LIMIT_WAIT["seconds"]

    # --- run ---------------------------------------------------------------------------------------------------

    def run(self) -> dict:
        from .chain import Chain
        self.thinker.ledger.reserve = float(self.o["writing_reserve"])
        self.pieces_folder.mkdir(parents=True, exist_ok=True)
        self.concepts = Concepts(self.pieces_folder / f"{self.plan_id}.sqlite")
        self.browser = _NoBrowser() if replay.in_replay() else Browser(
            "about:blank", configuration.folder("engines_folder"), self.profile, headless=True,
            local_allow=self.local_allow)
        try:
            self.chain = Chain(self)
            self.chain.walk()
            if self.o.get("model_checks", True):
                self.chain.final_review()
            self.end_reason = self.chain.reason or "chain walked"
            self._phase(f"Collection finished: {self.end_reason}.")
        finally:
            self.browser.close()
            self._workers.shutdown(wait=False, cancel_futures=True)
            if self._archive is not None:
                self._archive.teardown()
        answer = self.write()
        self.cost = max(self.cost, self.thinker.ledger.spent)
        outcome = {"question": self.topic, "answer": answer, "sources": self._sources_list(),
                 "value": (self.chain.value or {}).get("value"), "found": bool(self.chain.value),
                 "confirmed": self.chain.checks()["confirmed"], "status": self.chain.checks()["status"],
                 "seconds": round(self._elapsed()), "cost_usd": round(self.cost, 4),
                 "end_reason": self.end_reason, "searches": self.searches_done, "author": self.author,
                 "read_pages": len(self.read_pages), "pieces": str(self.pieces_folder / f"{self.plan_id}.sqlite"),
                 "chain": self.chain.summary(), "thinker": self.thinker.summary()}
        self.concepts.teardown()
        return outcome

    def _check(self) -> None:
        if self.stopped():
            raise Stopped()

    def _phase(self, text: str) -> None:
        self.publish({"kind": "phase", "text": text})

    def _elapsed(self) -> float:
        """Seconds since the start, excluding waits for the Claude session limit."""
        return time.monotonic() - self._start - (claude_json.LIMIT_WAIT["seconds"] - self._initial_wait)

    def _reached_limit(self) -> str:
        if self.thinker.exhausted:
            return "Claude budget exhausted"
        if len(self.read_pages) >= int(self.o["max_pages"]):
            return f"reached the limit of {len(self.read_pages)} web pages read"
        if len(self.visited) >= 3 * int(self.o["max_pages"]):
            return f"reached the cap of {3 * int(self.o['max_pages'])} pages visited"
        if self._elapsed() > 60 * float(self.o["max_minutes"]):
            return f"reached the safety limit of {float(self.o['max_minutes']):.0f} minutes"
        return ""

    def _ask(self, task_instructions: str, text: str) -> str:
        """Free-text requests to Claude (choice of results, rephrasings), at the "reading" call point; the text from
        the web goes through sentence cleaning."""
        answer = self.thinker.ask("reading", task_instructions, cleaning.text(text, origin="search"))
        return answer if isinstance(answer, str) else ""

    # --- searches ----------------------------------------------------------------------------------------------

    def _filter_query(self, searches: list[str]) -> list[str]:
        """A query too similar (Jaccard ≥ threshold) to one already run is not sent: Claude rephrases it once, and if it
        is still a duplicate it is skipped."""
        threshold = config_model.load().query.duplicate_threshold
        kept = []
        for research in searches:
            similarity, similar = duplicates_query.most_similar(research, self.executed_query)
            if similarity >= threshold:
                fresh = self._rephrase(research)
                duplicate = not fresh or duplicates_query.duplicate(fresh, self.executed_query, threshold)
                self.publish({"kind": "duplicate_query", "query": research, "similar_to": similar,
                               "similarity": round(similarity, 2), "rephrased": fresh, "discarded": duplicate})
                if duplicate:
                    continue
                research = fresh
            self.executed_query.append(research)
            kept.append(research)
        return kept

    def _rephrase(self, research: str) -> str:
        answer = self._ask(
            "The QUERY is almost the same as one of the DONE QUERIES of this research, so it would find the same "
            "pages. Write ONE different short search query (same language, exact names) that looks for information "
            'the DONE QUERIES do not cover. Reply only with JSON: {"query": "..."}.',
            f"TOPIC: {self.topic}\n\nDONE QUERIES:\n" + "\n".join(self.executed_query) + f"\n\nQUERY: {research}")
        try:
            return str((_json(answer) or {}).get("query") or "").strip()[:200]
        except (ValueError, AttributeError):
            return ""

    def _results(self, url: str, script: str) -> list[dict]:
        return replay.through("search", url, lambda: self._alive_results(url, script))

    def _alive_results(self, url: str, script: str) -> list[dict]:
        try:
            self.browser.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self.browser.page.wait_for_timeout(1500)
            return self.browser.page.evaluate(script) or []
        except Exception:  # noqa: BLE001 - an engine that does not answer does not stop the research
            return []

    def collect(self, searches: list[str]) -> list[dict]:
        """Search engine results (DuckDuckGo, Bing as fallback): address, title and snippet."""
        seen: dict[str, dict] = {}
        for research in searches:
            self._check()
            self._phase(f"Searching: {research}")
            results = self._results(
                f"https://html.duckduckgo.com/html/?q={quote_plus(research)}",
                """[...document.querySelectorAll('.result')].map(r => ({url: r.querySelector('a.result__a')?.href,
                    title: r.querySelector('a.result__a')?.innerText, snippet: r.querySelector('.result__snippet')?.innerText}))""")
            if not results:
                results = self._results(
                    f"https://www.bing.com/search?q={quote_plus(research)}",
                    """[...document.querySelectorAll('li.b_algo')].map(r => ({url: r.querySelector('h2 a')?.href,
                        title: r.querySelector('h2 a')?.innerText, snippet: r.querySelector('.b_caption p, p')?.innerText}))""")
            for r in results:
                url = r.get("url") or ""
                if "duckduckgo.com/l/" in url:  # DuckDuckGo redirect link
                    url = parse_qs(urlsplit(url).query).get("uddg", [""])[0]
                if not url.startswith("http") or url in seen or url in self.visited:
                    continue
                if canonical_key(url) or self._excluded(url):
                    continue  # platforms the page reader does not read (video, Q&A, Wikidata)
                seen[url] = {"url": url, "title": (r.get("title") or "")[:150],
                              "snippet": (r.get("snippet") or "")[:300]}
                self.research_of.setdefault(url, research)  # the search that found the page (link being sought)
        return list(seen.values())

    def _add_candidates(self, candidates: list[dict], purpose: str = "") -> None:
        """Claude ranks the results by usefulness and drops the useless ones; they are added to the queue. With `purpose`
        (one step of the chain) usefulness is for that step, not for the whole question."""
        if not candidates:
            return
        listing = "\n".join(f"{i + 1}. {c['title']} — {urlsplit(c['url']).netloc}\n   {c['snippet']}"
                           for i, c in enumerate(candidates[:40]))
        answer = self._ask(
            "Rank the search results that are useful and reliable for an accurate, in-depth report on the TOPIC, "
            "from most to least useful: prefer encyclopedias, specialised wikis, official, academic or expert sites; "
            "leave out shops, thin forums, unrelated pages and duplicates. "
            'Reply only with JSON: {"sources": [numbers in order of usefulness]}.' + (
                " The TOPIC is one step of a research chain: the useful pages are the ones likely to state that fact "
                "(official site, company or project page, profile, news, encyclopedia), not general pages on the "
                "subject." if purpose else ""),
            f"TOPIC: {purpose or self.topic}\n\nRESULTS:\n{listing}")
        try:
            numbers = [int(n) for n in (_json(answer) or {}).get("sources", [])]
        except (ValueError, TypeError, AttributeError):
            numbers = []
        ordered = [candidates[n - 1] for n in dict.fromkeys(numbers) if 1 <= n <= min(40, len(candidates))]
        if not ordered:  # Claude did not answer properly: keep the search engines' order
            ordered = candidates[:12]
        for c in ordered:
            domain = urlsplit(c["url"]).netloc
            already = sum(urlsplit(u).netloc == domain for u in self.visited) + \
                sum(urlsplit(x["url"]).netloc == domain for x in self.queue)
            if self.failed[domain] >= 2:
                continue  # site that already failed twice (error or anti-bot block)
            if c["url"] not in self.visited and already < int(self.o["max_per_site"]):
                self.queue.append(c)

    def _excluded(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        if self.o.get("without_wikidata") and host.endswith("wikidata.org"):
            return True
        return bool(EXCLUDED.search(host)) or bool(REDDIT.search(host))

    # --- reading ------------------------------------------------------------------------------------------------

    def read(self, source: dict) -> None:
        url = source["url"]
        self.visited.add(url)
        if not replay.through("safe", url, lambda: self.local_allow or safe_address(url)):
            return
        number = len(self.visited)
        self.publish({"kind": "page", "number": number, "url": url, "title": source.get("title"),
                       "status": "reading"})
        try:
            snippet = replay.through("page", url, lambda: self._extract(url))
            if snippet.get("antibot_verification"):
                self.failed[urlsplit(url).netloc] += 1
                self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                               "text": "the site blocks automated browsers (anti-bot check)"})
                return
        except replay.UnrecordedRequest:
            raise
        except Exception as error:  # noqa: BLE001
            self.failed[urlsplit(url).netloc] += 1
            self.publish({"kind": "page", "number": number, "url": url, "status": "error",
                           "text": str(error).splitlines()[0][:150]})
            return
        self._annotate(number, url, snippet.get("title") or source.get("title"), snippet.get("text", ""))

    def _extract(self, url: str) -> dict:
        snippet = self._from_archive_wiki(url)
        if snippet is None:
            self.browser.page.goto(url, wait_until="load", timeout=30_000)
            self.browser.page.wait_for_timeout(1200)
            snippet = self.browser.page.evaluate(EXTRACTOR)
        return snippet

    def _annotate(self, number: int, url: str, title: str | None, text: str) -> None:
        """Text read → blocks → Claude reads them in the background."""
        if len(text) < MIN_TEXT:
            self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                           "text": "too little text"})
            return
        # The whole text: some sites (Treccani) have the same long preamble on every page.
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", text).encode()).hexdigest()
        if fingerprint in self.fingerprints:
            self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                           "text": "same content as a page already read"})
            return
        self.fingerprints.add(fingerprint)
        self.read_pages.add(url)
        blocks = split_in_blocks(text)
        deferred: dict[int, tuple] = {}
        futures = [self._workers.submit(self._read_block, title, url, i, len(blocks), block, deferred)
                  for i, block in enumerate(blocks, start=1)]
        self._in_progress.append(({"number": number, "url": url, "title": title, "chars": len(text),
                                "blocks": len(blocks)}, futures, deferred))
        self.publish({"kind": "page", "number": number, "url": url, "title": title, "status": "notes",
                       "chars": len(text), "blocks": len(blocks)})

    def _read_block(self, title: str, url: str, i: int, total: int, block: str,
                      deferred: dict[int, tuple] | None = None) -> tuple[int, str]:
        """Claude reads the (cleaned) block and gives notes, relevance and concepts with the sentence they come from; the
        piece is saved among the question's pieces, from which the chain picks rows. With `deferred` saving waits:
        blocks finish in random order, and the graph must come out the same on every run."""
        def save(*arguments: Any) -> None:
            if deferred is None:
                self.concepts.record(*arguments)
            else:
                deferred[i] = arguments

        if self.stopped():
            return 0, ""
        clean = cleaning.text(block, origin=url)
        if not self.o.get("chain_reading", True):
            # Model reading off (22 Oct): the piece is saved as is; the chain picks the rows itself.
            save(url, title or url, i, clean, [], [], [])
            return 2, ""
        data = self.thinker.ask(
            "reading", instructions("reading"),
            f"TOPIC: {self.topic}\n"
            + (f"SEARCH THAT FOUND THE PAGE: {cleaning.field(self.research_of[url], 200)}\n"
               if url in self.research_of else "")
            + f"SOURCE: {cleaning.field(title, 200)} ({url}) — part {i} of {total}"
            f"\n\n<<<DATA\n{clean}\nDATA>>>", SCHEMAS["reading"])
        if not isinstance(data, dict):
            return 0, ""
        relevance = int(data.get("relevance", 0))
        if relevance >= 1 and (data.get("concepts") or data.get("facts")):  # also pieces mentioned in passing
            save(url, title or url, i, clean, data.get("concepts", []), data.get("ties", []), data.get("facts", []))
        notes = str(data.get("notes") or "").strip()
        return (relevance, notes) if relevance >= 2 and notes else (relevance, "")

    def _evaluate_completed(self, wait_for: bool) -> None:
        """Records the pages whose notes are ready, in reading order, and measures their relevance and novelty."""
        while self._in_progress:
            source, futures, deferred = self._in_progress[0]
            if not wait_for and not all(f.done() for f in futures):
                return
            self._in_progress.pop(0)
            results = [f.result() for f in futures]
            for block_number in sorted(deferred):
                self.concepts.record(*deferred[block_number])
            self._check()
            notes = [n for _, n in results if n]
            relevance = self._relevance([p for p, _ in results])
            if relevance <= 1 or not notes:
                self.publish({"kind": "page", "number": source["number"], "url": source["url"],
                               "status": "discarded", "text": "off topic" if relevance <= 1 else
                               "no useful information"})
                continue
            text = "\n".join(notes)
            present, linked = self._anchored(text)
            if not linked:
                self.publish({"kind": "page", "number": source["number"], "url": source["url"],
                               "status": "discarded", "text": "not connected to what is known about the topic"})
                continue
            new_ones = present - self.knowledge
            novelty = len(new_ones) / max(1, len(present))
            # A page that covers the topic only in part (relevance 2) must pass double thresholds to count as
            # "new".
            factor, minimum = 1 if relevance == 3 else 2, int(self.o["min_new"])
            little = len(new_ones) < factor * minimum or novelty < factor * float(self.o["novelty_threshold"])
            self.little_new = self.little_new + 1 if little else 0
            self.knowledge |= new_ones
            self.quotes.update(present)
            self.sources.append({**source, "number": len(self.sources) + 1, "notes": text, "new_ones": len(new_ones),
                               "relevance": relevance})
            self.publish({"kind": "page", "number": source["number"], "url": source["url"],
                           "title": source["title"], "status": "read", "chars": source["chars"],
                           "new_information": len(new_ones), "relevance": relevance})

    def _anchored(self, notes: str) -> tuple[set[str], bool]:
        """Entities of the "anchored" note rows (those that also name something already known), and whether the page is
        connected to the topic (at least 30% of the rows with names are anchored). The first page is the baseline."""
        if len(self.knowledge) < 30:
            return entity(notes), True
        rows = [e for e in (entity(r) for r in notes.splitlines()) if e]
        anchored = [e for e in rows if e & self.knowledge]
        if rows and len(anchored) / len(rows) < 0.3:
            return set(), False
        return set().union(*anchored) if anchored else set(), True

    @staticmethod
    def _relevance(votes: list[int]) -> int:
        """Page relevance (0-3): average of Claude's votes on the blocks."""
        if not votes:
            return 0
        media = sum(votes) / len(votes)
        return 3 if media >= 2.5 else 2 if media >= 2 else 1 if media >= 1 else 0

    def _client_archive(self, host: str) -> SourceClient:
        """The safe HTTP client for api.php (redirects and addresses checked). Wikipedia goes through the chain's
        client, with its User-Agent and rate limits."""
        wiki = self.chain.wiki if self.chain is not None else None
        if wiki is not None and (host == "wikipedia.org" or host.endswith(".wikipedia.org")):
            return wiki.client
        if self._archive is None:
            self._archive = SourceClient("Wiki archive", user_agent="chain-research/0.1 (personal use)", domains=None,
                                         limiter=limiter("archivio-wiki", 1.0),
                                         local_allow=self.local_allow)
        return self._archive

    def _from_archive_wiki(self, url: str) -> dict | None:
        """For wikis (MediaWiki: Fandom, Wikipedia...) the text comes from the official api.php archive: it is the HTML
        of the content only, and it works even when the page is protected by anti-bot checks."""
        parts = urlsplit(url)
        if "/wiki/" not in parts.path:
            return None
        title = unquote(parts.path.split("/wiki/", 1)[1])
        for path in ("/w/api.php", "/api.php"):
            archive = f"{parts.scheme}://{parts.netloc}{path}"
            try:
                data = self._client_archive(parts.hostname or "").get_json(archive, params={
                    "action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": 2,
                    "redirects": 1})
            except Exception:  # noqa: BLE001 - archive missing or not allowed: read the page with the browser
                continue
            html = (data.get("parse") or {}).get("text")
            if isinstance(html, str) and len(html) > 500:
                # Blank page first: the security rules of the previous page (e.g. DuckDuckGo) would block the
                # <base>, and links would be built with the wrong address.
                base = encode_html.escape(url, quote=True)
                name = encode_html.escape(str(data["parse"].get("title") or ""))
                self.browser.page.goto("about:blank")
                self.browser.page.set_content(
                    f'<html><head><base href="{base}"><title>{name}</title></head>'
                    f'<body><div class="mw-parser-output">{html}</div></body></html>', wait_until="domcontentloaded")
                return self.browser.page.evaluate(EXTRACTOR)
        return None

    # --- answer -------------------------------------------------------------------------------------------------

    def write(self) -> str:
        text = self.chain.write(SHORT_INSTRUCTIONS) if self.chain is not None else None
        self.author = f"Claude ({self.thinker.model}) from the chain"
        reason = (self.chain.reason if self.chain is not None else "") or "chain not walked"
        return text or f"**Answer:** Not found: {reason}."

    def _source_number(self, url: str) -> int:
        """A page with evidence but no useful notes: it is added to the sources, so the citation [n] exists."""
        self.sources.append({"url": url, "title": url, "number": len(self.sources) + 1, "notes": "", "new_ones": 0,
                           "chars": 0, "relevance": 2})
        return len(self.sources)

    def _sources_list(self) -> list[dict]:
        return [{"number": f["number"], "title": f["title"], "url": f["url"]} for f in self.sources]
