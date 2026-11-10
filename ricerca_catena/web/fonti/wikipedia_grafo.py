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

from ...modello import rigioco
from .base import ErroreFonte
from .config import email_account_claude, segreti
from .http import ClientFonte
from .limiti import limitatore

log = logging.getLogger("ricerca_catena.fonti")
USER_AGENT = "ricerca-catena/0.1 (uso personale)"


CONTATTO_VALIDO = re.compile(r"[^\s;()<>\"]{3,120}")
ERRORE_TEMPORANEO = "_errore_temporaneo"  # chiave della risposta quando Wikipedia non ha risposto


def contatto_valido(contatto: str) -> bool:
    """Un'email o un indirizzo web, senza caratteri che romperebbero lo User-Agent."""
    return bool(CONTATTO_VALIDO.fullmatch(contatto)) and ("@" in contatto or contatto.startswith(("http://", "https://")))


def contatto_wikipedia() -> tuple[str, str]:
    """(contatto, origine) per lo User-Agent di Wikipedia. Origine: "impostazioni" (scritto dall'utente, vince),
    "account" (l'email dell'account Claude, 1/11) o "" (nessun contatto: spento o non valido). Sta in
    config/segreti.json, fuori da git, e va SOLO nelle richieste a Wikipedia (il client accetta solo
    wikipedia.org)."""
    dati = segreti()
    if dati.get("contatto_wikipedia_attivo") is False:
        return "", ""
    scritto = str(dati.get("contatto_wikipedia") or "").strip()
    if scritto:
        return (scritto, "impostazioni") if contatto_valido(scritto) else ("", "")
    email = email_account_claude()
    return (email, "account") if contatto_valido(email) else ("", "")


def user_agent() -> tuple[str, bool]:
    """Lo User-Agent per Wikipedia e se contiene un contatto (vedi contatto_wikipedia)."""
    contatto, _ = contatto_wikipedia()
    if contatto:
        return f"ricerca-catena/0.1 (uso personale; {contatto}) httpx", True
    return USER_AGENT, False


def intervallo_per(parallele: int) -> float:
    """Secondi tra le richieste di ogni processo, con `parallele` domande insieme, per restare nei limiti di Wikimedia
    in totale: 200 al minuto con un contatto, 10 senza."""
    return max(0.7, 0.3 * parallele) if user_agent()[1] else 6.5 * parallele


LINGUA = re.compile(r"^[a-z]{2,3}(-[a-z]+)?$")
# Spazi dei nomi da non seguire (file, categorie, aiuto, portali...) in varie lingue.
SERVIZIO = re.compile(r"^(file|image|immagine|categoria|category|aiuto|help|portale|portal|template|wikipedia|"
                      r"speciale|special|discussione|talk|progetto|modulo|module|kategorie|datei|catégorie|fichier|"
                      r"categoría|archivo|categoria|ficheiro|κατηγορία|αρχείο)\s*:", re.I)
# Parti della pagina che non sono contenuto della voce.
RUMORE = ("reference", "mw-editsection", "navbox", "metadata", "noprint", "mw-references-wrap", "reflist",
          "hatnote", "dablink", "sistersitebox", "mw-empty-elt", "shortdescription", "toc", "thumbcaption",
          "mw-cite-backlink", "printfooter", "catlinks", "ambox", "avviso", "vedi-anche", "noexcerpt")
# Sezioni senza fatti sull'entità (fonti, collegamenti, note).
SEZIONI_INUTILI = re.compile(r"^(note|bibliografia|collegamenti esterni|altri progetti|voci correlate|fonti|"
                             r"references|notes|external links|further reading|see also|sources|bibliography|"
                             r"einzelnachweise|literatur|weblinks|références|liens externes|notes et références|"
                             r"bibliographie|referencias|enlaces externos|referências|ligações externas|"
                             r"παραπομπές|εξωτερικοί σύνδεσμοι)\b", re.I)
# Collegamenti a voci di date e anni (it.wikipedia collega "8 marzo", "1566"): non sono entità.
MESI = (r"(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|january|"
        r"february|march|april|may|june|july|august|september|october|november|december)")
DATA = re.compile(rf"^(\d{{1,4}}( a\.c\.| bc| ad)?|\d{{1,2}} {MESI}|{MESI} \d{{1,2}}|anni \d+|\d+(st|nd|rd|th) century|"
                  rf"\w+ secolo|\d{{3,4}}s)$", re.I)
FINE_FRASE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÖØ-Þ«\"(\[0-9])")
# Un punto dopo queste parole non chiude la frase (24/10, Wikivoyage: «Vorsitzende des Wikivoyage e. V., Roland Unger»
# era spezzata in «… Wikivoyage e.» e «V., Roland Unger …»; «Roland Unger (* 15. Dezember 1941» in «(* 15.» e
# «Dezember …»): iniziali e lettere sole, numeri ordinali di una o due cifre (date tedesche), abbreviazioni.
NON_FINE = re.compile(r"(?:^|[\s(])(?:\w|\d{1,2}|dr|prof|st|sr|jr|mr|mrs|ms|mt|ca|sig|geb|bzw|vgl|nr|hl|gest|"
                      r"[a-zäöü]+geb)\.$", re.I)


def dividi_frasi(testo: str) -> list[str]:
    """Il testo diviso in frasi (FINE_FRASE), senza tagliare dopo iniziali, ordinali e abbreviazioni (NON_FINE)."""
    frasi: list[str] = []
    inizio = 0
    for m in FINE_FRASE.finditer(testo):
        if NON_FINE.search(testo[max(inizio, m.start() - 12):m.start()]):
            continue
        frasi.append(testo[inizio:m.start()])
        inizio = m.end()
    frasi.append(testo[inizio:])
    return [f.strip() for f in frasi if f.strip()]


@dataclass
class Riga:
    """Una frase o una riga di riquadro/tabella, con le voci collegate che contiene."""
    testo: str
    collegamenti: list[str] = field(default_factory=list)  # titoli delle voci collegate
    tipo: str = "frase"  # frase | riquadro | tabella | elenco | categorie
    sezione: str = ""


@dataclass
class Voce:
    titolo: str
    lingua: str
    url: str
    righe: list[Riga] = field(default_factory=list)
    lingue: dict[str, str] = field(default_factory=dict)  # lingua → titolo
    categorie: list[str] = field(default_factory=list)  # solo quelle visibili («Gesuiti italiani», «Nati nel 1566»)
    disambigua: bool = False  # proprietà della pagina (DBpedia: la verità sta lì, non nel testo)
    qid: str = ""  # codice Wikidata della voce, letto da Wikipedia (pageprops); solo per gli oracoli
    rinvio_da: str = ""  # il titolo chiesto, se Wikipedia ha rinviato a questa voce

    @property
    def inizio(self) -> str:
        """Le prime frasi della voce (la definizione, con date e luoghi di nascita, sede, fondazione...)."""
        frasi = [r.testo for r in self.righe if r.tipo == "frase" and not r.sezione][:6]
        return " ".join(frasi)

    def riquadro(self) -> list[Riga]:
        return [r for r in self.righe if r.tipo == "riquadro"]


def _titolo_da_href(href: str, lingua: str) -> str | None:
    if not href:
        return None
    parti = urlsplit(href)
    if parti.netloc and not parti.netloc.startswith(f"{lingua}.wikipedia.org"):
        return None
    if "/wiki/" not in parti.path or parti.query:
        return None
    titolo = unquote(parti.path.split("/wiki/", 1)[1]).replace("_", " ").strip()
    if not titolo or SERVIZIO.match(titolo) or DATA.match(titolo):
        return None
    return titolo


# Elementi che separano voci diverse dentro una cella o un paragrafo: senza separatore «text_content» incolla gli
# elementi di un elenco («CastelliMario», 23/10, Galileo: «Notable students» del riquadro inglese).
BLOCCHI = {"li", "br", "div", "p", "dd", "dt", "ul", "ol", "tr"}
SEPARATORE = " · "


def _testo(el) -> str:
    parti: list[str] = []

    def visita(e, radice: bool = False) -> None:
        if isinstance(e.tag, str):
            blocco = e.tag in BLOCCHI and not radice
            if blocco:
                parti.append(SEPARATORE)
            if e.text:
                parti.append(e.text)
            for figlio in e:
                visita(figlio)
            if blocco and e.tag != "br":
                parti.append(SEPARATORE)
        if e.tail and not radice:
            parti.append(e.tail)

    visita(el, radice=True)
    testo = " ".join("".join(parti).split())
    testo = re.sub(r"(\s*·\s*)+", SEPARATORE, testo)  # separatori di fila: uno solo
    return testo.strip(" ·")


def _collegamenti(el, lingua: str) -> list[str]:
    visti: list[str] = []
    for a in el.iter("a"):
        t = _titolo_da_href(a.get("href", ""), lingua)
        if t and t not in visti:
            visti.append(t)
    return visti


def _frasi_con_collegamenti(el, lingua: str) -> list[tuple[str, list[str]]]:
    """Il testo di un paragrafo diviso in frasi; a ogni frase i collegamenti il cui testo compare in lei."""
    testo = _testo(el)
    if not testo:
        return []
    ancore = []
    for a in el.iter("a"):
        t = _titolo_da_href(a.get("href", ""), lingua)
        if t:
            ancore.append((" ".join(a.text_content().split()), t))
    frasi = dividi_frasi(testo)
    uscita = []
    for frase in frasi:
        mie = []
        for ancora, titolo in ancore:
            if ancora and ancora in frase and titolo not in mie:
                mie.append(titolo)
        uscita.append((frase, mie))
    return uscita


def analizza(html: str, titolo: str, lingua: str) -> list[Riga]:
    """Righe della voce: riquadro (sinottico/infobox), frasi dei paragrafi, voci degli elenchi, righe delle tabelle."""
    radice = lxml.html.fromstring(f"<div>{html}</div>")
    for el in list(radice.iter()):
        if not isinstance(el.tag, str):
            continue
        classi = set((el.get("class") or "").split())
        if el.tag in ("style", "script"):
            el.drop_tree()
            continue
        if classi & set(RUMORE) or el.get("role") == "navigation" or (el.get("id") or "") == "toc":
            el.drop_tree()
    righe: list[Riga] = []
    sezione = ""
    for el in radice.iter():
        if not isinstance(el.tag, str):
            continue
        classi = (el.get("class") or "").lower()
        if el.tag in ("h2", "h3", "h4"):
            sezione = _testo(el)[:80]
            continue
        if SEZIONI_INUTILI.match(sezione):
            continue
        if el.tag == "table":
            riquadro = "infobox" in classi or "sinottico" in classi or "infobox" in (el.get("id") or "")
            for tr in el.iter("tr"):
                celle = [c for c in tr if isinstance(c.tag, str) and c.tag in ("th", "td")]
                if not celle:
                    continue
                if riquadro and len(celle) >= 2:
                    chiave, valore = _testo(celle[0]), " · ".join(_testo(c) for c in celle[1:])
                    testo = f"{chiave}: {valore}"
                else:
                    testo = " | ".join(_testo(c) for c in celle)
                testo = " ".join(testo.split())
                if len(testo) > 3:
                    righe.append(Riga(testo[:600], _collegamenti(tr, lingua),
                                      "riquadro" if riquadro else "tabella", sezione))
            continue
        if el.tag == "p" and not any(p.tag == "table" for p in el.iterancestors()):
            for frase, coll in _frasi_con_collegamenti(el, lingua):
                righe.append(Riga(frase[:800], coll, "frase", sezione))
        elif el.tag in ("li", "dd") and not any(p.tag in ("table", "li") for p in el.iterancestors()):
            testo = _testo(el)
            if 3 < len(testo):
                righe.append(Riga(testo[:600], _collegamenti(el, lingua), "elenco", sezione))
    # Il riquadro per primo: è il pezzo più denso di fatti.
    return [r for r in righe if r.tipo == "riquadro"] + [r for r in righe if r.tipo != "riquadro"]


class WikipediaGrafo:
    """Client dell'API di Wikipedia con le voci tenute in memoria per la durata di una ricerca."""

    # Un nome scritto nel testo di una voce vicina senza collegamento, in `scegli_voce`: "favore" = vale come legame,
    # "contro" = in una riga con altri collegamenti la voce con quel titolo è di un omonimo, "nulla" = non conta.
    # Oracolo delle voci (24/10): favore 125 giuste / 8 sbagliate, nulla 126/6, contro (riga con collegamenti) vedi
    # REGISTRO.
    menzione = "contro"

    def __init__(self, client: ClientFonte | None = None, consenti_locali: bool = False,
                 massimo_voci: int = 80, ottieni: Callable[[str, dict], dict] | None = None,
                 intervallo_s: float | None = None) -> None:
        """`ottieni(url, params)` = come chiedere il JSON (per i test); senza, il client HTTP delle fonti.
        Regole di Wikimedia (16/10): con uno User-Agent senza un contatto il limite è 10 richieste al minuto (oltre:
        HTTP 429 con Retry-After); con un contatto (email o indirizzo web) 200 al minuto. Il nostro User-Agent non ha
        contatti: una richiesta ogni 6,5 s; con il contatto una ogni 0,7 s (due corse in parallelo restano sotto 200
        al minuto). Retry-After sempre rispettato. Il browser senza testa viene bloccato: non si usa per aggirare."""
        agente, con_contatto = user_agent()
        if intervallo_s is None:
            intervallo_s = 0.7 if con_contatto else 6.5
        self.limite = limitatore("wikipedia", intervallo_s, int(3600 / max(intervallo_s, 0.3)))
        self.ottieni = ottieni
        self.client = client or ClientFonte("Wikipedia", user_agent=agente, limitatore=self.limite,
                                            domini={"wikipedia.org"}, consenti_locali=consenti_locali)
        self.richieste = 0
        self.voci: dict[tuple[str, str], Voce | None] = {}
        self.massimo_voci = massimo_voci
        self.lette = 0
        self.esaurite = False
        self._serratura = threading.Lock()

    @staticmethod
    def _api(lingua: str) -> str:
        if not LINGUA.match(lingua):
            raise ErroreFonte(f"wikipedia: lingua non valida ({lingua})")
        return f"https://{lingua}.wikipedia.org/w/api.php"

    def _json(self, lingua: str, **params) -> dict:
        params = {"format": "json", "formatversion": 2, **params}
        self.richieste += 1
        try:
            if self.ottieni is None:
                return rigioco.attraverso("wikipedia", [lingua, params],
                                          lambda: self.client.get_json(self._api(lingua), params=params))
            for _ in range(3):
                self.limite.attendi(attesa_massima_s=120)
                dati, attesa = self.ottieni(self._api(lingua), params)
                if attesa is None:
                    return dati if isinstance(dati, dict) else {}
                self.limite.pausa(min(max(attesa, 5.0), 90.0))  # HTTP 429: si rispetta Retry-After
            return {ERRORE_TEMPORANEO: True}
        except rigioco.RichiestaNonRegistrata:
            raise
        except Exception:  # noqa: BLE001 - Wikipedia non raggiungibile: la catena passa al web
            return {ERRORE_TEMPORANEO: True}

    def url(self, titolo: str, lingua: str) -> str:
        return f"https://{lingua}.wikipedia.org/wiki/{quote(titolo.replace(' ', '_'), safe='()_,-.:')}"

    def cerca(self, testo: str, lingua: str, n: int = 5) -> list[dict]:
        """Ricerca nel testo delle voci: [{titolo, frammento}]."""
        dati = self._json(lingua, action="query", list="search", srsearch=testo[:250], srlimit=n,
                          srprop="snippet", srnamespace=0)
        uscita = []
        for x in (dati.get("query") or {}).get("search", []):
            frammento = re.sub(r"<[^>]+>", "", x.get("snippet", ""))
            uscita.append({"titolo": x.get("title", ""), "frammento": " ".join(frammento.split())})
        return uscita

    def voce(self, titolo: str, lingua: str) -> Voce | None:
        chiave = (lingua, titolo)
        with self._serratura:
            if chiave in self.voci:
                return self.voci[chiave]
            if self.lette >= self.massimo_voci:
                if not self.esaurite:  # 17/10, Pace di Lodi: tetto finito prima del valore, senza saperlo
                    self.esaurite = True
                    log.warning("wikipedia: raggiunto il tetto di %d voci", self.massimo_voci)
                return None
            self.lette += 1
        # Categorie e proprietà (disambiguazione, codice) arrivano con la stessa richiesta (23/10, idea di YAGO).
        dati = self._json(lingua, action="parse", page=titolo, prop="text|langlinks|displaytitle|categories|properties",
                          redirects=1)
        if dati.get(ERRORE_TEMPORANEO):  # rete o 429: la voce non è «inesistente», si potrà richiedere
            with self._serratura:
                self.lette -= 1
            return None
        parse = dati.get("parse") or {}
        html = parse.get("text")
        voce = None
        if isinstance(html, str) and html:
            vero = parse.get("title") or titolo
            lingue = {x.get("lang"): x.get("title") for x in parse.get("langlinks", []) if x.get("lang")}
            proprieta = parse.get("properties") or {}
            if isinstance(proprieta, list):  # formato vecchio: [{name, *}]
                proprieta = {x.get("name"): x.get("*", "") for x in proprieta if isinstance(x, dict)}
            categorie = [str(c.get("category", "")).replace("_", " ") for c in parse.get("categories") or []
                         if isinstance(c, dict) and not c.get("hidden") and c.get("category")]
            righe = analizza(html, vero, lingua)
            if categorie:  # una riga citabile: la verifica legge «Gesuiti italiani; Nati a Bologna»
                righe.append(Riga("Categorie: " + "; ".join(categorie), [], "categorie", ""))
            rinvio = next((r.get("from", "") for r in parse.get("redirects") or [] if isinstance(r, dict)), "")
            voce = Voce(vero, lingua, self.url(vero, lingua), righe, lingue, categorie,
                        "disambiguation" in proprieta, str(proprieta.get("wikibase_item") or ""), rinvio)
        with self._serratura:
            self.voci[chiave] = voce
            if voce is not None:
                self.voci[(lingua, voce.titolo)] = voce
        return voce

    def inizi(self, titoli: list[str], lingua: str) -> dict[str, str]:
        """Le prime frasi di più voci con una richiesta sola (al massimo 20)."""
        uscita: dict[str, str] = {}
        for i in range(0, len(titoli), 20):
            gruppo = titoli[i:i + 20]
            dati = self._json(lingua, action="query", prop="extracts", exintro=1, explaintext=1, exsentences=4,
                              exlimit=20, titles="|".join(gruppo), redirects=1)
            for p in (dati.get("query") or {}).get("pages", []):
                if p.get("extract"):
                    uscita[p.get("title", "")] = " ".join(p["extract"].split())[:900]
        return uscita

    def entranti(self, titolo: str, lingua: str, n: int = 60) -> list[str]:
        """Voci che collegano a questa (relazioni al contrario: chi ha progettato, chi è membro di...)."""
        dati = self._json(lingua, action="query", list="backlinks", bltitle=titolo, blnamespace=0, bllimit=n,
                          blfilterredir="nonredirects")
        return [x.get("title", "") for x in (dati.get("query") or {}).get("backlinks", []) if x.get("title")]

    def trova(self, nome: str, lingua: str, contesto: str = "",
              accetta: Callable[[Voce], bool] | None = None) -> Voce | None:
        """La voce di un'entità: prima il titolo esatto (con i redirect), poi la ricerca con il contesto; `accetta`
        scarta gli omonimi (si prova il risultato successivo)."""
        buona = lambda v: v is not None and not _disambigua(v) and (accetta is None or accetta(v))  # noqa: E731
        voce = self.voce(nome, lingua)
        if buona(voce):
            return voce
        provati = {voce.titolo} if voce is not None else set()
        for testo in ([f"{nome} {contesto}".strip()] if contesto else []) + [nome]:
            for x in self.cerca(testo, lingua, n=4):
                if x["titolo"] in provati or GRADI[corrisponde(nome, x["titolo"], contesto)] < GRADI["parentesi"]:
                    continue
                provati.add(x["titolo"])
                v = self.voce(x["titolo"], lingua)
                if buona(v):
                    return v
        return None

    # --- dal nome alla voce (fasi 2-3 del piano entità e righe) ---------------------------------------------------

    def candidati_voce(self, nome: str, lingua: str, contesto: str = "", parole_ricerca: str = "",
                       massimo: int = 7) -> list[tuple[str, str]]:
        """Le voci che possono essere il nome, con il grado di `corrisponde`: (titolo, grado). Prima il titolo esatto
        (con il rinvio di Wikipedia), poi le voci di una pagina di disambiguazione, poi la ricerca. Al massimo 7
        (REL: 4 per probabilità + 3 per contesto)."""
        uscita: list[tuple[str, str]] = []

        def aggiungi(titolo: str, grado: str) -> None:
            if grado and titolo not in {t for t, _ in uscita} and len(uscita) < massimo:
                uscita.append((titolo, grado))

        voce = self.voce(nome, lingua)
        if voce is not None:
            if voce.disambigua or _disambigua(voce):
                for r in voce.righe:
                    for t in r.collegamenti:
                        aggiungi(t, corrisponde(nome, t, contesto))
            elif not non_entita(voce):
                grado = corrisponde(nome, voce.titolo, contesto)
                if not grado and voce.rinvio_da and _contenuto(nome, voce.titolo):
                    # Rinvio con un nome dentro l'altro («Mozilla Firefox» → «Firefox», «Einstein» → «Albert
                    # Einstein»): è lo stesso nome. Un rinvio verso un'altra cosa non vale mai (23/10, Arduino:
                    # «Phillip Torrone» → «Limor Fried», la moglie; «Democratic Federal Yugoslavia» → «Socialist…»).
                    grado = "variante"
                aggiungi(voce.titolo, grado)
        for testo in dict.fromkeys(x for x in (f"{nome} {parole_ricerca}".strip(), nome) if x):
            for x in self.cerca(testo, lingua, n=5):
                aggiungi(x["titolo"], corrisponde(nome, x["titolo"], contesto))
        return uscita

    def info_voci(self, titoli: list[str], lingua: str, vicini: list[str]) -> dict[str, dict]:
        """Per molte voci con una richiesta: disambiguazione, categorie visibili, collegamenti verso i `vicini`
        (prop=links&pltitles: GLOW, indicatore «c'è un collegamento»)."""
        uscita: dict[str, dict] = {t: {"disambigua": False, "categorie": [], "verso": set()} for t in titoli}
        if not titoli:
            return uscita
        params = dict(action="query", titles="|".join(titoli[:40]), redirects=1, prop="pageprops|categories",
                      ppprop="disambiguation", clshow="!hidden", cllimit="max")
        if vicini:
            params.update(prop="pageprops|categories|links", pltitles="|".join(vicini[:40]), pllimit="max")
        dati = self._json(lingua, **params)
        q = dati.get("query") or {}
        rinvii = {r.get("from"): r.get("to") for r in q.get("normalized", []) + q.get("redirects", [])}
        per_titolo = {p.get("title"): p for p in q.get("pages", []) if isinstance(p, dict)}
        for t in titoli:
            vero = t
            for _ in range(3):
                vero = rinvii.get(vero, vero)
            p = per_titolo.get(vero) or {}
            uscita[t] = {"disambigua": "disambiguation" in (p.get("pageprops") or {}),
                         "categorie": [str(c.get("title", "")).split(":", 1)[-1] for c in p.get("categories") or []],
                         "verso": {x.get("title") for x in p.get("links") or []}, "titolo": vero}
        return uscita

    def scegli_voce(self, nome: str, lingue: list[str], contesto: str = "", vicini: list[tuple[str, str]] = (),
                    parole_ricerca: str = "", accetta: Callable[[Voce], bool] | None = None,
                    spareggio: Callable[[str, list[tuple[str, str]]], int | None] | None = None) -> Voce | None:
        """La voce del nome, decisa dal grafo (TAGME, Milne-Witten, GLOW adattati: poche entità di contesto, niente
        link entranti). Per ogni voce candidata (`candidati_voce`, grado di `corrisponde`):
        - grafo: collega una voce vicina (+1), è collegata da una voce vicina o nominata nel suo testo (+1);
        - categorie: una sua categoria ha una parola del contesto (nazionalità, secoli, nati/morti esclusi).
        Qualcuna ha grafo > 0: si tengono quelle entro il 70% della migliore (ε = 30%), vince la priorità del grado
        (parentesi giustificata, esatto, variante, base), poi grafo, categorie, ordine. Nessuna: vince un titolo
        esatto/variante o una parentesi giustificata, o una voce non «base» con le categorie; `accetta` decide su
        queste. Pareggio tra gradi deboli → `spareggio` (lista chiusa, numero o nessuna). Altrimenti nessuna voce:
        meglio niente di un omonimo. `vicini` = voci (lingua, titolo) dell'entità di prima."""
        voci_vicine = [v for lg, t in vicini if (v := self.voce(t, lg)) is not None]
        radici = {p[:5] for p in _piano(contesto).split() if len(p) >= 5 and p not in LEGAMI}
        radici -= {p[:5] for p in _piano(nome).split()}  # il nome stesso non prova niente (Freedo, 22/10)
        for lingua in lingue:
            candidati = self.candidati_voce(nome, lingua, contesto, parole_ricerca)
            if not candidati:
                continue
            titoli_vicini = [v.titolo for v in voci_vicine if v.lingua == lingua]
            titoli_vicini += [v.lingue[lingua] for v in voci_vicine if v.lingua != lingua and lingua in v.lingue]
            titoli_vicini = list(dict.fromkeys(titoli_vicini))
            info = self.info_voci([t for t, _ in candidati], lingua, titoli_vicini)
            collegati_da = {_piano(t) for v in voci_vicine if v.lingua == lingua for r in v.righe
                            for t in r.collegamenti}
            testi_vicini = " ".join(_piano(r.testo) for v in voci_vicine if v.lingua == lingua for r in v.righe)
            # righe con collegamenti: lì chi scriveva collegava le voci che esistono
            testi_collegati = " ".join(_piano(r.testo) for v in voci_vicine if v.lingua == lingua for r in v.righe
                                       if r.collegamenti)
            punteggi = []
            for posto, (titolo, grado) in enumerate(candidati):
                x = info.get(titolo) or {}
                vero = x.get("titolo") or titolo
                if x.get("disambigua") or NON_ENTITA_TITOLO.search(vero) \
                        or any(NON_ENTITA_CATEGORIA.match(c) for c in x.get("categorie", [])):
                    continue
                grafo = 1.0 if x.get("verso") else 0.0
                base = _piano(re.sub(r"\s*\([^)]*\)\s*$", "", vero))
                nominata = len(base) >= 6 and bool(re.search(rf"\b{re.escape(base)}\b", testi_vicini))
                slegata = False
                if _piano(vero) in collegati_da or _piano(titolo) in collegati_da:
                    grafo += 1.0
                elif nominata and self.menzione == "favore":
                    grafo += 1.0
                elif nominata and self.menzione == "contro":
                    # Nominata senza collegamento in una riga che collega altre voci: quella voce è di un omonimo
                    # (Roland Unger nella voce tedesca di Wikivoyage); in una riga senza collegamenti non dice niente
                    # (24/10, oracolo: «per gli editori Adelphi e Sellerio» e la voce giusta «Adelphi»).
                    slegata = bool(re.search(rf"\b{re.escape(base)}\b", testi_collegati))
                parole_cat = {p[:5] for c in x.get("categorie", []) for p in _piano(c).split()} - CATEGORIE_GENERICHE
                categorie = 1.0 if radici & parole_cat else 0.0
                punteggi.append({"titolo": vero, "grado": grado, "grafo": grafo, "categorie": categorie,
                                 "posto": posto, "slegata": slegata})
            if not punteggi:
                continue
            migliore = max(p["grafo"] for p in punteggi)
            if migliore > 0:
                # Collegamenti nel grafo (TAGME: entro il 30% del più collegato, poi il più «comune»).
                scelti = [p for p in punteggi if p["grafo"] >= 0.7 * migliore]
                ordine = lambda p: (-PRIORITA[p["grado"]], -p["grafo"], -p["categorie"], p["posto"])  # noqa: E731
            else:
                # Niente collegamenti: un titolo esatto vince (la voce principale di quel nome); altrimenti servono le
                # categorie con una parola del contesto («Tux (mascotte)» per una domanda su una mascotte).
                # (un «base», cognome o parentesi non giustificata, non basta con le sole categorie: 23/10, «Arduino»
                # → «Gino Arduino», produttore discografico, per «produce» nella domanda)
                scelti = [p for p in punteggi if not p["slegata"] and (
                    PRIORITA[p["grado"]] >= PRIORITA["variante"] or (p["categorie"] > 0 and p["grado"] != "base"))]
                ordine = lambda p: (-PRIORITA[p["grado"]], -p["categorie"], p["posto"])  # noqa: E731
            if not scelti:
                continue
            scelti.sort(key=ordine)
            primo = scelti[0]
            pari = [p for p in scelti if ordine(p)[:-1] == ordine(primo)[:-1]]
            if len(pari) > 1 and GRADI[primo["grado"]] < GRADI["variante"] and spareggio is not None:
                scelta = spareggio(nome, [(p["titolo"], self._inizio_breve(p["titolo"], lingua)) for p in pari])
                if scelta is None:
                    return None
                primo = pari[min(max(scelta, 0), len(pari) - 1)]
            voce = self.voce(primo["titolo"], lingua)
            if voce is None or non_entita(voce):
                continue
            if migliore == 0 and accetta is not None and not accetta(voce):
                continue
            log.info("wikipedia: «%s» → %s (%s, grafo %.0f, categorie %.0f)", nome, voce.titolo, primo["grado"],
                     primo["grafo"], primo["categorie"])
            return voce
        return None

    def _inizio_breve(self, titolo: str, lingua: str, parole: int = 40) -> str:
        """Scheda del candidato per lo spareggio (BLINK: titolo + inizio della voce)."""
        testo = self.inizi([titolo], lingua).get(titolo, "")
        return " ".join(testo.split()[:parole])


def _norma(testo: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(testo or "").lower()).split())


VUOTE = {"del", "della", "delle", "dei", "degli", "the", "von", "van", "des", "der", "die", "and"}

# Parole di legame ignorate nel confronto nome/titolo (non articoli iniziali: quelli si tolgono a parte).
LEGAMI = {"di", "da", "de", "del", "della", "dello", "dei", "degli", "delle", "d", "of", "the", "von", "van", "der",
          "des", "du", "la", "le", "il", "lo", "l", "e", "and", "y", "et", "und", "a", "al", "in"}
ARTICOLI = {"il", "lo", "la", "i", "gli", "le", "l", "the", "a", "an", "el", "los", "las", "les", "der", "die", "das"}
ROMANI = re.compile(r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
GRADI = {"esatto": 3.0, "variante": 2.5, "parentesi": 2.0, "base": 1.0, "": 0.0}
# Per scegliere tra le voci di un nome: la parentesi giustificata dal contesto è più precisa del titolo esatto (che è
# solo il significato principale: «Haiku» poesia, «Haiku (sistema operativo)» se la domanda parla di sistemi).
PRIORITA = {"parentesi": 4.0, "esatto": 3.0, "variante": 2.5, "base": 1.0, "": 0.0}
# Parole delle categorie troppo generiche per dire qualcosa sull'entità (nazionalità, secoli, nati/morti).
CATEGORIE_GENERICHE = {"itali", "ameri", "stati", "uniti", "franc", "ingle", "engli", "briti", "germa", "tedes",
                       "spagn", "europ", "secol", "centu", "nati", "morti", "birth", "death", "perso", "peopl",
                       "anni", "years", "citta", "citie"}


def _piano(testo: str) -> str:
    """Minuscole, senza accenti né punteggiatura (graphify: NFKC + casefold; qui anche gli accenti)."""
    testo = unicodedata.normalize("NFKD", str(testo or "")).casefold()
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", testo).split())


def _senza_accenti(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", str(testo or ""))
    return "".join(c for c in testo if not unicodedata.combining(c))


# Forme societarie in fondo al nome: non distinguono l'entità (29/10, Raspberry Pi: «Premier Farnell Ltd.» senza voce e
# allargata come entità diversa da «Premier Farnell»).
SUFFISSI_SOCIETARI = {"ltd", "limited", "plc", "inc", "incorporated", "llc", "gmbh", "spa", "srl", "corp", "corporation",
                      "co", "ag", "sa", "nv", "bv", "sas", "sarl", "kg", "ab", "oy", "as", "asa", "pty", "lp", "llp"}


def senza_suffisso_societario(testo: str) -> str:
    """Il nome senza le forme societarie finali («Premier Farnell Ltd.» → «Premier Farnell»; «S.p.A.» compresa)."""
    parole = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z]\.)", r"\1", str(testo)).split()
    while len(parole) > 1 and re.sub(r"[\W_]+", "", _piano(parole[-1])) in SUFFISSI_SOCIETARI:
        parole = parole[:-1]
    return " ".join(parole).rstrip(" ,")


def _parole_nome(testo: str) -> list[str]:
    parole = _piano(senza_suffisso_societario(testo)).split()
    while len(parole) > 1 and parole[0] in ARTICOLI:
        parole = parole[1:]
    return [p for p in parole if p not in LEGAMI] or parole


def _numerica(parola: str) -> bool:
    return parola.isdigit() or bool(ROMANI.match(parola))


def _stessa_parola(a: str, b: str) -> bool:
    """Uguali, o varianti della stessa parola (Bettinus/Bettini, Federale/Federal, Guisa/Guise). Numeri e numeri
    romani solo uguali (Pio II ≠ Pio III; graphify: «numeric tokens differ» blocca sempre)."""
    if a == b:
        return True
    if _numerica(a) or _numerica(b):
        return False
    if min(len(a), len(b)) >= 6:
        return a[:5] == b[:5] and abs(len(a) - len(b)) <= 3
    # parole corte: solo un refuso di una lettera, a parità di lunghezza (graphify: niente inserzioni sulle corte)
    return len(a) == len(b) >= 4 and sum(x != y for x, y in zip(a, b)) == 1


def _stesse_parole(a: list[str], b: list[str]) -> bool:
    """Stesse parole in ordine diverso («Jugoslavia Democratica Federale» / «Jugoslavia Federale Democratica»)."""
    resto = list(b)
    for x in a:
        y = next((y for y in resto if _stessa_parola(x, y)), None)
        if y is None:
            return False
        resto.remove(y)
    return True


def _contenuto(nome: str, titolo: str) -> bool:
    """Le parole di un nome stanno tutte nell'altro (titolo senza parentesi)."""
    a = _parole_nome(nome)
    b = _parole_nome(re.sub(r"\s*\([^)]*\)\s*$", "", titolo))
    if not a or not b:
        return False
    corto, lungo = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(_stessa_parola(x, y) for y in lungo) for x in corto)


def corrisponde(nome: str, titolo: str, contesto: str = "") -> str:
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
    if not nome or not titolo:
        return ""
    if _piano(nome) == _piano(titolo):
        # maiuscole diverse oltre la prima lettera: «Tex» (fumetto) non è «TeX» (23/10, oracolo)
        a, b = _senza_accenti(nome).strip(), _senza_accenti(titolo).strip()
        return "esatto" if a[1:] == b[1:] or a.casefold() == a or b.casefold() == b else "variante"
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", titolo)
    base, parentesi = (m.group(1), m.group(2)) if m else (titolo, "")
    a, b = _parole_nome(nome), _parole_nome(base)
    if not a or not b:
        return ""
    if len(a) == len(b) and (all(_stessa_parola(x, y) for x, y in zip(a, b)) or _stesse_parole(a, b)):
        if a != b and len("".join(a)) < 12 and not (
                len("".join(a)) == len("".join(b)) and sum(x != y for x, y in zip("".join(a), "".join(b))) <= 1):
            return ""  # nomi corti: solo un refuso di una lettera (graphify; 23/10, «Freedo» ≠ «Freedom»)
        if not parentesi:
            return "esatto" if a == b else "variante"
        # radici di 6 lettere: «astronomo» non giustifica «(astrologia)» (Plutone)
        radici = {p[:6] for p in _piano(contesto).split() if len(p) >= 4}
        if radici & {p[:6] for p in _piano(parentesi).split() if len(p) >= 4 and p not in LEGAMI}:
            return "parentesi"
        return "base"
    # Il nome è il cognome (o la fine) del titolo: «Fermi» → «Enrico Fermi»; mai il contrario (titolo più corto).
    # Solo un nome di una parola: «Traforo del Monte Bianco» non è «Monte Bianco» (23/10, oracolo).
    if not parentesi and len(a) == 1 < len(b) and len(a[0]) >= 4 and _stessa_parola(a[0], b[-1]):
        return "base"
    return ""


# Voci che non sono un'entità: prenomi, cognomi, elenchi (YAGO: titoli «List of», «(disambiguation)» → NIL).
NON_ENTITA_TITOLO = re.compile(r"\((nome|cognome|prenome|name|given name|surname|nome proprio|disambigua|"
                               r"disambiguation)\)$|^(lista|elenco|list|lists|index|outline|cronologia) (di|of|dei|"
                               r"delle|degli)\b", re.I)
NON_ENTITA_CATEGORIA = re.compile(r"^(pagine di disambiguazione|prenomi|cognomi|nomi propri|given names|surnames|"
                                  r"disambiguation pages)\b|(given names|surnames|family names)$", re.I)


def non_entita(voce: Voce) -> bool:
    return (_disambigua(voce) or bool(NON_ENTITA_TITOLO.search(voce.titolo))
            or any(NON_ENTITA_CATEGORIA.match(c) for c in voce.categorie))


def _disambigua(voce: Voce) -> bool:
    if voce.disambigua:
        return True
    inizio = voce.inizio.lower()
    return ("può riferirsi a" in inizio or "may refer to" in inizio or "disambigua" in voce.titolo.lower()
            or "può indicare" in inizio or "puede referirse" in inizio or "peut désigner" in inizio
            or "steht für" in inizio)
