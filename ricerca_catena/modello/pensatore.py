"""Il pensatore: Claude come lettore della catena, con budget e predefiniti del programma.

Claude riceve dati brevi e strutturati (righe numerate, mai pagine intere da navigare) e risponde con JSON validato
da uno schema. Se Claude non risponde, se la risposta non è valida dopo una correzione o se il budget della domanda
non basta, il punto usa il suo predefinito e la ricerca prosegue: il risultato lo dice.
"""
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from . import claude_json, pulizia, registro
from . import config as config_pensatore

CARTELLA_PROMPT = Path(__file__).resolve().parent / "prompt"

# Prezzi per la stima prudente prima della chiamata (dollari per milione di token): ingresso scritto in cache
# (il caso peggiore della prima chiamata) e uscita. Dopo la prima chiamata conta il costo osservato.
PREZZO_INGRESSO = 7.5
PREZZO_USCITA = 30.0
USCITA_STIMATA = 1500

SCHEMI: dict[str, dict] = {
    "lettura": {
        "type": "object", "additionalProperties": False,
        "required": ["appunti", "pertinenza", "concetti", "legami", "fatti"],
        "properties": {
            "legami": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["da", "relazione", "a", "frase", "traduzione", "perche"],
                "properties": {"da": {"type": "string", "minLength": 1, "maxLength": 120},
                               "relazione": {"type": "string", "minLength": 2, "maxLength": 80},
                               "a": {"type": "string", "minLength": 1, "maxLength": 120},
                               "frase": {"type": "string", "minLength": 3, "maxLength": 300},
                               "traduzione": {"type": "string", "maxLength": 400},
                               "perche": {"type": "string", "maxLength": 200}}}},
            "fatti": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["concetto", "attributo", "valore", "frase", "traduzione"],
                "properties": {"concetto": {"type": "string", "minLength": 1, "maxLength": 120},
                               "attributo": {"type": "string", "minLength": 2, "maxLength": 60},
                               "valore": {"type": "string", "minLength": 1, "maxLength": 150},
                               "frase": {"type": "string", "minLength": 3, "maxLength": 300},
                               "traduzione": {"type": "string", "maxLength": 400}}}},
            "appunti": {"type": "string", "maxLength": 8000},
            "pertinenza": {"type": "integer", "minimum": 0, "maximum": 3},
            "concetti": {"type": "array", "maxItems": 12, "items": {
                "type": "object", "additionalProperties": False, "required": ["nome", "frase", "traduzione"],
                "properties": {"nome": {"type": "string", "minLength": 1, "maxLength": 120},
                               "frase": {"type": "string", "minLength": 3, "maxLength": 300},
                               "traduzione": {"type": "string", "maxLength": 400}}}},
        },
    },
    "interpreta": {
        "type": "object", "additionalProperties": False, "required": ["riformulazioni", "indizi", "sotto_domande"],
        "properties": {
            "riformulazioni": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 500}},
            "indizi": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "additionalProperties": False, "required": ["indizio", "riferito_a", "letture"],
                "properties": {"indizio": {"type": "string", "maxLength": 200},
                               "riferito_a": {"type": "string", "maxLength": 300},
                               "letture": {"type": "array", "maxItems": 3,
                                           "items": {"type": "string", "maxLength": 200}}}}},
            "sotto_domande": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
        },
    },
    "catena": {
        "type": "object", "additionalProperties": False,
        "required": ["partenza", "passi", "ancore", "risposta", "lingua_ricerca", "lingue", "motivo"],
        "properties": {
            "partenza": {"type": "object", "additionalProperties": False, "required": ["indizio", "entita"],
                         "properties": {"indizio": {"type": "string", "maxLength": 200},
                                        "entita": {"type": "string", "minLength": 2, "maxLength": 120}}},
            "passi": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["relazione", "tipo", "parole", "descrizioni", "controlli"],
                "properties": {"relazione": {"type": "string", "minLength": 2, "maxLength": 150},
                               "tipo": {"type": "string", "maxLength": 60},
                               "parole": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 40}},
                               "descrizioni": {"type": "array", "maxItems": 4,
                                               "items": {"type": "string", "maxLength": 80}},
                               "controlli": {"type": "array", "maxItems": 6, "items": {
                                   "type": "object", "additionalProperties": False,
                                   "required": ["descrizione", "letture", "peso"],
                                   "properties": {"descrizione": {"type": "string", "maxLength": 200},
                                                  "letture": {"type": "array", "maxItems": 3,
                                                              "items": {"type": "string", "maxLength": 150}},
                                                  "peso": {"type": "integer", "minimum": 1, "maximum": 3}}}}}}},
            "risposta": {"type": "object", "additionalProperties": False,
                         "required": ["cosa", "parole", "controlli", "tipo_valore", "e_entita"],
                         "properties": {"cosa": {"type": "string", "minLength": 2, "maxLength": 200},
                                        "e_entita": {"type": "boolean"},
                                        "parole": {"type": "array", "maxItems": 12,
                                                   "items": {"type": "string", "maxLength": 40}},
                                        "controlli": {"type": "array", "maxItems": 4,
                                                      "items": {"type": "string", "maxLength": 200}},
                                        "tipo_valore": {"type": "string", "enum": [
                                            "data completa", "anno", "giorno e mese", "numero", "codice", "nome",
                                            "altro"]},
                                        "minimo": {"type": "number"}, "massimo": {"type": "number"}}},
            "ancore": {"type": "array", "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["entita", "indizio", "relazione", "passo"],
                "properties": {"entita": {"type": "string", "minLength": 2, "maxLength": 120},
                               "indizio": {"type": "string", "maxLength": 200},
                               "relazione": {"type": "string", "minLength": 2, "maxLength": 150},
                               "passo": {"type": "integer", "minimum": 1, "maximum": 6}}}},
            "lingua_ricerca": {"type": "string", "minLength": 2, "maxLength": 3},
            "lingue": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 3}},
            "motivo": {"type": "string", "maxLength": 300},
        },
    },
    "passo": {
        "type": "object", "additionalProperties": False, "required": ["ricerche"],
        "properties": {"ricerche": {"type": "array", "minItems": 1, "maxItems": 3,
                                    "items": {"type": "string", "minLength": 2, "maxLength": 120}}},
    },
    "candidati": {
        "type": "object", "additionalProperties": False, "required": ["candidati"],
        "properties": {"candidati": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["nome", "prova"],
            "properties": {"nome": {"type": "string", "minLength": 2, "maxLength": 120},
                           "prova": {"type": "integer", "minimum": 0}}}}},
    },
    "verifica": {
        "type": "object", "additionalProperties": False, "required": ["candidati"],
        "properties": {"candidati": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False, "required": ["nome", "controlli"],
            "properties": {"nome": {"type": "string", "maxLength": 120},
                           "controlli": {"type": "array", "maxItems": 6, "items": {
                               "type": "object", "additionalProperties": False,
                               "required": ["controllo", "esito", "prova", "citazione"],
                               "properties": {"controllo": {"type": "string", "maxLength": 200},
                                              "esito": {"type": "string", "enum": ["sì", "in parte", "no", "non so"]},
                                              "prova": {"type": "integer", "minimum": 0},
                                              "citazione": {"type": "string", "maxLength": 160}}}}}}}},
    },
    "valore": {
        "type": "object", "additionalProperties": False, "required": ["valore", "prova", "motivo"],
        "properties": {"valore": {"type": "string", "maxLength": 150}, "prova": {"type": "integer", "minimum": 0},
                       "motivo": {"type": "string", "maxLength": 300}},
    },
    "voce": {
        "type": "object", "additionalProperties": False, "required": ["scelta", "motivo"],
        "properties": {"scelta": {"type": "integer", "minimum": 0, "maximum": 7},
                       "motivo": {"type": "string", "maxLength": 200}},
    },
    "finale": {
        "type": "object", "additionalProperties": False, "required": ["scelta", "motivo"],
        "properties": {"scelta": {"type": "integer", "minimum": 0, "maximum": 6},
                       "motivo": {"type": "string", "maxLength": 300}},
    },
}


PUNTI_FINALI = ("scrittura", "valore")


class Bilancio:
    """Quanto si può ancora spendere per Claude in questo piano."""

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = max(0.0, float(budget_usd))
        self.speso = 0.0
        self.chiamate = 0
        # Quota tenuta da parte per scaletta, scrittura e controllo finale (15/10: ricerche lunghe finivano il budget
        # prima della scrittura, "Claude non ha scritto la risposta").
        self.riserva = 0.0

    @property
    def residuo(self) -> float:
        return max(0.0, self.budget_usd - self.speso)

    def disponibile(self, punto: str) -> float:
        """Quanto può spendere questo punto: i punti della raccolta lasciano intatta la riserva."""
        return self.residuo if punto in PUNTI_FINALI else max(0.0, self.residuo - self.riserva)

    @staticmethod
    def stima(punto: str, caratteri: int) -> float:
        """Costo massimo osservato per questo punto (ultime 200 chiamate riuscite), oppure una stima prudente."""
        osservati = [r.get("costo_usd", 0.0) for r in registro.leggi("pensatore")[-200:]
                     if r.get("punto") == punto and r.get("esito") == "ok"]
        prudente = caratteri / 3 * PREZZO_INGRESSO / 1e6 + USCITA_STIMATA * PREZZO_USCITA / 1e6
        return max(osservati) * 1.2 if osservati else prudente

    def basta(self, punto: str, caratteri: int) -> bool:
        return self.stima(punto, caratteri) <= self.disponibile(punto)


def errori_schema(dati: Any, schema: dict) -> list[str]:
    """Tutti gli errori di schema (al massimo 8), in forma leggibile per il modello: «campo: problema»."""
    validatore = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.path) or '(radice)'}: {e.message[:200]}"
            for e in sorted(validatore.iter_errors(dati), key=lambda e: list(e.path))][:8]


def istruzioni(punto: str) -> str:
    return (CARTELLA_PROMPT / f"{punto}.md").read_text(encoding="utf-8").strip()


class Pensatore:
    def __init__(self, id_piano: str, budget_usd: float, pubblica: Callable[[dict], None] | None = None) -> None:
        self.id_piano = id_piano
        self.bilancio = Bilancio(budget_usd)
        self.modello: str | None = None  # modello per tutti i punti di questo piano (es. "haiku"); None = config
        self.esaurito = False  # True dopo la prima chiamata rifiutata per budget
        self._serratura = threading.Lock()
        self._costo_massimo: dict[str, float] = {}
        self.pubblica = pubblica or (lambda e: None)
        self.decisioni: list[dict] = []

    def _evento(self, punto: str, da: str, motivo: str, dati: Any = None) -> None:
        voce = {"tipo": "pensatore", "piano": self.id_piano, "punto": punto, "da": da, "motivo": motivo,
                "speso_usd": round(self.bilancio.speso, 4), "budget_usd": self.bilancio.budget_usd}
        self.decisioni.append({**voce, "dati": dati})
        registro.scrivi("decisioni", {**voce, "dati": dati})
        self.pubblica(voce)

    def decidi(self, punto: str, oggetto: Any, predefinito: Callable[[], Any],
               valida: Callable[[Any], list[str]] | None = None) -> tuple[Any, str]:
        """Chiede a Claude la decisione `punto` sui dati `oggetto`; altrimenti il predefinito. Restituisce (dati, da)."""
        testo = pulizia.dati(oggetto)
        regole = istruzioni(punto)
        if not self.bilancio.basta(punto, len(regole) + len(testo)):
            self._evento(punto, "locale", f"budget di Claude esaurito ({self.bilancio.speso:.3f} $ su "
                                           f"{self.bilancio.budget_usd:.2f} $)")
            return predefinito(), "locale"
        schema = SCHEMI[punto]
        richiesta = testo
        for tentativo in (1, 2):
            try:
                dati, consumo = claude_json.chiedi(regole, richiesta, schema, punto, modello=self.modello,
                                                   max_budget_usd=max(0.01, self.bilancio.disponibile(punto)),
                                                   piano=self.id_piano)
            except claude_json.ErroreClaude as errore:
                consumo_errore = getattr(errore, "consumo", None)
                with self._serratura:
                    self.bilancio.speso += consumo_errore.costo_usd if consumo_errore else 0.0
                self._evento(punto, "locale", f"Claude non disponibile: {errore}")
                return predefinito(), "locale"
            finally:
                with self._serratura:
                    self.bilancio.chiamate += 1
            with self._serratura:  # la catena chiede in parallelo (un thread per entità del fascio)
                self.bilancio.speso += consumo.costo_usd
            problemi = errori_schema(dati, schema) + (valida(dati) if valida else [])
            if not problemi:
                self._evento(punto, "claude", dati.get("motivo", "") if isinstance(dati, dict) else "", dati)
                return dati, "claude"
            if tentativo == 2 or not self.bilancio.basta(punto, len(regole) + len(richiesta)):
                break
            richiesta = f"{testo}\n\nLA RISPOSTA PRECEDENTE NON ERA VALIDA:\n- " + "\n- ".join(problemi[:8])
        self._evento(punto, "locale", "risposta di Claude non valida: " + "; ".join(problemi[:3]))
        return predefinito(), "locale"

    def chiedi(self, punto: str, regole: str, testo: str, schema: dict | None = None) -> Any:
        """Chiamata diretta a Claude per i compiti ripetuti (lettura dei blocchi), sicura con più thread.

        Restituisce i dati (dict con schema, testo senza) oppure None se Claude non è disponibile, non risponde o il
        budget non basta più (allora `esaurito` diventa True). La stima prima della chiamata usa il costo massimo
        osservato in QUESTO piano per il punto (+20%), oppure la stima prudente: niente lettura del registro a ogni
        blocco."""
        caratteri = len(regole) + len(testo)
        with self._serratura:
            stima = self._costo_massimo[punto] * 1.2 if punto in self._costo_massimo else \
                caratteri / 3 * PREZZO_INGRESSO / 1e6 + USCITA_STIMATA * PREZZO_USCITA / 1e6
            if stima > self.bilancio.disponibile(punto):
                if not self.esaurito:
                    self._evento(punto, "locale", f"budget di Claude esaurito ({self.bilancio.speso:.3f} $ su "
                                                  f"{self.bilancio.budget_usd:.2f} $)")
                self.esaurito = True
                return None
            tetto = max(0.01, self.bilancio.disponibile(punto))
        try:
            dati, consumo = claude_json.chiedi(regole, testo, schema, punto, modello=self.modello,
                                               max_budget_usd=tetto, piano=self.id_piano)
        except claude_json.ErroreClaude as errore:
            costo = getattr(getattr(errore, "consumo", None), "costo_usd", 0.0)
            with self._serratura:
                self.bilancio.speso += costo
                self.bilancio.chiamate += 1
            return None
        if schema is not None and errori_schema(dati, schema):
            dati = None
        with self._serratura:
            self.bilancio.speso += consumo.costo_usd
            self.bilancio.chiamate += 1
            self._costo_massimo[punto] = max(self._costo_massimo.get(punto, 0.0), consumo.costo_usd)
        return dati

    def riepilogo(self) -> dict:
        return {"speso_usd": round(self.bilancio.speso, 4), "budget_usd": self.bilancio.budget_usd,
                "chiamate": self.bilancio.chiamate,
                "decisioni": [{k: d[k] for k in ("punto", "da", "motivo")} for d in self.decisioni]}
