"""Raccolta per una domanda: la catena (catena.py) percorsa con il browser e Claude come lettore.

La catena guida la ricerca; questa classe le dà gli attrezzi: ricerche sul web (DuckDuckGo, Bing di riserva),
scelta dei risultati, lettura delle pagine con il browser (le wiki dall'archivio api.php), lettura dei blocchi da
parte di Claude con i pezzi salvati nel grafo dei concetti, limiti di pagine, minuti e budget, e la risposta scritta
con le fonti. È la stessa raccolta del banco di prova (36/40 con Claude Haiku), senza le strade dei modelli locali.
"""
import hashlib
import html as codifica_html
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

from .. import configurazione
from ..modello import claude_json, pulizia, rigioco
from ..modello import config as config_modello
from ..modello import query as query_doppie
from ..modello.pensatore import SCHEMI, Pensatore, istruzioni
from ..prove.concetti import Concetti
from .browser import Browser, indirizzo_sicuro
from .fonti import chiave_canonica
from .fonti.http import ClientFonte
from .fonti.limiti import limitatore

ESTRATTORE = Path(__file__).with_name("estrattore.js").read_text(encoding="utf-8")
BLOCCO = 10_000          # caratteri per blocco di lettura
MAX_BLOCCHI = 8          # al massimo ~80.000 caratteri letti per pagina
MIN_TESTO = 600          # sotto questa lunghezza la pagina non è una fonte utile
# Sul nome dell'host, a parole intere: «x.com» dentro «linux.com» non conta.
ESCLUSI = re.compile(r"(^|\.)((youtube|facebook|instagram|tiktok|pinterest|twitter|amazon|ebay|linkedin|duckduckgo|bing|"
                     r"google)\.[a-z.]+|x\.com|youtu\.be)$", re.IGNORECASE)
REDDIT = re.compile(r"(^|\.)(reddit\.com|redd\.it)$", re.IGNORECASE)
PAROLE_COMUNI = {"the", "this", "that", "these", "in", "on", "at", "a", "an", "it", "he", "she", "they", "his",
                 "her", "il", "la", "lo", "le", "gli", "i", "un", "una", "di", "da", "per", "con", "nel", "nella",
                 "however", "after", "before", "during", "when", "while", "also", "some", "many", "according",
                 "source", "sources", "fonte", "fonti", "part", "none", "topic", "notes"}
# Limiti di una domanda: quelli del banco di prova (30 pagine, 35 minuti, 0,50 $ di Claude).
OPZIONI_PREDEFINITE = {"soglia_novita": 0.25, "minimo_nuove": 10, "max_per_sito": 3, "pagine_catena": 30,
                       "minuti_catena": 35, "budget_claude": 0.50, "riserva_scrittura": 0.08}
ISTRUZIONI_BREVE = (
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


class Fermata(Exception):
    """L'utente ha fermato la ricerca."""


def _json(testo: str) -> Any:
    trovato = re.search(r"[\[{].*[\]}]", testo, re.S)
    return json.loads(trovato.group(0)) if trovato else None


def entita(testo: str) -> set[str]:
    """Nomi propri, date e numeri citati in un testo: servono a misurare quanta informazione NUOVA porta una pagina."""
    # Solo parole maiuscole a metà frase: a inizio frase o di punto elenco sono spesso parole comuni ("During").
    trovate = {m.group(0).lower() for m in re.finditer(r"(?<=[a-zà-ÿ0-9,;:)] )[A-ZÀ-Ý][\w'’-]{2,}", testo)} \
        - PAROLE_COMUNI
    trovate.update(m.group(0).lower() for m in re.finditer(r"\b\d{2,4}(?:\s?(?:dr|bc|ad|d\.c\.|a\.c\.))?\b",
                                                            testo, re.IGNORECASE))
    return trovate


def dividi_in_blocchi(testo: str) -> list[str]:
    """Blocchi da BLOCCO caratteri, al massimo MAX_BLOCCHI."""
    return [testo[i:i + BLOCCO] for i in range(0, min(len(testo), BLOCCO * MAX_BLOCCHI), BLOCCO)]


class _SenzaBrowser:
    """Nel rigioco le pagine vengono dalla registrazione."""

    def close(self) -> None:
        pass


class Raccolta:
    """Una domanda: `esegui()` percorre la catena e restituisce la risposta con le fonti."""

    def __init__(self, domanda: str, *, pubblica: Callable[[dict], None] = lambda e: None,
                 fermata: Callable[[], bool] = lambda: False, opzioni: dict | None = None,
                 consenti_locali: bool = False, profilo: Path | None = None,
                 cartella_pezzi: Path | None = None) -> None:
        self.o = {**OPZIONI_PREDEFINITE, **(opzioni or {})}
        self.o["max_pagine"] = int(self.o["pagine_catena"])
        self.o["max_minuti"] = float(self.o["minuti_catena"])
        self.argomento = domanda.strip()
        self.oggi = rigioco.oggi()
        self.id_piano = "p-" + secrets.token_hex(4)
        self.pensatore = Pensatore(self.id_piano, float(self.o["budget_claude"]), pubblica=pubblica)
        self.pensatore.modello = str(self.o.get("modello_claude") or configurazione.carica()["modello_catena"])
        self.pubblica = pubblica
        self.fermata = fermata
        self.consenti_locali = consenti_locali
        self.profilo = profilo or configurazione.cartella("cartella_profili") / "principale"
        self.cartella_pezzi = cartella_pezzi or configurazione.cartella("cartella_indagini") / ".pezzi"
        self.fonti: list[dict] = []           # pagine utili, numerate per le citazioni
        self.coda: list[dict] = []            # pagine candidate da leggere
        self.visitati: set[str] = set()
        self.pagine_lette: set[str] = set()   # pagine con testo utile (contano per il limite di pagine)
        self.ricerca_di: dict[str, str] = {}  # url → ricerca che l'ha trovata (l'anello cercato)
        self.falliti: Counter[str] = Counter()  # errori e blocchi anti-robot per dominio
        self.impronte: set[str] = set()       # contenuti già letti (la stessa pagina da indirizzi diversi)
        self.ricerche_fatte: list[str] = []
        self.query_eseguite: list[str] = []
        self.conoscenza: set[str] = set()     # entità già note (per misurare la novità)
        self.citazioni: Counter[str] = Counter()
        self.poco_nuove = 0
        self.concetti: Concetti | None = None  # pezzi di pagina salvati (la catena ne sceglie le righe)
        self.catena = None
        self.costo = 0.0
        self.motivo_fine = ""
        self.autore = ""
        self.browser: Browser | None = None
        self._in_corso: list[tuple[dict, list[Future], dict[int, tuple]]] = []
        self._lavoratori = ThreadPoolExecutor(max_workers=3)
        self._archivio: ClientFonte | None = None
        self._inizio = time.monotonic()
        self._attesa_iniziale = claude_json.ATTESA_LIMITE["secondi"]

    # --- svolgimento -------------------------------------------------------------------------------------------

    def esegui(self) -> dict:
        from .catena import Catena
        self.pensatore.bilancio.riserva = float(self.o["riserva_scrittura"])
        self.cartella_pezzi.mkdir(parents=True, exist_ok=True)
        self.concetti = Concetti(self.cartella_pezzi / f"{self.id_piano}.sqlite")
        self.browser = _SenzaBrowser() if rigioco.in_rigioco() else Browser(
            "about:blank", configurazione.cartella("cartella_motori"), self.profilo, headless=True,
            consenti_locali=self.consenti_locali)
        try:
            self.catena = Catena(self)
            self.catena.percorri()
            self.motivo_fine = self.catena.motivo or "catena percorsa"
            self._fase(f"Raccolta conclusa: {self.motivo_fine}.")
        finally:
            self.browser.close()
            self._lavoratori.shutdown(wait=False, cancel_futures=True)
            if self._archivio is not None:
                self._archivio.chiudi()
        risposta = self.scrivi()
        self.costo = max(self.costo, self.pensatore.bilancio.speso)
        esito = {"domanda": self.argomento, "risposta": risposta, "fonti": self._elenco_fonti(),
                 "valore": (self.catena.valore or {}).get("valore"), "trovata": bool(self.catena.valore),
                 "confermata": self.catena.controlli()["confermata"],
                 "secondi": round(self._trascorsi()), "costo_usd": round(self.costo, 4),
                 "motivo_fine": self.motivo_fine, "ricerche": self.ricerche_fatte, "autore": self.autore,
                 "pagine_lette": len(self.pagine_lette), "pezzi": str(self.cartella_pezzi / f"{self.id_piano}.sqlite"),
                 "catena": self.catena.riepilogo(), "pensatore": self.pensatore.riepilogo()}
        self.concetti.chiudi()
        return esito

    def _controlla(self) -> None:
        if self.fermata():
            raise Fermata()

    def _fase(self, testo: str) -> None:
        self.pubblica({"tipo": "fase", "testo": testo})

    def _trascorsi(self) -> float:
        """Secondi dall'inizio, senza le attese per il limite di sessione di Claude."""
        return time.monotonic() - self._inizio - (claude_json.ATTESA_LIMITE["secondi"] - self._attesa_iniziale)

    def _limite_raggiunto(self) -> str:
        if self.pensatore.esaurito:
            return "budget di Claude esaurito"
        if len(self.pagine_lette) >= int(self.o["max_pagine"]):
            return f"raggiunto il limite di {len(self.pagine_lette)} pagine web lette"
        if len(self.visitati) >= 3 * int(self.o["max_pagine"]):
            return f"raggiunto il tetto di {3 * int(self.o['max_pagine'])} pagine visitate"
        if self._trascorsi() > 60 * float(self.o["max_minuti"]):
            return f"raggiunto il limite di sicurezza di {float(self.o['max_minuti']):.0f} minuti"
        return ""

    def _chiedi(self, istruzioni_compito: str, testo: str) -> str:
        """Le richieste di testo libero (scelta dei risultati, riformulazioni) a Claude, punto "lettura"; il testo
        dal web passa dalla pulizia per frasi."""
        risposta = self.pensatore.chiedi("lettura", istruzioni_compito, pulizia.testo(testo, origine="ricerca"))
        return risposta if isinstance(risposta, str) else ""

    # --- ricerche ----------------------------------------------------------------------------------------------

    def _filtra_query(self, ricerche: list[str]) -> list[str]:
        """Una query troppo simile (Jaccard ≥ soglia) a una già eseguita non parte: Claude la riformula una volta, e se
        è ancora doppia si salta."""
        soglia = config_modello.carica().query.soglia_doppia
        tenute = []
        for ricerca in ricerche:
            somiglianza, simile = query_doppie.piu_simile(ricerca, self.query_eseguite)
            if somiglianza >= soglia:
                nuova = self._riformula(ricerca)
                doppia = not nuova or query_doppie.doppia(nuova, self.query_eseguite, soglia)
                self.pubblica({"tipo": "query_doppia", "query": ricerca, "simile_a": simile,
                               "somiglianza": round(somiglianza, 2), "riformulata": nuova, "scartata": doppia})
                if doppia:
                    continue
                ricerca = nuova
            self.query_eseguite.append(ricerca)
            tenute.append(ricerca)
        return tenute

    def _riformula(self, ricerca: str) -> str:
        risposta = self._chiedi(
            "The QUERY is almost the same as one of the DONE QUERIES of this research, so it would find the same "
            "pages. Write ONE different short search query (same language, exact names) that looks for information "
            'the DONE QUERIES do not cover. Reply only with JSON: {"query": "..."}.',
            f"TOPIC: {self.argomento}\n\nDONE QUERIES:\n" + "\n".join(self.query_eseguite) + f"\n\nQUERY: {ricerca}")
        try:
            return str((_json(risposta) or {}).get("query") or "").strip()[:200]
        except (ValueError, AttributeError):
            return ""

    def _risultati(self, url: str, script: str) -> list[dict]:
        return rigioco.attraverso("ricerca", url, lambda: self._risultati_vivi(url, script))

    def _risultati_vivi(self, url: str, script: str) -> list[dict]:
        try:
            self.browser.pagina.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self.browser.pagina.wait_for_timeout(1500)
            return self.browser.pagina.evaluate(script) or []
        except Exception:  # noqa: BLE001 - un motore che non risponde non ferma la ricerca
            return []

    def raccogli(self, ricerche: list[str]) -> list[dict]:
        """Risultati dei motori di ricerca (DuckDuckGo, Bing di riserva): indirizzo, titolo ed estratto."""
        visti: dict[str, dict] = {}
        for ricerca in ricerche:
            self._controlla()
            self._fase(f"Cerco: {ricerca}")
            risultati = self._risultati(
                f"https://html.duckduckgo.com/html/?q={quote_plus(ricerca)}",
                """[...document.querySelectorAll('.result')].map(r => ({url: r.querySelector('a.result__a')?.href,
                    titolo: r.querySelector('a.result__a')?.innerText, estratto: r.querySelector('.result__snippet')?.innerText}))""")
            if not risultati:
                risultati = self._risultati(
                    f"https://www.bing.com/search?q={quote_plus(ricerca)}",
                    """[...document.querySelectorAll('li.b_algo')].map(r => ({url: r.querySelector('h2 a')?.href,
                        titolo: r.querySelector('h2 a')?.innerText, estratto: r.querySelector('.b_caption p, p')?.innerText}))""")
            for r in risultati:
                url = r.get("url") or ""
                if "duckduckgo.com/l/" in url:  # collegamento di reindirizzamento di DuckDuckGo
                    url = parse_qs(urlsplit(url).query).get("uddg", [""])[0]
                if not url.startswith("http") or url in visti or url in self.visitati:
                    continue
                if chiave_canonica(url) or self._escluso(url):
                    continue  # piattaforme che il lettore di pagine non legge (video, domande, Wikidata)
                visti[url] = {"url": url, "titolo": (r.get("titolo") or "")[:150],
                              "estratto": (r.get("estratto") or "")[:300]}
                self.ricerca_di.setdefault(url, ricerca)  # la ricerca che ha trovato la pagina (anello cercato)
        return list(visti.values())

    def _aggiungi_candidati(self, candidati: list[dict], scopo: str = "") -> None:
        """Claude ordina i risultati per utilità e scarta quelli inutili; si aggiungono in coda. Con `scopo` (un passo
        della catena) l'utilità è per quel passo, non per la domanda intera."""
        if not candidati:
            return
        elenco = "\n".join(f"{i + 1}. {c['titolo']} — {urlsplit(c['url']).netloc}\n   {c['estratto']}"
                           for i, c in enumerate(candidati[:40]))
        risposta = self._chiedi(
            "Rank the search results that are useful and reliable for an accurate, in-depth report on the TOPIC, "
            "from most to least useful: prefer encyclopedias, specialised wikis, official, academic or expert sites; "
            "leave out shops, thin forums, unrelated pages and duplicates. "
            'Reply only with JSON: {"fonti": [numbers in order of usefulness]}.' + (
                " The TOPIC is one step of a research chain: the useful pages are the ones likely to state that fact "
                "(official site, company or project page, profile, news, encyclopedia), not general pages on the "
                "subject." if scopo else ""),
            f"TOPIC: {scopo or self.argomento}\n\nRESULTS:\n{elenco}")
        try:
            numeri = [int(n) for n in (_json(risposta) or {}).get("fonti", [])]
        except (ValueError, TypeError, AttributeError):
            numeri = []
        ordinati = [candidati[n - 1] for n in dict.fromkeys(numeri) if 1 <= n <= min(40, len(candidati))]
        if not ordinati:  # Claude non ha risposto bene: si tiene l'ordine dei motori di ricerca
            ordinati = candidati[:12]
        for c in ordinati:
            dominio = urlsplit(c["url"]).netloc
            gia = sum(urlsplit(u).netloc == dominio for u in self.visitati) + \
                sum(urlsplit(x["url"]).netloc == dominio for x in self.coda)
            if self.falliti[dominio] >= 2:
                continue  # sito che ha già fallito due volte (errore o blocco anti-robot)
            if c["url"] not in self.visitati and gia < int(self.o["max_per_sito"]):
                self.coda.append(c)

    def _escluso(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        if self.o.get("senza_wikidata") and host.endswith("wikidata.org"):
            return True
        return bool(ESCLUSI.search(host)) or bool(REDDIT.search(host))

    # --- lettura ------------------------------------------------------------------------------------------------

    def leggi(self, fonte: dict) -> None:
        url = fonte["url"]
        self.visitati.add(url)
        if not rigioco.attraverso("sicuro", url, lambda: self.consenti_locali or indirizzo_sicuro(url)):
            return
        numero = len(self.visitati)
        self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "titolo": fonte.get("titolo"),
                       "stato": "lettura"})
        try:
            estratto = rigioco.attraverso("pagina", url, lambda: self._estrai(url))
            if estratto.get("verifica_antibot"):
                self.falliti[urlsplit(url).netloc] += 1
                self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "stato": "scartata",
                               "testo": "il sito blocca i browser automatici (verifica anti-bot)"})
                return
        except rigioco.RichiestaNonRegistrata:
            raise
        except Exception as errore:  # noqa: BLE001
            self.falliti[urlsplit(url).netloc] += 1
            self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "stato": "errore",
                           "testo": str(errore).splitlines()[0][:150]})
            return
        self._annota(numero, url, estratto.get("titolo") or fonte.get("titolo"), estratto.get("testo", ""))

    def _estrai(self, url: str) -> dict:
        estratto = self._da_archivio_wiki(url)
        if estratto is None:
            self.browser.pagina.goto(url, wait_until="load", timeout=30_000)
            self.browser.pagina.wait_for_timeout(1200)
            estratto = self.browser.pagina.evaluate(ESTRATTORE)
        return estratto

    def _annota(self, numero: int, url: str, titolo: str | None, testo: str) -> None:
        """Testo letto → blocchi → lettura di Claude in sottofondo."""
        if len(testo) < MIN_TESTO:
            self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "stato": "scartata",
                           "testo": "troppo poco testo"})
            return
        # Tutto il testo: alcuni siti (Treccani) hanno lo stesso lungo preambolo in ogni pagina.
        impronta = hashlib.sha256(re.sub(r"\s+", " ", testo).encode()).hexdigest()
        if impronta in self.impronte:
            self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "stato": "scartata",
                           "testo": "stesso contenuto di una pagina già letta"})
            return
        self.impronte.add(impronta)
        self.pagine_lette.add(url)
        blocchi = dividi_in_blocchi(testo)
        rinviati: dict[int, tuple] = {}
        futuri = [self._lavoratori.submit(self._leggi_blocco, titolo, url, i, len(blocchi), blocco, rinviati)
                  for i, blocco in enumerate(blocchi, start=1)]
        self._in_corso.append(({"numero": numero, "url": url, "titolo": titolo, "caratteri": len(testo),
                                "blocchi": len(blocchi)}, futuri, rinviati))
        self.pubblica({"tipo": "pagina", "numero": numero, "url": url, "titolo": titolo, "stato": "appunti",
                       "caratteri": len(testo), "blocchi": len(blocchi)})

    def _leggi_blocco(self, titolo: str, url: str, i: int, totale: int, blocco: str,
                      rinviati: dict[int, tuple] | None = None) -> tuple[int, str]:
        """Claude legge il blocco (ripulito) e dà appunti, pertinenza e concetti con la frase da cui vengono; il pezzo
        si salva tra i pezzi della domanda, da cui la catena sceglie le righe. Con `rinviati` il salvataggio aspetta:
        i blocchi finiscono in un ordine a caso, e il grafo deve venire uguale a ogni corsa."""
        def salva(*argomenti: Any) -> None:
            if rinviati is None:
                self.concetti.registra(*argomenti)
            else:
                rinviati[i] = argomenti

        if self.fermata():
            return 0, ""
        pulito = pulizia.testo(blocco, origine=url)
        if not self.o.get("lettura_catena", True):
            # Lettura del modello spenta (22/10): il pezzo si salva così com'è; la catena sceglie da sé le righe.
            salva(url, titolo or url, i, pulito, [], [], [])
            return 2, ""
        dati = self.pensatore.chiedi(
            "lettura", istruzioni("lettura"),
            f"ARGOMENTO: {self.argomento}\n"
            + (f"RICERCA CHE HA TROVATO LA PAGINA: {pulizia.campo(self.ricerca_di[url], 200)}\n"
               if url in self.ricerca_di else "")
            + f"FONTE: {pulizia.campo(titolo, 200)} ({url}) — parte {i} di {totale}"
            f"\n\n<<<DATI\n{pulito}\nDATI>>>", SCHEMI["lettura"])
        if not isinstance(dati, dict):
            return 0, ""
        pertinenza = int(dati.get("pertinenza", 0))
        if pertinenza >= 1 and (dati.get("concetti") or dati.get("fatti")):  # anche i pezzi citati di passaggio
            salva(url, titolo or url, i, pulito, dati.get("concetti", []), dati.get("legami", []), dati.get("fatti", []))
        appunti = str(dati.get("appunti") or "").strip()
        return (pertinenza, appunti) if pertinenza >= 2 and appunti else (pertinenza, "")

    def _valuta_completati(self, attendi: bool) -> None:
        """Registra le pagine i cui appunti sono pronti, nell'ordine di lettura, e ne misura pertinenza e novità."""
        while self._in_corso:
            fonte, futuri, rinviati = self._in_corso[0]
            if not attendi and not all(f.done() for f in futuri):
                return
            self._in_corso.pop(0)
            risultati = [f.result() for f in futuri]
            for numero_blocco in sorted(rinviati):
                self.concetti.registra(*rinviati[numero_blocco])
            self._controlla()
            appunti = [n for _, n in risultati if n]
            pertinenza = self._pertinenza([p for p, _ in risultati])
            if pertinenza <= 1 or not appunti:
                self.pubblica({"tipo": "pagina", "numero": fonte["numero"], "url": fonte["url"],
                               "stato": "scartata", "testo": "fuori tema" if pertinenza <= 1 else
                               "nessuna informazione utile"})
                continue
            testo = "\n".join(appunti)
            presenti, collegata = self._ancorate(testo)
            if not collegata:
                self.pubblica({"tipo": "pagina", "numero": fonte["numero"], "url": fonte["url"],
                               "stato": "scartata", "testo": "non collegata a ciò che si sa dell'argomento"})
                continue
            nuove = presenti - self.conoscenza
            novita = len(nuove) / max(1, len(presenti))
            # Una pagina che parla dell'argomento solo in parte (pertinenza 2) per contare come "nuova" deve
            # superare soglie doppie.
            fattore, minimo = 1 if pertinenza == 3 else 2, int(self.o["minimo_nuove"])
            poca = len(nuove) < fattore * minimo or novita < fattore * float(self.o["soglia_novita"])
            self.poco_nuove = self.poco_nuove + 1 if poca else 0
            self.conoscenza |= nuove
            self.citazioni.update(presenti)
            self.fonti.append({**fonte, "numero": len(self.fonti) + 1, "appunti": testo, "nuove": len(nuove),
                               "pertinenza": pertinenza})
            self.pubblica({"tipo": "pagina", "numero": fonte["numero"], "url": fonte["url"],
                           "titolo": fonte["titolo"], "stato": "letta", "caratteri": fonte["caratteri"],
                           "nuove_informazioni": len(nuove), "pertinenza": pertinenza})

    def _ancorate(self, appunti: str) -> tuple[set[str], bool]:
        """Entità delle righe di appunti "ancorate" (che nominano anche qualcosa di già noto), e se la pagina è
        collegata all'argomento (almeno il 30% delle righe con nomi è ancorata). La prima pagina fa da base."""
        if len(self.conoscenza) < 30:
            return entita(appunti), True
        righe = [e for e in (entita(r) for r in appunti.splitlines()) if e]
        ancorate = [e for e in righe if e & self.conoscenza]
        if righe and len(ancorate) / len(righe) < 0.3:
            return set(), False
        return set().union(*ancorate) if ancorate else set(), True

    @staticmethod
    def _pertinenza(voti: list[int]) -> int:
        """Pertinenza della pagina (0-3): media dei voti di Claude sui blocchi."""
        if not voti:
            return 0
        media = sum(voti) / len(voti)
        return 3 if media >= 2.5 else 2 if media >= 2 else 1 if media >= 1 else 0

    def _client_archivio(self, host: str) -> ClientFonte:
        """Il client HTTP sicuro per api.php (redirect e indirizzi controllati). Wikipedia passa dal client della
        catena, con il suo User-Agent e i suoi limiti di frequenza."""
        wiki = self.catena.wiki if self.catena is not None else None
        if wiki is not None and (host == "wikipedia.org" or host.endswith(".wikipedia.org")):
            return wiki.client
        if self._archivio is None:
            self._archivio = ClientFonte("Archivio wiki", user_agent="ricerca-catena/0.1 (uso personale)", domini=None,
                                         limitatore=limitatore("archivio-wiki", 1.0),
                                         consenti_locali=self.consenti_locali)
        return self._archivio

    def _da_archivio_wiki(self, url: str) -> dict | None:
        """Per le wiki (MediaWiki: Fandom, Wikipedia...) il testo si prende dall'archivio ufficiale api.php: è l'HTML
        del solo contenuto, e funziona anche quando la pagina è protetta da verifiche anti-bot."""
        parti = urlsplit(url)
        if "/wiki/" not in parti.path:
            return None
        titolo = unquote(parti.path.split("/wiki/", 1)[1])
        for percorso in ("/w/api.php", "/api.php"):
            archivio = f"{parti.scheme}://{parti.netloc}{percorso}"
            try:
                dati = self._client_archivio(parti.hostname or "").get_json(archivio, params={
                    "action": "parse", "page": titolo, "prop": "text", "format": "json", "formatversion": 2,
                    "redirects": 1})
            except Exception:  # noqa: BLE001 - archivio assente o non permesso: si legge la pagina con il browser
                continue
            html = (dati.get("parse") or {}).get("text")
            if isinstance(html, str) and len(html) > 500:
                # Pagina vuota prima: le regole di sicurezza della pagina precedente (es. DuckDuckGo) bloccherebbero
                # il <base> e i collegamenti verrebbero costruiti con l'indirizzo sbagliato.
                base = codifica_html.escape(url, quote=True)
                nome = codifica_html.escape(str(dati["parse"].get("title") or ""))
                self.browser.pagina.goto("about:blank")
                self.browser.pagina.set_content(
                    f'<html><head><base href="{base}"><title>{nome}</title></head>'
                    f'<body><div class="mw-parser-output">{html}</div></body></html>', wait_until="domcontentloaded")
                return self.browser.pagina.evaluate(ESTRATTORE)
        return None

    # --- risposta -----------------------------------------------------------------------------------------------

    def scrivi(self) -> str:
        testo = self.catena.scrivi(ISTRUZIONI_BREVE) if self.catena is not None else None
        self.autore = f"Claude ({self.pensatore.modello}) dalla catena"
        motivo = (self.catena.motivo if self.catena is not None else "") or "catena non percorsa"
        return testo or f"**Risposta:** Non trovata: {motivo}."

    def _numero_fonte(self, url: str) -> int:
        """Una pagina con prove ma senza appunti utili: si aggiunge alle fonti, così la citazione [n] esiste."""
        self.fonti.append({"url": url, "titolo": url, "numero": len(self.fonti) + 1, "appunti": "", "nuove": 0,
                           "caratteri": 0, "pertinenza": 2})
        return len(self.fonti)

    def _elenco_fonti(self) -> list[dict]:
        return [{"numero": f["numero"], "titolo": f["titolo"], "url": f["url"]} for f in self.fonti]
