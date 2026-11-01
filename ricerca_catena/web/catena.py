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

from ..modello.pensatore import istruzioni
from ..prove.triple import citazione_presente

from .fonti.wikipedia_grafo import (GRADI, LINGUA, Riga, Voce, WikipediaGrafo, _piano, corrisponde,
                                    senza_suffisso_societario)

PROVE_MASSIME = 80
log = logging.getLogger(__name__)
PAROLE_VUOTE = {"della", "delle", "dello", "degli", "dalla", "nella", "sulla", "come", "anche", "questo", "quella",
                "quello", "sono", "essere", "stato", "stata", "with", "from", "that", "which", "their", "about",
                "entità", "relazione", "ricerca", "anni"}
CARATTERI_PROVE = 45_000
# Passi verso cose che di solito non hanno un nome proprio né una pagina: si possono saltare (`_piano_salto`).
OGGETTI_SENZA_NOME = re.compile(r"\b(simulacr\w*|statu[ae]\w*|dipint\w*|scultur\w*|affresc\w*|oggett\w*|"
                                r"immagin\w*|ritratt\w*|reliqui\w*|statue|painting|sculpture|artwork|object)\b")
# Radici (5 lettere) di parole di tipo o di misura, che nei controlli non distinguono una riga dall'altra.
RADICI_GENERICHE = {"paese", "comun", "citta", "picco", "grand", "celeb", "famos", "local", "terri", "luogo", "perso",
                    "azien", "organ", "socie", "compa", "town", "villa", "city", "small", "large", "frazi", "borgo",
                    "stato", "regio", "nazio", "itali", "franc", "ameri", "tedes", "ingle", "inter", "milan"}
# Righe del riquadro senza relazioni utili (ToG `abandon_rels`: ID, codici, siti, immagini).
RIQUADRO_INUTILE = re.compile(r"^(immagine|image|logo|firma|signature|mappa|map|coordinate|coordinates|sito( web)?|"
                              r"website|url|codice|code|id|isbn|issn|iso|didascalia|caption|stemma|bandiera|flag|"
                              r"coat of arms|cap|prefisso|fuso orario|time zone|targa|zip code|postal code|"
                              r"area code|dialing code|codice istat|codice catastale|nome abitanti)\b[^:]{0,20}:", re.I)
CANDIDATI_MASSIMI = 8
# Verifica a quattro valori con pesi (WisPaper, 2026): ogni esito vale tanto quanto il peso del controllo; il conto lo
# fa il programma. I modelli generici dicono "sì" troppo facilmente e sbagliano proprio sui casi a metà, per questo
# c'è "in parte" e per questo un "sì" vale solo se le due letture lo danno entrambe.
VALORE_ESITO = {"sì": 1.0, "in parte": 0.5, "non so": 0.0, "no": -1.0}
MESI = ("gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|january|"
        "february|march|april|may|june|july|august|september|october|november|december")


@dataclass
class Prova:
    testo: str
    url: str
    collegamenti: list[str] = field(default_factory=list)  # voci di Wikipedia collegate nella riga
    lingua: str = ""  # lingua della voce di Wikipedia da cui viene la riga


@dataclass
class Anello:
    nome: str
    punti: int = 0  # numero di controlli con "sì" meno quelli con "no"
    qualita: float = 0.0  # somma pesata degli esiti diviso la somma dei pesi, da -1 a 1
    esiti: list[dict] = field(default_factory=list)  # {controllo, esito, prova: Prova | None}
    da: "Anello | None" = None
    prova_legame: Prova | None = None  # la frase che lo lega all'anello precedente
    debole: bool = False  # tenuto solo perché nessun altro candidato passava i controlli
    contesto: str = ""  # come la catena lo ha raggiunto, per le ricerche senza omonimi ("progettista di Freedo")
    titolo: tuple[str, str] | None = None  # (lingua, titolo) della voce di Wikipedia, se raggiunto da un collegamento
    voci: dict[str, Voce] = field(default_factory=dict)  # lingua → voce di Wikipedia dell'entità
    voci_cercate: bool = False
    voci_altre: int = -1  # quante altre lingue sono già state cercate oltre alla prima voce
    inizio: "Prova | None" = None  # prime frasi della voce di Wikipedia (verifica economica)
    fallimenti: float = 0.0  # frontiera: rami che da qui non hanno portato a niente
    preferito: float = 0.0  # frontiera: premio dello spareggio finale tra candidati pari
    vuoto_wiki: bool = False  # allargato su Wikipedia senza nessun candidato (il web è l'unica strada)
    passo_base: int = 0  # ancora: quanti passi del piano porta già fatti (0 per la partenza)
    relazione: str = ""  # la relazione seguita per arrivare qui (per l'ancora, quella che parte da lei)
    piano: dict | None = None  # radici della frontiera: il piano di cui seguono i passi


def _norma(testo: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(testo or "").lower()).split())


def _chiave_entita(nome: str) -> str:
    """Una chiave per le varianti dello stesso nome: minuscole, senza accenti, spazi né punteggiatura («Keyboard.io» =
    «Keyboardio»; graphify: normalizzazione prima di tutto). «Keyboard.io Model01» resta un'altra entità."""
    return re.sub(r"[\W_]+", "", _piano(senza_suffisso_societario(nome)))


def _chiave_nome(nome: str) -> str:
    """L'ultima parola lunga del nome (di solito il cognome), per ritrovarlo nei testi anche scritto in altro modo.
    Senza le parentesi (24/10, Monte Bianco: «La Thuile (Italia)» dava «italia», e la voce presa era «La Salle
    (Italia)», primo collegamento della riga con «italia»)."""
    senza = _norma(re.sub(r"\([^)]*\)", " ", nome)) or _norma(nome)
    parole = [p for p in senza.split() if len(p) >= 4]
    return parole[-1] if parole else senza


def _peso(e: dict) -> float:
    return float(e.get("peso") or 1)


def qualita(esiti: list[dict]) -> float:
    """Somma pesata degli esiti diviso la somma dei pesi: 1 = tutti i controlli provati, -1 = tutti smentiti."""
    totale = sum(_peso(e) for e in esiti)
    return sum(_peso(e) * VALORE_ESITO.get(e["esito"], 0.0) for e in esiti) / totale if totale else 0.0


def _numeri(testo: str) -> list[float]:
    """I numeri di un valore in tutte le letture possibili (20,65 / 3.274 / 1,234.5): basta che una rispetti
    l'intervallo."""
    trovati = []
    for m in re.finditer(r"\d(?:[\d.,\u202f\u00a0 ]*\d)?", str(testo)):
        t = re.sub(r"[\u202f\u00a0 ]", "", m.group(0))
        for x in {t.replace(".", "").replace(",", "."), t.replace(",", ""), t.replace(",", ".")}:
            try:
                trovati.append(float(x))
            except ValueError:
                pass
    return trovati


def valore_ammesso(valore: str, risposta: dict) -> str:
    """Controllo meccanico del valore (DS4A: il tipo di dato decide quali righe valgono): tipo e intervallo dati dal
    piano. Restituisce il motivo del rifiuto, vuoto se va bene. (17/10, Due Sicilie: «29 novembre 1943» per uno Stato
    «nato e sciolto nel 1945»; Divina Commedia: 3.274 abitanti per «tra 300 e 350».)"""
    tipo = str(risposta.get("tipo_valore") or "")
    testo = str(valore or "").lower()
    anni = [int(x) for x in re.findall(r"\b(1\d{3}|20\d{2})\b", testo)]
    giorno_mese = re.search(rf"\b\d{{1,2}}(?:°|º)?\s+(?:{MESI})\b", testo) or re.search(
        rf"\b(?:{MESI})\s+\d{{1,2}}\b", testo) or re.search(r"\b\d{4}-\d{2}-\d{2}\b", testo)
    if tipo == "data completa" and not (giorno_mese and anni):
        return "non è una data completa"
    if tipo == "anno" and not anni:
        return "non contiene un anno"
    if tipo == "giorno e mese" and not giorno_mese:
        return "non contiene giorno e mese"
    if tipo in ("numero", "codice") and not re.search(r"\d", testo):
        return "non contiene un numero"
    minimo, massimo = risposta.get("minimo"), risposta.get("massimo")
    if not isinstance(minimo, (int, float)) and not isinstance(massimo, (int, float)):
        return ""
    minimo = float(minimo) if isinstance(minimo, (int, float)) else float("-inf")
    massimo = float(massimo) if isinstance(massimo, (int, float)) else float("inf")
    if minimo > massimo:
        minimo, massimo = massimo, minimo
    numeri = [float(x) for x in anni] if tipo in ("data completa", "anno") else _numeri(testo)
    if tipo in ("data completa", "anno", "numero", "codice") and numeri \
            and not any(minimo <= x <= massimo for x in numeri):
        return f"fuori dall'intervallo {minimo:g}-{massimo:g}"
    return ""


class Catena:
    def __init__(self, ricerca) -> None:
        self.r = ricerca
        self.o = ricerca.o
        self.interpretazione: dict = {}
        self.piano: dict = {}
        self.anelli: list[list[Anello]] = []  # fascio tenuto a ogni passo
        self.valore: dict | None = None  # {valore, prova: Prova, anello: Anello}
        self.motivo = ""
        self.ricerche_passo: list[str] = []
        self.storia: list[dict] = []  # per il resoconto e le diagnosi
        # Wikipedia come grafo (16/10): voci = nodi, collegamenti = archi; prima di cercare sul web.
        self.wiki = WikipediaGrafo(consenti_locali=ricerca.consenti_locali,
                                   massimo_voci=int(self.o.get("voci_wikipedia", 600)),
                                   intervallo_s=self.o.get("wikipedia_intervallo")) \
            if self.o.get("wikipedia_grafo", True) else None
        self._numeri_segmento: dict[str, int] = {}
        self._vivi: list[Anello] = []  # candidati non smentiti dell'ultimo passo percorso
        self.azioni: list[str] = []  # frontiera: azioni fatte, in ordine (per le diagnosi)
        self._registrate: set[tuple[str, str, str]] = set()  # legami scritti da _registra: mai prove
        self._estratti: list[Prova] = []  # estratti dei risultati dell'ultima ricerca (pagine non lette)

    # --- chiamate al modello ------------------------------------------------------------------------------------

    def _decidi(self, punto: str, dati: dict, predefinito: dict) -> dict:
        esito, da = self.r.pensatore.decidi(punto, dati, lambda: predefinito)
        self.r.costo = max(self.r.costo, self.r.pensatore.bilancio.speso)
        return esito if da == "claude" else predefinito

    # --- 1. interpretazione e catena ---------------------------------------------------------------------------

    def prepara(self) -> bool:
        self.r._fase("Claude interpreta la domanda…")
        self.interpretazione = self._decidi("interpreta", {"domanda": self.r.argomento[:1000], "oggi": self.r.oggi},
                                            {"riformulazioni": [], "indizi": [], "sotto_domande": []})
        self.r._fase("Claude prepara la catena da percorrere…")
        for _ in range(2):  # 25/10, LibreOffice: «Claude non ha risposto in tempo» sul piano, catena mai partita
            self.piano = self._decidi("catena", {"domanda": self.r.argomento[:1000], "oggi": self.r.oggi,
                                                 "interpretazione": self.interpretazione},
                                      {"partenza": {}, "passi": [], "risposta": {}, "motivo": ""})
            ok = bool(self.piano.get("passi")) and bool((self.piano.get("partenza") or {}).get("entita"))
            if ok or self.r.pensatore.esaurito:
                break
        self.r.pubblica({"tipo": "ricerca_catena", "interpretazione": self.interpretazione, "piano": self.piano})
        return ok

    # --- prove dal grafo ---------------------------------------------------------------------------------------

    def _segmenti_dopo(self, ultimo: int) -> list[dict]:
        return [dict(r) for r in self.r.concetti.grafo.db.execute(
            "SELECT s.id, s.testo, f.url FROM segmento s JOIN fonte f ON f.id = s.fonte_id WHERE s.id > ? "
            "ORDER BY s.id", (ultimo,))]

    def _ultimo_segmento(self) -> int:
        riga = self.r.concetti.grafo.db.execute("SELECT MAX(id) FROM segmento").fetchone()
        return int(riga[0] or 0)

    def _trova(self, nome: str) -> int | None:
        """Il nodo del grafo per un nome, anche scritto in modo un po' diverso (cognome contenuto nel nome)."""
        grafo = self.r.concetti.grafo
        trovato = grafo.trova(nome)
        if trovato is not None:
            return trovato
        chiave = _chiave_nome(nome)
        if len(chiave) < 4:
            return None
        for riga in grafo.db.execute("SELECT id, nome FROM entita"):
            if chiave in _norma(riga["nome"]).split():
                return int(riga["id"])
        return None

    def _finestre(self, testo: str, chiave: str, raggio: int = 450, massimo: int = 3) -> list[str]:
        basso, finestre, inizio = testo.lower(), [], 0
        while len(finestre) < massimo:
            i = basso.find(chiave, inizio)
            if i < 0:
                break
            a, b = max(0, i - raggio), min(len(testo), i + len(chiave) + raggio)
            finestre.append(("…" if a else "") + " ".join(testo[a:b].split()) + ("…" if b < len(testo) else ""))
            inizio = b
        return finestre

    def _prove_entita(self, nome: str, segmenti: list[dict] | None = None, massimo: int = 30) -> list[Prova]:
        """Fatti, legami e menzioni del nodo nel grafo, più le zone dei pezzi di pagina in cui compare il nome."""
        prove: list[Prova] = []
        c = self.r.concetti
        nodo = self._trova(nome)
        if nodo is not None:
            for f in c.fatti():
                if f["concetto_id"] == nodo:
                    prove.append(Prova(f"{f['concetto']} — {f['attributo']}: {f['valore']} «{f['frase']}»", f["url"]))
            url_di = {r["id"]: r["url"] for r in c.grafo.db.execute(
                "SELECT s.id, f.url FROM segmento s JOIN fonte f ON f.id = s.fonte_id")}
            for t in c.legami():
                # Mai i legami scritti dalla catena stessa: sono conclusioni, non prove (24/10, Monte Bianco: il primo
                # piano registra «Saint-Gervais → gemellata con → Vado Ligure», anello debole citato con l'inizio della
                # voce di Vado Ligure; il secondo piano lo legge come prova e Vado Ligure diventa «forte»).
                if (_norma(t["soggetto"]), _norma(t["relazione"]), _norma(t["oggetto"])) in self._registrate:
                    continue
                if nodo in (t["soggetto_id"], t["oggetto_id"]) and t.get("citazione"):
                    prove.append(Prova(f"{t['soggetto']} → {t['relazione']} → {t['oggetto']} «{t['citazione']}»",
                                       url_di.get(t["segmento_id"], "")))
            for d in c.dove(nodo)[:10]:
                prove.append(Prova(f"«{d['frase']}»", d["url"]))
        chiave = _chiave_nome(nome)
        tutti = segmenti if segmenti is not None else self._segmenti_dopo(0)
        for s in tutti:
            for finestra in self._finestre(s["testo"], chiave, massimo=2):
                prove.append(Prova(finestra, s["url"]))
        viste, uniche = set(), []
        for p in prove:
            if p.testo not in viste:
                viste.add(p.testo)
                uniche.append(p)
        return uniche[:massimo]

    def _prove_passo(self, entita: str, segmenti: list[dict], gia: list[dict]) -> list[Prova]:
        """Per elencare i candidati: per intero i pezzi delle pagine trovate dalle ricerche di QUESTO passo (sono
        state cercate per la relazione: elenchi di allievi, schede), quelli già salvati solo se la pagina è dedicata
        all'entità (nome nell'indirizzo), gli altri intorno al nome; più fatti e legami del nodo.
        (15/10: la pagina della Mathematics Genealogy di Galileo era letta ma l'elenco degli allievi restava fuori.)"""
        chiave = _chiave_nome(entita)
        prove = self._prove_entita(entita, segmenti=[], massimo=25)
        for s in segmenti:
            prove.append(Prova(" ".join(s["testo"].split())[:4000], s["url"]))
        nuovi = {s["id"] for s in segmenti}
        for s in gia:
            if s["id"] in nuovi:
                continue
            if chiave in _norma(unquote(urlsplit(s["url"]).path)).replace("_", " "):
                prove.append(Prova(" ".join(s["testo"].split())[:4000], s["url"]))
            else:
                prove += [Prova(f, s["url"]) for f in self._finestre(s["testo"], chiave, raggio=600)]
        tenute, caratteri = [], 0
        for p in prove[:PROVE_MASSIME]:
            if caratteri + len(p.testo) > CARATTERI_PROVE:
                continue
            tenute.append(p)
            caratteri += len(p.testo)
        return tenute

    @staticmethod
    def _numera(prove: list[Prova]) -> str:
        return "\n".join(f"[{i}] {p.testo[:4000]}" for i, p in enumerate(prove, start=1))

    @staticmethod
    def _prova(prove: list[Prova], numero: int) -> Prova | None:
        return prove[numero - 1] if isinstance(numero, int) and 1 <= numero <= len(prove) else None

    # --- ricerca e lettura di un gruppo di pagine --------------------------------------------------------------

    def _cerca_e_leggi(self, ricerche: list[str], pagine: int, scopo: str = "") -> list[dict]:
        """Cerca, legge fino a `pagine` pagine trovate da QUESTE ricerche e restituisce i pezzi salvati nuovi. `scopo`:
        il passo per cui si cerca, per scegliere le pagine (default: la domanda intera)."""
        self._estratti = []
        ricerche = [q for q in self.r._filtra_query(ricerche) if q]
        if not ricerche:
            return []
        ultimo = self._ultimo_segmento()
        self.r.ricerche_fatte += ricerche
        self.ricerche_passo += ricerche
        self.r.pubblica({"tipo": "ricerca_piano", "ricerche": ricerche, "da": "catena"})
        risultati = self.r.raccogli(ricerche)
        # Gli estratti dei risultati sono testo delle pagine e costano zero letture: prove in più per i candidati
        # (25/10: si leggono 3 pagine su ~25 risultati; «Keyboardio was founded in 2012 by Jesse Vincent and Kaia
        # Dekker» stava nell'estratto di una pagina mai letta).
        self._estratti = [Prova(f"[risultato di ricerca: {x.get('titolo', '')}] {x['estratto']}", x["url"])
                          for x in risultati if len(x.get("estratto") or "") >= 40][:15]
        self.r._aggiungi_candidati(risultati, scopo=scopo)
        mie = [x for x in self.r.coda if self.r.ricerca_di.get(x.get("url")) in ricerche]
        self.r.coda = [x for x in self.r.coda if x not in mie]
        for fonte in mie[:pagine]:
            if self.r._limite_raggiunto():
                break
            self.r.leggi(fonte)
        self.r._valuta_completati(attendi=True)
        return self._segmenti_dopo(ultimo)

    # --- Wikipedia come grafo -----------------------------------------------------------------------------------

    def _lingua_ricerca(self) -> str:
        """La lingua della ricerca (22/10, Niccolò): inglese, salvo domande legate a un paese (la Cappella Sistina in
        italiano, un villaggio greco in greco), come deciso nel piano."""
        lingua = str(self.piano.get("lingua_ricerca") or "en").lower()
        return lingua if LINGUA.match(lingua) else "en"

    def _lingue(self) -> list[str]:
        """Lingua della ricerca, inglese, lingue del piano e sempre l'italiano, ultimo (24/10, LibreOffice: con la
        ricerca in inglese la voce italiana di BeOS, quella con «Release corrente: R5.1d0 (15 novembre 2001)», non
        veniva mai letta; le domande sono in italiano e i riquadri italiani spesso hanno il dato chiesto)."""
        altre = [str(x).lower() for x in self.piano.get("lingue") or [] if LINGUA.match(str(x).lower())]
        return list(dict.fromkeys([self._lingua_ricerca(), "en", *altre, "it"]))[:5]

    @staticmethod
    def _parole_contesto(testo: str) -> list[str]:
        return [p for p in _norma(testo).split() if len(p) >= 5 and p not in PAROLE_VUOTE][:8]

    def _pertinente(self, voce: Voce, a: Anello) -> bool:
        """Contro gli omonimi: la voce di un'entità raggiunta dalla catena deve collegare la voce dell'entità di prima
        (in quella lingua) o nominarla per intero; se l'entità di prima non ha voci, basta il cognome o una parola
        del contesto. (16/10: «René II de Lorraine-Guise» portava al duca di Lorena nato ad Angers, non a René
        d'Elbeuf figlio di Claudio di Guisa; 15/10: «Freedo» al produttore svizzero, non alla mascotte.)"""
        if a.titolo is not None:
            return True
        if a.da is None:  # partenza: la voce deve condividere almeno una parola dell'indizio (Freedo: mascotte)
            radici = {p[:4] for p in _norma(a.contesto).split() if len(p) >= 5 and p not in PAROLE_VUOTE}
            radici |= {p[:4].lower() for p in re.findall(r"\b[A-Z][\w-]{2,}", a.contesto)}
            # Il nome stesso non conta: compare in ogni omonimo (22/10, Freedo: la voce inglese del produttore
            # svedese passava perché conteneva «Freedo»).
            radici -= {p[:4] for p in _norma(a.nome).split()}
            if not radici:
                return True
            testo = {p[:4] for p in _norma(" ".join(r.testo for r in voce.righe[:60])).split()}
            return bool(radici & testo)
        prima = a.da.voci
        if prima:
            titoli = {v.titolo for v in prima.values() if v.lingua == voce.lingua}
            titoli |= {t for v in prima.values() for lg, t in v.lingue.items() if lg == voce.lingua}
            if any(t in titoli for r in voce.righe for t in r.collegamenti):
                return True
            testo = _norma(" ".join(r.testo for r in voce.righe))
            return bool(_norma(a.da.nome)) and _norma(a.da.nome) in testo
        testo = _norma(" ".join(r.testo for r in voce.righe))
        chiavi = {_chiave_nome(a.da.nome), *self._parole_contesto(a.contesto)}
        return any(k and k in testo for k in chiavi)

    def _voci(self, a: Anello, altre: int = 2) -> list[Voce]:
        """Le voci dell'entità: la prima trovata (italiano, inglese, lingue del piano) e fino ad `altre` in altre
        lingue, prima quelle del piano (la voce nella lingua del luogo ha più dettagli locali). Poche richieste:
        Wikipedia ne concede 10 al minuto."""
        if self.wiki is None:
            return []
        # Una voce letta per la verifica (una lingua) non basta per il valore: le altre lingue si aggiungono dopo
        # (18/10, Debian: il W3C in italiano dà solo «ottobre 1994», in inglese la data completa non è mai arrivata).
        if a.voci_cercate and (altre <= a.voci_altre or not a.voci):
            return list(a.voci.values())
        if a.voci_cercate:
            prima = next(iter(a.voci.values()))
            a.voci_altre = altre
            for lingua in self._lingue()[2:] + self._lingue()[:2]:
                if len(a.voci) > altre:
                    break
                if lingua not in a.voci and lingua in prima.lingue:
                    voce = self.wiki.voce(prima.lingue[lingua], lingua)
                    if voce is not None:
                        a.voci[lingua] = voce
            return list(a.voci.values())
        a.voci_cercate = True
        a.voci_altre = altre
        prima = None
        if a.titolo:
            prima = self.wiki.voce(a.titolo[1], a.titolo[0])
        if prima is None:
            # 23/10 (piano entità e righe, fasi 2-3): voce decisa dal grafo, non dal titolo «simile». Per la partenza
            # (nessuna entità di prima) le voci senza legame passano ancora da `_pertinente` (indizio).
            # Un'entità senza voce presta ai figli le voci di chi l'ha trovata (24/10, Wikipedia: «Wikivoyage e.V.»
            # non ha voce; senza vicini il pittore «Roland Unger» (1941) passava come voce del presidente dell'associazione,
            # nominato senza collegamento nella voce tedesca di Wikivoyage).
            ospite = a.da
            while ospite is not None and not ospite.voci and ospite.da is not None and ospite is not a.da.da:
                ospite = ospite.da
            vicini = [(v.lingua, v.titolo) for v in ospite.voci.values()] if ospite is not None else []
            prima = self.wiki.scegli_voce(
                a.nome, self._lingue(), contesto=a.contesto, vicini=vicini,
                parole_ricerca=" ".join(self._parole_contesto(a.contesto)[:3]),
                accetta=(lambda v: self._pertinente(v, a)) if a.da is None else None,
                spareggio=lambda nome, opzioni: self._spareggio_voce(a, opzioni))
        if prima is None:
            return []
        a.voci[prima.lingua] = prima
        ordine = self._lingue()[2:] + self._lingue()[:2]
        for lingua in ordine:
            if len(a.voci) > altre:
                break
            if lingua not in a.voci and lingua in prima.lingue:
                voce = self.wiki.voce(prima.lingue[lingua], lingua)
                if voce is not None:
                    a.voci[lingua] = voce
        return list(a.voci.values())

    def _spareggio_voce(self, a: Anello, opzioni: list[tuple[str, str]]) -> int | None:
        """Due voci con lo stesso legame e lo stesso grado: lista chiusa numerata (GENRE: solo titoli validi o
        «nessuna»; DeepDive: risposta = numero). 0 = nessuna."""
        elenco = "\n".join(f"{i}. {titolo} — {inizio}" for i, (titolo, inizio) in enumerate(opzioni, 1))
        esito = self._decidi("voce", {"nome": a.nome, "contesto": a.contesto[:300],
                                      "entita_di_prima": a.da.nome if a.da is not None else "",
                                      "voci": elenco}, {"scelta": 1, "motivo": ""})
        scelta = int(esito.get("scelta") or 0)
        return None if scelta <= 0 or scelta > len(opzioni) else scelta - 1

    @staticmethod
    def _radici(parole: list[str]) -> set[str]:
        return {p[:6] for x in parole for p in _norma(x).split() if len(p) >= 3 and p not in PAROLE_VUOTE}

    @staticmethod
    def _termini(testo: str) -> list[str]:
        """Radici (5 lettere: «succeduto»/«succeeded», «derivate»/«derivative») e coppie di radici vicine: i termini
        del TF-IDF delle righe."""
        radici = [p[:5] for p in _norma(testo).split() if len(p) >= 3 and p not in PAROLE_VUOTE]
        return radici + [f"{a}_{b}" for a, b in zip(radici, radici[1:])]

    @staticmethod
    def _righe_unite(v: Voce, caratteri: int = 700) -> list[tuple[int, "Riga"]]:
        """Le righe della voce con le voci di un elenco della stessa sezione unite in una riga (fino a 700
        caratteri): «Distribuzioni derivate: Ubuntu · Knoppix · Bharat Linux …» come «Notable students» del
        riquadro; un elenco lungo non riempie da solo le righe date al modello (23/10, Debian → Bharat Linux)."""
        uscita: list[tuple[int, Riga]] = []
        for i, r in enumerate(v.righe):
            if r.tipo == "elenco" and uscita:
                j, prima = uscita[-1]
                if prima.tipo == "elenco" and prima.sezione == r.sezione \
                        and len(prima.testo) + len(r.testo) + 3 <= caratteri:
                    uscita[-1] = (j, Riga(f"{prima.testo} · {r.testo}",
                                          prima.collegamenti + [c for c in r.collegamenti if c not in prima.collegamenti],
                                          "elenco", r.sezione))
                    continue
            uscita.append((i, r))
        return uscita

    def _righe_wiki(self, voci: list[Voce], parole: list[str], chiave: str = "", massimo: int = 20,
                    caratteri: int = 6000, bonus=None, riquadro: bool = True, nudo: bool = False,
                    escludi: str = "") -> list[Prova]:
        """Le righe delle voci più utili per le `parole`. 23/10 (piano entità e righe, fase 4; prima: tutto il
        riquadro e 40 frasi d'inizio sempre dentro, conteggio grezzo, 70 righe: Galileo, 69 righe per 4 utili):
        - punteggio TF-IDF con l'IDF calcolato sulle righe di queste voci (FEVER, `OnlineTfidfDocRanker`), radici e
          coppie di radici; il titolo della sezione conta come la riga;
        - il riquadro compete con le altre righe (bonus piccolo), senza le righe inutili (ToG `abandon_rels`:
          immagini, codici, siti, coordinate); le prime 2 frasi della voce (la definizione) entrano sempre;
        - per la verifica (`riquadro=False`) entra sempre la riga delle categorie (gesuita, nato a…);
        - righe uguali una volta sola. Con `chiave` solo le righe che la contengono (voci su altro).
        Con `nudo` solo le parole contano (niente definizione, categorie, riquadro o collegamenti in più)."""
        domanda = {t for x in parole for t in self._termini(x)}
        if escludi:  # il nome dell'entità sta in tutta la sua voce: non distingue le righe
            nome = set(self._termini(escludi))
            domanda = {t for t in domanda if t not in nome and not any(p in nome for p in t.split("_"))}
        if nudo:  # una riga «di un controllo» deve averne le parole proprie, non «paese» o «comune»
            domanda = {t for t in domanda if t.split("_")[0] not in RADICI_GENERICHE}
        righe = [(v, i, r) for v in voci for i, r in self._righe_unite(v)
                 if not (chiave and chiave not in _norma(r.testo))
                 and not (r.tipo == "riquadro" and RIQUADRO_INUTILE.match(r.testo))]
        termini = [set(self._termini(r.testo)) for _, _, r in righe]
        n = len(righe)
        presenze: dict[str, int] = {}
        for ts in termini:
            for t in ts & domanda:
                presenze[t] = presenze.get(t, 0) + 1
        massimo_idf = math.log(n + 1) + 1

        def peso(t: str) -> float:  # 0..1: raro nelle righe = più informativo
            return (math.log((n + 1) / (presenze.get(t, 0) + 1)) + 1) / massimo_idf * (1.5 if "_" in t else 1.0)

        punteggi, viste = [], set()
        frasi_inizio: dict[str, int] = {}
        riquadri: dict[str, int] = {}
        for (v, i, r), ts in zip(righe, termini):
            chiave_testo = _norma(r.testo)
            if chiave_testo in viste:
                continue
            viste.add(chiave_testo)
            punti = 10 * sum(peso(t) for t in ts & domanda)
            # il titolo della sezione conta come la riga (16/10, Galvani: l'elenco dei presidenti dell'Accademia
            # dei XL, righe con solo nomi e date sotto la sezione «Presidenti»)
            punti += 20 * sum(peso(t) for t in set(self._termini(r.sezione)) & domanda)
            if nudo:
                pass
            elif r.tipo == "riquadro":
                # Il riquadro è denso di relazioni, ma per la verifica passa davanti solo con le parole dei controlli
                # (17/10, Ducato di Milano: «Gemellaggi: Linards» restava fuori dietro 25 righe del riquadro).
                punti = punti * 1.5 + ((3 if riquadro else 1) if punti > 0 else 0)
            elif r.tipo == "categorie":
                punti += 0 if riquadro else 60
            elif not r.sezione and r.tipo == "frase":
                # la definizione: le prime 2 frasi vere (non i pezzi di pronuncia «( gal-VAH-nee;», Galvani)
                if len(r.testo) >= 60:
                    frasi_inizio[v.titolo] = frasi_inizio.get(v.titolo, 0) + 1
                punti += 60 if len(r.testo) >= 60 and frasi_inizio[v.titolo] <= 2 else 2
            elif r.collegamenti:
                punti += 1.0  # righe con voci collegate: riempiono i posti liberi (i candidati sono voci)
            if bonus is not None:
                punti += bonus(r.testo)
            if punti > 0.6:
                punteggi.append((punti, v, i, r))
        # Le 6 righe migliori del riquadro di ogni voce entrano comunque (per i candidati): relazioni come la
        # cittadinanza stanno in «Born: … Como, Duchy of Milan» senza parole in comune (23/10, oracolo righe).
        if riquadro and not nudo:
            punteggi.sort(key=lambda x: -x[0])
            for k, (punti, v, i, r) in enumerate(punteggi):
                if r.tipo == "riquadro":
                    riquadri[v.titolo] = riquadri.get(v.titolo, 0) + 1
                    if riquadri[v.titolo] <= 6:
                        punteggi[k] = (punti + 40, v, i, r)
            for v in voci:
                for i, r in self._righe_unite(v):
                    if r.tipo == "riquadro" and riquadri.get(v.titolo, 0) < 6 and _norma(r.testo) not in viste \
                            and not RIQUADRO_INUTILE.match(r.testo) and not (chiave and chiave not in _norma(r.testo)):
                        riquadri[v.titolo] = riquadri.get(v.titolo, 0) + 1
                        viste.add(_norma(r.testo))
                        punteggi.append((40.0, v, i, r))
        punteggi.sort(key=lambda x: -x[0])
        tenute, totale = [], 0
        for _, v, i, r in punteggi:
            testo = f"[{v.titolo} ({v.lingua}){' § ' + r.sezione if r.sezione else ''}] {r.testo}"
            if r.collegamenti:  # i titoli esatti delle voci collegate: il candidato si nomina come la sua voce
                testo += f" ⟶ voci: {'; '.join(r.collegamenti[:25 if r.tipo == 'elenco' else 10])}"
            if len(tenute) >= massimo:
                break
            if totale + len(testo) > caratteri:
                continue  # una riga troppo lunga non ferma le altre
            tenute.append((v.lingua, v.titolo, i, Prova(testo, v.url, list(r.collegamenti), v.lingua)))
            totale += len(testo)
        tenute.sort(key=lambda x: (x[0], x[1], x[2]))  # in ordine di pagina: si legge come un testo
        return [x[3] for x in tenute]

    def _pagine_che_nominano(self, a: Anello, parole: list[str]) -> list[Prova]:
        """Se l'entità non ha una voce: le righe delle voci che la nominano (HierSearch, «adjacent passages»)."""
        if self.wiki is None:
            return []
        chiave = _chiave_nome(a.nome)
        contesto = " ".join(self._parole_contesto(a.contesto)[:3])
        prove: list[Prova] = []
        for lingua in self._lingue()[:3]:
            for x in self.wiki.cerca(f'"{a.nome}" {contesto}'.strip(), lingua, n=3):
                voce = self.wiki.voce(x["titolo"], lingua)
                if voce is not None:
                    prove += self._righe_wiki([voce], parole, chiave=chiave, massimo=15, caratteri=5000)
        return prove

    def _entranti(self, a: Anello) -> list[Prova]:
        """Relazioni al contrario: le voci che collegano all'entità, con le loro prime frasi. Solo per entità poco
        collegate (con centinaia di voci entranti l'elenco non dice niente)."""
        prove: list[Prova] = []
        for voce in self._voci(a)[:1]:
            titoli = self.wiki.entranti(voce.titolo, voce.lingua, n=100)
            if not titoli or len(titoli) >= 100:
                continue
            inizi = self.wiki.inizi(titoli[:40], voce.lingua)
            for titolo, testo in inizi.items():
                prove.append(Prova(f"[{titolo} ({voce.lingua}), voce che collega a {voce.titolo}] {testo[:500]}",
                                   self.wiki.url(titolo, voce.lingua), [titolo], voce.lingua))
        return prove

    def _candidati_wiki(self, numero: int, passo: dict, origine: Anello, tentativo: int,
                        candidati: dict[str, Anello]) -> None:
        """Il passo sul grafo di Wikipedia: righe della voce dell'entità (o delle voci che la nominano) scelte dal
        programma per le parole della relazione; il modello indica solo i candidati con il numero della riga."""
        if self.wiki is None:
            return
        parole = list(passo.get("parole") or []) + [passo["relazione"], passo.get("tipo", "")]
        self.r._fase(f"Passo {numero} su Wikipedia: {passo['relazione']} {origine.nome}…")
        voci = self._voci(origine)
        if tentativo == 1:
            # 29/10, Cappella Sistina: «Assunta», «Assunzione» tra le parole del passo dalla voce dell'Assunzione di
            # Maria: 20 righe sul dogma, fuori la galleria con i comuni («Celebrazione dell'Assunta a Novara di Sicilia»).
            nomi = " ".join([origine.nome] + [v.titolo for v in voci])
            prove = self._righe_wiki(voci, parole, escludi=nomi) if voci else []
            if not voci or len(prove) < 8:
                prove += self._pagine_che_nominano(origine, parole)
            if not voci:
                # Senza voce: anche i pezzi già letti che la nominano, a costo zero di pagine (24/10, Arduino: «Keyboardio
                # is an Oakland-based company founded in 2012 by the husband-and-wife team, Jesse Vincent and Kaia
                # Dekker» era in una pagina letta; i fondatori si cercavano solo su Wikipedia e poi sul web, mai fatto).
                prove += self._prove_passo(origine.nome, [], self._segmenti_su(origine.nome))[:12]
        else:
            prove = self._entranti(origine)
        if not prove:
            return
        elenco = self._decidi("candidati", {"entita": origine.nome, "relazione": passo["relazione"],
                                            "tipo": passo.get("tipo", ""), "prove": self._numera(prove),
                                            "di_nuovo": f"{passo['relazione']} — {origine.nome}"},
                              {"candidati": []})["candidati"]
        self._aggiungi(elenco, prove, origine, candidati, persone=self._di_persone(passo))

    def _candidati_wiki_descrizione(self, passo: dict, fascio: list[Anello], candidati: dict[str, Anello]) -> None:
        """Ricerca per descrizione dentro Wikipedia (i controlli come parole), prime frasi delle voci trovate."""
        if self.wiki is None:
            return
        descrizione = "; ".join(c.get("descrizione", "") for c in passo.get("controlli", []))
        ricerche = [str(x) for x in passo.get("descrizioni") or [] if str(x).strip()][:4]
        if not descrizione and not ricerche:
            return
        prove: list[Prova] = []
        viste: set[str] = set()
        for lingua in self._lingue()[:4]:  # ogni ricerca in ogni lingua: non sappiamo in che lingua è scritta
            titoli = []
            for q in ricerche:
                titoli += [x["titolo"] for x in self.wiki.cerca(q[:200], lingua, 4) if x["titolo"] not in viste]
            titoli = list(dict.fromkeys(titoli))[:12]
            viste |= set(titoli)
            for titolo, testo in self.wiki.inizi(titoli, lingua).items():
                prove.append(Prova(f"[{titolo} ({lingua})] {testo[:500]}", self.wiki.url(titolo, lingua), [titolo],
                                   lingua))
        if not prove:
            return
        elenco = self._decidi("candidati", {"descrizione": descrizione, "tipo": passo.get("tipo", ""),
                                            "prove": self._numera(prove), "di_nuovo": descrizione},
                              {"candidati": []})["candidati"]
        self._aggiungi(elenco, prove, fascio[0], candidati, persone=self._di_persone(passo))

    def _prove_wiki_candidato(self, a: Anello, controlli: list[dict], massimo: int = 14) -> list[Prova]:
        """Per la verifica: riquadro, inizio della voce del candidato e righe con le parole dei controlli o il nome
        dell'entità di prima."""
        voci = self._voci(a, altre=0)
        if not voci:
            return []
        parole = [c.get("descrizione", "") for c in controlli] + [x for c in controlli for x in c.get("letture") or []]
        if a.da is not None:
            parole.append(a.da.nome)
        # Ogni controllo ha le sue 2 righe migliori, prima i più pesanti; poi la scelta comune riempie i posti. Con
        # tutte le parole insieme un controllo raro perdeva contro tante parole comuni (24/10, Ducato: «gemellato con
        # un paese dell'Alta Vienna» contro «Lomellina», «Pavia», «paese»: «Gemellaggi: Linards» fuori dalle 14).
        proprie: list[Prova] = []
        for c in sorted(controlli, key=lambda c: -_peso(c)):
            mie = [c.get("descrizione", "")] + list(c.get("letture") or [])
            proprie += self._righe_wiki(voci, mie, massimo=2, caratteri=1200, riquadro=False, nudo=True)
        comuni = self._righe_wiki(voci, parole, massimo=massimo, caratteri=5000, riquadro=False)
        viste, prove = set(), []
        for p in proprie + comuni:
            if p.testo not in viste and len(prove) < massimo + len(controlli):
                viste.add(p.testo)
                prove.append(p)
        return self._con_voci_adiacenti(prove, [c for c in controlli if _peso(c) >= 3])

    def _con_voci_adiacenti(self, prove: list[Prova], decisivi: list[dict], massimo: int = 12) -> list[Prova]:
        """Passaggi adiacenti (HierSearch): alle righe che riguardano un controllo decisivo (peso 3) e collegano altre
        voci si aggiunge la prima frase di quelle voci, con una richiesta per lingua (18/10, Ducato di Milano:
        «Gemellaggi: Linards (Francia)» non dice che Linards è nell'Alta Vienna; lo dice la voce di Linards)."""
        if not decisivi or self.wiki is None:
            return prove
        radici = self._radici([c.get("descrizione", "") for c in decisivi]
                              + [x for c in decisivi for x in c.get("letture") or []])
        # Solo le parole rare tra le righe del candidato: «provincia», «comune» stanno in metà delle righe e i 12 posti
        # finivano negli inizi di «Provincia (Italia)» e «Comune (Italia)» (24/10, Ducato di Milano: «Gemellaggi:
        # Linards» restava senza l'inizio di Linards, «comune dell'Alta Vienna», e Gravellona prendeva «non so»).
        parole_riga = [{x[:6] for x in _norma(p.testo.split(" ⟶ voci:")[0]).split()} for p in prove]
        frequenza = {r: sum(1 for x in parole_riga if r in x) for r in radici if len(r) >= 4}
        rare = {r: f for r, f in frequenza.items() if 0 < f <= max(2, len(prove) // 4)}
        # Prima le righe con le parole più rare del controllo (24/10, Ducato: «paese», «comune» in 3 righe su 14
        # passavano davanti a «Gemellaggi: Linards»).
        punti = {i: sum(1 / rare[r] for r in parole_riga[i] if r in rare) for i in range(len(prove))}
        scelte: dict[str, list[tuple[int, str]]] = {}
        totale = 0
        for i in sorted((i for i in punti if punti[i] > 0), key=lambda i: -punti[i]):
            p = prove[i]
            if not p.collegamenti or not p.lingua:
                continue
            for titolo in p.collegamenti[:3]:
                if totale < massimo:
                    scelte.setdefault(p.lingua, []).append((i, titolo))
                    totale += 1
        for lingua, coppie in scelte.items():
            inizi = self.wiki.inizi(list(dict.fromkeys(t for _, t in coppie)), lingua)
            for i, titolo in coppie:
                testo = inizi.get(titolo) or next((x for k, x in inizi.items()
                                                   if corrisponde(titolo, k) in ("esatto", "variante")), "")
                if testo:
                    p = prove[i]
                    prove[i] = Prova(f"{p.testo} ⟶ {titolo}: {testo[:220]}", p.url, p.collegamenti, p.lingua)
        return prove

    def _registra(self, a: Anello, relazione: str) -> None:
        """L'anello accettato entra nel grafo dei concetti (per la vista e per chi scrive): nodi, legame, frase."""
        if a.da is None or a.prova_legame is None or self.r.concetti is None:
            return
        g = self.r.concetti.grafo
        url = a.prova_legame.url or "catena"
        try:
            with self.r.concetti._serratura:
                fonte = g.fonte(url, dominio=urlsplit(url).hostname or "")
                self._numeri_segmento[url] = self._numeri_segmento.get(url, 1000) + 1
                segmento = g.segmento(fonte, self._numeri_segmento[url], a.prova_legame.testo, url)
                for nome in (a.da.nome, a.nome):
                    g.db.execute("INSERT OR IGNORE INTO menzione (entita_id, segmento_id, frase) VALUES (?, ?, ?)",
                                 (g.entita(nome), segmento, a.prova_legame.testo[:300]))
                g.tripla(a.da.nome, relazione[:80], a.nome, "entita", "fatto", segmento,
                         citazione=a.prova_legame.testo[:300])
                self._registrate.add((_norma(a.da.nome), _norma(relazione[:80]), _norma(a.nome)))
                g.db.commit()
        except Exception:  # noqa: BLE001 - la vista non deve fermare la ricerca
            pass

    # --- 2. percorso ---------------------------------------------------------------------------------------------

    def percorri(self) -> None:
        """Uno o più piani (ODS: se il primo ragionamento non arriva a una risposta, se ne prova un altro): il secondo
        piano sa dove si è fermato il primo e cosa ha trovato."""
        if not self.prepara():
            self.motivo = "catena non preparata"
            return
        migliore = None  # (anelli deboli, stato) della catena completa più solida
        for giro in range(int(self.o.get("piani_catena", 2))):
            if giro and not self._ripianifica():
                break
            if self.o.get("frontiera", True):
                piani = [self.piano]
                if not giro and int(self.o.get("piani_paralleli", 2)) > 1:
                    alternativo = self._piano_alternativo()
                    piani += [alternativo] if alternativo else []
                    salto = self._piano_salto(self.piano)
                    if salto is not None and all(self._firma(salto) != self._firma(x) for x in piani):
                        piani.append(salto)
                self._percorri_frontiera(piani)
            else:
                self._percorri_piano()
            if self.valore:
                deboli = self._anelli_deboli()
                # La catena più solida: meno anelli deboli, poi l'anello peggiore migliore (17/10, Congresso di
                # Vienna: «Elena», qualità -0,1, tenuta contro catene più promettenti).
                strada = self._percorso(self.valore["anello"])
                solidita = (-deboli, min((a.qualita for a in strada), default=0.0))
                if migliore is None or solidita > migliore[0]:
                    migliore = (solidita, (self.piano, self.anelli, self.valore, self.motivo))
                if deboli == 0 or (self.o.get("frontiera", True) and self._strada_buona()):
                    break
                # Catena completa ma con anelli non confermati: si prova un'altra catena e si tiene la più solida
                # (autoconsistenza alla Open Deep Search; 16/10, Blender: Hallstatt preso per descrizione).
                self.motivo = f"catena completa ma con {deboli} anelli non confermati (valore {self.valore['valore']})"
            if self.r._limite_raggiunto() or self.r.pensatore.esaurito:
                break
        if migliore is not None:
            self.piano, self.anelli, self.valore, self.motivo = migliore[1]

    def _strada_buona(self) -> bool:
        """Con la frontiera i rami alternativi sono già stati esplorati: un altro piano serve solo se la strada della
        soluzione ha un anello sotto metà del peso dei controlli (17/10, Debian: secondo piano di 12 minuti per
        ritrovare lo stesso valore)."""
        strada = self._percorso((self.valore or {}).get("anello"))
        return bool(strada) and min(a.qualita for a in strada) >= 0.5

    def _anelli_deboli(self) -> int:
        a, deboli = (self.valore or {}).get("anello"), 0
        while a is not None:
            deboli += 1 if a.debole else 0
            a = a.da
        return deboli

    @staticmethod
    def _firma(piano: dict) -> tuple:
        return (_norma((piano.get("partenza") or {}).get("entita")),
                tuple(_norma(p.get("relazione")) for p in piano.get("passi", [])))

    @staticmethod
    def _piano_salto(piano: dict) -> dict | None:
        """Un passo intermedio verso una cosa senza nome proprio né pagina sua (statua, simulacro, dipinto, oggetto):
        piano con quel passo unito al successivo, che parte dall'entità di prima e ne eredita controlli, parole e
        descrizioni (25/10, Cappella Sistina: «festa → simulacro che la raffigura → comune» si perde tra statue e
        dipinti; «festa → comune in cui si trova un suo simulacro» trova Novara di Sicilia nella galleria della voce
        dell'Assunzione. Nel prompt la stessa regola era seguita 1 volta su 3)."""
        passi = list(piano.get("passi") or [])
        for i, passo in enumerate(passi[:-1]):
            testo = _norma(f"{passo.get('tipo', '')} {passo.get('relazione', '')}")
            if not OGGETTI_SENZA_NOME.search(testo):
                continue
            dopo = passi[i + 1]
            unito = {**dopo,
                     "relazione": f"{dopo['relazione']} (attraverso: {passo['relazione']})"[:150],
                     "parole": list(dict.fromkeys(list(dopo.get("parole") or []) + list(passo.get("parole") or [])))[:14],
                     "descrizioni": list(dopo.get("descrizioni") or []) + list(passo.get("descrizioni") or []),
                     "controlli": list(dopo.get("controlli") or []) + [
                         {**c, "descrizione": f"legato a {passo.get('tipo') or passo['relazione']}: "
                                             f"{c.get('descrizione', '')}"[:200]} for c in passo.get("controlli") or []]}
            return {**piano, "passi": passi[:i] + [unito] + passi[i + 2:],
                    "motivo": f"salto di «{passo['relazione']}» (cosa senza nome proprio)"}
        return None

    def _piano_alternativo(self) -> dict | None:
        """Un secondo piano, diverso dal primo, da percorrere insieme a lui nella stessa frontiera."""
        piano = self._decidi("catena", {"domanda": self.r.argomento[:1000], "oggi": self.r.oggi,
                                        "interpretazione": self.interpretazione,
                                        "alternativa_a": {"partenza": (self.piano.get("partenza") or {}).get("entita"),
                                                          "passi": [p.get("relazione") for p in self.piano["passi"]]}},
                             {"partenza": {}, "passi": [], "risposta": {}, "motivo": ""})
        if not piano.get("passi") or not (piano.get("partenza") or {}).get("entita") \
                or self._firma(piano) == self._firma(self.piano):
            return None
        self.r.pubblica({"tipo": "ricerca_catena", "interpretazione": self.interpretazione, "piano": piano})
        self.storia.append({"passo": 0, "relazione": "piano alternativo", "tentativo": 0, "candidati": [],
                            "tenuti": [], "scartati": [], "piano_alternativo": piano})
        return piano

    def _ripianifica(self) -> bool:
        trovati = [{"relazione": passo.get("relazione"), "entita": [a.nome + (" (debole)" if a.debole else "")
                                                                     for a in fascio]}
                   for passo, fascio in zip(self.piano.get("passi", []), self.anelli, strict=False)]
        precedente = {"partenza": (self.piano.get("partenza") or {}).get("entita"),
                      "passi": [p.get("relazione") for p in self.piano.get("passi", [])],
                      "dove_si_e_fermata": self.motivo, "anelli_trovati": trovati}
        self.r._fase("La catena non è arrivata alla risposta: Claude prepara una catena diversa…")
        piano = self._decidi("catena", {"domanda": self.r.argomento[:1000], "interpretazione": self.interpretazione,
                                        "catena_precedente": precedente},
                             {"partenza": {}, "passi": [], "risposta": {}, "motivo": ""})
        if not piano.get("passi") or not (piano.get("partenza") or {}).get("entita"):
            return False
        firma = lambda x: (_norma((x.get("partenza") or {}).get("entita")),  # noqa: E731
                           tuple(_norma(p.get("relazione")) for p in x.get("passi", [])))
        if firma(piano) == firma(self.piano):  # 16/10, Linux kernel: la stessa catena ripercorsa, 9 minuti persi
            self.r._fase("Il nuovo piano è uguale al primo: non lo ripercorro.")
            return False
        self.storia.append({"passo": 0, "relazione": "nuovo piano", "tentativo": 0, "candidati": [],
                            "tenuti": [], "scartati": [], "piano_precedente": precedente})
        self.piano, self.anelli, self.motivo, self.valore = piano, [], "", None
        self.r.pubblica({"tipo": "ricerca_catena", "interpretazione": self.interpretazione, "piano": self.piano})
        return True

    def _percorri_piano(self) -> None:
        partenza = Anello(self.piano["partenza"]["entita"],
                          contesto=str(self.piano["partenza"].get("indizio") or "")[:150])
        fascio = [partenza]
        larghezza = int(self.o.get("larghezza_catena", 3))
        for numero, passo in enumerate(self.piano["passi"], start=1):
            nuovi = self._percorri_passo(numero, passo, fascio)
            if not nuovi:
                self.motivo = f"catena interrotta al passo {numero} ({passo.get('relazione')})"
                return
            fascio = nuovi[:larghezza]
            self.anelli.append(fascio)
            if self.r._limite_raggiunto():
                self.motivo = self.r._limite_raggiunto()
                if numero < len(self.piano["passi"]):
                    return
        # Dietro al fascio, gli altri candidati non smentiti dell'ultimo passo, solo su Wikipedia (16/10, Wikipedia:
        # Roland Unger, giusto, era tra i candidati senza "sì" e fuori dal fascio).
        altri = [a for a in self._vivi if a not in fascio][:4]
        self._valore(fascio, altri)

    # --- frontiera globale (DeepSearch, ICLR 2026) -----------------------------------------------------------------

    @staticmethod
    def _percorso(a: Anello) -> list[Anello]:
        """Gli anelli dalla partenza (esclusa) ad `a`."""
        catena = []
        while a is not None and a.da is not None:
            catena.append(a)
            a = a.da
        return catena[::-1]

    def _profondita(self, a: Anello) -> int:
        """Il passo del piano a cui è arrivato il nodo: quelli della sua radice (partenza 0, ancora passo_base) più
        gli anelli percorsi da lì."""
        return self._radice(a).passo_base + len(self._percorso(a))

    def _priorita(self, a: Anello, passi: int, azione: str) -> float:
        """Quanto conviene la prossima azione su un nodo: qualità della strada che lo raggiunge (metà la peggiore,
        metà la media: una catena vale quanto il suo anello più debole), un piccolo premio per la profondità (radice
        di profondità/passi, per finire le catene) e una penalità per le azioni care (il web dopo Wikipedia)."""
        # Un anello senza il legame provato con quello di prima vale molto meno (18/10, Arduino: Damian Kulash,
        # trovato solo per descrizione «nato a metà anni '70», preso come coniuge della fondatrice).
        qualita_strada = [x.qualita - (0.15 if x.debole else 0.0)
                          - (0.0 if self._legato(x) else 0.05 if self._co_occorre(x) else 0.25)
                          for x in self._percorso(a)]
        # Una radice vale 0,7: finché non si allarga non si sa cosa darà (18/10, Wikipedia: con 1,0 le ricerche sul web
        # dalla partenza passavano sempre davanti al valore di Roland Unger, giusto, fino al limite delle pagine).
        base = 0.5 * min(qualita_strada) + 0.5 * sum(qualita_strada) / len(qualita_strada) if qualita_strada else 0.7
        # Il valore su Wikipedia di un nodo finale costa una chiamata e fa anche da controllo (tipo e intervallo):
        # passa davanti al web (17/10, Promessi sposi: Ponchielli, giusto, tra i candidati; la frontiera ha allargato
        # sul web fino al limite delle 30 pagine senza cercarne il valore).
        # I nodi finali sono a un'azione dalla risposta: il loro valore, anche sul web (2 pagine), costa meno di un
        # passo sul web (6 pagine e la verifica).
        costo = {"wiki": 0.0, "web": 0.35, "valore": -0.45, "valore_web": -0.1}[azione]
        # Un nodo che su Wikipedia non ha dato nessun candidato (di solito: non ha una voce) ha nel web l'unica
        # strada, non un ripiego: con una buona qualità la penalità scende (24/10, Arduino: Keyboardio mai allargato
        # sul web in 3 corse su 4; nelle corse passate così anche «Simone da Orsenigo», Duomo, e «Assunzione di
        # Maria Vergine», Cappella Sistina. Soglia 0,5 e penalità 0,1 scelte sulle 95 corse con la frontiera:
        # 6 entità giuste su 10 anticipate contro 7 allargamenti inutili nelle corse giuste).
        if azione == "web" and a.vuoto_wiki and (a.qualita if a.da is not None else 0.7) >= 0.5:
            costo = 0.1
        return base + 0.1 * math.sqrt(self._profondita(a) / max(passi, 1)) - costo - 0.4 * a.fallimenti + a.preferito

    def _radice(self, a: Anello) -> Anello:
        while a.da is not None:
            a = a.da
        return a

    def _piano_di(self, a: Anello) -> dict:
        return self._radice(a).piano or self.piano

    def _radici_piano(self, piano: dict) -> list[Anello]:
        """Partenza e ancore di un piano. Ancore (Think-on-Graph, HierSearch): altre entità che la domanda nomina con
        certezza e da cui, con una loro relazione, si arriva al risultato di un passo; più sono vicine alla risposta,
        prima si esplorano (17/10, Raspberry Pi: la Borsa di Londra, nominata, era a due passi dalla risposta; la
        catena partita dal Raspberry Pi ha finito le pagine sui distributori)."""
        n = len(piano["passi"])
        partenza = Anello(piano["partenza"]["entita"], contesto=str(piano["partenza"].get("indizio") or "")[:150],
                          qualita=1.0, piano=piano)
        radici = [partenza]
        for x in piano.get("ancore") or []:
            passo = int(x.get("passo") or 0)
            nome = " ".join(str(x.get("entita") or "").split())[:120]
            if 1 <= passo <= n and nome and str(x.get("relazione") or "").strip() \
                    and _norma(nome) != _norma(partenza.nome):
                radici.append(Anello(nome, qualita=1.0, passo_base=passo - 1, relazione=str(x["relazione"])[:150],
                                     contesto=str(x.get("indizio") or "")[:150], piano=piano))
        return radici

    def _percorri_frontiera(self, piani: list[dict] | None = None) -> None:
        """Ricerca a frontiera globale: invece di andare passo per passo con un fascio fisso di 3 entità, il programma
        tiene una lista unica di tutte le azioni aperte, di tutti i passi (allargare un nodo su Wikipedia, sul web,
        cercarne il valore) e fa ogni volta la migliore. Se un ramo non porta a niente si torna da soli al candidato
        di riserva del passo giusto, senza rifare il piano. Un nodo i cui figli falliscono tutti prende una penalità
        (fallimenti); chi ha già portato a un valore non viene penalizzato (regola asimmetrica). Con più piani
        (autoconsistenza alla Open Deep Search, ma in parallelo) le strade di tutti i piani stanno nella stessa
        frontiera: va avanti quella che funziona meglio (17/10, Lepanto: un piano con «repubblica sorella di» al posto
        di «sostituita da» non trovava la Repubblica Ligure)."""
        piani = piani or [self.piano]
        azioni: list[tuple[Anello, str]] = [(r, "wiki") for piano in piani for r in self._radici_piano(piano)]
        livelli: dict[tuple[int, int], list[Anello]] = {}  # (piano, profondità) → anelli raggiunti
        visti: dict[tuple[int, int], set[str]] = {}  # nomi già raggiunti (un'entità entra una volta sola)
        soluzioni: list[tuple[float, dict, dict]] = []
        gia_allargati: dict[tuple, tuple[str, list[Anello]]] = {}  # firma → (passo, figli trovati)
        valori_web: dict[int, int] = {}  # genitore → valori cercati sul web per i suoi figli
        gia_segni: dict[tuple, set[str]] = {}  # firma → passi (relazione + controlli) già allargati
        fatte, massimo = 0, int(self.o.get("azioni_frontiera", 30))
        priorita_di = lambda x: self._priorita(x[0], len(self._piano_di(x[0])["passi"]), x[1])  # noqa: E731
        while azioni and fatte < massimo:
            if self.r._limite_raggiunto() or self.r.pensatore.esaurito:
                self.motivo = self.motivo or self.r._limite_raggiunto() or "budget di Claude esaurito"
                break
            azioni.sort(key=lambda x: -priorita_di(x))
            nodo, azione = azioni.pop(0)
            priorita = priorita_di((nodo, azione))
            # Trovato un valore, si continua solo con azioni promettenti quanto la soluzione (autoconsistenza): una
            # seconda strada che porta allo stesso valore lo conferma, una migliore lo sostituisce. La soluzione vale
            # quanto la sua strada, senza il premio della ricerca del valore (25/10, Arduino: «Chris Martin», coniuge
            # trovato per descrizione, valeva 0,82 col premio di 0,45 e Keyboardio, 0,58, non veniva mai allargato).
            if soluzioni and (priorita <= soluzioni[0][0] or fatte >= massimo - 2):
                break
            # Il tetto conta solo le espansioni (le ricerche costose): cercare il valore di un nodo finale costa una
            # chiamata e non consuma il tetto (22/10, Linux kernel: le 30 azioni finite in ricerche di valore prima di
            # arrivare ad Alexandre Oliva, candidato giusto ma debole). Il limite di tempo e di budget resta.
            if azione in ("wiki", "web"):
                fatte += 1
            piano = self._piano_di(nodo)
            passi, n = piano["passi"], len(piano["passi"])
            profondita = self._profondita(nodo)
            self.azioni.append(f"{azione} {nodo.nome} ({priorita:.2f})" + (
                f" [piano {piani.index(piano) + 1}]" if len(piani) > 1 else ""))
            if azione in ("valore", "valore_web"):
                self.valore, self.piano = None, piano
                if self._valore_entita(nodo, (1,) if azione == "valore" else (2, 3)):
                    soluzioni.append((self._priorita(nodo, n, "wiki"), self.valore, piano))
                    soluzioni.sort(key=lambda x: -x[0])
                    self.storia.append({"passo": n + 1, "relazione": "valore", "tentativo": 0, "candidati": [nodo.nome],
                                        "tenuti": [(self.valore["valore"], round(priorita, 2), "soluzione")],
                                        "scartati": []})
                    if all(not x.debole for x in self._percorso(nodo)):
                        break  # strada tutta confermata: basta così
                elif azione == "valore" and valori_web.get(id(nodo.da), 0) < 3:
                    # Al massimo 3 valori sul web per genitore (25/10, Wikipedia: «consiglio direttivo di» dava ~10
                    # membri, ognuno con 2 pagine di valore sul web, fino al limite dei 35 minuti).
                    valori_web[id(nodo.da)] = valori_web.get(id(nodo.da), 0) + 1
                    azioni.append((nodo, "valore_web"))
                else:
                    self._fallito(nodo)
                continue
            passo = passi[profondita]
            if nodo.da is None and nodo.passo_base:
                # Da un'ancora si segue la sua relazione, con i controlli del passo più quello della relazione saltata
                # (peso 3): l'ancora non deve far perdere l'identità della catena (18/10, Concilio di Trento: ancora
                # «Alta Marna», Chaumont passava tutti i controlli senza essere il luogo di nascita del marchese
                # d'Elbeuf).
                prima = passi[profondita - 1].get("controlli") or [] if profondita >= 1 else []
                chi = "; ".join(str(c.get("descrizione") or "") for c in prima) or \
                    str((piano.get("partenza") or {}).get("entita") or "")
                saltata = {"descrizione": f"è «{passo['relazione']}» di: {chi}"[:200], "peso": 3,
                           "letture": [f"{passo['relazione']} (della persona o cosa del passo precedente)"]}
                passo = {**passo, "relazione": nodo.relazione,
                         "controlli": list(passo.get("controlli") or []) + [saltata]}
            # La stessa entità, allo stesso passo e con la stessa relazione, si allarga una volta sola anche se due
            # piani (o due varianti del nome) ci arrivano: le pagine web non si spendono due volte (23/10, Arduino:
            # «Keyboard.io» e «Keyboardio» allargati nei due piani, tetto di 30 pagine finito prima del coniuge).
            if azione == "wiki" and self.wiki is not None:
                self._voci(nodo)  # la voce serve comunque all'allargamento; qui dà la chiave dell'entità
            firma = self._firma_azione(azione, nodo, profondita, passo["relazione"])
            segno_passo = json.dumps([passo.get("relazione"), passo.get("controlli")], sort_keys=True, default=str)
            chiave = (id(piano), profondita + 1)
            visti.setdefault(chiave, set())
            figli = []
            riuso = False
            caso = self._gia_fatta(firma, azione, segno_passo, gia_allargati, gia_segni)
            if caso in ("salta", "riusa"):
                fatte -= 1
                gia_segno, gia_figli = gia_allargati[firma]
                if caso == "salta" or nodo in [x.da for x in gia_figli]:
                    self.azioni[-1] += " (già fatta)"
                    continue
                # Stesso passo già allargato da un altro piano: i suoi figli (già verificati) valgono anche qui, sotto
                # questo nodo, senza rifare ricerche (25/10: un piano con lo stesso primo passo restava fermo).
                figli = [replace(a, da=nodo, esiti=list(a.esiti)) for a in gia_figli]
                self.azioni[-1] += " (figli riusati)"
                riuso = True
            prima_storia = len(self.storia)
            for tentativo in (() if riuso else (1, 2) if azione == "wiki" else (3, 4)):
                if tentativo in (1, 2) and self.wiki is None:
                    continue
                figli += self._tentativo(tentativo, profondita + 1, passo, [nodo], visti[chiave], tutti=True)
                if any(not a.debole and self._completo(a) for a in figli) or self.r._limite_raggiunto():
                    break
            if not riuso:
                # Stessa relazione con controlli diversi (un altro piano): su Wikipedia si rifà, costa solo la verifica
                # (29/10, Galileo: Biancani scartato con i controlli del piano 1, tenuto con quelli del piano 2, che
                # però non partiva mai: «già fatta»).
                gia_allargati.setdefault(firma, (segno_passo, list(figli)))
                gia_segni.setdefault(firma, set()).add(segno_passo)
            if azione == "wiki" and not riuso:
                # Vuoto: niente candidati seguendo la relazione e niente tenuti dalla ricerca per descrizione, che dà
                # nomi qualsiasi (24/10, Arduino: per «fondatori di Keyboardio» Art Lazere, Julius Chein, Steve Jobs,
                # tutti scartati, e il web di Keyboardio non è mai partito. Sulle corse passate: +2 nodi giusti
                # anticipati, +4 allargamenti inutili nelle corse giuste; «nessun tenuto» ovunque: +5 e +26).
                nodo.vuoto_wiki = not any(x.get("candidati") if x.get("tentativo") == 1 else x.get("tenuti")
                                          for x in self.storia[prima_storia:])
                azioni.append((nodo, "web"))  # il web resta come riserva, con la sua penalità
            gia = {k for x in livelli.get(chiave, []) for k in (self._chiave_canonica(x), _chiave_entita(x.nome))}
            nuovi = []
            for a in figli:
                if self._chiave_canonica(a) not in gia and _chiave_entita(a.nome) not in gia:
                    nuovi.append(a)
                    gia |= {self._chiave_canonica(a), _chiave_entita(a.nome)}
            if not nuovi and azione == "web":
                self._fallito(nodo)
            for a in nuovi:
                a.relazione = passo["relazione"]
                livelli.setdefault(chiave, []).append(a)
                azioni.append((a, "valore" if profondita + 1 == n else "wiki"))
            if profondita + 1 == n and len(nuovi) > 1:
                self._spareggio_finale(nuovi, lambda x: self._priorita(x, n, "valore"))
        self.piano = soluzioni[0][2] if soluzioni else piani[0]
        mie = sorted(d for (p, d) in livelli if p == id(self.piano))
        self.anelli = [sorted(livelli[(id(self.piano), d)], key=self._ordine) for d in mie]
        passi, n = self.piano["passi"], len(self.piano["passi"])
        if soluzioni:
            # Due strade allo stesso valore: si tiene la migliore (le altre lo confermano).
            self.valore = soluzioni[0][1]
            uguali = [x for x in soluzioni if _norma(x[1]["valore"]) == _norma(self.valore["valore"])]
            self.motivo = f"catena completa: {self.valore['anello'].nome} → {self.valore['valore']}" + (
                f" (confermato da {len(uguali)} strade)" if len(uguali) > 1 else "")
            for a in self._percorso(self.valore["anello"]):
                self._registra(a, a.relazione or passi[self._profondita(a) - 1]["relazione"])
            ultimo = self.anelli[-1] if self.anelli else []
            if self.valore["anello"] in ultimo:  # il fascio finale comincia dall'anello della soluzione
                ultimo.remove(self.valore["anello"])
                ultimo.insert(0, self.valore["anello"])
        elif not self.motivo:
            arrivati = max(mie) if mie else 0
            self.motivo = (f"catena interrotta al passo {arrivati + 1} ({passi[arrivati].get('relazione')})"
                           if arrivati < n else "valore richiesto non trovato")

    @staticmethod
    def _gia_fatta(firma: tuple, azione: str, segno: str, gia_allargati: dict, gia_segni: dict) -> str:
        """Un allargamento già fatto da un altro piano: «nuova» (mai fatto), «riusa» (stesso passo: i suoi figli),
        «rifai» (Wikipedia, stessa relazione con controlli diversi: costa solo la verifica; 29/10, Galileo),
        «salta» (web con controlli diversi: le pagine non si spendono due volte)."""
        if firma not in gia_allargati:
            return "nuova"
        if gia_allargati[firma][0] == segno:
            return "riusa"
        if azione == "wiki" and segno not in gia_segni.get(firma, set()):
            return "rifai"
        return "salta"

    def _spareggio_finale(self, candidati: list[Anello], priorita, margine: float = 0.05, massimo: int = 5) -> int:
        """Candidati dell'ultimo passo alla pari (priorità entro `margine` dalla migliore): Haiku li confronta tra loro
        con la domanda intera e alcune righe delle loro voci (inizio, righe dei controlli, righe con le parole della
        domanda) e ne sceglie uno o nessuno; lo scelto passa davanti. I controlli dicono sì/no a uno alla volta, il
        confronto vede gli indizi di sfumatura (24/10, Monte Bianco: Alassio e Laigueglia gemellate entrambe con
        La Thuile, 0,71 tutte e due; solo Laigueglia è «un piccolo borgo di mare», la domanda dice «borgo»; nelle
        corse passate 17 soluzioni su 84 venivano da un pareggio, in 4 sbagliate la giusta era tra i pari).
        Restituisce l'indice scelto (da 0) o -1."""
        punti = {id(a): priorita(a) for a in candidati}
        migliore = max(punti.values())
        # Tra candidati legati con una riga e senza nessun «no» lo scarto può essere più largo: la verifica sceglie
        # una riga per controllo e può dare «in parte» al giusto (25/10, Monte Bianco: Laigueglia 0,7 con la riga
        # «piccolo borgo di mare» senza Savona, Alassio 0,9; 2 corse su 5 davano Alassio).
        puliti = [a for a in candidati if self._legato(a) and not self._no(a)]
        primo = max(candidati, key=lambda a: punti[id(a)])
        soglia = primo.qualita - 0.4 if primo in puliti else 2.0  # qualità: i fratelli hanno la stessa strada
        pari = sorted((a for a in candidati if punti[id(a)] >= migliore - margine
                       or (a in puliti and a.qualita >= soglia)),
                      key=lambda a: -punti[id(a)])[:massimo]
        if len(pari) < 2:
            return -1
        schede = []
        for i, a in enumerate(pari, 1):
            righe = [x.testo for x in (a.prova_legame, a.inizio) if x is not None]
            righe += [e["prova"].testo for e in a.esiti if e.get("prova") is not None]
            righe += [x.testo for x in self._righe_wiki(self._voci(a), [self.r.argomento], massimo=6, caratteri=1500)]
            righe = list(dict.fromkeys(" ".join(r.split())[:300] for r in righe))[:10]
            schede.append(f"{i}. {a.nome}\n" + "\n".join(f"   - {r}" for r in righe))
        esito = self._decidi("finale", {"domanda": self.r.argomento[:1000], "candidati": "\n".join(schede)},
                             {"scelta": 0, "motivo": ""})
        scelta = int(esito.get("scelta") or 0)
        self.storia.append({"passo": -2, "relazione": "spareggio finale", "tentativo": 0,
                            "candidati": [a.nome for a in pari],
                            "tenuti": [(pari[scelta - 1].nome, scelta, "scelto")] if 0 < scelta <= len(pari) else [],
                            "scartati": [], "motivo": str(esito.get("motivo") or "")[:300]})
        if not 0 < scelta <= len(pari):
            return -1
        scelto = pari[scelta - 1]  # passa davanti al migliore, anche se partiva più indietro (spareggio largo)
        scelto.preferito = migliore - punti[id(scelto)] + margine
        return scelta - 1

    @staticmethod
    def _fallito(a: Anello) -> None:
        """Penalità a un nodo senza esito e, a metà, alla sua origine (si propaga poco: altri figli possono
        ancora funzionare)."""
        a.fallimenti += 1.0
        if a.da is not None:
            a.da.fallimenti += 0.25

    def _controlli_passo(self, passo: dict, fascio: list[Anello]) -> list[dict]:
        """I controlli di un passo con i loro pesi: quelli del piano, il tipo (peso 1) e il legame con l'entità di
        prima (peso 2, controllo per tutti: 16/10, Monte Bianco: Alassio, ligure e savonese, preferito a Laigueglia
        senza che nessuna riga lo dicesse gemellato con La Thuile)."""
        controlli = [{**c, "peso": c.get("peso") or 2} for c in passo.get("controlli", [])]
        # Un controllo del piano che nomina l'entità di prima ripete il legame («indicato come allievo di Galileo»):
        # contava due volte (23/10, paper §9.1: Guiducci e Viviani «forti» senza essere gesuiti bolognesi). Diventa
        # una lettura del legame.
        chiavi = {p for a in fascio for p in _norma(a.nome).split() if len(p) >= 5 and p not in PAROLE_VUOTE}
        ripetuti = [c for c in controlli if any(re.search(rf"\b{re.escape(k)}\b", _norma(c.get("descrizione", "")))
                                                for k in chiavi)]
        controlli = [c for c in controlli if c not in ripetuti]
        if str(passo.get("tipo") or "").strip():  # il tipo è un controllo (16/10: BeBox, un computer, preso per BeOS)
            controlli.append({"descrizione": f"è di questo tipo: {passo['tipo']}", "peso": 1,
                              "letture": [f"{passo['tipo']} (anche come genere più ampio o più stretto)"]})
        controlli.append({"descrizione": f"è in relazione «{passo['relazione']}» con {' o '.join(a.nome for a in fascio)}",
                          "peso": 2, "letture": ["in senso largo: allievo, discepolo, studiò con, collaboratore, opera "
                                                 "di, parte di, come richiede la relazione"]
                          + [str(c.get("descrizione", "")) for c in ripetuti]})
        return controlli

    def _raccogli(self, tentativo: int, numero: int, passo: dict,
                  fascio: list[Anello]) -> tuple[dict[str, Anello], dict[str, Anello]]:
        """I candidati di un tentativo: (trovati seguendo la relazione, trovati per descrizione)."""
        candidati: dict[str, Anello] = {}
        per_descrizione: dict[str, Anello] = {}
        if tentativo in (1, 2):  # Wikipedia: un thread per entità del fascio (le chiamate al modello sono lente)
            parziali = [{} for _ in fascio]
            lavori = [(self._candidati_wiki, (numero, passo, o, tentativo, parziali[i]))
                      for i, o in enumerate(fascio)]
            if tentativo == 2:  # ancora Wikipedia: voci che collegano all'entità, ricerca per descrizione
                lavori.append((self._candidati_wiki_descrizione, (passo, fascio, per_descrizione)))
            with ThreadPoolExecutor(max_workers=4) as gruppo:
                for futuro in [gruppo.submit(f, *a) for f, a in lavori]:
                    futuro.result()
            for p in parziali:
                for k, v in p.items():
                    candidati.setdefault(k, v)
        elif tentativo == 3:
            for origine in fascio:
                self._candidati_in_avanti(numero, passo, origine, 1, candidati)
        else:
            for origine in fascio:
                self._candidati_in_avanti(numero, passo, origine, 2, candidati)
            self._candidati_per_descrizione(numero, passo, fascio, per_descrizione)
            if not candidati:
                self._candidati_dal_grafo(fascio, per_descrizione)
        return candidati, per_descrizione

    def _tentativo(self, tentativo: int, numero: int, passo: dict, fascio: list[Anello], visti: set[str],
                   tutti: bool = False) -> list[Anello]:
        """Un tentativo: candidati raccolti, verificati con i controlli del passo; restano i non smentiti."""
        controlli = self._controlli_passo(passo, fascio)
        candidati, per_descrizione = self._raccogli(tentativo, numero, passo, fascio)
        nuovi = [a for k, a in candidati.items() if k not in visti]
        buoni = self._verifica(nuovi[:CANDIDATI_MASSIMI], controlli, tutti=tutti)
        altri = [a for k, a in per_descrizione.items() if k not in visti and k not in candidati]
        if altri:
            buoni += self._verifica(altri[:CANDIDATI_MASSIMI], controlli, tutti=tutti)
        visti |= set(candidati) | set(per_descrizione)
        tutti_visti = nuovi + altri
        self.storia.append({"passo": numero, "relazione": passo["relazione"], "tentativo": tentativo,
                            "da": [a.nome for a in fascio],
                            "candidati": [a.nome for a in tutti_visti],
                            "tenuti": [(a.nome, round(a.qualita, 2), "debole" if a.debole else "forte")
                                       for a in buoni],
                            "scartati": [(a.nome, [e["controllo"] for e in a.esiti if e["esito"] == "no"])
                                         for a in tutti_visti if a not in buoni],
                            "verifiche": [self._verifica_in_breve(a) for a in tutti_visti]})
        self.r.pubblica({"tipo": "ricerca_catena_passo", **self.storia[-1]})
        return buoni

    def _firma_azione(self, azione: str, nodo: Anello, profondita: int, relazione: str) -> tuple:
        """Quando due allargamenti sono lo stesso: stessa entità, stesso passo e (su Wikipedia) stessa relazione. Sul
        web la relazione non conta: due piani che scrivono la relazione in modo diverso leggono le stesse pagine
        (24/10: sulle 95 corse con la frontiera 31 allargamenti web doppi, il secondo non ha mai dato un anello di
        una soluzione giusta; 12 corse sbagliate su 32 finiscono per il tetto delle pagine)."""
        parole = frozenset() if azione == "web" else frozenset(
            p[:5] for p in _norma(relazione).split() if len(p) >= 4)
        return (azione, self._chiave_canonica(nodo), profondita, parole)

    @staticmethod
    def _verifica_in_breve(a: Anello) -> dict:
        """Per la storia (23/10, Arduino: senza le righe citate non si capiva perché Keyboardio valeva 0,3): voce,
        riga del legame e, per ogni controllo, esito detto dal modello, esito tenuto dal programma e riga citata."""
        voce = next(iter(a.voci.values()), None)
        citata = lambda p: p.testo[:220] if p is not None else ""  # noqa: E731
        return {"nome": a.nome, "qualita": round(a.qualita, 2),
                "voce": f"{voce.titolo} ({voce.lingua})" if voce else (f"{a.titolo[1]} ({a.titolo[0]})" if a.titolo else ""),
                "legame": citata(a.prova_legame),
                "esiti": [{"controllo": str(e.get("controllo", ""))[:100], "peso": e.get("peso"),
                           "detto": e.get("detto", e.get("esito")), "tenuto": e.get("esito"),
                           "riga": citata(e.get("citata") or e.get("prova"))} for e in a.esiti]}

    @staticmethod
    def _chiave_canonica(a: Anello) -> str:
        """La stessa entità con nomi diversi: se la voce di Wikipedia è nota, la chiave è la voce (titolo inglese se
        c'è: «Adafruit» e «Adafruit Industries» portano alla stessa voce, 23/10); altrimenti le varianti del nome."""
        voce = next(iter(a.voci.values()), None)
        if voce is not None:
            inglese = voce.titolo if voce.lingua == "en" else voce.lingue.get("en")
            return "voce:" + _chiave_entita(inglese or voce.titolo)
        return _chiave_entita(a.nome)

    def _percorri_passo(self, numero: int, passo: dict, fascio: list[Anello]) -> list[Anello]:
        """Quattro tentativi, dal più economico; ci si ferma al primo che dà candidati forti.
        1. Wikipedia come grafo: righe della voce dell'entità scelte per le parole della relazione (16/10).
        2. Ancora Wikipedia: voci che collegano all'entità e ricerca per descrizione (i controlli come parole).
        3. Web in avanti (ricerche per la relazione, pagine lette).
        4. Web con ricerche diverse e PER DESCRIZIONE (i controlli come chiave di ricerca), più i vicini nel grafo;
           per i candidati trovati per descrizione il legame con l'entità di prima diventa un controllo.
           (15/10: Biancani non compare nelle pagine sugli allievi di Galileo, ma sì tra i gesuiti bolognesi.)
        Un candidato debole resta di riserva."""
        riserva: list[Anello] = []
        trovati_forti: list[Anello] = []
        visti: set[str] = set()
        self._vivi = []
        for tentativo in (1, 2, 3, 4):
            if tentativo in (1, 2) and self.wiki is None:
                continue
            buoni = self._tentativo(tentativo, numero, passo, fascio, visti)
            self._vivi = sorted(self._vivi + buoni, key=self._ordine)
            forti = sorted([a for a in buoni if not a.debole], key=lambda a: -a.qualita)
            trovati_forti = sorted(trovati_forti + forti,
                                   key=lambda a: (bool(a.da and a.da.debole), -a.qualita, self._dubbi(a)))
            completo = any(self._completo(a) for a in trovati_forti)
            # Con Wikipedia si fanno sempre i due tentativi economici prima di scegliere, salvo un candidato che passa
            # tutti i controlli (16/10, Emacs: Bobrow "forte" al primo tentativo, Tomlinson arrivava solo dopo).
            if trovati_forti and (completo or tentativo >= 2 or self.wiki is None):
                scelti = trovati_forti + [a for a in self._vivi if a.debole and a not in trovati_forti]
                for a in scelti:
                    self._registra(a, passo["relazione"])
                return scelti
            riserva += [a for a in buoni if a.debole]  # i deboli di tutti i tentativi, poi i migliori
            if self.r._limite_raggiunto() or self.r.pensatore.esaurito:
                break
        riserva = sorted(riserva, key=self._ordine)[
            :int(self.o.get("larghezza_catena", 3))]
        for a in riserva:
            self._registra(a, passo["relazione"])
        return riserva

    def _candidati_in_avanti(self, numero: int, passo: dict, origine: Anello, tentativo: int,
                             candidati: dict[str, Anello]) -> None:
        self.r._fase(f"Passo {numero}: {passo['relazione']} {origine.nome}…")
        ricerche = self._decidi("passo", {"lingua_ricerca": self._lingua_ricerca(),
                                          "entita": origine.nome, "contesto": origine.contesto,
                                          "relazione": passo["relazione"],
                                          "tipo": passo.get("tipo", ""), "indizi": self._indizi(passo),
                                          "ricerche_gia_fatte":
                                              self.ricerche_passo[-12:] if tentativo == 2 else []},
                                {"ricerche": [f"{origine.nome} {passo['relazione']}"]})["ricerche"]
        indizi = "; ".join(self._indizi(passo))
        segmenti = self._cerca_e_leggi(ricerche, int(self.o.get("pagine_per_passo", 3)),
                                       scopo=f"{passo['relazione']} {origine.nome}" + (f" ({indizi})" if indizi else ""))
        prove = self._prove_passo(origine.nome, segmenti, self._segmenti_su(origine.nome))
        letti = {p.url for p in prove}
        prove += [p for p in self._estratti if p.url not in letti]
        if not prove:
            return
        elenco = self._decidi("candidati", {"entita": origine.nome, "relazione": passo["relazione"],
                                            "tipo": passo.get("tipo", ""), "prove": self._numera(prove),
                                            "di_nuovo": f"{passo['relazione']} — {origine.nome}"},
                              {"candidati": []})["candidati"]
        self._aggiungi(elenco, prove, origine, candidati, persone=self._di_persone(passo))

    @staticmethod
    def _indizi(passo: dict) -> list[str]:
        """Gli indizi della domanda sul risultato del passo (i controlli, prima i più pesanti) per le ricerche sul web:
        «aziende ispirate da Arduino» porta a centinaia di risultati, «Arduino programmable keyboard company» al
        risultato (25/10, Arduino: le prime ricerche web non nominavano mai le tastiere)."""
        controlli = sorted(passo.get("controlli") or [], key=lambda c: -_peso(c))
        return [" / ".join([str(c.get("descrizione") or "")] + [str(x) for x in (c.get("letture") or [])[:1]])[:160]
                for c in controlli[:3] if c.get("descrizione")]

    def _candidati_per_descrizione(self, numero: int, passo: dict, fascio: list[Anello],
                                   candidati: dict[str, Anello]) -> None:
        descrizione = "; ".join(c.get("descrizione", "") for c in passo.get("controlli", []))
        if not descrizione:
            return
        self.r._fase(f"Passo {numero}: cerco per descrizione ({descrizione[:80]})…")
        ricerche = self._decidi("passo", {"lingua_ricerca": self._lingua_ricerca(),
                                          "descrizione": descrizione, "tipo": passo.get("tipo", ""),
                                          "relazione_con": f"{passo['relazione']} {fascio[0].nome}",
                                          "ricerche_gia_fatte": self.ricerche_passo[-12:]},
                                {"ricerche": [descrizione[:120]]})["ricerche"]
        segmenti = self._cerca_e_leggi(ricerche, int(self.o.get("pagine_per_passo", 3)),
                                       scopo=f"{passo['relazione']} {fascio[0].nome} ({descrizione[:200]})")
        prove = [Prova(" ".join(s["testo"].split())[:4000], s["url"]) for s in segmenti][:12]
        prove += [p for p in self._estratti if p.url not in {x.url for x in prove}]
        if not prove:
            return
        elenco = self._decidi("candidati", {"descrizione": descrizione, "tipo": passo.get("tipo", ""),
                                            "prove": self._numera(prove), "di_nuovo": descrizione},
                              {"candidati": []})["candidati"]
        self._aggiungi(elenco, prove, fascio[0], candidati, persone=self._di_persone(passo))

    def _candidati_dal_grafo(self, fascio: list[Anello], candidati: dict[str, Anello]) -> None:
        """Se la relazione cercata non ha dato candidati, le entità già collegate all'entità di prima nel grafo, con
        qualunque legame, diventano candidati (il legame cercato diventa un controllo). 15/10, Arduino: Jesse Vincent
        era nel grafo come co-fondatore di Keyboardio, nessuna pagina lo diceva "coniuge di Kaia Dekker"."""
        c = self.r.concetti
        url_di = {r["id"]: r["url"] for r in c.grafo.db.execute(
            "SELECT s.id, f.url FROM segmento s JOIN fonte f ON f.id = s.fonte_id")}
        for origine in fascio:
            nodo = self._trova(origine.nome)
            if nodo is None:
                continue
            vicini = 0
            for t in c.legami():
                if nodo not in (t["soggetto_id"], t["oggetto_id"]) or vicini >= CANDIDATI_MASSIMI:
                    continue
                altro = t["oggetto"] if t["soggetto_id"] == nodo else t["soggetto"]
                chiave = _chiave_entita(altro)
                if not chiave or chiave == _chiave_entita(origine.nome) or chiave in candidati:
                    continue
                prova = Prova(f"{t['soggetto']} → {t['relazione']} → {t['oggetto']} «{t.get('citazione') or ''}»",
                              url_di.get(t["segmento_id"], ""))
                candidati[chiave] = Anello(altro, da=origine, prova_legame=prova,
                                           contesto=f"{origine.nome}: {origine.contesto}"[:150])
                vicini += 1

    @staticmethod
    def _nome_nella_riga(nome: str, prova: Prova) -> bool:
        """Il candidato sta davvero nella riga che cita: il nome (o il cognome) nel testo, o una voce collegata della
        riga che è lui (GENRE: il modello sceglie tra nomi che ci sono, non li inventa)."""
        testo = _piano(prova.testo)
        chiave = _piano(_chiave_nome(nome))
        if _piano(nome) in testo or (len(chiave) >= 4 and re.search(rf"\b{re.escape(chiave)}\b", testo)):
            return True
        return any(corrisponde(nome, t) for t in prova.collegamenti)

    @staticmethod
    def _nome_intero(nome: str, prove: list[Prova]) -> str:
        """Un nome di una parola sola («Kaia», «jesse») diventa la forma intera che le prove scrivono più spesso
        («Kaia Dekker»): con il solo nome di battesimo il passo dopo cerca omonimi (25/10, Arduino: «coniuge di Kaia»
        → Andy Bell, Idha). Solo forme Nome Cognome con iniziali maiuscole, mai un'altra persona con quel cognome."""
        parole = nome.split()
        if len(parole) != 1 or len(nome) < 3:
            return nome
        forme: dict[str, int] = {}
        maiuscola = r"[A-ZÀ-Þ][\w'’-]+"
        for p in prove:
            for m in re.finditer(rf"\b{re.escape(nome[0].upper() + nome[1:])}((?:\s+(?:van|von|de|da|di|del|der|le|la))?"
                                 rf"(?:\s+{maiuscola}){{1,2}})", p.testo):
                forme[(nome[0].upper() + nome[1:] + m.group(1)).strip()] = forme.get(
                    (nome[0].upper() + nome[1:] + m.group(1)).strip(), 0) + 1
        if not forme:
            return nome
        return max(forme.items(), key=lambda x: (x[1], -len(x[0])))[0]

    @staticmethod
    def _di_persone(passo: dict) -> bool:
        """Il passo cerca persone (dal tipo o dalla relazione del piano)."""
        testo = _norma(f"{passo.get('tipo', '')} {passo.get('relazione', '')}")
        return bool(re.search(r"\b(person\w*|donna|uomo|coniuge|marito|moglie|fondat\w*|autor\w*|scrittor\w*|"
                              r"pittor\w*|architett\w*|scienziat\w*|presidente|sindaco|allievi|studente\w*|figli\w*|"
                              r"padre|madre|people|man|woman|founder\w*|spouse|wife|husband)\b", testo))

    def _aggiungi(self, elenco: list[dict], prove: list[Prova], origine: Anello,
                  candidati: dict[str, Anello], persone: bool = False) -> None:
        for c in elenco:
            nome = " ".join(str(c.get("nome") or "").split())[:120]
            nome = self._nome_intero(nome, prove) if persone else nome
            chiave = _chiave_entita(nome)
            if nome and chiave and chiave != _chiave_entita(origine.nome) and chiave not in candidati:
                prova = self._prova(prove, c.get("prova"))
                if prova is None or not self._nome_nella_riga(nome, prova):
                    log.info("catena: candidato «%s» scartato: non sta nella riga %s", nome, c.get("prova"))
                    continue
                candidati[chiave] = Anello(nome, da=origine, prova_legame=prova,
                                                 contesto=f"{origine.nome}: {origine.contesto}"[:150],
                                                 titolo=self._titolo_da_prova(nome, prova))

    @staticmethod
    def _titolo_da_prova(nome: str, prova: Prova | None) -> tuple[str, str] | None:
        """La voce di Wikipedia del candidato, se la riga che lo indica la collega (niente omonimi possibili)."""
        if prova is None or not prova.collegamenti or not prova.lingua:
            return None
        chiave = _chiave_nome(nome)
        # Prima il titolo che corrisponde meglio (esatto, variante, parentesi, base), poi il cognome: «La Thuile
        # (Italia)» non deve prendere un altro collegamento della stessa riga.
        # «base» (nome di una parola dentro un titolo lungo) non basta qui: 25/10, Cappella Sistina: «Napoli» →
        # «Regno di Napoli»; il cognome passa sotto, con al massimo una parola in più (Enrico Fermi).
        gradi = [(GRADI.get(corrisponde(nome, t), 0.0), -i, t) for i, t in enumerate(prova.collegamenti)]
        migliore = max(gradi, default=(0.0, 0, ""))
        if migliore[0] > GRADI["base"]:
            return (prova.lingua, migliore[2])
        # Il cognome basta solo per un nome di una parola («Fermi»): con nome e cognome un altro collegamento con lo
        # stesso cognome è un'altra persona (24/10: Paul McCartney → Linda McCartney, Michael Dell → Susan Dell,
        # Guiniforte Solari → Giovanni Solari; 57 voci sbagliate su 523 verifiche registrate, in gran parte da qui).
        if len([p for p in _norma(re.sub(r"\([^)]*\)", " ", nome)).split() if len(p) >= 4]) != 1:
            return None
        for titolo in prova.collegamenti:
            parole = _norma(re.sub(r"\([^)]*\)", " ", titolo)).split()
            # al massimo una parola in più (Enrico Fermi, Adafruit Industries; non «Microsoft ergonomic keyboards»)
            if len(chiave) >= 4 and chiave in parole and len(parole) <= 2:
                return (prova.lingua, titolo)
        return None

    def _segmenti_su(self, nome: str) -> list[dict]:
        """Pezzi già salvati in cui compare il nome (letti in passi precedenti)."""
        chiave = _chiave_nome(nome)
        return [s for s in self._segmenti_dopo(0) if chiave in s["testo"].lower()][:12]

    def _verifica(self, candidati: list[Anello], controlli: list[dict], tutti: bool = False) -> list[Anello]:
        """Controlli su tutti i candidati; un "no" con prova scarta. Ai migliori con dei "non so" si cerca la scheda e
        si rifà la verifica una volta."""
        if not candidati:
            return []
        if not controlli:
            return candidati
        self._inizi_wiki(candidati)
        self._esiti(candidati, controlli)
        vivi = [a for a in candidati if not self._smentito(a)]
        dubbi = [a for a in sorted(vivi, key=lambda a: -a.qualita)
                 if any(e["esito"] in ("non so", "in parte") for e in a.esiti)]
        # Con Wikipedia la scheda costa una richiesta: si approfondiscono tutti i dubbi (16/10, Galileo: Zucchi
        # "rettore del Collegio Romano" solo nel corpo della voce, fuori dai primi tre per punti).
        da_approfondire = dubbi[:int(self.o.get("schede_per_passo", 8 if self.wiki is not None else 3))]
        if da_approfondire and not self.r._limite_raggiunto():
            dal_web = 0
            for a in da_approfondire:
                # La scheda è prima di tutto la voce intera di Wikipedia (riquadro e testo), poi il web (al massimo 3).
                if not a.voci_cercate and self._voci(a, altre=0):
                    continue
                if a.voci or dal_web >= 3:
                    continue
                dal_web += 1
                self.r._fase(f"Cerco la scheda di {a.nome}…")
                parole = " ".join(_norma(a.da.nome).split()[:3]) if a.da else ""
                self._cerca_e_leggi([f"{a.nome} {parole}".strip(), self._ricerca_controllo(a)],
                                    int(self.o.get("pagine_scheda", 2)))
            self._esiti(da_approfondire, controlli)
            vivi = [a for a in candidati if not self._smentito(a)]
        # Dopo la scheda un "non so" pesa contro: passa chi ha più di metà del peso dei controlli provato (15/10:
        # Viviani, né gesuita né bolognese, passava perché nessuna pagina lo negava). Se nessuno passa, il migliore
        # resta, segnato debole. A parità di qualità decide il nome, così due corse uguali scelgono uguale.
        ordinati = sorted(vivi, key=lambda a: (-a.qualita, not self._co_occorre(a), -a.punti, a.nome))
        # Forte solo con il legame provato (16/10, Blender: Traun, Wels e Gmunden "forti" perché città dell'Alta
        # Austria sul Traun, senza nessuna riga che ci facesse nascere lo sviluppatore).
        con_legame = any(str(c.get("descrizione", "")).startswith("è in relazione «") for c in controlli)
        legato = lambda a: not con_legame or any(  # noqa: E731
            e["esito"] == "sì" and e["prova"] and str(e["controllo"]).startswith("è in relazione") for e in a.esiti)
        forti = [a for a in ordinati if a.qualita > 0.5 and legato(a) and not self._decisivo_ignoto(a)
                 and self._identificato(a)]
        if forti:
            # Dietro ai forti restano i non smentiti, segnati deboli: se dal forte non si arriva al valore, la catena
            # prova loro (16/10, Linux kernel: Islene Calciolari Garcia senza data di nascita, Alexandre Oliva dietro).
            altri = [a for a in ordinati if a not in forti]
            # Con la frontiera restano tutti i non smentiti (16/10, Monte Bianco: La Thuile, giusta, fuori dai tre
            # tenuti a pari punti); senza, solo quanti ne servono per il fascio.
            altri = altri[:int(self.o.get("figli_massimi", 6))] if tutti else \
                altri[:max(0, int(self.o.get("larghezza_catena", 3)) - 1)]
            for a in altri:
                a.debole = True
            return forti + altri
        # Senza candidati forti restano fino a `larghezza` deboli non smentiti, anche senza "sì": il passo dopo li
        # verifica comunque (15/10: Joinville, risposta giusta, scartato perché le pagine non dicevano "Alta Marna").
        deboli = ordinati[:int(self.o.get("figli_massimi", 6) if tutti else self.o.get("larghezza_catena", 3))]
        for a in deboli:
            a.debole = True
        return deboli

    @staticmethod
    def _ricerca_controllo(a: Anello) -> str:
        """Seconda ricerca della scheda web: il nome con il controllo più pesante non ancora provato (prima lettura o
        descrizione, parole piene), non «biografia» (24/10, Arduino: «Seeed Studio biografia», «Cytron Technologies
        biografia» per aziende candidate a «produce tastiere programmabili»)."""
        aperti = sorted((e for e in a.esiti if e["esito"] in ("non so", "in parte")
                         and not str(e["controllo"]).startswith(("è in relazione", "è di questo tipo"))),
                        key=lambda e: -float(e.get("peso") or 1))
        if not aperti:
            return f"{a.nome} biografia"
        testo = next(iter(aperti[0].get("letture") or []), "") or str(aperti[0]["controllo"])
        parole = [p for p in re.findall(r"[\w'’-]+", testo) if len(p) >= 4 and _norma(p) not in PAROLE_VUOTE]
        return f"{a.nome} {Catena._contesto_breve(a)} {' '.join(parole[:5])}".replace("  ", " ").strip()

    @staticmethod
    def _contesto_breve(a: Anello) -> str:
        """Una o due parole dell'entità da cui il candidato è stato trovato, per le ricerche sul web senza omonimi
        («Roland Unger Wikivoyage»), senza parole già nel nome."""
        if a.da is None:
            return ""
        nome = set(_norma(a.nome).split())
        parole = [p for p in re.sub(r"\([^)]*\)", " ", a.da.nome).split()
                  if len(p) >= 3 and _norma(p) not in nome and _norma(p) not in PAROLE_VUOTE]
        return " ".join(parole[:2])

    def _ordine(self, a: Anello) -> tuple:
        """Prima chi viene da un anello forte, poi chi ha il legame provato, poi più sì (16/10, Duomo di Milano:
        «Francesco (nome)» trovato da un architetto debole passava davanti a Simone da Orsenigo, forte)."""
        return (bool(a.da and a.da.debole), not self._legato(a), -a.qualita, not self._co_occorre(a), a.nome)

    @staticmethod
    def _legato(a: Anello) -> bool:
        """Il legame con l'entità di prima ha un sì con prova (16/10, Hubble: Ken Mattingly, mai su STS-103, davanti
        a Grunsfeld nella riserva perché aveva più sì sugli altri controlli)."""
        return any(e["esito"] == "sì" and e["prova"] and str(e["controllo"]).startswith("è in relazione")
                   for e in a.esiti)

    def _co_occorre(self, a: Anello) -> bool:
        """Logica del grafo, senza modello: il candidato compare negli stessi pezzi salvati (o nelle stesse righe delle
        voci) dell'entità di prima. Un legame non provato ma con co-occorrenza vale più di uno trovato solo per
        descrizione (22/10, Blender con Sonnet: Bad Ischl, Gmunden, Traun e Wels a pari merito come città natale di
        Robert Szeleney; vinceva la prima in ordine alfabetico)."""
        if a.da is None:
            return True
        chiave = _chiave_nome(a.nome)
        if len(chiave) < 4:
            return False
        if not hasattr(self, "_co_occorrenze"):
            self._co_occorrenze: dict[tuple[str, str], bool] = {}
        indice = (_norma(a.da.nome), chiave)
        if indice not in self._co_occorrenze:
            testi = [s["testo"] for s in self._segmenti_su(a.da.nome)]
            testi += [r.testo for v in a.da.voci.values() for r in v.righe]
            self._co_occorrenze[indice] = any(chiave in _norma(t).split() for t in testi)
        return self._co_occorrenze[indice]

    @staticmethod
    def _decisivo_ignoto(a: Anello) -> bool:
        """Un controllo di peso 3 (lo identifica quasi da solo) senza prova: il candidato non è confermato, anche se
        gli altri controlli passano (17/10, Ducato di Milano: sei comuni confinanti "forti" a pari merito, nessuno
        gemellato con un paese dell'Alta Vienna)."""
        return any(_peso(e) >= 3 and e["esito"] in ("non so", "no") for e in a.esiti)

    @staticmethod
    def _identificato(a: Anello) -> bool:
        """Almeno un tratto che lo identifica (non il tipo, non il legame, peso ≥ 2) ha un sì con prova, se il piano
        ne ha (23/10, paper §9.1: Guiducci «forte» con il solo legame e il «gesuita bolognese» non provato)."""
        propri = [e for e in a.esiti if _peso(e) >= 2
                  and not str(e["controllo"]).startswith(("è di questo tipo", "è in relazione"))]
        return not propri or any(e["esito"] == "sì" and e["prova"] for e in propri)

    @staticmethod
    def _dubbi(a: Anello) -> int:
        return sum(1 for e in a.esiti if e["esito"] == "non so")

    @staticmethod
    def _completo(a: Anello) -> bool:
        """Tutti i controlli che lo identificano (non il tipo) hanno un sì con prova."""
        propri = [e for e in a.esiti if not str(e["controllo"]).startswith("è di questo tipo")]
        return bool(propri) and all(e["esito"] == "sì" and e["prova"] for e in propri)

    @staticmethod
    def _no(a: Anello) -> int:
        return sum(1 for e in a.esiti if e["esito"] == "no" and e["prova"])

    def _smentito(self, a: Anello) -> bool:
        """Scartato se i "no" con prova sono almeno quanti i "sì": un solo "no" contro più "sì" è spesso una lettura
        troppo stretta (16/10, Git: GitHub scartato per «aperto al pubblico nella primavera 2008», che è il valore
        cercato, non un tratto per riconoscerlo). `punti` = sì meno no."""
        no = sum(_peso(e) for e in a.esiti if e["esito"] == "no" and e["prova"])
        si = sum(_peso(e) * VALORE_ESITO[e["esito"]] for e in a.esiti if e["esito"] in ("sì", "in parte")
                 and e["prova"] and not str(e["controllo"]).startswith(("è di questo tipo", "è in relazione")))
        # tipo e legame non compensano un "no": dicono che il candidato è del genere giusto, non che è quello giusto
        return no > 0 and si <= no

    def _inizi_wiki(self, candidati: list[Anello]) -> None:
        """Le prime frasi delle voci dei candidati raggiunti da un collegamento, con una richiesta per lingua (la
        voce intera si legge solo per chi resta in dubbio: Wikipedia concede 10 richieste al minuto)."""
        if self.wiki is None:
            return
        per_lingua: dict[str, list[Anello]] = {}
        for a in candidati:
            if a.titolo and not a.voci and not a.inizio:
                per_lingua.setdefault(a.titolo[0], []).append(a)
        for lingua, gruppo in per_lingua.items():
            inizi = self.wiki.inizi([a.titolo[1] for a in gruppo], lingua)
            for a in gruppo:
                testo = inizi.get(a.titolo[1]) or next((t for k, t in inizi.items()
                                                        if corrisponde(a.titolo[1], k) in ("esatto", "variante")), "")
                if testo:
                    a.inizio = Prova(f"[{a.titolo[1]} ({lingua}), inizio della voce] {testo}",
                                     self.wiki.url(a.titolo[1], lingua), [], lingua)

    def _esiti(self, candidati: list[Anello], controlli: list[dict]) -> None:
        """Verifica a gruppi di 2 candidati, in parallelo: richieste più corte (meno confusione tra le righe dei
        candidati) e più veloci. Le prove si preparano prima, nel thread principale (grafo e browser)."""
        gruppi = []
        for k in range(0, len(candidati), 2):
            gruppo = candidati[k:k + 2]
            prove: list[Prova] = []
            blocchi, intervalli = [], {}
            for a in gruppo:
                mie = self._prove_wiki_candidato(a, controlli) if a.voci else ([a.inizio] if a.inizio else [])
                mie += self._prove_entita(a.nome, segmenti=self._segmenti_su(a.nome), massimo=12 if not mie else 5)
                if a.prova_legame and a.prova_legame not in mie:
                    mie.insert(0, a.prova_legame)
                inizio = len(prove) + 1
                prove += mie
                intervalli[id(a)] = range(inizio, inizio + len(mie))
                blocchi.append(f"CANDIDATO: {a.nome}\n" + "\n".join(
                    f"[{inizio + i}] {p.testo[:1000]}" for i, p in enumerate(mie)))
            gruppi.append((gruppo, prove, blocchi, intervalli))
        testo_controlli = [f"C{i}: {c.get('descrizione', '')} (letture: {'; '.join(c.get('letture') or [])})"
                           for i, c in enumerate(controlli, start=1)]
        chiedi = lambda blocchi: self._decidi(  # noqa: E731
            "verifica", {"controlli": testo_controlli, "candidati": "\n\n".join(blocchi)}, {"candidati": []})
        # Due letture indipendenti per gruppo (autoconsistenza, come in Open Deep Search): un "sì" vale solo se
        # entrambe lo danno, un "no" basta che sia in una (16/10: Bobrow "premiato nel 2009", la Marsigliese "dedicata
        # a un Borbone": sì inventati che una seconda lettura raramente ripete).
        letture = int(self.o.get("letture_verifica", 2))
        with ThreadPoolExecutor(max_workers=6) as pool:
            esiti = list(pool.map(chiedi, [g[2] for g in gruppi for _ in range(letture)]))
        for n, (gruppo, prove, _, intervalli) in enumerate(gruppi):
            prime: dict[int, list[dict]] = {}
            for k in range(letture):
                self._applica_esiti(gruppo, prove, intervalli, esiti[n * letture + k], controlli)
                for a in gruppo:
                    prime[id(a)] = self._unisci_esiti(prime[id(a)], a.esiti) if id(a) in prime else a.esiti
            for a in gruppo:
                a.esiti = prime[id(a)]
                a.punti = sum(1 for e in a.esiti if e["esito"] == "sì" and e["prova"]) - self._no(a)
                a.qualita = qualita(a.esiti)

    @staticmethod
    def _unisci_esiti(primi: list[dict], secondi: list[dict]) -> list[dict]:
        chiave = lambda e: _norma(e["controllo"])[:50]  # noqa: E731
        altri = {chiave(e): e for e in secondi}
        uniti = []
        for e in primi:
            f = altri.get(chiave(e), {"esito": "non so", "prova": None})
            positivi = {"sì", "in parte"}
            if e["esito"] == "no" and e["prova"] or f["esito"] == "no" and f["prova"]:
                uniti.append(e if e["esito"] == "no" and e["prova"] else {**e, "esito": "no", "prova": f["prova"]})
            elif e["esito"] == "sì" and e["prova"] and f["esito"] == "sì" and f["prova"]:
                uniti.append(e)
            elif e["esito"] in positivi and e["prova"] and f["esito"] in positivi and f["prova"]:
                uniti.append({**e, "esito": "in parte"})  # una lettura dice solo "in parte": vale a metà
            else:
                uniti.append({**e, "esito": "non so", "prova": None})
        return uniti

    @staticmethod
    def _risolvi_controllo(testo: str, controlli: list[dict]) -> dict | None:
        """Il controllo a cui risponde Haiku: dal codice (C2) o, se ha riscritto il testo, dall'inizio del testo."""
        m = re.match(r"\s*C(\d+)\b", str(testo or ""))
        if m and 1 <= int(m.group(1)) <= len(controlli):
            return controlli[int(m.group(1)) - 1]
        n = _norma(testo)
        for c in controlli:
            d = _norma(c.get("descrizione"))
            if d and n and (n.startswith(d[:40]) or d.startswith(n[:40]) or (len(d) >= 4 and d in n)):
                return c
        return None

    def _applica_esiti(self, candidati: list[Anello], prove: list[Prova], intervalli: dict[int, range],
                       esito: dict, controlli: list[dict] | None = None) -> None:
        per_nome = {_norma(x.get("nome")): x for x in esito.get("candidati", [])}
        controlli = controlli or []
        anni_precisi = {}
        for c in controlli:
            testo = str(c.get("descrizione") or "")
            if not re.search(r"\b(anni|circa|metà|inizio|fine|primi|ultimi|secolo|tra|decennio)\b", testo, re.I):
                anni_precisi[_norma(testo)[:50]] = set(re.findall(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b", testo))
        for a in candidati:
            risposta = per_nome.get(_norma(a.nome)) or next(
                (x for k, x in per_nome.items() if _chiave_nome(a.nome) in k.split()), {"controlli": []})
            # Controllo del programma: la prova di un "sì" o di un "no" deve stare nel blocco DEL candidato (16/10,
            # Galileo: Viviani e Guiducci "gesuiti di Bologna" con le righe di altri candidati).
            mie = intervalli.get(id(a), range(0))
            proprie = {v.url for v in a.voci.values()} | ({a.inizio.url} if a.inizio else set())
            chiave = _chiave_nome(a.nome)
            a.esiti = []
            risposte = []
            for e in risposta.get("controlli", []):
                c = self._risolvi_controllo(e.get("controllo", ""), controlli) if controlli else None
                if controlli and c is None:
                    continue
                risposte.append({**e, "controllo": c["descrizione"] if c else e.get("controllo", ""),
                                 "peso": c.get("peso") if c else None})
            # Ogni controllo ha un esito, anche quelli su cui Haiku tace: "non so" (pesano nel conto della qualità).
            dati = {_norma(e["controllo"])[:50]: e for e in risposte}
            risposte = [dati.get(_norma(c["descrizione"])[:50]) or {"controllo": c["descrizione"], "esito": "non so",
                                                                      "prova": 0, "peso": c.get("peso")}
                        for c in controlli] if controlli else risposte
            for e in risposte:
                numero = e.get("prova")
                valida = isinstance(numero, int) and numero in mie
                if valida:  # e la riga parla del candidato: sua voce, riga del legame o riga che lo nomina
                    p = prove[numero - 1]
                    valida = p.url in proprie or p is a.prova_legame or chiave in _norma(p.testo)
                    # e le parole citate da Haiku sono davvero nella riga (16/10, Emacs: Daniel Murphy "premiato nel
                    # 2009" con una riga che non ne parla)
                    valida = valida and citazione_presente(str(e.get("citazione") or ""), p.testo)
                    # un anno preciso nel controllo deve stare nelle parole citate (Bobrow "premiato nel 2009")
                    anni = anni_precisi.get(_norma(e.get("controllo", ""))[:50], set())
                    valida = valida and (not anni or any(x in str(e.get("citazione") or "") for x in anni))
                a.esiti.append({"controllo": e.get("controllo", ""), "peso": e.get("peso") or 1,
                                "esito": e.get("esito", "non so") if valida or e.get("esito") == "non so" else "non so",
                                "prova": self._prova(prove, numero) if valida else None,
                                # per capire dopo chi ha deciso: l'esito detto dal modello e la riga che ha citato
                                "detto": e.get("esito", "non so"), "citata": self._prova(prove, numero)})
            a.punti = sum(1 for e in a.esiti if e["esito"] == "sì" and e["prova"]) - self._no(a)
            a.qualita = qualita(a.esiti)

    # --- 3. valore ---------------------------------------------------------------------------------------------

    def _valore(self, fascio: list[Anello], altri: list[Anello] | None = None) -> None:
        """Il valore dalle voci di Wikipedia dell'entità finale (riquadro, inizio, righe con le parole della
        risposta), poi dalle pagine lette, poi da due pagine nuove."""
        for a in fascio + (altri or []):
            if self._valore_entita(a, (1, 2, 3) if a in fascio else (1,)):
                return
        self.motivo = self.motivo or "valore richiesto non trovato"

    def _valore_entita(self, a: Anello, tentativi: tuple[int, ...]) -> bool:
        """Tentativi per il valore di un'entità: 1 = voci di Wikipedia, 2 = pezzi già letti (poi due pagine nuove),
        3 = pezzi letti di nuovo. Vero se il valore è stato trovato (in self.valore)."""
        risposta = self.piano.get("risposta") or {}
        if risposta.get("e_entita"):  # niente ricerca del «valore» sul web: il valore è l'entità
            return self._valore_e_entita(a) if 1 in tentativi else False
        parole = list(risposta.get("parole") or []) + [risposta.get("cosa", "")] + list(risposta.get("controlli") or [])
        bonus = self._bonus_valore(risposta)
        for tentativo in tentativi:
            if tentativo == 1:
                prove = self._righe_wiki(self._voci(a), parole, massimo=60, caratteri=14_000, bonus=bonus)
                if not prove:
                    continue
            else:
                prove = self._prove_entita(a.nome, segmenti=self._segmenti_su(a.nome), massimo=40)
                if tentativo == 3:
                    # Anche gli estratti dei risultati della ricerca del valore che nominano l'entità (29/10, Congresso
                    # di Vienna: «Sophie … Stackelberg (фон Мейендорф…) (1806 - 1891)» nell'estratto di Geni, pagina
                    # mai letta: si leggono solo 2 pagine).
                    chiave = _chiave_nome(a.nome)
                    prove += [p for p in self._estratti if chiave in _norma(p.testo)][:8]
            esito = self._decidi("valore", {"domanda": self.r.argomento[:1000], "entita": a.nome,
                                            "cosa": risposta.get("cosa", ""),
                                            "controlli": risposta.get("controlli", []),
                                            **self._vincoli_valore(risposta),
                                            "prove": self._numera(prove)}, {"valore": "", "prova": 0})
            prova = self._prova(prove, esito.get("prova"))
            rifiuto = valore_ammesso(esito.get("valore", ""), risposta) if esito.get("valore") else ""
            if rifiuto:  # il programma, non Haiku, decide se il valore rispetta tipo e intervallo
                self.storia.append({"passo": -1, "relazione": "valore rifiutato", "tentativo": tentativo,
                                    "candidati": [a.nome], "tenuti": [],
                                    "scartati": [(f"{esito.get('valore')}", [rifiuto])]})
                prova = None
            # La riga del valore deve parlare dell'entità: sua voce o suo nome (16/10, Ducato di Milano:
            # l'altitudine di Bereguardo presa dalla voce di Vigevano).
            if prova is not None and not (prova.url in {v.url for v in a.voci.values()}
                                          or _chiave_nome(a.nome) in _norma(prova.testo)):
                prova = None
            if esito.get("valore") and prova:
                self.valore = {"valore": esito["valore"], "prova": prova, "anello": a,
                               "motivo": esito.get("motivo", "")}
                self.motivo = f"catena completa: {a.nome} → {esito['valore']}"
                return True
            self.storia.append({"passo": -1, "relazione": "valore non trovato", "tentativo": tentativo,
                                "candidati": [a.nome], "tenuti": [],
                                "scartati": [(str(esito.get("valore") or "(vuoto)"),
                                              [f"{len(prove)} righe", "voci: " + ", ".join(
                                                  f"{v.titolo} ({v.lingua})" for v in a.voci.values()),
                                               str(esito.get("motivo") or "")[:150]])]})
            if tentativo == 2 and not self.r.pensatore.esaurito and not self.r._limite_raggiunto():
                self.o["max_pagine"] = int(self.o["max_pagine"]) + 2
                # le parole della risposta, nella lingua della ricerca (le prime del piano), non la frase italiana
                chiave = " ".join(str(x) for x in (risposta.get("parole") or [])[:2]) or risposta.get("cosa", "")
                # Mai il nome da solo: con una parola dell'entità di prima si evitano gli omonimi più famosi (25/10,
                # Wikipedia: «Roland Unger» da solo dava il pittore nato nel 1941, non il presidente di Wikivoyage e.V.).
                # Anche il nome da solo (pagine di profilo che non nominano il contesto), ma le 2 pagine si scelgono
                # per lo scopo, con il contesto (25/10, Wikipedia: senza il nome da solo la pagina di Commons di
                # Roland Unger non arrivava più; con il solo nome e nessuno scopo arrivava il pittore omonimo).
                contesto = self._contesto_breve(a)
                self._cerca_e_leggi(list(dict.fromkeys([f"{a.nome} {chiave}"[:120], f"{a.nome} {contesto}".strip(),
                                                        a.nome])), 2,
                                    scopo=f"{risposta.get('cosa', '')} di {a.nome}" + (f" ({a.da.nome})" if a.da else ""))
        return False

    def _valore_e_entita(self, a: Anello) -> bool:
        """La domanda chiede il nome dell'entità dell'ultimo passo (piano: risposta.e_entita): il valore è lei, con la
        riga che la lega all'anello di prima o l'inizio della sua voce. (24/10, Cappella Sistina: «in quale comune si
        trova il simulacro» con ultimo passo «comune in cui si trova»; Haiku cercava nelle righe del comune «il nome
        del comune del simulacro» e rispondeva vuoto, la catena finiva senza risposta.) Chi è giusto lo decidono i
        controlli del passo, già fatti. Ma il legame con l'anello di prima deve avere una riga (sì o in parte): un
        valore cercato fa da filtro, il nome dell'entità no (25/10, Cappella Sistina: Capri Leone, trovato solo per
        descrizione, legame «non so», qualità 0,4, dato come risposta; giusto il 24/10 Novara di Sicilia, legame «in
        parte» con la riga sulla statua dell'Assunta)."""
        if a.qualita < 0.5 and a.esiti:  # più di metà del peso dei controlli (Napoli 0,4; Novara di Sicilia 0,6)
            return False
        if a.da is not None and not any(e["esito"] in ("sì", "in parte") and e["prova"]
                                        and str(e["controllo"]).startswith("è in relazione") for e in a.esiti):
            return False
        prova = a.prova_legame or a.inizio
        if prova is None:
            voce = next(iter(self._voci(a)), None)
            frase = next((r for r in voce.righe if r.tipo == "frase"), None) if voce is not None else None
            if frase is None:
                return False
            prova = Prova(f"[{voce.titolo} ({voce.lingua})] {frase.testo}", voce.url, frase.collegamenti, voce.lingua)
        self.valore = {"valore": a.nome, "prova": prova, "anello": a,
                       "motivo": "la domanda chiede l'entità dell'ultimo passo"}
        self.motivo = f"catena completa: la risposta è l'entità {a.nome}"
        return True

    @staticmethod
    def _bonus_valore(risposta: dict):
        """Righe bersaglio (DS4A): il programma porta avanti le righe che contengono il tipo di dato chiesto, e ancora
        di più quelle con un valore nell'intervallo, prima che Haiku le veda."""
        tipo = str(risposta.get("tipo_valore") or "")
        forme = {"data completa": rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}\b|\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}\b",
                 "giorno e mese": rf"\b\d{{1,2}}\s+(?:{MESI})\b|\b(?:{MESI})\s+\d{{1,2}}\b",
                 "anno": r"\b(?:1\d{3}|20\d{2})\b", "numero": r"\d", "codice": r"\b\d{4,6}\b"}
        forma = forme.get(tipo)
        intervallo = isinstance(risposta.get("minimo"), (int, float)) or isinstance(risposta.get("massimo"), (int, float))
        if forma is None and not intervallo:
            return None

        def bonus(testo: str) -> int:
            punti = 0
            if forma and re.search(forma, testo, re.I):
                punti += 30
                if intervallo:
                    pezzi = re.findall(r"[^;|·]{0,40}?\d[\d.,\u202f\u00a0 ]*", testo)
                    if any(not valore_ammesso(x, risposta) for x in pezzi):
                        punti += 60
            return punti
        return bonus

    @staticmethod
    def _vincoli_valore(risposta: dict) -> dict:
        vincoli = {"tipo_valore": risposta.get("tipo_valore", "")}
        if isinstance(risposta.get("minimo"), (int, float)) or isinstance(risposta.get("massimo"), (int, float)):
            vincoli["intervallo"] = f"{risposta.get('minimo', '…')} - {risposta.get('massimo', '…')}"
        return vincoli

    # --- 4. scrittura --------------------------------------------------------------------------------------------

    def materiale(self) -> str:
        """La catena trovata con le prove, citate con il numero della fonte."""
        numero = lambda url: self.r._numero_fonte(url) if url not in {f["url"] for f in self.r.fonti} else \
            next(f["numero"] for f in self.r.fonti if f["url"] == url)  # noqa: E731
        catena = []
        a = self.valore["anello"] if self.valore else (self.anelli[-1][0] if self.anelli else None)
        while a is not None:
            catena.append(a)
            a = a.da
        radice = catena[-1] if catena else None
        righe = [f"PARTENZA: {radice.nome if radice else self.piano.get('partenza', {}).get('entita', '')}"
                 + (f" (entità nominata dalla domanda, «{radice.relazione}» porta al passo {radice.passo_base + 1})"
                    if radice is not None and radice.passo_base else "")]
        passi = self.piano.get("passi", [])
        for i, a in enumerate(reversed(catena[:-1])):
            relazione = a.relazione or (passi[(radice.passo_base if radice else 0) + i]["relazione"]
                                        if (radice.passo_base if radice else 0) + i < len(passi) else "")
            righe.append(f"\nANELLO «{relazione}» {a.da.nome if a.da else ''} → {a.nome}"
                         + (" (NON CONFERMATO del tutto: i controlli non sono tutti provati, ma nessuna prova lo smentisce)"
                            if a.debole else ""))
            if a.prova_legame:
                righe.append(f"- [{numero(a.prova_legame.url)}] {a.prova_legame.testo[:600]}")
            for e in a.esiti:
                if e["prova"]:
                    righe.append(f"- controllo «{e['controllo']}»: {e['esito']} [{numero(e['prova'].url)}] "
                                 f"{e['prova'].testo[:500]}")
        if self.valore:
            righe.append(f"\nVALORE CERCATO ({self.piano.get('risposta', {}).get('cosa', '')}): "
                         f"{self.valore['valore']} [{numero(self.valore['prova'].url)}] "
                         f"{self.valore['prova'].testo[:600]}")
        altri = [x for fascio in self.anelli[-1:] for x in fascio if not catena or x is not catena[0]]
        if altri:
            righe.append("\nALTRI CANDIDATI DELL'ULTIMO PASSO: " + "; ".join(f"{x.nome} ({x.punti} controlli sì)"
                                                                            for x in altri))
        return "\n".join(righe)

    def scrivi(self, compito: str) -> str | None:
        if not self.anelli:
            return None
        self.r._fase("Claude scrive la risposta dalla catena trovata…")
        letture = [f"{i.get('indizio')}: {'; '.join(i.get('letture') or [])}"
                   for i in self.interpretazione.get("indizi", []) if len(i.get("letture") or []) > 1]
        testo = self.r.pensatore.chiedi(
            "scrittura", istruzioni("scrittura"),
            f"TOPIC: {self.r.argomento}\nTODAY: {self.r.oggi}\n"
            + ("INDIZI AMBIGUI (se la risposta dipende da una lettura, dillo): " + " | ".join(letture) + "\n"
               if letture else "")
            + f"NOTES (la catena percorsa, con le prove e la loro fonte [n]):\n<<<DATI\n{self.materiale()}\nDATI>>>"
            + (f"\n\nLa catena non è completa: {self.motivo}. Nella riga **Risposta:** scrivi «Non trovata» e "
               "quale anello manca." if not self.valore else
               "\n\nLa catena è completa: nella riga **Risposta:** dai il VALORE CERCATO con la sua fonte. Se un anello "
               "è segnato NON CONFERMATO, rispondi comunque con il valore, dillo nei **Limiti** e abbassa la "
               "**Fiducia**: non scrivere «Non trovata».")
            + f"\n\nCOMPITO:\n{compito}")
        self.r.costo = max(self.r.costo, self.r.pensatore.bilancio.speso)
        if not isinstance(testo, str) or not testo.strip():
            return None
        return self._valore_in_testa(testo.strip())

    def _valore_in_testa(self, testo: str) -> str:
        """La riga **Risposta:** la scrive il programma con il valore trovato e la sua fonte; quello che Haiku aveva
        scritto lì (spiegazione e dubbi) va nella riga **Spiegazione:**. (16/10, Firefox e Raspberry Pi: valore giusto
        trovato, ma la riga di Haiku era piena di dubbi e non si capiva quale fosse la risposta.)"""
        if not self.valore:
            return testo
        valore = " ".join(str(self.valore["valore"]).split())
        url = self.valore["prova"].url
        numero = next((f["numero"] for f in self.r.fonti if f.get("url") == url), None)
        riga = f"**Risposta:** {valore}" + (f" [{numero}]" if numero else "") + "."
        trovata = re.search(r"\*\*Risposta:?\*\*:?[ \t]*([^\n]*)", testo)
        if trovata is None:
            return f"{riga}\n\n{testo}"
        resto = trovata.group(1).strip()
        if _norma(resto.replace("*", "")).startswith(_norma(valore)) and len(resto) <= len(valore) + 20:
            return testo
        nuova = riga + (f"\n\n**Spiegazione:** {resto}" if resto else "")
        return testo[:trovata.start()] + nuova + testo[trovata.end():]

    def riepilogo(self) -> dict:
        return {"interpretazione": self.interpretazione, "piano": self.piano, "storia": self.storia,
                "valore": {"valore": self.valore["valore"], "di": self.valore["anello"].nome,
                           "url": self.valore["prova"].url} if self.valore else None,
                "motivo": self.motivo, "azioni": self.azioni,
                "wikipedia": {"voci": self.wiki.lette, "richieste": self.wiki.richieste, "tetto": self.wiki.esaurite}
                if self.wiki is not None else None}
