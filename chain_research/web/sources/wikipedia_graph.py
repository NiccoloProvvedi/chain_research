"""Wikipedia come grafo (16/10, idee dai PDF in reference/): le voci sono i nodi, i collegamenti tra voci gli archi,
la frase in cui sta il collegamento (o la riga del riquadro) dice di che legame si tratta.

- Open Deep Search: trattamento dedicato dei siti principali (Wikipedia) invece della pagina generica;
- Think-on-Graph: si esplorano i vicini di un nodo (collegamenti in uscita e, se serve, in entrata) e il modello
  sceglie solo tra pochi archi numerati;
- HierSearch: per un'entità si prendono i pezzi di testo che la nominano (come `get_adjacent_passages`).

Tutto passa dall'API pubblica di MediaWiki (`api.php`): testo della voce (`action=parse`), ricerca (`list=search`),
inizio delle voci (`prop=extracts`), voci che collegano (`list=backlinks`), lingue (`langlinks`). Nessuna chiamata a un
modello: frasi, riquadri e collegamenti li estrae il programma. Niente Wikidata (esami del banco).
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
USER_AGENT = "ricerca-catena/0.1 (uso personale)"


VALID_CONTACT = re.compile(r"[^\s;()<>\"]{3,120}")
TEMPORARY_ERROR = "_errore_temporaneo"  # chiave della risposta quando Wikipedia non ha risposto


def valid_contact(contact: str) -> bool:
    """Un'email o un indirizzo web, senza caratteri che romperebbero lo User-Agent."""
    return bool(VALID_CONTACT.fullmatch(contact)) and ("@" in contact or contact.startswith(("http://", "https://")))


def wikipedia_contact() -> tuple[str, str]:
    """(contatto, origine) per lo User-Agent di Wikipedia. Origine: "impostazioni" (scritto dall'utente, vince),
    "account" (l'email dell'account Claude, 1/11) o "" (nessun contatto: spento o non valido). Sta in
    config/segreti.json, fuori da git, e va SOLO nelle richieste a Wikipedia (il client accetta solo
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
    """Lo User-Agent per Wikipedia e se contiene un contatto (vedi contatto_wikipedia)."""
    contact, _ = wikipedia_contact()
    if contact:
        return f"ricerca-catena/0.1 (uso personale; {contact}) httpx", True
    return USER_AGENT, False


def interval_per(parallel: int) -> float:
    """Secondi tra le richieste di ogni processo, con `parallele` domande insieme, per restare nei limiti di Wikimedia
    in totale: 200 al minuto con un contatto, 10 senza."""
    return max(0.7, 0.3 * parallel) if user_agent()[1] else 6.5 * parallel


LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z]+)?$")
# Spazi dei nomi da non seguire (file, categorie, aiuto, portali...) in varie lingue.
SERVICE = re.compile(r"^(file|image|immagine|categoria|category|aiuto|help|portale|portal|template|wikipedia|"
                      r"speciale|special|discussione|talk|progetto|modulo|module|kategorie|datei|catégorie|fichier|"
                      r"categoría|archivo|categoria|ficheiro|κατηγορία|αρχείο)\s*:", re.I)
# Parti della pagina che non sono contenuto della voce.
NOISE = ("reference", "mw-editsection", "navbox", "metadata", "noprint", "mw-references-wrap", "reflist",
          "hatnote", "dablink", "sistersitebox", "mw-empty-elt", "shortdescription", "toc", "thumbcaption",
          "mw-cite-backlink", "printfooter", "catlinks", "ambox", "avviso", "vedi-anche", "noexcerpt")
# Sezioni senza fatti sull'entità (fonti, collegamenti, note).
USELESS_SECTIONS = re.compile(r"^(note|bibliografia|collegamenti esterni|altri progetti|voci correlate|fonti|"
                             r"references|notes|external links|further reading|see also|sources|bibliography|"
                             r"einzelnachweise|literatur|weblinks|références|liens externes|notes et références|"
                             r"bibliographie|referencias|enlaces externos|referências|ligações externas|"
                             r"παραπομπές|εξωτερικοί σύνδεσμοι)\b", re.I)
# Collegamenti a voci di date e anni (it.wikipedia collega "8 marzo", "1566"): non sono entità.
MONTHS = (r"(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|january|"
        r"february|march|april|may|june|july|august|september|october|november|december)")
DATE_FORMAT = re.compile(rf"^(\d{{1,4}}( a\.c\.| bc| ad)?|\d{{1,2}} {MONTHS}|{MONTHS} \d{{1,2}}|anni \d+|\d+(st|nd|rd|th) century|"
                  rf"\w+ secolo|\d{{3,4}}s)$", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-Þ«\"(\[0-9])")
# Un punto dopo queste parole non chiude la frase (24/10, Wikivoyage: «Vorsitzende des Wikivoyage e. V., Roland Unger»
# era spezzata in «… Wikivoyage e.» e «V., Roland Unger …»; «Roland Unger (* 15. Dezember 1941» in «(* 15.» e
# «Dezember …»): iniziali e lettere sole, numeri ordinali di una o due cifre (date tedesche), abbreviazioni.
NOT_SENTENCE_END = re.compile(r"(?:^|[\s(])(?:\w|\d{1,2}|dr|prof|st|sr|jr|mr|mrs|ms|mt|ca|sig|geb|bzw|vgl|nr|hl|gest|"
                      r"[a-zäöü]+geb)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    """Il testo diviso in frasi (FINE_FRASE), senza tagliare dopo iniziali, ordinali e abbreviazioni (NON_FINE)."""
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
    """Una frase o una riga di riquadro/tabella, con le voci collegate che contiene."""
    text: str
    hyperlinks: list[str] = field(default_factory=list)  # titoli delle voci collegate
    kind: str = "sentence"  # frase | riquadro | tabella | elenco | categorie
    section: str = ""


@dataclass
class Article:
    title: str
    language: str
    url: str
    rows: list[Row] = field(default_factory=list)
    languages: dict[str, str] = field(default_factory=dict)  # lingua → titolo
    categories: list[str] = field(default_factory=list)  # solo quelle visibili («Gesuiti italiani», «Nati nel 1566»)
    disambiguation: bool = False  # proprietà della pagina (DBpedia: la verità sta lì, non nel testo)
    qid: str = ""  # codice Wikidata della voce, letto da Wikipedia (pageprops); solo per gli oracoli
    redirect_from: str = ""  # il titolo chiesto, se Wikipedia ha rinviato a questa voce

    @property
    def start(self) -> str:
        """Le prime frasi della voce (la definizione, con date e luoghi di nascita, sede, fondazione...)."""
        sentences = [r.text for r in self.rows if r.kind == "sentence" and not r.section][:6]
        return " ".join(sentences)

    def infobox(self) -> list[Row]:
        return [r for r in self.rows if r.kind == "riquadro"]


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


# Elementi che separano voci diverse dentro una cella o un paragrafo: senza separatore «text_content» incolla gli
# elementi di un elenco («CastelliMario», 23/10, Galileo: «Notable students» del riquadro inglese).
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
    text = re.sub(r"(\s*·\s*)+", SEPARATOR, text)  # separatori di fila: uno solo
    return text.strip(" ·")


def _hyperlinks(el, language: str) -> list[str]:
    seen: list[str] = []
    for a in el.iter("a"):
        t = _title_from_href(a.get("href", ""), language)
        if t and t not in seen:
            seen.append(t)
    return seen


def _sentences_with_hyperlinks(el, language: str) -> list[tuple[str, list[str]]]:
    """Il testo di un paragrafo diviso in frasi; a ogni frase i collegamenti il cui testo compare in lei."""
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
    """Righe della voce: riquadro (sinottico/infobox), frasi dei paragrafi, voci degli elenchi, righe delle tabelle."""
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
                                      "riquadro" if infobox else "tabella", section))
            continue
        if el.tag == "p" and not any(p.tag == "table" for p in el.iterancestors()):
            for sentence, coll in _sentences_with_hyperlinks(el, language):
                rows.append(Row(sentence[:800], coll, "sentence", section))
        elif el.tag in ("li", "dd") and not any(p.tag in ("table", "li") for p in el.iterancestors()):
            text = _text(el)
            if 3 < len(text):
                rows.append(Row(text[:600], _hyperlinks(el, language), "elenco", section))
    # Il riquadro per primo: è il pezzo più denso di fatti.
    return [r for r in rows if r.kind == "riquadro"] + [r for r in rows if r.kind != "riquadro"]


class WikipediaGraph:
    """Client dell'API di Wikipedia con le voci tenute in memoria per la durata di una ricerca."""

    # Un nome scritto nel testo di una voce vicina senza collegamento, in `scegli_voce`: "favore" = vale come legame,
    # "contro" = in una riga con altri collegamenti la voce con quel titolo è di un omonimo, "nulla" = non conta.
    # Oracolo delle voci (24/10): favore 125 giuste / 8 sbagliate, nulla 126/6, contro (riga con collegamenti) vedi
    # REGISTRO.
    mention = "against"

    def __init__(self, client: SourceClient | None = None, local_allow: bool = False,
                 max_articles: int = 80, fetch: Callable[[str, dict], dict] | None = None,
                 interval_s: float | None = None) -> None:
        """`ottieni(url, params)` = come chiedere il JSON (per i test); senza, il client HTTP delle fonti.
        Regole di Wikimedia (16/10): con uno User-Agent senza un contatto il limite è 10 richieste al minuto (oltre:
        HTTP 429 con Retry-After); con un contatto (email o indirizzo web) 200 al minuto. Il nostro User-Agent non ha
        contatti: una richiesta ogni 6,5 s; con il contatto una ogni 0,7 s (due corse in parallelo restano sotto 200
        al minuto). Retry-After sempre rispettato. Il browser senza testa viene bloccato: non si usa per aggirare."""
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
            raise SourceError(f"wikipedia: lingua non valida ({language})")
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
                self.limit.pause(min(max(wait, 5.0), 90.0))  # HTTP 429: si rispetta Retry-After
            return {TEMPORARY_ERROR: True}
        except replay.UnrecordedRequest:
            raise
        except Exception:  # noqa: BLE001 - Wikipedia non raggiungibile: la catena passa al web
            return {TEMPORARY_ERROR: True}

    def url(self, title: str, language: str) -> str:
        return f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_,-.:')}"

    def search(self, text: str, language: str, n: int = 5) -> list[dict]:
        """Ricerca nel testo delle voci: [{titolo, frammento}]."""
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
                if not self.exhausted:  # 17/10, Pace di Lodi: tetto finito prima del valore, senza saperlo
                    self.exhausted = True
                    log.warning("wikipedia: raggiunto il tetto di %d voci", self.max_articles)
                return None
            self.read_count += 1
        # Categorie e proprietà (disambiguazione, codice) arrivano con la stessa richiesta (23/10, idea di YAGO).
        data = self._json(language, action="parse", page=title, prop="text|langlinks|displaytitle|categories|properties",
                          redirects=1)
        if data.get(TEMPORARY_ERROR):  # rete o 429: la voce non è «inesistente», si potrà richiedere
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
            if isinstance(properties, list):  # formato vecchio: [{name, *}]
                properties = {x.get("name"): x.get("*", "") for x in properties if isinstance(x, dict)}
            categories = [str(c.get("category", "")).replace("_", " ") for c in parse.get("categories") or []
                         if isinstance(c, dict) and not c.get("hidden") and c.get("category")]
            rows = analyze(html, true, language)
            if categories:  # una riga citabile: la verifica legge «Gesuiti italiani; Nati a Bologna»
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
        """Le prime frasi di più voci con una richiesta sola (al massimo 20)."""
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
        """Voci che collegano a questa (relazioni al contrario: chi ha progettato, chi è membro di...)."""
        data = self._json(language, action="query", list="backlinks", bltitle=title, blnamespace=0, bllimit=n,
                          blfilterredir="nonredirects")
        return [x.get("title", "") for x in (data.get("query") or {}).get("backlinks", []) if x.get("title")]

    def find(self, name: str, language: str, context: str = "",
              accept: Callable[[Article], bool] | None = None) -> Article | None:
        """La voce di un'entità: prima il titolo esatto (con i redirect), poi la ricerca con il contesto; `accetta`
        scarta gli omonimi (si prova il risultato successivo)."""
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

    # --- dal nome alla voce (fasi 2-3 del piano entità e righe) ---------------------------------------------------

    def article_candidates(self, name: str, language: str, context: str = "", research_words: str = "",
                       maximum: int = 7) -> list[tuple[str, str]]:
        """Le voci che possono essere il nome, con il grado di `corrisponde`: (titolo, grado). Prima il titolo esatto
        (con il rinvio di Wikipedia), poi le voci di una pagina di disambiguazione, poi la ricerca. Al massimo 7
        (REL: 4 per probabilità + 3 per contesto)."""
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
                    # Rinvio con un nome dentro l'altro («Mozilla Firefox» → «Firefox», «Einstein» → «Albert
                    # Einstein»): è lo stesso nome. Un rinvio verso un'altra cosa non vale mai (23/10, Arduino:
                    # «Phillip Torrone» → «Limor Fried», la moglie; «Democratic Federal Yugoslavia» → «Socialist…»).
                    degree = "variant"
                add(article.title, degree)
        for text in dict.fromkeys(x for x in (f"{name} {research_words}".strip(), name) if x):
            for x in self.search(text, language, n=5):
                add(x["title"], matches(name, x["title"], context))
        return output

    def info_articles(self, titles: list[str], language: str, neighbours: list[str]) -> dict[str, dict]:
        """Per molte voci con una richiesta: disambiguazione, categorie visibili, collegamenti verso i `vicini`
        (prop=links&pltitles: GLOW, indicatore «c'è un collegamento»)."""
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
        """La voce del nome, decisa dal grafo (TAGME, Milne-Witten, GLOW adattati: poche entità di contesto, niente
        link entranti). Per ogni voce candidata (`candidati_voce`, grado di `corrisponde`):
        - grafo: collega una voce vicina (+1), è collegata da una voce vicina o nominata nel suo testo (+1);
        - categorie: una sua categoria ha una parola del contesto (nazionalità, secoli, nati/morti esclusi).
        Qualcuna ha grafo > 0: si tengono quelle entro il 70% della migliore (ε = 30%), vince la priorità del grado
        (parentesi giustificata, esatto, variante, base), poi grafo, categorie, ordine. Nessuna: vince un titolo
        esatto/variante o una parentesi giustificata, o una voce non «base» con le categorie; `accetta` decide su
        queste. Pareggio tra gradi deboli → `spareggio` (lista chiusa, numero o nessuna). Altrimenti nessuna voce:
        meglio niente di un omonimo. `vicini` = voci (lingua, titolo) dell'entità di prima."""
        nearby_articles = [v for lg, t in neighbours if (v := self.article(t, lg)) is not None]
        roots = {p[:5] for p in _plan(context).split() if len(p) >= 5 and p not in TIES}
        roots -= {p[:5] for p in _plan(name).split()}  # il nome stesso non prova niente (Freedo, 22/10)
        for language in languages:
            candidates = self.article_candidates(name, language, context, research_words)
            if not candidates:
                continue
            neighbours_titles = [v.title for v in nearby_articles if v.language == language]
            neighbours_titles += [v.languages[language] for v in nearby_articles if v.language != language and language in v.languages]
            neighbours_titles = list(dict.fromkeys(neighbours_titles))
            info = self.info_articles([t for t, _ in candidates], language, neighbours_titles)
            linked_from = {_plan(t) for v in nearby_articles if v.language == language for r in v.rows
                            for t in r.hyperlinks}
            neighbours_texts = " ".join(_plan(r.text) for v in nearby_articles if v.language == language for r in v.rows)
            # righe con collegamenti: lì chi scriveva collegava le voci che esistono
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
                elif named and self.mention == "favore":
                    graph += 1.0
                elif named and self.mention == "against":
                    # Nominata senza collegamento in una riga che collega altre voci: quella voce è di un omonimo
                    # (Roland Unger nella voce tedesca di Wikivoyage); in una riga senza collegamenti non dice niente
                    # (24/10, oracolo: «per gli editori Adelphi e Sellerio» e la voce giusta «Adelphi»).
                    unlinked = bool(re.search(rf"\b{re.escape(base)}\b", linked_texts))
                words_cat = {p[:5] for c in x.get("categories", []) for p in _plan(c).split()} - GENERIC_CATEGORIES
                categories = 1.0 if roots & words_cat else 0.0
                scores.append({"title": true, "degree": degree, "graph": graph, "categories": categories,
                                 "place": place, "unlinked": unlinked})
            if not scores:
                continue
            best = max(p["graph"] for p in scores)
            if best > 0:
                # Collegamenti nel grafo (TAGME: entro il 30% del più collegato, poi il più «comune»).
                picked = [p for p in scores if p["graph"] >= 0.7 * best]
                order = lambda p: (-PRIORITY[p["degree"]], -p["graph"], -p["categories"], p["place"])  # noqa: E731
            else:
                # Niente collegamenti: un titolo esatto vince (la voce principale di quel nome); altrimenti servono le
                # categorie con una parola del contesto («Tux (mascotte)» per una domanda su una mascotte).
                # (un «base», cognome o parentesi non giustificata, non basta con le sole categorie: 23/10, «Arduino»
                # → «Gino Arduino», produttore discografico, per «produce» nella domanda)
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
            log.info("wikipedia: «%s» → %s (%s, grafo %.0f, categorie %.0f)", name, article.title, first_one["degree"],
                     first_one["graph"], first_one["categories"])
            return article
        return None

    def _short_start(self, title: str, language: str, words: int = 40) -> str:
        """Scheda del candidato per lo spareggio (BLINK: titolo + inizio della voce)."""
        text = self.starts([title], language).get(title, "")
        return " ".join(text.split()[:words])


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


EMPTY = {"del", "della", "delle", "dei", "degli", "the", "von", "van", "des", "der", "die", "and"}

# Parole di legame ignorate nel confronto nome/titolo (non articoli iniziali: quelli si tolgono a parte).
TIES = {"di", "da", "de", "del", "della", "dello", "dei", "degli", "delle", "d", "of", "the", "von", "van", "der",
          "des", "du", "la", "le", "il", "lo", "l", "e", "and", "y", "et", "und", "a", "al", "in"}
ARTICLES = {"il", "lo", "la", "i", "gli", "le", "l", "the", "a", "an", "el", "los", "las", "les", "der", "die", "das"}
ROMAN = re.compile(r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
DEGREES = {"exact": 3.0, "variant": 2.5, "parentheses": 2.0, "base": 1.0, "": 0.0}
# Per scegliere tra le voci di un nome: la parentesi giustificata dal contesto è più precisa del titolo esatto (che è
# solo il significato principale: «Haiku» poesia, «Haiku (sistema operativo)» se la domanda parla di sistemi).
PRIORITY = {"parentheses": 4.0, "exact": 3.0, "variant": 2.5, "base": 1.0, "": 0.0}
# Parole delle categorie troppo generiche per dire qualcosa sull'entità (nazionalità, secoli, nati/morti).
GENERIC_CATEGORIES = {"itali", "ameri", "stati", "uniti", "franc", "ingle", "engli", "briti", "germa", "tedes",
                       "spagn", "europ", "secol", "centu", "nati", "morti", "birth", "death", "perso", "peopl",
                       "anni", "years", "citta", "citie"}


def _plan(text: str) -> str:
    """Minuscole, senza accenti né punteggiatura (graphify: NFKC + casefold; qui anche gli accenti)."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def _without_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c))


# Forme societarie in fondo al nome: non distinguono l'entità (29/10, Raspberry Pi: «Premier Farnell Ltd.» senza voce e
# allargata come entità diversa da «Premier Farnell»).
CORPORATE_SUFFIXES = {"ltd", "limited", "plc", "inc", "incorporated", "llc", "gmbh", "spa", "srl", "corp", "corporation",
                      "co", "ag", "sa", "nv", "bv", "sas", "sarl", "kg", "ab", "oy", "as", "asa", "pty", "lp", "llp"}


def without_corporate_suffix(text: str) -> str:
    """Il nome senza le forme societarie finali («Premier Farnell Ltd.» → «Premier Farnell»; «S.p.A.» compresa)."""
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
    """Uguali, o varianti della stessa parola (Bettinus/Bettini, Federale/Federal, Guisa/Guise). Numeri e numeri
    romani solo uguali (Pio II ≠ Pio III; graphify: «numeric tokens differ» blocca sempre)."""
    if a == b:
        return True
    if _numeric(a) or _numeric(b):
        return False
    if min(len(a), len(b)) >= 6:
        return a[:5] == b[:5] and abs(len(a) - len(b)) <= 3
    # parole corte: solo un refuso di una lettera, a parità di lunghezza (graphify: niente inserzioni sulle corte)
    return len(a) == len(b) >= 4 and sum(x != y for x, y in zip(a, b)) == 1


def _same_words(a: list[str], b: list[str]) -> bool:
    """Stesse parole in ordine diverso («Jugoslavia Democratica Federale» / «Jugoslavia Federale Democratica»)."""
    rest = list(b)
    for x in a:
        y = next((y for y in rest if _same_word(x, y)), None)
        if y is None:
            return False
        rest.remove(y)
    return True


def _content(name: str, title: str) -> bool:
    """Le parole di un nome stanno tutte nell'altro (titolo senza parentesi)."""
    a = _name_words(name)
    b = _name_words(re.sub(r"\s*\([^)]*\)\s*$", "", title))
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(_same_word(x, y) for y in long) for x in short)


def matches(name: str, title: str, context: str = "") -> str:
    """Quanto il titolo di una voce è il nome cercato. 23/10, al posto della vecchia `_simile` (60% di parole in comune:
    «Mario Bettinus» → «Mario (nome)», «Silvio Piccolomini» → papa Pio II). Regole dal codice di UNC-FEVER (titolo
    con la parentesi) e di graphify (mai unire due entità diverse):
    - "esatto": stesso nome (a meno di accenti, maiuscole, articolo iniziale), anche con la parentesi scritta;
    - "variante": stesse parole, alcune in variante (Bettinus/Bettini);
    - "parentesi": il nome è la base del titolo e le parole della parentesi stanno nel contesto («Tux (mascot)» con
      «mascotte» nel contesto);
    - "base": il nome è la base di un titolo con parentesi non giustificata, o il cognome della voce («Fermi» →
      «Enrico Fermi»): vale solo con un collegamento nel grafo;
    - "": un'altra entità (parola diversa, numero diverso, titolo più corto del nome: «Mario» per «Mario Bettinus»).
    """
    if not name or not title:
        return ""
    if _plan(name) == _plan(title):
        # maiuscole diverse oltre la prima lettera: «Tex» (fumetto) non è «TeX» (23/10, oracolo)
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
            return ""  # nomi corti: solo un refuso di una lettera (graphify; 23/10, «Freedo» ≠ «Freedom»)
        if not parentheses:
            return "exact" if a == b else "variant"
        # radici di 6 lettere: «astronomo» non giustifica «(astrologia)» (Plutone)
        roots = {p[:6] for p in _plan(context).split() if len(p) >= 4}
        if roots & {p[:6] for p in _plan(parentheses).split() if len(p) >= 4 and p not in TIES}:
            return "parentheses"
        return "base"
    # Il nome è il cognome (o la fine) del titolo: «Fermi» → «Enrico Fermi»; mai il contrario (titolo più corto).
    # Solo un nome di una parola: «Traforo del Monte Bianco» non è «Monte Bianco» (23/10, oracolo).
    if not parentheses and len(a) == 1 < len(b) and len(a[0]) >= 4 and _same_word(a[0], b[-1]):
        return "base"
    return ""


# Voci che non sono un'entità: prenomi, cognomi, elenchi (YAGO: titoli «List of», «(disambiguation)» → NIL).
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
