"""Triple estratte dalle pagine (Fase D, Parte 8.2 del piano): validazione e controllo della citazione.

Il lettore restituisce per ogni segmento al massimo 8 triple
`{soggetto, relazione, oggetto, tipo_oggetto, forza, citazione, segmento}`. Il controllo più importante contro le
invenzioni del modello piccolo: la `citazione` deve comparire **davvero** nel segmento indicato (dopo aver normalizzato
spazi, virgolette e trattini). Se no, la tripla si scarta e si conta. Le triple che passano hanno origine `estratta`;
solo queste contano come prove.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

FORZE = ("fatto", "affermazione", "segnale_debole", "ipotesi", "interpretazione")
TIPI_OGGETTO = ("entita", "valore", "data", "numero")
MAX_PER_SEGMENTO = 8
MAX_CITAZIONE = 200
MAX_CAMPO = 200

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["triple"],
    "properties": {"triple": {"type": "array", "maxItems": 40, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["soggetto", "relazione", "oggetto", "tipo_oggetto", "forza", "citazione", "segmento"],
        "properties": {
            "soggetto": {"type": "string", "minLength": 1, "maxLength": MAX_CAMPO},
            "relazione": {"type": "string", "minLength": 1, "maxLength": MAX_CAMPO},
            "oggetto": {"type": "string", "minLength": 1, "maxLength": MAX_CAMPO},
            "tipo_oggetto": {"type": "string", "enum": list(TIPI_OGGETTO)},
            "forza": {"type": "string", "enum": list(FORZE)},
            "citazione": {"type": "string", "minLength": 3, "maxLength": MAX_CITAZIONE},
            "segmento": {"type": "string", "pattern": r"^§?\d+$"},
        }}}},
}

_VIRGOLETTE = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "‚": "'",
                             "–": "-", "—": "-", " ": " "})


def _per_confronto(testo: str) -> str:
    testo = unicodedata.normalize("NFKC", str(testo)).translate(_VIRGOLETTE)
    return re.sub(r"\s+", " ", testo).strip().lower()


def citazione_presente(citazione: str, segmento: str) -> bool:
    """La citazione compare nel testo del segmento (spazi, virgolette, trattini e maiuscole normalizzati)."""
    cercata = _per_confronto(citazione).strip(" .…")
    return len(cercata) >= 3 and cercata in _per_confronto(segmento)


def numero_segmento(valore: str) -> int | None:
    trovato = re.fullmatch(r"§?(\d+)", str(valore).strip())
    return int(trovato.group(1)) if trovato else None


@dataclass
class Esito:
    valide: list[dict]
    scartate: list[dict]  # ognuna con il campo "motivo"

    @property
    def quota_scartate(self) -> float:
        totale = len(self.valide) + len(self.scartate)
        return len(self.scartate) / totale if totale else 0.0


def valida(triple: list[dict[str, Any]], segmenti: dict[int, str]) -> Esito:
    """Controlla le triple del lettore contro i segmenti letti ({numero: testo}). Le valide ricevono origine 'estratta'
    e il numero di segmento; al massimo 8 per segmento (le altre si scartano)."""
    valide: list[dict] = []
    scartate: list[dict] = []
    per_segmento: dict[int, int] = {}
    for tripla in triple:
        k = numero_segmento(tripla.get("segmento", ""))
        motivo = ""
        if k is None or k not in segmenti:
            motivo = "segmento inesistente"
        elif tripla.get("forza") not in FORZE or tripla.get("tipo_oggetto") not in TIPI_OGGETTO:
            motivo = "forza o tipo non validi"
        elif not all(str(tripla.get(c, "")).strip() for c in ("soggetto", "relazione", "oggetto")):
            motivo = "campo vuoto"
        elif not citazione_presente(tripla.get("citazione", ""), segmenti[k]):
            motivo = "citazione non trovata nel segmento"
        elif per_segmento.get(k, 0) >= MAX_PER_SEGMENTO:
            motivo = "oltre 8 triple per segmento"
        if motivo:
            scartate.append({**tripla, "motivo": motivo})
            continue
        per_segmento[k] = per_segmento.get(k, 0) + 1
        valide.append({**{c: str(tripla[c]).strip()[:MAX_CAMPO] for c in ("soggetto", "relazione", "oggetto")},
                       "tipo_oggetto": tripla["tipo_oggetto"], "forza": tripla["forza"],
                       "citazione": str(tripla["citazione"]).strip()[:MAX_CITAZIONE], "segmento": k,
                       "origine": "estratta"})
    return Esito(valide, scartate)
