"""Catena guidata (15/10, decisione di Niccolò): il grafo e il programma guidano il ragionamento, il modello fa solo
compiti piccoli e verificabili. Pensato per far fare ricerche a catena anche a modelli piccoli.

1. Claude interpreta la domanda (riformulazioni, indizi con le letture possibili, sotto-domande) e la trasforma in una
   catena: partenza certa, passi (relazione da seguire, tipo del risultato, controlli con le letture), risposta.
2. Il programma percorre la catena un anello alla volta, con un fascio di al massimo `larghezza` entità:
   - per ogni entità del fascio: ricerche per la relazione (punto "passo"), pagine lette con la lettura normale
     (concetti, legami e fatti entrano nel grafo);
   - il modello elenca i candidati che le prove numerate indicano nella relazione (punto "candidati");
   - il modello dice per ogni candidato e controllo "sì / no / non so" con il numero della prova (punto "verifica");
     un "no" con prova scarta il candidato; ai candidati migliori con dei "non so" si cerca la scheda e si rifà la
     verifica, una volta;
   - restano i candidati senza "no", ordinati per numero di "sì"; se non ne resta nessuno si ritenta il passo con
     ricerche diverse, una volta, poi la catena si ferma lì.
3. Il valore richiesto si prende dalle prove dell'entità finale (punto "valore"), cercando la scheda se manca.
4. Chi scrive riceve la catena trovata con le prove di ogni anello.
Le prove sono righe numerate costruite dal programma dal grafo e dai pezzi di pagina salvati: il modello indica un
numero, non copia frasi, e ogni affermazione resta legata alla sua pagina.
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
# Passi verso cose che di solito non hanno un nome proprio né una pagina: si possono saltare (`_piano_salto`).
NAMELESS_OBJECTS = re.compile(r"\b(simulacr\w*|statu[ae]\w*|dipint\w*|scultur\w*|affresc\w*|oggett\w*|"
                                r"immagin\w*|ritratt\w*|reliqui\w*|statue|painting|sculpture|artwork|object)\b")
# Radici (5 lettere) di parole di tipo o di misura, che nei controlli non distinguono una riga dall'altra.
GENERIC_ROOTS = {"paese", "comun", "citta", "picco", "grand", "celeb", "famos", "local", "terri", "luogo", "perso",
                    "azien", "organ", "socie", "compa", "town", "villa", "city", "small", "large", "frazi", "borgo",
                    "stato", "regio", "nazio", "itali", "franc", "ameri", "tedes", "ingle", "inter", "milan"}
# Righe del riquadro senza relazioni utili (ToG `abandon_rels`: ID, codici, siti, immagini).
USELESS_INFOBOX = re.compile(r"^(immagine|image|logo|firma|signature|mappa|map|coordinate|coordinates|sito( web)?|"
                              r"website|url|codice|code|id|isbn|issn|iso|didascalia|caption|stemma|bandiera|flag|"
                              r"coat of arms|cap|prefisso|fuso orario|time zone|targa|zip code|postal code|"
                              r"area code|dialing code|codice istat|codice catastale|nome abitanti)\b[^:]{0,20}:", re.I)
MAX_CANDIDATES = 8
# Verifica a quattro valori con pesi (WisPaper, 2026): ogni esito vale tanto quanto il peso del controllo; il conto lo
# fa il programma. I modelli generici dicono "sì" troppo facilmente e sbagliano proprio sui casi a metà, per questo
# c'è "in parte" e per questo un "sì" vale solo se le due letture lo danno entrambe.
OUTCOME_VALUE = {"yes": 1.0, "partly": 0.5, "unknown": 0.0, "no": -1.0}
MONTHS = ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|"
        "january|february|march|april|may|june|july|august|september|october|november|december|"
        "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec|"
        "januar|februar|märz|mai|juni|juli|oktober|dezember|"
        "janvier|février|mars|avril|juin|juillet|août|septembre|octobre|novembre|décembre|"
        "enero|febrero|abril|mayo|junio|julio|septiembre|octubre|noviembre|diciembre|"
        "janeiro|fevereiro|março|maio|junho|julho|setembro|outubro|novembro|dezembro")
# Anni con l'era scritta: «44 a.C.», «800 d.C.», «44 BC», «800 AD».
ERA = re.compile(r"\b(\d{1,4})\s*(a\.?\s?c\b\.?|b\.?\s?c\b\.?(?:e\b\.?)?|d\.?\s?c\b\.?|a\.?\s?d\b\.?|c\.?\s?e\b\.?)", re.I)
_ORDINAL = r"(?:°|º|st|nd|rd|th|er|\.)?"


@dataclass
class Proof:
    text: str
    url: str
    hyperlinks: list[str] = field(default_factory=list)  # voci di Wikipedia collegate nella riga
    language: str = ""  # lingua della voce di Wikipedia da cui viene la riga


@dataclass
class Link:
    name: str
    points: int = 0  # numero di controlli con "sì" meno quelli con "no"
    quality: float = 0.0  # somma pesata degli esiti diviso la somma dei pesi, da -1 a 1
    outcomes: list[dict] = field(default_factory=list)  # {controllo, esito, prova: Prova | None}
    parent: "Link | None" = None
    tie_proof: Proof | None = None  # la frase che lo lega all'anello precedente
    weak: bool = False  # tenuto solo perché nessun altro candidato passava i controlli
    context: str = ""  # come la catena lo ha raggiunto, per le ricerche senza omonimi ("progettista di Freedo")
    title: tuple[str, str] | None = None  # (lingua, titolo) della voce di Wikipedia, se raggiunto da un collegamento
    articles: dict[str, Article] = field(default_factory=dict)  # lingua → voce di Wikipedia dell'entità
    searched_articles: bool = False
    articles_other: int = -1  # quante altre lingue sono già state cercate oltre alla prima voce
    start: "Proof | None" = None  # prime frasi della voce di Wikipedia (verifica economica)
    failures: float = 0.0  # frontiera: rami che da qui non hanno portato a niente
    preferred: float = 0.0  # frontiera: premio dello spareggio finale tra candidati pari
    empty_wiki: bool = False  # allargato su Wikipedia senza nessun candidato (il web è l'unica strada)
    base_step: int = 0  # ancora: quanti passi del piano porta già fatti (0 per la partenza)
    relation: str = ""  # la relazione seguita per arrivare qui (per l'ancora, quella che parte da lei)
    plan: dict | None = None  # radici della frontiera: il piano di cui seguono i passi


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def _entity_key(name: str) -> str:
    """Una chiave per le varianti dello stesso nome: minuscole, senza accenti, spazi né punteggiatura («Keyboard.io» =
    «Keyboardio»; graphify: normalizzazione prima di tutto). «Keyboard.io Model01» resta un'altra entità."""
    return re.sub(r"[\W_]+", "", _plan(without_corporate_suffix(name)))


def _name_key(name: str) -> str:
    """L'ultima parola lunga del nome (di solito il cognome), per ritrovarlo nei testi anche scritto in altro modo.
    Senza le parentesi (24/10, Monte Bianco: «La Thuile (Italia)» dava «italia», e la voce presa era «La Salle
    (Italia)», primo collegamento della riga con «italia»)."""
    without = _norm(re.sub(r"\([^)]*\)", " ", name)) or _norm(name)
    words = [p for p in without.split() if len(p) >= 4]
    return words[-1] if words else without


def _weight(e: dict) -> float:
    return float(e.get("weight") or 1)


def quality(outcomes: list[dict]) -> float:
    """Somma pesata degli esiti diviso la somma dei pesi: 1 = tutti i controlli provati, -1 = tutti smentiti."""
    total = sum(_weight(e) for e in outcomes)
    return sum(_weight(e) * OUTCOME_VALUE.get(e["outcome"], 0.0) for e in outcomes) / total if total else 0.0


def _numbers(text: str) -> list[float]:
    """I numeri di un valore in tutte le letture possibili (20,65 / 3.274 / 1,234.5): basta che una rispetti
    l'intervallo."""
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
    """Gli anni scritti nel testo; prima di Cristo sono negativi. Senza l'era servono almeno 3 cifre."""
    years = []
    for m in ERA.finditer(text):
        first = m.group(2).lower().replace(".", "").replace(" ", "") in ("ac", "bc", "bce")
        years.append(-int(m.group(1)) if first else int(m.group(1)))
    rest = ERA.sub(" ", text)
    return years + [int(x) for x in re.findall(r"\b\d{3,4}\b", rest)]


def allowed_value(value: str, answer: dict) -> str:
    """Controllo meccanico del valore (DS4A: il tipo di dato decide quali righe valgono): tipo e intervallo dati dal
    piano. Restituisce il motivo del rifiuto, vuoto se va bene. (17/10, Due Sicilie: «29 novembre 1943» per uno Stato
    «nato e sciolto nel 1945»; Divina Commedia: 3.274 abitanti per «tra 300 e 350».)"""
    kind = str(answer.get("value_kind") or "")
    text = str(value or "").lower()
    years = _years(text)
    month_day = re.search(rf"\b\d{{1,2}}{_ORDINAL}\s+(?:(?:of|de|del)\s+)?(?:{MONTHS})\b", text) or re.search(
        rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", text) or re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if kind == "full date" and not (month_day and years):
        return "non è una data completa"
    if kind == "year" and not years:
        return "non contiene un anno"
    if kind == "day and month" and not month_day:
        return "non contiene giorno e mese"
    if kind in ("number", "code") and not re.search(r"\d", text):
        return "non contiene un numero"
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
        return f"fuori dall'intervallo {minimum:g}-{maximum:g}"
    return ""


class Chain:
    def __init__(self, research) -> None:
        self.r = research
        self.o = research.o
        self.interpretation: dict = {}
        self.plan: dict = {}
        self.links: list[list[Link]] = []  # fascio tenuto a ogni passo
        self.value: dict | None = None  # {valore, prova: Prova, anello: Anello}
        self.reason = ""
        self.step_searches: list[str] = []
        self.history: list[dict] = []  # per il resoconto e le diagnosi
        # Wikipedia come grafo (16/10): voci = nodi, collegamenti = archi; prima di cercare sul web.
        self.wiki = WikipediaGraph(local_allow=research.local_allow,
                                   max_articles=int(self.o.get("wikipedia_articles", 600)),
                                   interval_s=self.o.get("wikipedia_interval")) \
            if self.o.get("wikipedia_graph", True) else None
        self._segment_numbers: dict[str, int] = {}
        self._alive: list[Link] = []  # candidati non smentiti dell'ultimo passo percorso
        self.actions: list[str] = []  # frontiera: azioni fatte, in ordine (per le diagnosi)
        self._recorded: set[tuple[str, str, str]] = set()  # legami scritti da _registra: mai prove
        self._snippets: list[Proof] = []  # estratti dei risultati dell'ultima ricerca (pagine non lette)
        self.conflicts: list[dict] = []  # candidati scartati che avevano una fonte forte (validazione.disaccordi)
        self.exams: dict | None = None  # controlli con il modello sulla soluzione (revisione_finale)
        self.discarded: dict | None = None  # il valore trovato e non dato: la revisione ha smentito la premessa
        self._added_pages = 0  # pagine concesse oltre il tetto per cercare il valore

    # --- chiamate al modello ------------------------------------------------------------------------------------

    def _decide(self, point: str, data: dict, default: dict) -> dict:
        outcome, parent = self.r.thinker.decide(point, data, lambda: default)
        self.r.cost = max(self.r.cost, self.r.thinker.ledger.spent)
        return outcome if parent == "claude" else default

    # --- 1. interpretazione e catena ---------------------------------------------------------------------------

    def prepare(self) -> bool:
        self.r._phase("Claude interpreta la domanda…")
        self.interpretation = self._decide("interpret", {"question": self.r.topic[:1000], "today": self.r.today},
                                            {"rephrasings": [], "clues": [], "sub_questions": []})
        self.r._phase("Claude prepara la catena da percorrere…")
        for _ in range(2):  # 25/10, LibreOffice: «Claude non ha risposto in tempo» sul piano, catena mai partita
            self.plan = self._decide("chain", {"question": self.r.topic[:1000], "today": self.r.today,
                                                 "interpretation": self.interpretation},
                                      {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
            ok = bool(self.plan.get("steps")) and bool((self.plan.get("starting_point") or {}).get("entity"))
            if ok or self.r.thinker.exhausted:
                break
        self.r.publish({"kind": "chain_search", "interpretation": self.interpretation, "plan": self.plan})
        return ok

    # --- prove dal grafo ---------------------------------------------------------------------------------------

    def _after_segments(self, last: int) -> list[dict]:
        return [dict(r) for r in self.r.concepts.graph.db.execute(
            "SELECT s.id, s.text, f.url FROM segment s JOIN source f ON f.id = s.source_id WHERE s.id > ? "
            "ORDER BY s.id", (last,))]

    def _last_segment(self) -> int:
        row = self.r.concepts.graph.db.execute("SELECT MAX(id) FROM segment").fetchone()
        return int(row[0] or 0)

    def _find(self, name: str) -> int | None:
        """Il nodo del grafo per un nome, anche scritto in modo un po' diverso (cognome contenuto nel nome)."""
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
        """Fatti, legami e menzioni del nodo nel grafo, più le zone dei pezzi di pagina in cui compare il nome."""
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
                # Mai i legami scritti dalla catena stessa: sono conclusioni, non prove (24/10, Monte Bianco: il primo
                # piano registra «Saint-Gervais → gemellata con → Vado Ligure», anello debole citato con l'inizio della
                # voce di Vado Ligure; il secondo piano lo legge come prova e Vado Ligure diventa «forte»).
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
        """Per elencare i candidati: per intero i pezzi delle pagine trovate dalle ricerche di QUESTO passo (sono
        state cercate per la relazione: elenchi di allievi, schede), quelli già salvati solo se la pagina è dedicata
        all'entità (nome nell'indirizzo), gli altri intorno al nome; più fatti e legami del nodo.
        (15/10: la pagina della Mathematics Genealogy di Galileo era letta ma l'elenco degli allievi restava fuori.)"""
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

    # --- ricerca e lettura di un gruppo di pagine --------------------------------------------------------------

    def _search_and_read(self, searches: list[str], pages: int, purpose: str = "") -> list[dict]:
        """Cerca, legge fino a `pagine` pagine trovate da QUESTE ricerche e restituisce i pezzi salvati nuovi. `scopo`:
        il passo per cui si cerca, per scegliere le pagine (default: la domanda intera)."""
        self._snippets = []
        searches = [q for q in self.r._filter_query(searches) if q]
        if not searches:
            return []
        last = self._last_segment()
        self.r.searches_done += searches
        self.step_searches += searches
        self.r.publish({"kind": "search_plan", "searches": searches, "parent": "chain"})
        results = self.r.collect(searches)
        # Gli estratti dei risultati sono testo delle pagine e costano zero letture: prove in più per i candidati
        # (25/10: si leggono 3 pagine su ~25 risultati; «Keyboardio was founded in 2012 by Jesse Vincent and Kaia
        # Dekker» stava nell'estratto di una pagina mai letta).
        self._snippets = [Proof(f"[risultato di ricerca: {x.get('title', '')}] {x['snippet']}", x["url"])
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

    # --- Wikipedia come grafo -----------------------------------------------------------------------------------

    def _research_language(self) -> str:
        """La lingua della ricerca (22/10, Niccolò): inglese, salvo domande legate a un paese (la Cappella Sistina in
        italiano, un villaggio greco in greco), come deciso nel piano."""
        language = str(self.plan.get("research_language") or "en").lower()
        return language if LANGUAGE.match(language) else "en"

    def _languages(self) -> list[str]:
        """Lingua della ricerca, inglese, lingue del piano e sempre l'italiano, ultimo (24/10, LibreOffice: con la
        ricerca in inglese la voce italiana di BeOS, quella con «Release corrente: R5.1d0 (15 novembre 2001)», non
        veniva mai letta; le domande sono in italiano e i riquadri italiani spesso hanno il dato chiesto)."""
        other_ones = [str(x).lower() for x in self.plan.get("languages") or [] if LANGUAGE.match(str(x).lower())]
        return list(dict.fromkeys([self._research_language(), "en", *other_ones, "it"]))[:5]

    @staticmethod
    def _context_words(text: str) -> list[str]:
        return [p for p in _norm(text).split() if len(p) >= 5 and p not in STOP_WORDS][:8]

    def _relevant(self, article: Article, a: Link) -> bool:
        """Contro gli omonimi: la voce di un'entità raggiunta dalla catena deve collegare la voce dell'entità di prima
        (in quella lingua) o nominarla per intero; se l'entità di prima non ha voci, basta il cognome o una parola
        del contesto. (16/10: «René II de Lorraine-Guise» portava al duca di Lorena nato ad Angers, non a René
        d'Elbeuf figlio di Claudio di Guisa; 15/10: «Freedo» al produttore svizzero, non alla mascotte.)"""
        if a.title is not None:
            return True
        if a.parent is None:  # partenza: la voce deve condividere almeno una parola dell'indizio (Freedo: mascotte)
            roots = {p[:4] for p in _norm(a.context).split() if len(p) >= 5 and p not in STOP_WORDS}
            roots |= {p[:4].lower() for p in re.findall(r"\b[A-Z][\w-]{2,}", a.context)}
            # Il nome stesso non conta: compare in ogni omonimo (22/10, Freedo: la voce inglese del produttore
            # svedese passava perché conteneva «Freedo»).
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
        """Le voci dell'entità: la prima trovata (italiano, inglese, lingue del piano) e fino ad `altre` in altre
        lingue, prima quelle del piano (la voce nella lingua del luogo ha più dettagli locali). Poche richieste:
        Wikipedia ne concede 10 al minuto."""
        if self.wiki is None:
            return []
        # Una voce letta per la verifica (una lingua) non basta per il valore: le altre lingue si aggiungono dopo
        # (18/10, Debian: il W3C in italiano dà solo «ottobre 1994», in inglese la data completa non è mai arrivata).
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
            # 23/10 (piano entità e righe, fasi 2-3): voce decisa dal grafo, non dal titolo «simile». Per la partenza
            # (nessuna entità di prima) le voci senza legame passano ancora da `_pertinente` (indizio).
            # Un'entità senza voce presta ai figli le voci di chi l'ha trovata (24/10, Wikipedia: «Wikivoyage e.V.»
            # non ha voce; senza vicini il pittore «Roland Unger» (1941) passava come voce del presidente dell'associazione,
            # nominato senza collegamento nella voce tedesca di Wikivoyage).
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
        """Due voci con lo stesso legame e lo stesso grado: lista chiusa numerata (GENRE: solo titoli validi o
        «nessuna»; DeepDive: risposta = numero). 0 = nessuna."""
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
        """Radici (5 lettere: «succeduto»/«succeeded», «derivate»/«derivative») e coppie di radici vicine: i termini
        del TF-IDF delle righe."""
        roots = [p[:5] for p in _norm(text).split() if len(p) >= 3 and p not in STOP_WORDS]
        return roots + [f"{a}_{b}" for a, b in zip(roots, roots[1:])]

    @staticmethod
    def _merged_rows(v: Article, chars: int = 700) -> list[tuple[int, "Row"]]:
        """Le righe della voce con le voci di un elenco della stessa sezione unite in una riga (fino a 700
        caratteri): «Distribuzioni derivate: Ubuntu · Knoppix · Bharat Linux …» come «Notable students» del
        riquadro; un elenco lungo non riempie da solo le righe date al modello (23/10, Debian → Bharat Linux)."""
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
        """Le righe delle voci più utili per le `parole`. 23/10 (piano entità e righe, fase 4; prima: tutto il
        riquadro e 40 frasi d'inizio sempre dentro, conteggio grezzo, 70 righe: Galileo, 69 righe per 4 utili):
        - punteggio TF-IDF con l'IDF calcolato sulle righe di queste voci (FEVER, `OnlineTfidfDocRanker`), radici e
          coppie di radici; il titolo della sezione conta come la riga;
        - il riquadro compete con le altre righe (bonus piccolo), senza le righe inutili (ToG `abandon_rels`:
          immagini, codici, siti, coordinate); le prime 2 frasi della voce (la definizione) entrano sempre;
        - per la verifica (`riquadro=False`) entra sempre la riga delle categorie (gesuita, nato a…);
        - righe uguali una volta sola. Con `chiave` solo le righe che la contengono (voci su altro).
        Con `nudo` solo le parole contano (niente definizione, categorie, riquadro o collegamenti in più)."""
        question = {t for x in words for t in self._terms(x)}
        if exclude:  # il nome dell'entità sta in tutta la sua voce: non distingue le righe
            name = set(self._terms(exclude))
            question = {t for t in question if t not in name and not any(p in name for p in t.split("_"))}
        if bare:  # una riga «di un controllo» deve averne le parole proprie, non «paese» o «comune»
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

        def weight(t: str) -> float:  # 0..1: raro nelle righe = più informativo
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
            # il titolo della sezione conta come la riga (16/10, Galvani: l'elenco dei presidenti dell'Accademia
            # dei XL, righe con solo nomi e date sotto la sezione «Presidenti»)
            points += 20 * sum(weight(t) for t in set(self._terms(r.section)) & question)
            if bare:
                pass
            elif r.kind == "riquadro":
                # Il riquadro è denso di relazioni, ma per la verifica passa davanti solo con le parole dei controlli
                # (17/10, Ducato di Milano: «Gemellaggi: Linards» restava fuori dietro 25 righe del riquadro).
                points = points * 1.5 + ((3 if infobox else 1) if points > 0 else 0)
            elif r.kind == "categories":
                points += 0 if infobox else 60
            elif not r.section and r.kind == "sentence":
                # la definizione: le prime 2 frasi vere (non i pezzi di pronuncia «( gal-VAH-nee;», Galvani)
                if len(r.text) >= 60:
                    start_sentences[v.title] = start_sentences.get(v.title, 0) + 1
                points += 60 if len(r.text) >= 60 and start_sentences[v.title] <= 2 else 2
            elif r.hyperlinks:
                points += 1.0  # righe con voci collegate: riempiono i posti liberi (i candidati sono voci)
            if bonus is not None:
                points += bonus(r.text)
            if points > 0.6:
                scores.append((points, v, i, r))
        # Le 6 righe migliori del riquadro di ogni voce entrano comunque (per i candidati): relazioni come la
        # cittadinanza stanno in «Born: … Como, Duchy of Milan» senza parole in comune (23/10, oracolo righe).
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
            if r.hyperlinks:  # i titoli esatti delle voci collegate: il candidato si nomina come la sua voce
                text += f" ⟶ voci: {'; '.join(r.hyperlinks[:25 if r.kind == 'elenco' else 10])}"
            if len(kept) >= maximum:
                break
            if total + len(text) > chars:
                continue  # una riga troppo lunga non ferma le altre
            kept.append((v.language, v.title, i, Proof(text, v.url, list(r.hyperlinks), v.language)))
            total += len(text)
        kept.sort(key=lambda x: (x[0], x[1], x[2]))  # in ordine di pagina: si legge come un testo
        return [x[3] for x in kept]

    def _pages_that_mention(self, a: Link, words: list[str]) -> list[Proof]:
        """Se l'entità non ha una voce: le righe delle voci che la nominano (HierSearch, «adjacent passages»)."""
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
        """Relazioni al contrario: le voci che collegano all'entità, con le loro prime frasi. Solo per entità poco
        collegate (con centinaia di voci entranti l'elenco non dice niente)."""
        proofs: list[Proof] = []
        for article in self._articles(a)[:1]:
            titles = self.wiki.incoming(article.title, article.language, n=100)
            if not titles or len(titles) >= 100:
                continue
            starts = self.wiki.starts(titles[:40], article.language)
            for title, text in starts.items():
                proofs.append(Proof(f"[{title} ({article.language}), voce che collega a {article.title}] {text[:500]}",
                                   self.wiki.url(title, article.language), [title], article.language))
        return proofs

    def _wiki_candidates(self, number: int, step: dict, origin: Link, attempt: int,
                        candidates: dict[str, Link]) -> None:
        """Il passo sul grafo di Wikipedia: righe della voce dell'entità (o delle voci che la nominano) scelte dal
        programma per le parole della relazione; il modello indica solo i candidati con il numero della riga."""
        if self.wiki is None:
            return
        words = list(step.get("words") or []) + [step["relation"], step.get("kind", "")]
        self.r._phase(f"Passo {number} su Wikipedia: {step['relation']} {origin.name}…")
        articles = self._articles(origin)
        if attempt == 1:
            # 29/10, Cappella Sistina: «Assunta», «Assunzione» tra le parole del passo dalla voce dell'Assunzione di
            # Maria: 20 righe sul dogma, fuori la galleria con i comuni («Celebrazione dell'Assunta a Novara di Sicilia»).
            names = " ".join([origin.name] + [v.title for v in articles])
            proofs = self._wiki_rows(articles, words, exclude=names) if articles else []
            if not articles or len(proofs) < 8:
                proofs += self._pages_that_mention(origin, words)
            if not articles:
                # Senza voce: anche i pezzi già letti che la nominano, a costo zero di pagine (24/10, Arduino: «Keyboardio
                # is an Oakland-based company founded in 2012 by the husband-and-wife team, Jesse Vincent and Kaia
                # Dekker» era in una pagina letta; i fondatori si cercavano solo su Wikipedia e poi sul web, mai fatto).
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
        """Ricerca per descrizione dentro Wikipedia (i controlli come parole), prime frasi delle voci trovate."""
        if self.wiki is None:
            return
        description = "; ".join(c.get("description", "") for c in step.get("checks", []))
        searches = [str(x) for x in step.get("descriptions") or [] if str(x).strip()][:4]
        if not description and not searches:
            return
        proofs: list[Proof] = []
        seen_items: set[str] = set()
        for language in self._languages()[:4]:  # ogni ricerca in ogni lingua: non sappiamo in che lingua è scritta
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
        """Per la verifica: riquadro, inizio della voce del candidato e righe con le parole dei controlli o il nome
        dell'entità di prima."""
        articles = self._articles(a, other_ones=0)
        if not articles:
            return []
        words = [c.get("description", "") for c in checks] + [x for c in checks for x in c.get("readings") or []]
        if a.parent is not None:
            words.append(a.parent.name)
        # Ogni controllo ha le sue 2 righe migliori, prima i più pesanti; poi la scelta comune riempie i posti. Con
        # tutte le parole insieme un controllo raro perdeva contro tante parole comuni (24/10, Ducato: «gemellato con
        # un paese dell'Alta Vienna» contro «Lomellina», «Pavia», «paese»: «Gemellaggi: Linards» fuori dalle 14).
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
        """Passaggi adiacenti (HierSearch): alle righe che riguardano un controllo decisivo (peso 3) e collegano altre
        voci si aggiunge la prima frase di quelle voci, con una richiesta per lingua (18/10, Ducato di Milano:
        «Gemellaggi: Linards (Francia)» non dice che Linards è nell'Alta Vienna; lo dice la voce di Linards)."""
        if not decisive or self.wiki is None:
            return proofs
        roots = self._roots([c.get("description", "") for c in decisive]
                              + [x for c in decisive for x in c.get("readings") or []])
        # Solo le parole rare tra le righe del candidato: «provincia», «comune» stanno in metà delle righe e i 12 posti
        # finivano negli inizi di «Provincia (Italia)» e «Comune (Italia)» (24/10, Ducato di Milano: «Gemellaggi:
        # Linards» restava senza l'inizio di Linards, «comune dell'Alta Vienna», e Gravellona prendeva «non so»).
        row_words = [{x[:6] for x in _norm(p.text.split(" ⟶ voci:")[0]).split()} for p in proofs]
        frequency = {r: sum(1 for x in row_words if r in x) for r in roots if len(r) >= 4}
        rare = {r: f for r, f in frequency.items() if 0 < f <= max(2, len(proofs) // 4)}
        # Prima le righe con le parole più rare del controllo (24/10, Ducato: «paese», «comune» in 3 righe su 14
        # passavano davanti a «Gemellaggi: Linards»).
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
        """L'anello accettato entra nel grafo dei concetti (per la vista e per chi scrive): nodi, legame, frase."""
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
                g.triple(a.parent.name, relation[:80], a.name, "entity", "fatto", segment,
                         quote=a.tie_proof.text[:300])
                self._recorded.add((_norm(a.parent.name), _norm(relation[:80]), _norm(a.name)))
                g.db.commit()
        except Exception:  # noqa: BLE001 - la vista non deve fermare la ricerca
            pass

    # --- 2. percorso ---------------------------------------------------------------------------------------------

    def walk(self) -> None:
        """Uno o più piani (ODS: se il primo ragionamento non arriva a una risposta, se ne prova un altro): il secondo
        piano sa dove si è fermato il primo e cosa ha trovato."""
        if not self.prepare():
            self.reason = "catena non preparata"
            return
        best = None  # (anelli deboli, stato) della catena completa più solida
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
                # La catena più solida: meno anelli deboli, poi l'anello peggiore migliore (17/10, Congresso di
                # Vienna: «Elena», qualità -0,1, tenuta contro catene più promettenti).
                route = self._path(self.value["link"])
                solidity = (-weak_ones, min((a.quality for a in route), default=0.0))
                if best is None or solidity > best[0]:
                    best = (solidity, (self.plan, self.links, self.value, self.reason))
                if weak_ones == 0 or (self.o.get("frontier", True) and self._good_route()):
                    break
                # Catena completa ma con anelli non confermati: si prova un'altra catena e si tiene la più solida
                # (autoconsistenza alla Open Deep Search; 16/10, Blender: Hallstatt preso per descrizione).
                self.reason = f"catena completa ma con {weak_ones} anelli non confermati (valore {self.value['value']})"
            if self.r._reached_limit() or self.r.thinker.exhausted:
                break
        if best is not None:
            self.plan, self.links, self.value, self.reason = best[1]

    def _good_route(self) -> bool:
        """Con la frontiera i rami alternativi sono già stati esplorati: un altro piano serve solo se la strada della
        soluzione ha un anello sotto metà del peso dei controlli (17/10, Debian: secondo piano di 12 minuti per
        ritrovare lo stesso valore)."""
        route = self._path((self.value or {}).get("link"))
        return bool(route) and min(a.quality for a in route) >= 0.5

    def checks(self) -> dict:
        """L'esito dei controlli sulla soluzione e lo stato della risposta (docs/validation.md §4).
        «Confermata»: stato `verificata` o `sostenuta`, cioè un valore, nessun anello debole, il valore preso da una
        pagina letta, nessuna fonte che indichi un'altra strada o un altro valore, nessuna obiezione della revisione."""
        exams = getattr(self, "exams", None)
        if not self.value:
            discarded = getattr(self, "discarded", None)
            premise = validation.refuted_premise(exams) if discarded else None
            return {"confirmed": False, "status": "not found", "weak_links": 0, "value_source": "",
                    "reasons": [f"premessa smentita: «{premise['what']}» («{premise['quote']}»)"] if premise
                    else [self.reason] if discarded else [],
                    "disagreements": [], "exams": exams, "premise": premise}
        outcome = {"value": self.value["value"], "weak_links": self._weak_links(),
                 "value_source": validation.level(self.value["proof"]),
                 "disagreements": validation.disagreements_on_route(self.conflicts, self.value["link"]), "exams": exams}
        outcome["status"], outcome["reasons"] = validation.status(outcome)
        outcome["confirmed"] = outcome["status"] in ("verified", "supported")
        return outcome

    # Sotto questa cifra residua i controlli con il modello non partono: la scrittura della risposta viene prima.
    MIN_BUDGET_FOR_EXAMS = 0.06

    def final_review(self) -> None:
        """I controlli con il modello sulla soluzione trovata, prima di scrivere la risposta: seconda lettura del valore
        (G2), lo stesso dato in un'altra fonte (G5), revisione contraria della catena con un altro modello (G6). Non
        cambiano la risposta: ne decidono lo stato. Gli esiti restano in `self.esami`."""
        if not self.value or self.exams is not None:
            return
        self.exams = {k: {"outcome": "not done"} for k in ("second_reading", "corroboration", "review")}
        if self.r.thinker.ledger.available("review") < self.MIN_BUDGET_FOR_EXAMS:
            self.exams["reason"] = "budget di Claude quasi finito"
            return
        self.r._phase("Controllo la risposta: seconda lettura, altre fonti, revisione…")
        if (self.plan.get("answer") or {}).get("is_entity") or "proofs" not in self.value:
            # il valore è l'entità dell'ultimo passo: non c'è un dato da rileggere o da ritrovare altrove
            self.exams["second_reading"] = self.exams["corroboration"] = {"outcome": "not needed"}
        else:
            self.exams["second_reading"] = self._second_reading()
            self.exams["corroboration"] = self._corroboration()
        self.exams["review"] = self._adversarial_review()
        premise = validation.refuted_premise(self.exams)
        if premise:
            # La domanda dà per vero qualcosa che una riga nega: la strada trovata è «abbastanza simile», non la
            # risposta. Il valore resta da parte (riepilogo: valore_scartato) e la domanda è «non trovata».
            self.discarded, self.value = self.value, None
            self.reason = (f"premessa smentita: la domanda dà per vero «{premise['what']}», le fonti dicono "
                           f"«{premise['quote']}»")
            self.history.append({"step": -3, "relation": "premessa smentita", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [premise["what"], premise["quote"]])]})
            return
        name = (self.exams["review"] or {}).get("starting_point_name", "")
        missing = validation.missing_name(name, self.r.topic, self._read_texts())
        if missing:
            # La domanda chiama per nome la sua partenza e quel nome non sta in nessuna pagina letta: la strada è
            # partita da un'altra cosa (15/11: entità inventate, la catena ne prendeva una vicina).
            self.discarded, self.value = self.value, None
            self.reason = (f"premessa smentita: «{name}», nominato dalla domanda, non compare in nessuna delle pagine "
                           f"lette (mancano: {', '.join(missing)})")
            self.history.append({"step": -3, "relation": "nome della partenza assente", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [name] + missing)]})
            return
        objection = validation.rejected_route(self.exams, self._weak_links())
        if objection:
            weak_ones = self._weak_links()
            self.discarded, self.value = self.value, None
            self.reason = (f"catena respinta dalla revisione: non torna «{objection['clue']}» (le fonti dicono "
                           f"«{objection['quote']}»), la premessa della domanda non è provata e {weak_ones} anelli "
                           "non sono confermati")
            self.history.append({"step": -3, "relation": "catena respinta", "attempt": 0,
                                "candidates": [str(self.discarded["value"])], "kept": [],
                                "discarded": [(str(self.discarded["value"]), [objection["clue"], objection["quote"]])]})

    def _value_request(self, a: Link, proofs: list[Proof]) -> dict:
        answer = self.plan.get("answer") or {}
        return {"question": self.r.topic[:1000], "entity": a.name, "what": answer.get("what", ""),
                "checks": answer.get("checks", []), **self._value_constraints(answer),
                "proofs": self._numbered(proofs)}

    def _read_value(self, a: Link, proofs: list[Proof]) -> tuple[str, Proof | None]:
        """Una lettura del valore sulle `prove`, con gli stessi controlli del programma della prima: tipo e intervallo,
        valore scritto nella riga, riga che parla dell'entità."""
        outcome = self._decide("value", self._value_request(a, proofs), {"value": "", "proof": 0})
        value, proof = str(outcome.get("value") or ""), self._proof(proofs, outcome.get("proof"))
        if not value or proof is None or allowed_value(value, self.plan.get("answer") or {}) \
                or not validation.value_in_row(value, proof.text) \
                or not (proof.url in {v.url for v in a.articles.values()} or _name_key(a.name) in _norm(proof.text)):
            return "", None
        return value, proof

    def _second_reading(self) -> dict:
        """G2. Le stesse righe in ordine inverso, lette un'altra volta: il valore deve tornare uguale."""
        value, proof = self._read_value(self.value["link"], self.value["proofs"][::-1])
        if not value:
            return {"outcome": "not found again"}
        if validation.same_value(value, self.value["value"]):
            return {"outcome": "agree"}
        return {"outcome": "disagree", "value": value, "url": proof.url, "row": " ".join(proof.text.split())[:300]}

    def _corroboration(self) -> dict:
        """G5. Lo stesso dato cercato nelle altre fonti già disponibili sull'entità finale: le sue voci di Wikipedia in
        altre lingue e le altre pagine lette (non gli estratti delle ricerche). Mai la pagina del valore."""
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
        """Le righe su cui poggia la soluzione: legami, controlli provati e valore, dalla partenza all'entità finale."""
        if not self.value:
            return []
        rows: list[Proof] = []
        for a in self._path(self.value["link"]):
            rows += [p for p in [a.tie_proof, a.start] + [e["proof"] for e in a.outcomes] if p is not None]
        return rows + [self.value["proof"]]

    _INFOBOX_ROW_START = re.compile(r"[^.:]{1,40}:\s")

    def _rows_of_other_articles(self, route: list[Link]) -> list[Proof]:
        """Per ogni anello, le righe che parlano della sua relazione nelle voci dell'anello di prima, comprese le altre
        lingue e senza la pagina da cui il legame è stato preso: se un'altra voce dà un'altra entità (un altro luogo di
        nascita, un altro relatore), chi rivede la catena deve poterlo vedere. Il riscontro del valore (G5) guarda solo
        l'ultimo dato; qui si guardano i passaggi (12/11, domande di controllo, Python: luogo di nascita diverso tra
        due voci, risposta «verificata»)."""
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
        """G6. Un altro modello rilegge la domanda, la strada e le righe usate, cercando l'indizio che non torna."""
        from .. import configuration
        route = self._path(self.value["link"])
        root = self._root(self.value["link"])
        rows = self._route_rows() + self._rows_of_other_articles(route)
        rows = list({(p.url, p.text): p for p in rows}.values())[:90]
        data = {"question": self.r.topic[:1500],
                "route": " → ".join([root.name] + [f"[{a.relation}] {a.name}" for a in route]),
                "answer": f"{self.value['value']} ({(self.plan.get('answer') or {}).get('what', '')} di "
                            f"{self.value['link'].name})",
                "rows": "\n".join(f"[{i}] {p.text[:700]}" for i, p in enumerate(rows, start=1))}
        model = str(self.o.get("review_model") or configuration.load()["review_model"])

        def one(_n: int) -> dict:
            outcome, parent = self.r.thinker.decide("review", data, lambda: {}, model=model)
            return validation.examine_review(outcome, rows) if parent == "claude" else {"outcome": "not done"}
        # Due revisioni indipendenti, vale la più severa: una sola oscilla tra «regge» e «non provato».
        readings = max(1, int(self.o.get("review_readings", 2)))
        with ThreadPoolExecutor(max_workers=readings) as pool:
            unit = validation.merge_reviews(list(pool.map(one, range(readings))))
        self.r.cost = max(self.r.cost, self.r.thinker.ledger.spent)
        return {**unit, "model": model} if unit.get("outcome") == "done" else unit

    def _read_texts(self) -> list[str]:
        """Tutto quello che la catena ha letto per questa domanda: pezzi di pagina salvati, voci di Wikipedia degli
        anelli (titolo e righe), estratti dei risultati delle ricerche."""
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
        """Un passo intermedio verso una cosa senza nome proprio né pagina sua (statua, simulacro, dipinto, oggetto):
        piano con quel passo unito al successivo, che parte dall'entità di prima e ne eredita controlli, parole e
        descrizioni (25/10, Cappella Sistina: «festa → simulacro che la raffigura → comune» si perde tra statue e
        dipinti; «festa → comune in cui si trova un suo simulacro» trova Novara di Sicilia nella galleria della voce
        dell'Assunzione. Nel prompt la stessa regola era seguita 1 volta su 3)."""
        steps = list(plan.get("steps") or [])
        for i, step in enumerate(steps[:-1]):
            text = _norm(f"{step.get('kind', '')} {step.get('relation', '')}")
            if not NAMELESS_OBJECTS.search(text):
                continue
            after = steps[i + 1]
            merged = {**after,
                     "relation": f"{after['relation']} (attraverso: {step['relation']})"[:150],
                     "words": list(dict.fromkeys(list(after.get("words") or []) + list(step.get("words") or [])))[:14],
                     "descriptions": list(after.get("descriptions") or []) + list(step.get("descriptions") or []),
                     "checks": list(after.get("checks") or []) + [
                         {**c, "description": f"legato a {step.get('kind') or step['relation']}: "
                                             f"{c.get('description', '')}"[:200]} for c in step.get("checks") or []]}
            return {**plan, "steps": steps[:i] + [merged] + steps[i + 2:],
                    "reason": f"salto di «{step['relation']}» (cosa senza nome proprio)"}
        return None

    def _alternative_plan(self) -> dict | None:
        """Un secondo piano, diverso dal primo, da percorrere insieme a lui nella stessa frontiera."""
        plan = self._decide("chain", {"question": self.r.topic[:1000], "today": self.r.today,
                                        "interpretation": self.interpretation,
                                        "alternative_to": {"starting_point": (self.plan.get("starting_point") or {}).get("entity"),
                                                          "steps": [p.get("relation") for p in self.plan["steps"]]}},
                             {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
        if not plan.get("steps") or not (plan.get("starting_point") or {}).get("entity") \
                or self._signature(plan) == self._signature(self.plan):
            return None
        self.r.publish({"kind": "chain_search", "interpretation": self.interpretation, "plan": plan})
        self.history.append({"step": 0, "relation": "piano alternativo", "attempt": 0, "candidates": [],
                            "kept": [], "discarded": [], "alternative_plan": plan})
        return plan

    def _replan(self) -> bool:
        hits = [{"relation": step.get("relation"), "entity": [a.name + (" (debole)" if a.weak else "")
                                                                     for a in beam]}
                   for step, beam in zip(self.plan.get("steps", []), self.links, strict=False)]
        previous = {"starting_point": (self.plan.get("starting_point") or {}).get("entity"),
                      "steps": [p.get("relation") for p in self.plan.get("steps", [])],
                      "where_it_stopped": self.reason, "found_links": hits}
        self.r._phase("La catena non è arrivata alla risposta: Claude prepara una catena diversa…")
        plan = self._decide("chain", {"question": self.r.topic[:1000], "interpretation": self.interpretation,
                                        "previous_chain": previous},
                             {"starting_point": {}, "steps": [], "answer": {}, "reason": ""})
        if not plan.get("steps") or not (plan.get("starting_point") or {}).get("entity"):
            return False
        signature = lambda x: (_norm((x.get("starting_point") or {}).get("entity")),  # noqa: E731
                           tuple(_norm(p.get("relation")) for p in x.get("steps", [])))
        if signature(plan) == signature(self.plan):  # 16/10, Linux kernel: la stessa catena ripercorsa, 9 minuti persi
            self.r._phase("Il nuovo piano è uguale al primo: non lo ripercorro.")
            return False
        self.history.append({"step": 0, "relation": "nuovo piano", "attempt": 0, "candidates": [],
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
                self.reason = f"catena interrotta al passo {number} ({step.get('relation')})"
                return
            beam = new_items[:width]
            self.links.append(beam)
            if self.r._reached_limit():
                self.reason = self.r._reached_limit()
                if number < len(self.plan["steps"]):
                    return
        # Dietro al fascio, gli altri candidati non smentiti dell'ultimo passo, solo su Wikipedia (16/10, Wikipedia:
        # Roland Unger, giusto, era tra i candidati senza "sì" e fuori dal fascio).
        others = [a for a in self._alive if a not in beam][:4]
        self._value(beam, others)

    # --- frontiera globale (DeepSearch, ICLR 2026) -----------------------------------------------------------------

    @staticmethod
    def _path(a: Link) -> list[Link]:
        """Gli anelli dalla partenza (esclusa) ad `a`."""
        chain = []
        while a is not None and a.parent is not None:
            chain.append(a)
            a = a.parent
        return chain[::-1]

    def _depth(self, a: Link) -> int:
        """Il passo del piano a cui è arrivato il nodo: quelli della sua radice (partenza 0, ancora passo_base) più
        gli anelli percorsi da lì."""
        return self._root(a).base_step + len(self._path(a))

    def _priority(self, a: Link, steps: int, action: str) -> float:
        """Quanto conviene la prossima azione su un nodo: qualità della strada che lo raggiunge (metà la peggiore,
        metà la media: una catena vale quanto il suo anello più debole), un piccolo premio per la profondità (radice
        di profondità/passi, per finire le catene) e una penalità per le azioni care (il web dopo Wikipedia)."""
        # Un anello senza il legame provato con quello di prima vale molto meno (18/10, Arduino: Damian Kulash,
        # trovato solo per descrizione «nato a metà anni '70», preso come coniuge della fondatrice).
        route_quality = [x.quality - (0.15 if x.weak else 0.0)
                          - (0.0 if self._linked(x) else 0.05 if self._co_occurs(x) else 0.25)
                          for x in self._path(a)]
        # Una radice vale 0,7: finché non si allarga non si sa cosa darà (18/10, Wikipedia: con 1,0 le ricerche sul web
        # dalla partenza passavano sempre davanti al valore di Roland Unger, giusto, fino al limite delle pagine).
        base = 0.5 * min(route_quality) + 0.5 * sum(route_quality) / len(route_quality) if route_quality else 0.7
        # Il valore su Wikipedia di un nodo finale costa una chiamata e fa anche da controllo (tipo e intervallo):
        # passa davanti al web (17/10, Promessi sposi: Ponchielli, giusto, tra i candidati; la frontiera ha allargato
        # sul web fino al limite delle 30 pagine senza cercarne il valore).
        # I nodi finali sono a un'azione dalla risposta: il loro valore, anche sul web (2 pagine), costa meno di un
        # passo sul web (6 pagine e la verifica).
        cost = {"wiki": 0.0, "web": 0.35, "value": -0.45, "web_value": -0.1}[action]
        # Un nodo che su Wikipedia non ha dato nessun candidato (di solito: non ha una voce) ha nel web l'unica
        # strada, non un ripiego: con una buona qualità la penalità scende (24/10, Arduino: Keyboardio mai allargato
        # sul web in 3 corse su 4; nelle corse passate così anche «Simone da Orsenigo», Duomo, e «Assunzione di
        # Maria Vergine», Cappella Sistina. Soglia 0,5 e penalità 0,1 scelte sulle 95 corse con la frontiera:
        # 6 entità giuste su 10 anticipate contro 7 allargamenti inutili nelle corse giuste).
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
        """Partenza e ancore di un piano. Ancore (Think-on-Graph, HierSearch): altre entità che la domanda nomina con
        certezza e da cui, con una loro relazione, si arriva al risultato di un passo; più sono vicine alla risposta,
        prima si esplorano (17/10, Raspberry Pi: la Borsa di Londra, nominata, era a due passi dalla risposta; la
        catena partita dal Raspberry Pi ha finito le pagine sui distributori)."""
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
        """Ricerca a frontiera globale: invece di andare passo per passo con un fascio fisso di 3 entità, il programma
        tiene una lista unica di tutte le azioni aperte, di tutti i passi (allargare un nodo su Wikipedia, sul web,
        cercarne il valore) e fa ogni volta la migliore. Se un ramo non porta a niente si torna da soli al candidato
        di riserva del passo giusto, senza rifare il piano. Un nodo i cui figli falliscono tutti prende una penalità
        (fallimenti); chi ha già portato a un valore non viene penalizzato (regola asimmetrica). Con più piani
        (autoconsistenza alla Open Deep Search, ma in parallelo) le strade di tutti i piani stanno nella stessa
        frontiera: va avanti quella che funziona meglio (17/10, Lepanto: un piano con «repubblica sorella di» al posto
        di «sostituita da» non trovava la Repubblica Ligure)."""
        plans = plans or [self.plan]
        actions: list[tuple[Link, str]] = [(r, "wiki") for plan in plans for r in self._plan_roots(plan)]
        levels: dict[tuple[int, int], list[Link]] = {}  # (piano, profondità) → anelli raggiunti
        seen: dict[tuple[int, int], set[str]] = {}  # nomi già raggiunti (un'entità entra una volta sola)
        solutions: list[tuple[float, dict, dict]] = []
        already_expanded: dict[tuple, tuple[str, list[Link]]] = {}  # firma → (passo, figli trovati)
        web_values: dict[int, int] = {}  # genitore → valori cercati sul web per i suoi figli
        already_marks: dict[tuple, set[str]] = {}  # firma → passi (relazione + controlli) già allargati
        done, maximum = 0, int(self.o.get("frontier_actions", 30))
        priority_of = lambda x: self._priority(x[0], len(self._plan_of(x[0])["steps"]), x[1])  # noqa: E731
        while actions and done < maximum:
            if self.r._reached_limit() or self.r.thinker.exhausted:
                self.reason = self.reason or self.r._reached_limit() or "budget di Claude esaurito"
                break
            actions.sort(key=lambda x: -priority_of(x))
            node, action = actions.pop(0)
            priority = priority_of((node, action))
            # Trovato un valore, si continua solo con azioni promettenti quanto la soluzione (autoconsistenza): una
            # seconda strada che porta allo stesso valore lo conferma, una migliore lo sostituisce. La soluzione vale
            # quanto la sua strada, senza il premio della ricerca del valore (25/10, Arduino: «Chris Martin», coniuge
            # trovato per descrizione, valeva 0,82 col premio di 0,45 e Keyboardio, 0,58, non veniva mai allargato).
            if solutions and (priority <= solutions[0][0] or done >= maximum - 2):
                break
            # Il tetto conta solo le espansioni (le ricerche costose): cercare il valore di un nodo finale costa una
            # chiamata e non consuma il tetto (22/10, Linux kernel: le 30 azioni finite in ricerche di valore prima di
            # arrivare ad Alexandre Oliva, candidato giusto ma debole). Il limite di tempo e di budget resta.
            if action in ("wiki", "web"):
                done += 1
            plan = self._plan_of(node)
            steps, n = plan["steps"], len(plan["steps"])
            depth = self._depth(node)
            self.actions.append(f"{action} {node.name} ({priority:.2f})" + (
                f" [piano {plans.index(plan) + 1}]" if len(plans) > 1 else ""))
            if action in ("value", "web_value"):
                self.value, self.plan = None, plan
                if self._entity_value(node, (1,) if action == "value" else (2, 3)):
                    solutions.append((self._priority(node, n, "wiki"), self.value, plan))
                    solutions.sort(key=lambda x: -x[0])
                    self.history.append({"step": n + 1, "relation": "value", "attempt": 0, "candidates": [node.name],
                                        "kept": [(self.value["value"], round(priority, 2), "soluzione")],
                                        "discarded": []})
                    if all(not x.weak for x in self._path(node)):
                        break  # strada tutta confermata: basta così
                elif action == "value" and web_values.get(id(node.parent), 0) < 3:
                    # Al massimo 3 valori sul web per genitore (25/10, Wikipedia: «consiglio direttivo di» dava ~10
                    # membri, ognuno con 2 pagine di valore sul web, fino al limite dei 35 minuti).
                    web_values[id(node.parent)] = web_values.get(id(node.parent), 0) + 1
                    actions.append((node, "web_value"))
                else:
                    self._failed(node)
                continue
            step = steps[depth]
            if node.parent is None and node.base_step:
                # Da un'ancora si segue la sua relazione, con i controlli del passo più quello della relazione saltata
                # (peso 3): l'ancora non deve far perdere l'identità della catena (18/10, Concilio di Trento: ancora
                # «Alta Marna», Chaumont passava tutti i controlli senza essere il luogo di nascita del marchese
                # d'Elbeuf).
                first = steps[depth - 1].get("checks") or [] if depth >= 1 else []
                who = "; ".join(str(c.get("description") or "") for c in first) or \
                    str((plan.get("starting_point") or {}).get("entity") or "")
                skipped = {"description": f"è «{step['relation']}» di: {who}"[:200], "weight": 3,
                           "readings": [f"{step['relation']} (della persona o cosa del passo precedente)"]}
                step = {**step, "relation": node.relation,
                         "checks": list(step.get("checks") or []) + [skipped]}
            # La stessa entità, allo stesso passo e con la stessa relazione, si allarga una volta sola anche se due
            # piani (o due varianti del nome) ci arrivano: le pagine web non si spendono due volte (23/10, Arduino:
            # «Keyboard.io» e «Keyboardio» allargati nei due piani, tetto di 30 pagine finito prima del coniuge).
            if action == "wiki" and self.wiki is not None:
                self._articles(node)  # la voce serve comunque all'allargamento; qui dà la chiave dell'entità
            signature = self._action_signature(action, node, depth, step["relation"])
            step_mark = json.dumps([step.get("relation"), step.get("checks")], sort_keys=True, default=str)
            key = (id(plan), depth + 1)
            seen.setdefault(key, set())
            children = []
            reuse = False
            case = self._already_done(signature, action, step_mark, already_expanded, already_marks)
            if case in ("salta", "riusa"):
                done -= 1
                already_mark, already_children = already_expanded[signature]
                if case == "salta" or node in [x.parent for x in already_children]:
                    self.actions[-1] += " (già fatta)"
                    continue
                # Stesso passo già allargato da un altro piano: i suoi figli (già verificati) valgono anche qui, sotto
                # questo nodo, senza rifare ricerche (25/10: un piano con lo stesso primo passo restava fermo).
                children = [replace(a, parent=node, outcomes=list(a.outcomes)) for a in already_children]
                self.actions[-1] += " (figli riusati)"
                reuse = True
            first_history = len(self.history)
            for attempt in (() if reuse else (1, 2) if action == "wiki" else (3, 4)):
                if attempt in (1, 2) and self.wiki is None:
                    continue
                children += self._attempt(attempt, depth + 1, step, [node], seen[key], all_items=True)
                if any(not a.weak and self._complete(a) for a in children) or self.r._reached_limit():
                    break
            if not reuse:
                # Stessa relazione con controlli diversi (un altro piano): su Wikipedia si rifà, costa solo la verifica
                # (29/10, Galileo: Biancani scartato con i controlli del piano 1, tenuto con quelli del piano 2, che
                # però non partiva mai: «già fatta»).
                already_expanded.setdefault(signature, (step_mark, list(children)))
                already_marks.setdefault(signature, set()).add(step_mark)
            if action == "wiki" and not reuse:
                # Vuoto: niente candidati seguendo la relazione e niente tenuti dalla ricerca per descrizione, che dà
                # nomi qualsiasi (24/10, Arduino: per «fondatori di Keyboardio» Art Lazere, Julius Chein, Steve Jobs,
                # tutti scartati, e il web di Keyboardio non è mai partito. Sulle corse passate: +2 nodi giusti
                # anticipati, +4 allargamenti inutili nelle corse giuste; «nessun tenuto» ovunque: +5 e +26).
                node.empty_wiki = not any(x.get("candidates") if x.get("attempt") == 1 else x.get("kept")
                                          for x in self.history[first_history:])
                actions.append((node, "web"))  # il web resta come riserva, con la sua penalità
            already = {k for x in levels.get(key, []) for k in (self._canonical_key(x), _entity_key(x.name))}
            new_items = []
            returned = [a for a in children if validation.repeats_ancestor(a, node)]
            if returned:  # una catena non torna su un'entità già attraversata (relazione letta al contrario)
                self.history.append({"step": depth + 1, "relation": "già nella strada", "attempt": 0,
                                    "candidates": [a.name for a in returned], "kept": [],
                                    "discarded": [(a.name, ["entità già attraversata dalla catena"]) for a in returned]})
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
            # Due strade allo stesso valore: si tiene la migliore (le altre lo confermano).
            self.value = solutions[0][1]
            equal = [x for x in solutions if _norm(x[1]["value"]) == _norm(self.value["value"])]
            self.reason = f"catena completa: {self.value['link'].name} → {self.value['value']}" + (
                f" (confermato da {len(equal)} strade)" if len(equal) > 1 else "")
            for a in self._path(self.value["link"]):
                self._record(a, a.relation or steps[self._depth(a) - 1]["relation"])
            last = self.links[-1] if self.links else []
            if self.value["link"] in last:  # il fascio finale comincia dall'anello della soluzione
                last.remove(self.value["link"])
                last.insert(0, self.value["link"])
        elif not self.reason:
            reached = max(mine) if mine else 0
            self.reason = (f"catena interrotta al passo {reached + 1} ({steps[reached].get('relation')})"
                           if reached < n else "valore richiesto non trovato")

    @staticmethod
    def _already_done(signature: tuple, action: str, mark: str, already_expanded: dict, already_marks: dict) -> str:
        """Un allargamento già fatto da un altro piano: «nuova» (mai fatto), «riusa» (stesso passo: i suoi figli),
        «rifai» (Wikipedia, stessa relazione con controlli diversi: costa solo la verifica; 29/10, Galileo),
        «salta» (web con controlli diversi: le pagine non si spendono due volte)."""
        if signature not in already_expanded:
            return "new"
        if already_expanded[signature][0] == mark:
            return "riusa"
        if action == "wiki" and mark not in already_marks.get(signature, set()):
            return "rifai"
        return "salta"

    def _final_tiebreak(self, candidates: list[Link], priority, margin: float = 0.05, maximum: int = 5) -> int:
        """Candidati dell'ultimo passo alla pari (priorità entro `margine` dalla migliore): Haiku li confronta tra loro
        con la domanda intera e alcune righe delle loro voci (inizio, righe dei controlli, righe con le parole della
        domanda) e ne sceglie uno o nessuno; lo scelto passa davanti. I controlli dicono sì/no a uno alla volta, il
        confronto vede gli indizi di sfumatura (24/10, Monte Bianco: Alassio e Laigueglia gemellate entrambe con
        La Thuile, 0,71 tutte e due; solo Laigueglia è «un piccolo borgo di mare», la domanda dice «borgo»; nelle
        corse passate 17 soluzioni su 84 venivano da un pareggio, in 4 sbagliate la giusta era tra i pari).
        Restituisce l'indice scelto (da 0) o -1."""
        points = {id(a): priority(a) for a in candidates}
        best = max(points.values())
        # Tra candidati legati con una riga e senza nessun «no» lo scarto può essere più largo: la verifica sceglie
        # una riga per controllo e può dare «in parte» al giusto (25/10, Monte Bianco: Laigueglia 0,7 con la riga
        # «piccolo borgo di mare» senza Savona, Alassio 0,9; 2 corse su 5 davano Alassio).
        clean = [a for a in candidates if self._linked(a) and not self._no(a)]
        first_one = max(candidates, key=lambda a: points[id(a)])
        threshold = first_one.quality - 0.4 if first_one in clean else 2.0  # qualità: i fratelli hanno la stessa strada
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
        self.history.append({"step": -2, "relation": "spareggio finale", "attempt": 0,
                            "candidates": [a.name for a in tied],
                            "kept": [(tied[choice - 1].name, choice, "scelto")] if 0 < choice <= len(tied) else [],
                            "discarded": [], "reason": str(outcome.get("reason") or "")[:300]})
        if not 0 < choice <= len(tied):
            return -1
        chosen = tied[choice - 1]  # passa davanti al migliore, anche se partiva più indietro (spareggio largo)
        chosen.preferred = best - points[id(chosen)] + margin
        return choice - 1

    @staticmethod
    def _failed(a: Link) -> None:
        """Penalità a un nodo senza esito e, a metà, alla sua origine (si propaga poco: altri figli possono
        ancora funzionare)."""
        a.failures += 1.0
        if a.parent is not None:
            a.parent.failures += 0.25

    def _step_checks(self, step: dict, beam: list[Link]) -> list[dict]:
        """I controlli di un passo con i loro pesi: quelli del piano, il tipo (peso 1) e il legame con l'entità di
        prima (peso 2, controllo per tutti: 16/10, Monte Bianco: Alassio, ligure e savonese, preferito a Laigueglia
        senza che nessuna riga lo dicesse gemellato con La Thuile)."""
        checks = [{**c, "weight": c.get("weight") or 2} for c in step.get("checks", [])]
        # Un controllo del piano che nomina l'entità di prima ripete il legame («indicato come allievo di Galileo»):
        # contava due volte (23/10, paper §9.1: Guiducci e Viviani «forti» senza essere gesuiti bolognesi). Diventa
        # una lettura del legame.
        keys = {p for a in beam for p in _norm(a.name).split() if len(p) >= 5 and p not in STOP_WORDS}
        repeated = [c for c in checks if any(re.search(rf"\b{re.escape(k)}\b", _norm(c.get("description", "")))
                                                for k in keys)]
        checks = [c for c in checks if c not in repeated]
        if str(step.get("kind") or "").strip():  # il tipo è un controllo (16/10: BeBox, un computer, preso per BeOS)
            checks.append({"description": f"è di questo tipo: {step['kind']}", "weight": 1,
                              "readings": [f"{step['kind']} (anche come genere più ampio o più stretto)"]})
        checks.append({"description": f"è in relazione «{step['relation']}» con {' o '.join(a.name for a in beam)}",
                          "weight": 2, "readings": ["in senso largo: allievo, discepolo, studiò con, collaboratore, opera "
                                                 "di, parte di, come richiede la relazione"]
                          + [str(c.get("description", "")) for c in repeated]})
        return checks

    def _collect(self, attempt: int, number: int, step: dict,
                  beam: list[Link]) -> tuple[dict[str, Link], dict[str, Link]]:
        """I candidati di un tentativo: (trovati seguendo la relazione, trovati per descrizione)."""
        candidates: dict[str, Link] = {}
        per_description: dict[str, Link] = {}
        if attempt in (1, 2):  # Wikipedia: un thread per entità del fascio (le chiamate al modello sono lente)
            partial = [{} for _ in beam]
            jobs = [(self._wiki_candidates, (number, step, o, attempt, partial[i]))
                      for i, o in enumerate(beam)]
            if attempt == 2:  # ancora Wikipedia: voci che collegano all'entità, ricerca per descrizione
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
        """Un tentativo: candidati raccolti, verificati con i controlli del passo; restano i non smentiti."""
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
                            "kept": [(a.name, round(a.quality, 2), "debole" if a.weak else "forte")
                                       for a in good],
                            "discarded": [(a.name, [e["check"] for e in a.outcomes if e["outcome"] == "no"])
                                         for a in all_seen if a not in good],
                            "verifications": [self._verification_in_short(a) for a in all_seen]})
        self.r.publish({"kind": "chain_search_step", **self.history[-1]})
        return good

    def _action_signature(self, action: str, node: Link, depth: int, relation: str) -> tuple:
        """Quando due allargamenti sono lo stesso: stessa entità, stesso passo e (su Wikipedia) stessa relazione. Sul
        web la relazione non conta: due piani che scrivono la relazione in modo diverso leggono le stesse pagine
        (24/10: sulle 95 corse con la frontiera 31 allargamenti web doppi, il secondo non ha mai dato un anello di
        una soluzione giusta; 12 corse sbagliate su 32 finiscono per il tetto delle pagine)."""
        words = frozenset() if action == "web" else frozenset(
            p[:5] for p in _norm(relation).split() if len(p) >= 4)
        return (action, self._canonical_key(node), depth, words)

    @staticmethod
    def _verification_in_short(a: Link) -> dict:
        """Per la storia (23/10, Arduino: senza le righe citate non si capiva perché Keyboardio valeva 0,3): voce,
        riga del legame e, per ogni controllo, esito detto dal modello, esito tenuto dal programma e riga citata."""
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
        """La stessa entità con nomi diversi: se la voce di Wikipedia è nota, la chiave è la voce (titolo inglese se
        c'è: «Adafruit» e «Adafruit Industries» portano alla stessa voce, 23/10); altrimenti le varianti del nome."""
        article = next(iter(a.articles.values()), None)
        if article is not None:
            english = article.title if article.language == "en" else article.languages.get("en")
            return "voce:" + _entity_key(english or article.title)
        return _entity_key(a.name)

    def _walk_step(self, number: int, step: dict, beam: list[Link]) -> list[Link]:
        """Quattro tentativi, dal più economico; ci si ferma al primo che dà candidati forti.
        1. Wikipedia come grafo: righe della voce dell'entità scelte per le parole della relazione (16/10).
        2. Ancora Wikipedia: voci che collegano all'entità e ricerca per descrizione (i controlli come parole).
        3. Web in avanti (ricerche per la relazione, pagine lette).
        4. Web con ricerche diverse e PER DESCRIZIONE (i controlli come chiave di ricerca), più i vicini nel grafo;
           per i candidati trovati per descrizione il legame con l'entità di prima diventa un controllo.
           (15/10: Biancani non compare nelle pagine sugli allievi di Galileo, ma sì tra i gesuiti bolognesi.)
        Un candidato debole resta di riserva."""
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
            # Con Wikipedia si fanno sempre i due tentativi economici prima di scegliere, salvo un candidato che passa
            # tutti i controlli (16/10, Emacs: Bobrow "forte" al primo tentativo, Tomlinson arrivava solo dopo).
            if found_strong and (complete or attempt >= 2 or self.wiki is None):
                picked = found_strong + [a for a in self._alive if a.weak and a not in found_strong]
                for a in picked:
                    self._record(a, step["relation"])
                return picked
            reserve += [a for a in good if a.weak]  # i deboli di tutti i tentativi, poi i migliori
            if self.r._reached_limit() or self.r.thinker.exhausted:
                break
        reserve = sorted(reserve, key=self._order)[
            :int(self.o.get("chain_width", 3))]
        for a in reserve:
            self._record(a, step["relation"])
        return reserve

    def _candidates_in_forward(self, number: int, step: dict, origin: Link, attempt: int,
                             candidates: dict[str, Link]) -> None:
        self.r._phase(f"Passo {number}: {step['relation']} {origin.name}…")
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
        """Gli indizi della domanda sul risultato del passo (i controlli, prima i più pesanti) per le ricerche sul web:
        «aziende ispirate da Arduino» porta a centinaia di risultati, «Arduino programmable keyboard company» al
        risultato (25/10, Arduino: le prime ricerche web non nominavano mai le tastiere)."""
        checks = sorted(step.get("checks") or [], key=lambda c: -_weight(c))
        return [" / ".join([str(c.get("description") or "")] + [str(x) for x in (c.get("readings") or [])[:1]])[:160]
                for c in checks[:3] if c.get("description")]

    def _candidates_per_description(self, number: int, step: dict, beam: list[Link],
                                   candidates: dict[str, Link]) -> None:
        description = "; ".join(c.get("description", "") for c in step.get("checks", []))
        if not description:
            return
        self.r._phase(f"Passo {number}: cerco per descrizione ({description[:80]})…")
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
        """Se la relazione cercata non ha dato candidati, le entità già collegate all'entità di prima nel grafo, con
        qualunque legame, diventano candidati (il legame cercato diventa un controllo). 15/10, Arduino: Jesse Vincent
        era nel grafo come co-fondatore di Keyboardio, nessuna pagina lo diceva "coniuge di Kaia Dekker"."""
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
        """Il candidato sta davvero nella riga che cita: il nome (o il cognome) nel testo, o una voce collegata della
        riga che è lui (GENRE: il modello sceglie tra nomi che ci sono, non li inventa)."""
        text = _plan(proof.text)
        key = _plan(_name_key(name))
        if _plan(name) in text or (len(key) >= 4 and re.search(rf"\b{re.escape(key)}\b", text)):
            return True
        return any(matches(name, t) for t in proof.hyperlinks)

    @staticmethod
    def _whole_name(name: str, proofs: list[Proof]) -> str:
        """Un nome di una parola sola («Kaia», «jesse») diventa la forma intera che le prove scrivono più spesso
        («Kaia Dekker»): con il solo nome di battesimo il passo dopo cerca omonimi (25/10, Arduino: «coniuge di Kaia»
        → Andy Bell, Idha). Solo forme Nome Cognome con iniziali maiuscole, mai un'altra persona con quel cognome."""
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
        """Il passo cerca persone (dal tipo o dalla relazione del piano)."""
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
                    log.info("catena: candidato «%s» scartato: non sta nella riga %s", name, c.get("proof"))
                    continue
                candidates[key] = Link(name, parent=origin, tie_proof=proof,
                                                 context=f"{origin.name}: {origin.context}"[:150],
                                                 title=self._title_from_proof(name, proof))

    @staticmethod
    def _title_from_proof(name: str, proof: Proof | None) -> tuple[str, str] | None:
        """La voce di Wikipedia del candidato, se la riga che lo indica la collega (niente omonimi possibili)."""
        if proof is None or not proof.hyperlinks or not proof.language:
            return None
        key = _name_key(name)
        # Prima il titolo che corrisponde meglio (esatto, variante, parentesi, base), poi il cognome: «La Thuile
        # (Italia)» non deve prendere un altro collegamento della stessa riga.
        # «base» (nome di una parola dentro un titolo lungo) non basta qui: 25/10, Cappella Sistina: «Napoli» →
        # «Regno di Napoli»; il cognome passa sotto, con al massimo una parola in più (Enrico Fermi).
        degrees = [(DEGREES.get(matches(name, t), 0.0), -i, t) for i, t in enumerate(proof.hyperlinks)]
        best = max(degrees, default=(0.0, 0, ""))
        if best[0] > DEGREES["base"]:
            return (proof.language, best[2])
        # Il cognome basta solo per un nome di una parola («Fermi»): con nome e cognome un altro collegamento con lo
        # stesso cognome è un'altra persona (24/10: Paul McCartney → Linda McCartney, Michael Dell → Susan Dell,
        # Guiniforte Solari → Giovanni Solari; 57 voci sbagliate su 523 verifiche registrate, in gran parte da qui).
        if len([p for p in _norm(re.sub(r"\([^)]*\)", " ", name)).split() if len(p) >= 4]) != 1:
            return None
        for title in proof.hyperlinks:
            words = _norm(re.sub(r"\([^)]*\)", " ", title)).split()
            # al massimo una parola in più (Enrico Fermi, Adafruit Industries; non «Microsoft ergonomic keyboards»)
            if len(key) >= 4 and key in words and len(words) <= 2:
                return (proof.language, title)
        return None

    def _segments_on(self, name: str) -> list[dict]:
        """Pezzi già salvati in cui compare il nome (letti in passi precedenti)."""
        key = _name_key(name)
        return [s for s in self._after_segments(0) if key in s["text"].lower()][:12]

    def _verification(self, candidates: list[Link], checks: list[dict], all_items: bool = False) -> list[Link]:
        """Controlli su tutti i candidati; un "no" con prova scarta. Ai migliori con dei "non so" si cerca la scheda e
        si rifà la verifica una volta."""
        if not candidates:
            return []
        if not checks:
            return candidates
        self._wiki_starts(candidates)
        self._outcomes(candidates, checks)
        alive = [a for a in candidates if not self._refuted(a)]
        doubts = [a for a in sorted(alive, key=lambda a: -a.quality)
                 if any(e["outcome"] in ("unknown", "partly") for e in a.outcomes)]
        # Con Wikipedia la scheda costa una richiesta: si approfondiscono tutti i dubbi (16/10, Galileo: Zucchi
        # "rettore del Collegio Romano" solo nel corpo della voce, fuori dai primi tre per punti).
        from_deepen = doubts[:int(self.o.get("profiles_per_step", 8 if self.wiki is not None else 3))]
        if from_deepen and not self.r._reached_limit():
            from_web = 0
            for a in from_deepen:
                # La scheda è prima di tutto la voce intera di Wikipedia (riquadro e testo), poi il web (al massimo 3).
                if not a.searched_articles and self._articles(a, other_ones=0):
                    continue
                if a.articles or from_web >= 3:
                    continue
                from_web += 1
                self.r._phase(f"Cerco la scheda di {a.name}…")
                words = " ".join(_norm(a.parent.name).split()[:3]) if a.parent else ""
                self._search_and_read([f"{a.name} {words}".strip(), self._check_research(a)],
                                    int(self.o.get("profile_pages", 2)))
            self._outcomes(from_deepen, checks)
            alive = [a for a in candidates if not self._refuted(a)]
        # Dopo la scheda un "non so" pesa contro: passa chi ha più di metà del peso dei controlli provato (15/10:
        # Viviani, né gesuita né bolognese, passava perché nessuna pagina lo negava). Se nessuno passa, il migliore
        # resta, segnato debole. A parità di qualità decide il nome, così due corse uguali scelgono uguale.
        ordered = sorted(alive, key=lambda a: (-a.quality, not self._co_occurs(a), -a.points, a.name))
        # Forte solo con il legame provato (16/10, Blender: Traun, Wels e Gmunden "forti" perché città dell'Alta
        # Austria sul Traun, senza nessuna riga che ci facesse nascere lo sviluppatore).
        with_tie = any(str(c.get("description", "")).startswith("è in relazione «") for c in checks)
        linked = lambda a: not with_tie or any(  # noqa: E731
            e["outcome"] == "yes" and e["proof"] and str(e["check"]).startswith("è in relazione") for e in a.outcomes)
        # Un legame provato solo dall'estratto di una ricerca (pagina mai letta) non fa un anello forte.
        strong = [a for a in ordered if a.quality > 0.5 and linked(a) and not self._decisive_unknown(a)
                 and self._identified(a) and not validation.tie_only_from_snippets(a)]
        relation = next((str(c.get("description", "")) for c in checks
                          if str(c.get("description", "")).startswith("è in relazione «")), "")
        for article in validation.disagreements([a for a in candidates if a not in alive], relation):
            if article not in self.conflicts:
                self.conflicts.append(article)
        if strong:
            # Dietro ai forti restano i non smentiti, segnati deboli: se dal forte non si arriva al valore, la catena
            # prova loro (16/10, Linux kernel: Islene Calciolari Garcia senza data di nascita, Alexandre Oliva dietro).
            others = [a for a in ordered if a not in strong]
            # Con la frontiera restano tutti i non smentiti (16/10, Monte Bianco: La Thuile, giusta, fuori dai tre
            # tenuti a pari punti); senza, solo quanti ne servono per il fascio.
            others = others[:int(self.o.get("max_children", 6))] if all_items else \
                others[:max(0, int(self.o.get("chain_width", 3)) - 1)]
            for a in others:
                a.weak = True
            return strong + others
        # Senza candidati forti restano fino a `larghezza` deboli non smentiti, anche senza "sì": il passo dopo li
        # verifica comunque (15/10: Joinville, risposta giusta, scartato perché le pagine non dicevano "Alta Marna").
        weak_ones = ordered[:int(self.o.get("max_children", 6) if all_items else self.o.get("chain_width", 3))]
        for a in weak_ones:
            a.weak = True
        return weak_ones

    @staticmethod
    def _check_research(a: Link) -> str:
        """Seconda ricerca della scheda web: il nome con il controllo più pesante non ancora provato (prima lettura o
        descrizione, parole piene), non «biografia» (24/10, Arduino: «Seeed Studio biografia», «Cytron Technologies
        biografia» per aziende candidate a «produce tastiere programmabili»)."""
        pending = sorted((e for e in a.outcomes if e["outcome"] in ("unknown", "partly")
                         and not str(e["check"]).startswith(("è in relazione", "è di questo tipo"))),
                        key=lambda e: -float(e.get("weight") or 1))
        if not pending:
            return f"{a.name} biografia"
        text = next(iter(pending[0].get("readings") or []), "") or str(pending[0]["check"])
        words = [p for p in re.findall(r"[\w'’-]+", text) if len(p) >= 4 and _norm(p) not in STOP_WORDS]
        return f"{a.name} {Chain._short_context(a)} {' '.join(words[:5])}".replace("  ", " ").strip()

    @staticmethod
    def _short_context(a: Link) -> str:
        """Una o due parole dell'entità da cui il candidato è stato trovato, per le ricerche sul web senza omonimi
        («Roland Unger Wikivoyage»), senza parole già nel nome."""
        if a.parent is None:
            return ""
        name = set(_norm(a.name).split())
        words = [p for p in re.sub(r"\([^)]*\)", " ", a.parent.name).split()
                  if len(p) >= 3 and _norm(p) not in name and _norm(p) not in STOP_WORDS]
        return " ".join(words[:2])

    def _order(self, a: Link) -> tuple:
        """Prima chi viene da un anello forte, poi chi ha il legame provato, poi più sì (16/10, Duomo di Milano:
        «Francesco (nome)» trovato da un architetto debole passava davanti a Simone da Orsenigo, forte)."""
        return (bool(a.parent and a.parent.weak), not self._linked(a), -a.quality, not self._co_occurs(a), a.name)

    @staticmethod
    def _linked(a: Link) -> bool:
        """Il legame con l'entità di prima ha un sì con prova (16/10, Hubble: Ken Mattingly, mai su STS-103, davanti
        a Grunsfeld nella riserva perché aveva più sì sugli altri controlli)."""
        return any(e["outcome"] == "yes" and e["proof"] and str(e["check"]).startswith("è in relazione")
                   for e in a.outcomes)

    def _co_occurs(self, a: Link) -> bool:
        """Logica del grafo, senza modello: il candidato compare negli stessi pezzi salvati (o nelle stesse righe delle
        voci) dell'entità di prima. Un legame non provato ma con co-occorrenza vale più di uno trovato solo per
        descrizione (22/10, Blender con Sonnet: Bad Ischl, Gmunden, Traun e Wels a pari merito come città natale di
        Robert Szeleney; vinceva la prima in ordine alfabetico)."""
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
        """Un controllo di peso 3 (lo identifica quasi da solo) senza prova: il candidato non è confermato, anche se
        gli altri controlli passano (17/10, Ducato di Milano: sei comuni confinanti "forti" a pari merito, nessuno
        gemellato con un paese dell'Alta Vienna)."""
        return any(_weight(e) >= 3 and e["outcome"] in ("unknown", "no") for e in a.outcomes)

    @staticmethod
    def _identified(a: Link) -> bool:
        """Almeno un tratto che lo identifica (non il tipo, non il legame, peso ≥ 2) ha un sì con prova, se il piano
        ne ha (23/10, paper §9.1: Guiducci «forte» con il solo legame e il «gesuita bolognese» non provato)."""
        own = [e for e in a.outcomes if _weight(e) >= 2
                  and not str(e["check"]).startswith(("è di questo tipo", "è in relazione"))]
        return not own or any(e["outcome"] == "yes" and e["proof"] for e in own)

    @staticmethod
    def _doubts(a: Link) -> int:
        return sum(1 for e in a.outcomes if e["outcome"] == "unknown")

    @staticmethod
    def _complete(a: Link) -> bool:
        """Tutti i controlli che lo identificano (non il tipo) hanno un sì con prova."""
        own = [e for e in a.outcomes if not str(e["check"]).startswith("è di questo tipo")]
        return bool(own) and all(e["outcome"] == "yes" and e["proof"] for e in own)

    @staticmethod
    def _no(a: Link) -> int:
        return sum(1 for e in a.outcomes if e["outcome"] == "no" and e["proof"])

    def _refuted(self, a: Link) -> bool:
        """Scartato se i "no" con prova sono almeno quanti i "sì": un solo "no" contro più "sì" è spesso una lettura
        troppo stretta (16/10, Git: GitHub scartato per «aperto al pubblico nella primavera 2008», che è il valore
        cercato, non un tratto per riconoscerlo). `punti` = sì meno no."""
        no = sum(_weight(e) for e in a.outcomes if e["outcome"] == "no" and e["proof"])
        si = sum(_weight(e) * OUTCOME_VALUE[e["outcome"]] for e in a.outcomes if e["outcome"] in ("yes", "partly")
                 and e["proof"] and not str(e["check"]).startswith(("è di questo tipo", "è in relazione")))
        # tipo e legame non compensano un "no": dicono che il candidato è del genere giusto, non che è quello giusto
        return no > 0 and si <= no

    def _wiki_starts(self, candidates: list[Link]) -> None:
        """Le prime frasi delle voci dei candidati raggiunti da un collegamento, con una richiesta per lingua (la
        voce intera si legge solo per chi resta in dubbio: Wikipedia concede 10 richieste al minuto)."""
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
                    a.start = Proof(f"[{a.title[1]} ({language}), inizio della voce] {text}",
                                     self.wiki.url(a.title[1], language), [], language)

    def _outcomes(self, candidates: list[Link], checks: list[dict]) -> None:
        """Verifica a gruppi di 2 candidati, in parallelo: richieste più corte (meno confusione tra le righe dei
        candidati) e più veloci. Le prove si preparano prima, nel thread principale (grafo e browser)."""
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
                blocks.append(f"CANDIDATO: {a.name}\n" + "\n".join(
                    f"[{start + i}] {p.text[:1000]}" for i, p in enumerate(mine)))
            groups.append((group, proofs, blocks, intervals))
        checks_text = [f"C{i}: {c.get('description', '')} (letture: {'; '.join(c.get('readings') or [])})"
                           for i, c in enumerate(checks, start=1)]
        ask = lambda blocks: self._decide(  # noqa: E731
            "verification", {"checks": checks_text, "candidates": "\n\n".join(blocks)}, {"candidates": []})
        # Due letture indipendenti per gruppo (autoconsistenza, come in Open Deep Search): un "sì" vale solo se
        # entrambe lo danno, un "no" basta che sia in una (16/10: Bobrow "premiato nel 2009", la Marsigliese "dedicata
        # a un Borbone": sì inventati che una seconda lettura raramente ripete).
        readings = int(self.o.get("verification_readings", 2))
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(ask, [g[2] for g in groups for _ in range(readings)]))
        for n, (group, proofs, _, intervals) in enumerate(groups):
            first_items: dict[int, list[dict]] = {}
            # Le letture finiscono in un ordine a caso e la prima dà la riga citata: ordine fisso, corse ripetibili.
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
                merged_items.append({**e, "outcome": "partly"})  # una lettura dice solo "in parte": vale a metà
            else:
                merged_items.append({**e, "outcome": "unknown", "proof": None})
        return merged_items

    @staticmethod
    def _resolve_check(text: str, checks: list[dict]) -> dict | None:
        """Il controllo a cui risponde Haiku: dal codice (C2) o, se ha riscritto il testo, dall'inizio del testo."""
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
                                        and str(e["check"]).startswith("è in relazione") for e in a.outcomes):
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
