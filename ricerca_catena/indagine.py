"""Indagine su un argomento: Claude prepara le domande, la catena risponde, il ciclo si ripete, alla fine il PDF.

1. Piano (regia): titolo, obiettivo, sezioni del documento e le prime domande, ognuna un dato preciso.
2. Risposte: ogni domanda va alla catena in un processo a sé (`python -m ricerca_catena.domanda`), alcune in
   parallelo, ognuna con il suo browser.
3. Valutazione (regia): copertura di ogni sezione, se la conoscenza è solida, nuove domande sui buchi e sui nomi
   emersi. Si torna al punto 2 finché la regia non la ritiene solida, o finiscono budget, tempo o giri.
4. Documento (documento.py): sezioni scritte dalle risposte con le fonti, sintesi, appendice, PDF.

Tutto lo stato sta in `indagini/<cartella>/stato.json` (si riprende dopo un'interruzione) e gli eventi in
`eventi.jsonl` (l'app li mostra). Uso: `python -m ricerca_catena.indagine <cartella>` (riprende o avvia).
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from . import RADICE, configurazione
from .modello import claude_json, pulizia
from .modello import query as query_doppie
from .modello.pensatore import errori_schema, istruzioni

GIRI_MASSIMI = 12               # anche senza limiti: oltre, le domande nuove aggiungono poco
STIMA_DOMANDA_USD = 0.20        # prima di avere costi osservati (banco: media 0,14 $, sbagliate 0,34 $)
RISERVA_MINUTI_DOCUMENTO = 10
# Costo stimato di una chiamata di scrittura del documento per modello (una per sezione, più la cornice).
STIMA_SCRITTURA_USD = {"haiku": 0.03, "sonnet": 0.10, "opus": 0.40}
SCHEMA_DOMANDE = {"type": "array", "maxItems": 16, "items": {
    "type": "object", "additionalProperties": False, "required": ["sezione", "domanda", "motivo"],
    "properties": {"sezione": {"type": "string", "maxLength": 20},
                   "domanda": {"type": "string", "minLength": 10, "maxLength": 400},
                   "motivo": {"type": "string", "maxLength": 300}}}}
SCHEMA_SEZIONI = {"type": "array", "maxItems": 10, "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "titolo", "scopo"],
    "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 20},
                   "titolo": {"type": "string", "minLength": 2, "maxLength": 150},
                   "scopo": {"type": "string", "maxLength": 400}}}}
SCHEMA_PIANO = {"type": "object", "additionalProperties": False,
                "required": ["titolo", "obiettivo", "sezioni", "domande"],
                "properties": {"titolo": {"type": "string", "minLength": 3, "maxLength": 200},
                               "obiettivo": {"type": "string", "maxLength": 1000},
                               "sezioni": {**SCHEMA_SEZIONI, "minItems": 2}, "domande": {**SCHEMA_DOMANDE, "minItems": 3}}}
SCHEMA_VALUTA = {"type": "object", "additionalProperties": False,
                 "required": ["sezioni", "conoscenza_solida", "motivo", "nuove_domande", "nuove_sezioni"],
                 "properties": {
                     "sezioni": {"type": "array", "items": {
                         "type": "object", "additionalProperties": False, "required": ["id", "copertura", "nota"],
                         "properties": {"id": {"type": "string"}, "copertura": {"type": "integer", "minimum": 0,
                                                                                  "maximum": 3},
                                        "nota": {"type": "string", "maxLength": 400}}}},
                     "conoscenza_solida": {"type": "boolean"},
                     "motivo": {"type": "string", "maxLength": 600},
                     "nuove_domande": {**SCHEMA_DOMANDE, "maxItems": 12},
                     "nuove_sezioni": {**SCHEMA_SEZIONI, "maxItems": 4}}}


def _ora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def nome_cartella(argomento: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", argomento.lower())[:50].strip("-") or "indagine"
    return f"{datetime.now():%Y-%m-%d-%H%M}-{base}"


def riga(testo: str, etichetta: str) -> str:
    """Il contenuto di una riga «**Etichetta:** …» della risposta della catena ("" se manca)."""
    trovata = re.search(rf"\*\*{etichetta}:?\*\*:?\s*(.+)", testo or "")
    return trovata.group(1).strip() if trovata else ""


class Regia:
    """Le decisioni dell'indagine e la scrittura del documento, con il modello scelto dall'utente."""

    def __init__(self, modello: str, al_costo: Callable[[float], None]) -> None:
        self.modello = modello
        self.al_costo = al_costo

    def chiedi(self, punto: str, oggetto: Any, schema: dict | None, tetto_usd: float = 2.0,
               attesa: float = 900, regole: str | None = None) -> Any:
        """Una chiamata con schema (validato, una correzione) o di testo libero. Solleva ErroreClaude."""
        regole = regole or istruzioni(punto)
        testo = pulizia.dati(oggetto)
        richiesta = testo
        for tentativo in (1, 2):
            try:
                dati, consumo = claude_json.chiedi(regole, richiesta, schema, punto, modello=self.modello,
                                                   max_budget_usd=tetto_usd, attesa=attesa)
            except claude_json.ErroreClaude as errore:
                costo = getattr(getattr(errore, "consumo", None), "costo_usd", 0.0)
                self.al_costo(costo)
                if tentativo == 2:
                    raise
                continue
            self.al_costo(consumo.costo_usd)
            problemi = errori_schema(dati, schema) if schema is not None else []
            if not problemi:
                return dati
            richiesta = f"{testo}\n\nLA RISPOSTA PRECEDENTE NON ERA VALIDA:\n- " + "\n- ".join(problemi)
        raise claude_json.ErroreClaude(f"{punto}: risposta non valida dopo la correzione")


class Indagine:
    def __init__(self, cartella: Path, pubblica: Callable[[dict], None] | None = None) -> None:
        self.cartella = Path(cartella)
        self.file_stato = self.cartella / "stato.json"
        self.stato: dict = json.loads(self.file_stato.read_text(encoding="utf-8"))
        self.pubblica = pubblica or (lambda e: None)
        self._serratura = threading.RLock()  # lo stato si cambia da più thread (una domanda per thread)
        self._fermata = threading.Event()
        self._processi: dict[str, subprocess.Popen] = {}
        self._inizio = time.monotonic() - float(self.stato.get("secondi", 0))
        self.regia = Regia(self.stato["modello_regia"], self._aggiungi_costo_regia)

    # --- creazione e stato --------------------------------------------------------------------------------------

    @classmethod
    def crea(cls, argomento: str, *, modello_regia: str | None = None, budget_usd: float | None = None,
             max_minuti: float | None = None, senza_limiti: bool | None = None,
             cartella_base: Path | None = None) -> "Indagine":
        impostazioni = configurazione.carica()
        argomento = " ".join(argomento.split())
        if not 3 <= len(argomento) <= 4000:
            raise ValueError("Scrivi l'argomento della ricerca (da 3 a 4000 caratteri).")
        modello = modello_regia or impostazioni["modello_regia"]
        if modello not in configurazione.MODELLI:
            raise ValueError(f"Modello non valido: {modello}")
        base = cartella_base or configurazione.cartella("cartella_indagini")
        cartella = base / nome_cartella(argomento)
        cartella.mkdir(parents=True, exist_ok=False)
        stato = {
            "id": cartella.name, "argomento": argomento, "creata": _ora(), "stato": "in_attesa", "fase": "",
            "modello_regia": modello, "modello_catena": impostazioni["modello_catena"],
            "limiti": {"senza_limiti": bool(impostazioni["senza_limiti"] if senza_limiti is None else senza_limiti),
                       "budget_usd": float(budget_usd if budget_usd is not None else impostazioni["budget_usd"]),
                       "max_minuti": float(max_minuti if max_minuti is not None else impostazioni["max_minuti"])},
            "piano": None, "domande": [], "valutazioni": [], "giro": 0, "speso_regia_usd": 0.0, "secondi": 0,
            "motivo_fine": "", "documento": None,
        }
        (cartella / "stato.json").write_text(json.dumps(stato, ensure_ascii=False, indent=1), encoding="utf-8")
        return cls(cartella)

    def salva(self) -> None:
        with self._serratura:
            self.stato["secondi"] = round(time.monotonic() - self._inizio)
            temporaneo = self.file_stato.with_suffix(".tmp")
            temporaneo.write_text(json.dumps(self.stato, ensure_ascii=False, indent=1), encoding="utf-8")
            temporaneo.replace(self.file_stato)

    def evento(self, evento: dict) -> None:
        voce = {"quando": _ora(), **evento}
        with self._serratura:
            with (self.cartella / "eventi.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(voce, ensure_ascii=False, default=str) + "\n")
        self.pubblica(voce)

    def _fase(self, testo: str) -> None:
        self.stato["fase"] = testo
        self.salva()
        self.evento({"tipo": "fase", "testo": testo})

    def _aggiungi_costo_regia(self, costo: float) -> None:
        with self._serratura:
            self.stato["speso_regia_usd"] = round(self.stato["speso_regia_usd"] + costo, 4)

    def speso(self) -> float:
        return round(self.stato["speso_regia_usd"] + sum(d.get("costo_usd") or 0 for d in self.stato["domande"]), 4)

    def trascorsi(self) -> float:
        return time.monotonic() - self._inizio

    # --- limiti -------------------------------------------------------------------------------------------------

    def _stima_domanda(self) -> float:
        costi = [d["costo_usd"] for d in self.stato["domande"] if d.get("costo_usd")]
        return max(STIMA_DOMANDA_USD, sum(costi) / len(costi)) if costi else STIMA_DOMANDA_USD

    def riserva_documento(self) -> float:
        sezioni = len((self.stato.get("piano") or {}).get("sezioni") or []) or 6
        return STIMA_SCRITTURA_USD.get(self.stato["modello_regia"], 0.25) * (sezioni + 2)

    def puo_partire(self, in_corso: int) -> str:
        """"" se un'altra domanda può partire, altrimenti il motivo per cui no."""
        if self._fermata.is_set():
            return "indagine fermata"
        limiti = self.stato["limiti"]
        if limiti["senza_limiti"]:
            return ""
        impegnato = self.speso() + (in_corso + 1) * self._stima_domanda() + self.riserva_documento()
        if impegnato > limiti["budget_usd"]:
            return f"budget di {limiti['budget_usd']:.2f} $ quasi raggiunto (speso {self.speso():.2f} $)"
        if self.trascorsi() / 60 > limiti["max_minuti"] - RISERVA_MINUTI_DOCUMENTO - 5:
            return f"tempo massimo di {limiti['max_minuti']:.0f} minuti quasi raggiunto"
        return ""

    def _minuti_domanda(self) -> float:
        """Minuti massimi per la prossima domanda: quelli della catena, o meno se il tempo dell'indagine finisce."""
        massimo = float(configurazione.carica()["minuti_domanda"])
        limiti = self.stato["limiti"]
        if limiti["senza_limiti"]:
            return massimo
        resto = limiti["max_minuti"] - RISERVA_MINUTI_DOCUMENTO - self.trascorsi() / 60
        return max(5.0, min(massimo, resto))

    def ferma(self) -> None:
        """Ferma l'indagine: nessuna nuova domanda, quelle in corso si chiudono."""
        self._fermata.set()
        for processo in list(self._processi.values()):
            if processo.poll() is None:
                processo.terminate()

    # --- svolgimento --------------------------------------------------------------------------------------------

    def esegui(self, solo_documento: bool = False) -> None:
        """Avvia o riprende l'indagine fino al documento."""
        from . import documento
        for d in self.stato["domande"]:
            if d["stato"] == "in_corso":  # interrotta a metà: si rifà
                d["stato"] = "in_attesa"
        self.stato["stato"] = "in_corso"
        self.salva()
        self.evento({"tipo": "inizio", "argomento": self.stato["argomento"], "modello_regia": self.stato["modello_regia"],
                     "limiti": self.stato["limiti"]})
        try:
            if not solo_documento:
                if self.stato["piano"] is None:
                    self._pianifica()
                self._ciclo()
            if self._fermata.is_set() and not solo_documento:
                self.stato["stato"] = "fermata"
                self.stato["motivo_fine"] = self.stato["motivo_fine"] or "fermata dall'utente"
                self.salva()
                self.evento({"tipo": "fine", "stato": "fermata", "motivo": self.stato["motivo_fine"]})
                return
            self._fase("Scrivo il documento…")
            self.stato["documento"] = documento.scrivi(self)
            self.stato["stato"] = "finita"
            self.salva()
            self.evento({"tipo": "fine", "stato": "finita", "motivo": self.stato["motivo_fine"],
                         "documento": self.stato["documento"], "speso_usd": self.speso()})
        except Exception as errore:  # noqa: BLE001 - lo stato resta salvato: l'indagine si può riprendere
            self.stato["stato"] = "errore"
            self.stato["motivo_fine"] = f"{type(errore).__name__}: {errore}"[:500]
            self.salva()
            self.evento({"tipo": "fine", "stato": "errore", "motivo": self.stato["motivo_fine"]})
            raise

    def _pianifica(self) -> None:
        self._fase("Claude prepara il piano e le prime domande…")
        piano = self.regia.chiedi("regia_piano", {"argomento": self.stato["argomento"],
                                                  "oggi": f"{datetime.now():%Y-%m-%d}"}, SCHEMA_PIANO)
        self.stato["piano"] = {k: piano[k] for k in ("titolo", "obiettivo", "sezioni")}
        self.stato["giro"] = 1
        aggiunte = self._aggiungi_domande(piano["domande"], giro=1)
        self.salva()
        self.evento({"tipo": "piano", "piano": self.stato["piano"], "domande": aggiunte,
                     "speso_usd": self.speso()})

    def _aggiungi_domande(self, domande: list[dict], giro: int) -> list[dict]:
        """Nuove domande, senza i doppioni di quelle già fatte (stessa domanda o quasi: Jaccard ≥ 0,7)."""
        sezioni = {s["id"] for s in self.stato["piano"]["sezioni"]}
        fatte = [d["domanda"] for d in self.stato["domande"]]
        aggiunte = []
        for d in domande:
            testo = " ".join(str(d["domanda"]).split())
            if query_doppie.doppia(testo, fatte, 0.7):
                continue
            fatte.append(testo)
            voce = {"id": f"d{len(self.stato['domande']) + 1}", "giro": giro,
                    "sezione": d["sezione"] if d["sezione"] in sezioni else next(iter(sezioni)),
                    "domanda": testo, "motivo": str(d.get("motivo") or ""), "stato": "in_attesa"}
            self.stato["domande"].append(voce)
            aggiunte.append(voce)
        return aggiunte

    def _ciclo(self) -> None:
        while not self._fermata.is_set():
            in_attesa = [d for d in self.stato["domande"] if d["stato"] == "in_attesa"]
            if in_attesa:
                self._fase(f"Giro {self.stato['giro']}: {len(in_attesa)} domande alla catena…")
                motivo = self._rispondi(in_attesa)
                if motivo:
                    self.stato["motivo_fine"] = motivo
                    break
                continue
            if self.stato["giro"] >= GIRI_MASSIMI:
                self.stato["motivo_fine"] = f"raggiunti {GIRI_MASSIMI} giri di domande"
                break
            if self.puo_partire(0):  # niente budget per un altro giro: si scrive con quello che c'è
                self.stato["motivo_fine"] = self.puo_partire(0)
                break
            if self._valuta():
                break
        self.salva()

    def _valuta(self) -> bool:
        """La regia valuta la conoscenza; True se basta (o non ci sono domande nuove)."""
        self._fase(f"Claude valuta cosa si sa dopo il giro {self.stato['giro']}…")
        valutazione = self.regia.chiedi("regia_valuta", {
            "piano": self.stato["piano"], "oggi": f"{datetime.now():%Y-%m-%d}",
            "domande": [self.in_breve(d) for d in self.stato["domande"]]}, SCHEMA_VALUTA)
        self.stato["giro"] += 1
        for s in valutazione["nuove_sezioni"]:
            if s["id"] not in {x["id"] for x in self.stato["piano"]["sezioni"]}:
                self.stato["piano"]["sezioni"].append(s)
        aggiunte = [] if valutazione["conoscenza_solida"] else \
            self._aggiungi_domande(valutazione["nuove_domande"], giro=self.stato["giro"])
        self.stato["valutazioni"].append({"giro": self.stato["giro"] - 1, "quando": _ora(), **valutazione,
                                          "domande_aggiunte": [d["id"] for d in aggiunte]})
        self.salva()
        self.evento({"tipo": "valutazione", "giro": self.stato["giro"] - 1, "sezioni": valutazione["sezioni"],
                     "conoscenza_solida": valutazione["conoscenza_solida"], "motivo": valutazione["motivo"],
                     "domande": aggiunte, "speso_usd": self.speso()})
        if valutazione["conoscenza_solida"] or not aggiunte:
            self.stato["motivo_fine"] = ("Claude ritiene la conoscenza solida: " if valutazione["conoscenza_solida"]
                                         else "nessuna domanda nuova utile: ") + valutazione["motivo"]
            return True
        return False

    @staticmethod
    def in_breve(d: dict) -> dict:
        """Una domanda e la sua risposta in breve, per la regia (il testo viene dal web: ripulito)."""
        risposta = d.get("risposta") or ""
        return {"id": d["id"], "sezione": d["sezione"], "domanda": d["domanda"],
                "esito": {"risposta": "trovata", "non_trovata": "non trovata"}.get(d["stato"], d["stato"]),
                "risposta": pulizia.testo(riga(risposta, "Risposta"), origine="risposta")[:400],
                "spiegazione": pulizia.testo(riga(risposta, "Spiegazione"), origine="risposta")[:400],
                "fiducia": riga(risposta, "Fiducia")[:120], "fonti": len(d.get("fonti") or [])}

    # --- risposte con la catena ---------------------------------------------------------------------------------

    def _rispondi(self, domande: list[dict]) -> str:
        """Risponde alle domande in parallelo; "" se tutte fatte, altrimenti il motivo per cui ci si è fermati."""
        parallele = max(1, int(configurazione.carica()["domande_in_parallelo"]))
        profili = [f"profilo_{i + 1}" for i in range(parallele)]
        liberi = list(profili)
        serratura = threading.Lock()
        motivo = {"testo": ""}
        coda = list(domande)
        in_corso = {"n": 0}
        fatto = threading.Condition(serratura)

        def lavora(d: dict, profilo: str) -> None:
            try:
                self._una_domanda(d, profilo, parallele)
            finally:
                with fatto:
                    liberi.append(profilo)
                    in_corso["n"] -= 1
                    fatto.notify_all()

        with ThreadPoolExecutor(max_workers=parallele) as esecutore:
            while coda:
                with fatto:
                    while not liberi:
                        fatto.wait()
                    perche = self.puo_partire(in_corso["n"])
                    if perche:
                        motivo["testo"] = perche
                        break
                    d = coda.pop(0)
                    profilo = liberi.pop(0)
                    in_corso["n"] += 1
                esecutore.submit(lavora, d, profilo)
        return motivo["testo"]

    def _una_domanda(self, d: dict, profilo: str, parallele: int) -> None:
        impostazioni = configurazione.carica()
        with self._serratura:
            d.update(stato="in_corso", iniziata=_ora())
        self.salva()
        self.evento({"tipo": "domanda", "id": d["id"], "stato": "in_corso", "domanda": d["domanda"]})
        richiesta = {"domanda": d["domanda"], "profilo": str(configurazione.cartella("cartella_profili") / profilo),
                     "cartella_pezzi": str(self.cartella / "pezzi"),
                     "opzioni": {"modello_claude": self.stato["modello_catena"],
                                 "budget_claude": float(impostazioni["budget_domanda_usd"]),
                                 "pagine_catena": int(impostazioni["pagine_domanda"]),
                                 "minuti_catena": self._minuti_domanda(),
                                 # Wikimedia: 200 richieste al minuto con il contatto, per tutte le domande insieme.
                                 "wikipedia_intervallo": max(0.7, 0.3 * parallele)}}
        python = RADICE / "venv" / "bin" / "python"
        processo = subprocess.Popen([str(python if python.exists() else sys.executable), "-m", "ricerca_catena.domanda"],
                                    cwd=RADICE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self._processi[d["id"]] = processo
        esito, errore = None, ""
        try:
            processo.stdin.write(json.dumps(richiesta, ensure_ascii=False) + "\n")
            processo.stdin.close()
            for linea in processo.stdout:
                try:
                    messaggio = json.loads(linea)
                except ValueError:
                    continue
                if "evento" in messaggio:
                    self._evento_domanda(d, messaggio["evento"])
                elif "esito" in messaggio:
                    esito = messaggio["esito"]
                elif "errore" in messaggio:
                    errore = messaggio["errore"]
            processo.wait()
        finally:
            self._processi.pop(d["id"], None)
        with self._serratura:
            if esito is None:
                d["stato"] = "in_attesa" if self._fermata.is_set() else "errore"
                d["errore"] = errore or ("fermata" if self._fermata.is_set() else
                                         f"processo chiuso ({processo.returncode})")
            else:
                d.update(stato="risposta" if esito.get("trovata") else "non_trovata", risposta=esito["risposta"],
                         valore=esito.get("valore"), fonti=esito["fonti"], costo_usd=esito["costo_usd"],
                         secondi=esito["secondi"], pagine_lette=esito.get("pagine_lette"),
                         motivo_catena=esito.get("motivo_fine"))
            d["finita"] = _ora()
        self.salva()
        self.evento({"tipo": "domanda", "id": d["id"], "stato": d["stato"], "domanda": d["domanda"],
                     "risposta": riga(d.get("risposta") or "", "Risposta")[:300], "errore": d.get("errore"),
                     "costo_usd": d.get("costo_usd"), "secondi": d.get("secondi"), "speso_usd": self.speso()})

    def _evento_domanda(self, d: dict, evento: dict) -> None:
        """Solo gli eventi utili all'app: fasi della catena e pagine lette."""
        if evento.get("tipo") == "fase":
            self.evento({"tipo": "domanda_fase", "id": d["id"], "testo": str(evento.get("testo"))[:200]})
        elif evento.get("tipo") == "pagina" and evento.get("stato") == "letta":
            self.evento({"tipo": "domanda_pagina", "id": d["id"], "url": evento.get("url"),
                         "titolo": evento.get("titolo")})


def main() -> int:
    """`python -m ricerca_catena.indagine <cartella> [--documento]`: avvia o riprende l'indagine."""
    cartella = Path(sys.argv[1])
    indagine = Indagine(cartella)
    signal.signal(signal.SIGTERM, lambda *_: indagine.ferma())
    signal.signal(signal.SIGINT, lambda *_: indagine.ferma())
    try:
        indagine.esegui(solo_documento="--documento" in sys.argv[2:])
    except Exception:  # noqa: BLE001 - già registrato nello stato
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
