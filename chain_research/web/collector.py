"""Raccolta per una domanda: la catena (catena.py) percorsa con il browser e Claude come lettore.

La catena guida la ricerca; questa classe le dà gli attrezzi: ricerche sul web (DuckDuckGo, Bing di riserva),
scelta dei risultati, lettura delle pagine con il browser (le wiki dall'archivio api.php), lettura dei blocchi da
parte di Claude con i pezzi salvati nel grafo dei concetti, limiti di pagine, minuti e budget, e la risposta scritta
con le fonti. È la stessa raccolta del banco di prova (36/40 con Claude Haiku), senza le strade dei modelli locali.
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
BLOCK = 10_000          # caratteri per blocco di lettura
MAX_BLOCKS = 8          # al massimo ~80.000 caratteri letti per pagina
MIN_TEXT = 600          # sotto questa lunghezza la pagina non è una fonte utile
# Sul nome dell'host, a parole intere: «x.com» dentro «linux.com» non conta.
EXCLUDED = re.compile(r"(^|\.)((youtube|facebook|instagram|tiktok|pinterest|twitter|amazon|ebay|linkedin|duckduckgo|bing|"
                     r"google)\.[a-z.]+|x\.com|youtu\.be)$", re.IGNORECASE)
REDDIT = re.compile(r"(^|\.)(reddit\.com|redd\.it)$", re.IGNORECASE)
COMMON_WORDS = {"the", "this", "that", "these", "in", "on", "at", "a", "an", "it", "he", "she", "they", "his",
                 "her", "il", "la", "lo", "le", "gli", "i", "un", "una", "di", "da", "per", "con", "nel", "nella",
                 "however", "after", "before", "during", "when", "while", "also", "some", "many", "according",
                 "source", "sources", "fonte", "fonti", "part", "none", "topic", "notes"}
# Limiti di una domanda: quelli del banco di prova (30 pagine, 35 minuti, 0,50 $ di Claude).
DEFAULT_OPTIONS = {"novelty_threshold": 0.25, "min_new": 10, "max_per_site": 3, "chain_pages": 30,
                       "chain_minutes": 35, "budget_claude": 0.50, "writing_reserve": 0.08}
SHORT_INSTRUCTIONS = (
    "The user needs a SHORT, precise answer, not a document. Write in ITALIAN, using ONLY the notes, exactly these "
    "lines in Markdown:\n"
    "**Risposta:** the answer in one or two sentences, with the source [n] of each fact.\n"
    "**Prove principali:** 1 to 3 bullet points, each a fact with its source [n] and, if the notes give it, the date "
    "of the information.\n"
    "**Fonti in disaccordo:** where sources disagree and how, or \"nessuno\".\n"
    "**Limiti:** what could not be verified or may be out of date.\n"
    "**Fiducia:** alta, media or bassa, with the reason in a few words.\n"
    "If the notes do not contain the answer, say so in the Risposta line instead of guessing. "
    "Cite the source number of each fact as [n].")


class Stopped(Exception):
    """L'utente ha fermato la ricerca."""


def _json(text: str) -> Any:
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(match.group(0)) if match else None


def entity(text: str) -> set[str]:
    """Nomi propri, date e numeri citati in un testo: servono a misurare quanta informazione NUOVA porta una pagina."""
    # Solo parole maiuscole a metà frase: a inizio frase o di punto elenco sono spesso parole comuni ("During").
    found_items = {m.group(0).lower() for m in re.finditer(r"(?<=[a-zà-ÿ0-9,;:)] )[A-ZÀ-Ý][\w'’-]{2,}", text)} \
        - COMMON_WORDS
    found_items.update(m.group(0).lower() for m in re.finditer(r"\b\d{2,4}(?:\s?(?:dr|bc|ad|d\.c\.|a\.c\.))?\b",
                                                            text, re.IGNORECASE))
    return found_items


def split_in_blocks(text: str) -> list[str]:
    """Blocchi da BLOCCO caratteri, al massimo MAX_BLOCCHI."""
    return [text[i:i + BLOCK] for i in range(0, min(len(text), BLOCK * MAX_BLOCKS), BLOCK)]


class _NoBrowser:
    """Nel rigioco le pagine vengono dalla registrazione."""

    def close(self) -> None:
        pass


class Collector:
    """Una domanda: `esegui()` percorre la catena e restituisce la risposta con le fonti."""

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
        self.sources: list[dict] = []           # pagine utili, numerate per le citazioni
        self.queue: list[dict] = []            # pagine candidate da leggere
        self.visited: set[str] = set()
        self.read_pages: set[str] = set()   # pagine con testo utile (contano per il limite di pagine)
        self.research_of: dict[str, str] = {}  # url → ricerca che l'ha trovata (l'anello cercato)
        self.failed: Counter[str] = Counter()  # errori e blocchi anti-robot per dominio
        self.fingerprints: set[str] = set()       # contenuti già letti (la stessa pagina da indirizzi diversi)
        self.searches_done: list[str] = []
        self.executed_query: list[str] = []
        self.knowledge: set[str] = set()     # entità già note (per misurare la novità)
        self.quotes: Counter[str] = Counter()
        self.little_new = 0
        self.concepts: Concepts | None = None  # pezzi di pagina salvati (la catena ne sceglie le righe)
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

    # --- svolgimento -------------------------------------------------------------------------------------------

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
            self.end_reason = self.chain.reason or "catena percorsa"
            self._phase(f"Raccolta conclusa: {self.end_reason}.")
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
        """Secondi dall'inizio, senza le attese per il limite di sessione di Claude."""
        return time.monotonic() - self._start - (claude_json.LIMIT_WAIT["seconds"] - self._initial_wait)

    def _reached_limit(self) -> str:
        if self.thinker.exhausted:
            return "budget di Claude esaurito"
        if len(self.read_pages) >= int(self.o["max_pages"]):
            return f"raggiunto il limite di {len(self.read_pages)} pagine web lette"
        if len(self.visited) >= 3 * int(self.o["max_pages"]):
            return f"raggiunto il tetto di {3 * int(self.o['max_pages'])} pagine visitate"
        if self._elapsed() > 60 * float(self.o["max_minutes"]):
            return f"raggiunto il limite di sicurezza di {float(self.o['max_minutes']):.0f} minuti"
        return ""

    def _ask(self, task_instructions: str, text: str) -> str:
        """Le richieste di testo libero (scelta dei risultati, riformulazioni) a Claude, punto "lettura"; il testo
        dal web passa dalla pulizia per frasi."""
        answer = self.thinker.ask("reading", task_instructions, cleaning.text(text, origin="ricerca"))
        return answer if isinstance(answer, str) else ""

    # --- ricerche ----------------------------------------------------------------------------------------------

    def _filter_query(self, searches: list[str]) -> list[str]:
        """Una query troppo simile (Jaccard ≥ soglia) a una già eseguita non parte: Claude la riformula una volta, e se
        è ancora doppia si salta."""
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
        return replay.through("ricerca", url, lambda: self._alive_results(url, script))

    def _alive_results(self, url: str, script: str) -> list[dict]:
        try:
            self.browser.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self.browser.page.wait_for_timeout(1500)
            return self.browser.page.evaluate(script) or []
        except Exception:  # noqa: BLE001 - un motore che non risponde non ferma la ricerca
            return []

    def collect(self, searches: list[str]) -> list[dict]:
        """Risultati dei motori di ricerca (DuckDuckGo, Bing di riserva): indirizzo, titolo ed estratto."""
        seen: dict[str, dict] = {}
        for research in searches:
            self._check()
            self._phase(f"Cerco: {research}")
            results = self._results(
                f"https://html.duckduckgo.com/html/?q={quote_plus(research)}",
                """[...document.querySelectorAll('.result')].map(r => ({url: r.querySelector('a.result__a')?.href,
                    titolo: r.querySelector('a.result__a')?.innerText, estratto: r.querySelector('.result__snippet')?.innerText}))""")
            if not results:
                results = self._results(
                    f"https://www.bing.com/search?q={quote_plus(research)}",
                    """[...document.querySelectorAll('li.b_algo')].map(r => ({url: r.querySelector('h2 a')?.href,
                        titolo: r.querySelector('h2 a')?.innerText, estratto: r.querySelector('.b_caption p, p')?.innerText}))""")
            for r in results:
                url = r.get("url") or ""
                if "duckduckgo.com/l/" in url:  # collegamento di reindirizzamento di DuckDuckGo
                    url = parse_qs(urlsplit(url).query).get("uddg", [""])[0]
                if not url.startswith("http") or url in seen or url in self.visited:
                    continue
                if canonical_key(url) or self._excluded(url):
                    continue  # piattaforme che il lettore di pagine non legge (video, domande, Wikidata)
                seen[url] = {"url": url, "title": (r.get("title") or "")[:150],
                              "snippet": (r.get("snippet") or "")[:300]}
                self.research_of.setdefault(url, research)  # la ricerca che ha trovato la pagina (anello cercato)
        return list(seen.values())

    def _add_candidates(self, candidates: list[dict], purpose: str = "") -> None:
        """Claude ordina i risultati per utilità e scarta quelli inutili; si aggiungono in coda. Con `scopo` (un passo
        della catena) l'utilità è per quel passo, non per la domanda intera."""
        if not candidates:
            return
        listing = "\n".join(f"{i + 1}. {c['title']} — {urlsplit(c['url']).netloc}\n   {c['snippet']}"
                           for i, c in enumerate(candidates[:40]))
        answer = self._ask(
            "Rank the search results that are useful and reliable for an accurate, in-depth report on the TOPIC, "
            "from most to least useful: prefer encyclopedias, specialised wikis, official, academic or expert sites; "
            "leave out shops, thin forums, unrelated pages and duplicates. "
            'Reply only with JSON: {"fonti": [numbers in order of usefulness]}.' + (
                " The TOPIC is one step of a research chain: the useful pages are the ones likely to state that fact "
                "(official site, company or project page, profile, news, encyclopedia), not general pages on the "
                "subject." if purpose else ""),
            f"TOPIC: {purpose or self.topic}\n\nRESULTS:\n{listing}")
        try:
            numbers = [int(n) for n in (_json(answer) or {}).get("sources", [])]
        except (ValueError, TypeError, AttributeError):
            numbers = []
        ordered = [candidates[n - 1] for n in dict.fromkeys(numbers) if 1 <= n <= min(40, len(candidates))]
        if not ordered:  # Claude non ha risposto bene: si tiene l'ordine dei motori di ricerca
            ordered = candidates[:12]
        for c in ordered:
            domain = urlsplit(c["url"]).netloc
            already = sum(urlsplit(u).netloc == domain for u in self.visited) + \
                sum(urlsplit(x["url"]).netloc == domain for x in self.queue)
            if self.failed[domain] >= 2:
                continue  # sito che ha già fallito due volte (errore o blocco anti-robot)
            if c["url"] not in self.visited and already < int(self.o["max_per_site"]):
                self.queue.append(c)

    def _excluded(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        if self.o.get("without_wikidata") and host.endswith("wikidata.org"):
            return True
        return bool(EXCLUDED.search(host)) or bool(REDDIT.search(host))

    # --- lettura ------------------------------------------------------------------------------------------------

    def read(self, source: dict) -> None:
        url = source["url"]
        self.visited.add(url)
        if not replay.through("sicuro", url, lambda: self.local_allow or safe_address(url)):
            return
        number = len(self.visited)
        self.publish({"kind": "page", "number": number, "url": url, "title": source.get("title"),
                       "status": "reading"})
        try:
            snippet = replay.through("page", url, lambda: self._extract(url))
            if snippet.get("antibot_verification"):
                self.failed[urlsplit(url).netloc] += 1
                self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                               "text": "il sito blocca i browser automatici (verifica anti-bot)"})
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
        """Testo letto → blocchi → lettura di Claude in sottofondo."""
        if len(text) < MIN_TEXT:
            self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                           "text": "troppo poco testo"})
            return
        # Tutto il testo: alcuni siti (Treccani) hanno lo stesso lungo preambolo in ogni pagina.
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", text).encode()).hexdigest()
        if fingerprint in self.fingerprints:
            self.publish({"kind": "page", "number": number, "url": url, "status": "discarded",
                           "text": "stesso contenuto di una pagina già letta"})
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
        """Claude legge il blocco (ripulito) e dà appunti, pertinenza e concetti con la frase da cui vengono; il pezzo
        si salva tra i pezzi della domanda, da cui la catena sceglie le righe. Con `rinviati` il salvataggio aspetta:
        i blocchi finiscono in un ordine a caso, e il grafo deve venire uguale a ogni corsa."""
        def save(*arguments: Any) -> None:
            if deferred is None:
                self.concepts.record(*arguments)
            else:
                deferred[i] = arguments

        if self.stopped():
            return 0, ""
        clean = cleaning.text(block, origin=url)
        if not self.o.get("chain_reading", True):
            # Lettura del modello spenta (22/10): il pezzo si salva così com'è; la catena sceglie da sé le righe.
            save(url, title or url, i, clean, [], [], [])
            return 2, ""
        data = self.thinker.ask(
            "reading", instructions("reading"),
            f"ARGOMENTO: {self.topic}\n"
            + (f"RICERCA CHE HA TROVATO LA PAGINA: {cleaning.field(self.research_of[url], 200)}\n"
               if url in self.research_of else "")
            + f"FONTE: {cleaning.field(title, 200)} ({url}) — parte {i} di {total}"
            f"\n\n<<<DATI\n{clean}\nDATI>>>", SCHEMAS["reading"])
        if not isinstance(data, dict):
            return 0, ""
        relevance = int(data.get("relevance", 0))
        if relevance >= 1 and (data.get("concepts") or data.get("facts")):  # anche i pezzi citati di passaggio
            save(url, title or url, i, clean, data.get("concepts", []), data.get("ties", []), data.get("facts", []))
        notes = str(data.get("notes") or "").strip()
        return (relevance, notes) if relevance >= 2 and notes else (relevance, "")

    def _evaluate_completed(self, wait_for: bool) -> None:
        """Registra le pagine i cui appunti sono pronti, nell'ordine di lettura, e ne misura pertinenza e novità."""
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
                               "status": "discarded", "text": "fuori tema" if relevance <= 1 else
                               "nessuna informazione utile"})
                continue
            text = "\n".join(notes)
            present, linked = self._anchored(text)
            if not linked:
                self.publish({"kind": "page", "number": source["number"], "url": source["url"],
                               "status": "discarded", "text": "non collegata a ciò che si sa dell'argomento"})
                continue
            new_ones = present - self.knowledge
            novelty = len(new_ones) / max(1, len(present))
            # Una pagina che parla dell'argomento solo in parte (pertinenza 2) per contare come "nuova" deve
            # superare soglie doppie.
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
        """Entità delle righe di appunti "ancorate" (che nominano anche qualcosa di già noto), e se la pagina è
        collegata all'argomento (almeno il 30% delle righe con nomi è ancorata). La prima pagina fa da base."""
        if len(self.knowledge) < 30:
            return entity(notes), True
        rows = [e for e in (entity(r) for r in notes.splitlines()) if e]
        anchored = [e for e in rows if e & self.knowledge]
        if rows and len(anchored) / len(rows) < 0.3:
            return set(), False
        return set().union(*anchored) if anchored else set(), True

    @staticmethod
    def _relevance(votes: list[int]) -> int:
        """Pertinenza della pagina (0-3): media dei voti di Claude sui blocchi."""
        if not votes:
            return 0
        media = sum(votes) / len(votes)
        return 3 if media >= 2.5 else 2 if media >= 2 else 1 if media >= 1 else 0

    def _client_archive(self, host: str) -> SourceClient:
        """Il client HTTP sicuro per api.php (redirect e indirizzi controllati). Wikipedia passa dal client della
        catena, con il suo User-Agent e i suoi limiti di frequenza."""
        wiki = self.chain.wiki if self.chain is not None else None
        if wiki is not None and (host == "wikipedia.org" or host.endswith(".wikipedia.org")):
            return wiki.client
        if self._archive is None:
            self._archive = SourceClient("Archivio wiki", user_agent="ricerca-catena/0.1 (uso personale)", domains=None,
                                         limiter=limiter("archivio-wiki", 1.0),
                                         local_allow=self.local_allow)
        return self._archive

    def _from_archive_wiki(self, url: str) -> dict | None:
        """Per le wiki (MediaWiki: Fandom, Wikipedia...) il testo si prende dall'archivio ufficiale api.php: è l'HTML
        del solo contenuto, e funziona anche quando la pagina è protetta da verifiche anti-bot."""
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
            except Exception:  # noqa: BLE001 - archivio assente o non permesso: si legge la pagina con il browser
                continue
            html = (data.get("parse") or {}).get("text")
            if isinstance(html, str) and len(html) > 500:
                # Pagina vuota prima: le regole di sicurezza della pagina precedente (es. DuckDuckGo) bloccherebbero
                # il <base> e i collegamenti verrebbero costruiti con l'indirizzo sbagliato.
                base = encode_html.escape(url, quote=True)
                name = encode_html.escape(str(data["parse"].get("title") or ""))
                self.browser.page.goto("about:blank")
                self.browser.page.set_content(
                    f'<html><head><base href="{base}"><title>{name}</title></head>'
                    f'<body><div class="mw-parser-output">{html}</div></body></html>', wait_until="domcontentloaded")
                return self.browser.page.evaluate(EXTRACTOR)
        return None

    # --- risposta -----------------------------------------------------------------------------------------------

    def write(self) -> str:
        text = self.chain.write(SHORT_INSTRUCTIONS) if self.chain is not None else None
        self.author = f"Claude ({self.thinker.model}) dalla catena"
        reason = (self.chain.reason if self.chain is not None else "") or "catena non percorsa"
        return text or f"**Risposta:** Non trovata: {reason}."

    def _source_number(self, url: str) -> int:
        """Una pagina con prove ma senza appunti utili: si aggiunge alle fonti, così la citazione [n] esiste."""
        self.sources.append({"url": url, "title": url, "number": len(self.sources) + 1, "notes": "", "new_ones": 0,
                           "chars": 0, "relevance": 2})
        return len(self.sources)

    def _sources_list(self) -> list[dict]:
        return [{"number": f["number"], "title": f["title"], "url": f["url"]} for f in self.sources]
