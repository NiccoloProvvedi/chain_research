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

FORCES = ("fact", "claim", "weak_signal", "hypothesis", "interpretation")
OBJECT_TYPES = ("entity", "value", "date", "number")
MAX_PER_SEGMENT = 8
MAX_QUOTE = 200
MAX_FIELD = 200

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["triples"],
    "properties": {"triples": {"type": "array", "maxItems": 40, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["subject", "relation", "object", "object_kind", "force", "quote", "segment"],
        "properties": {
            "subject": {"type": "string", "minLength": 1, "maxLength": MAX_FIELD},
            "relation": {"type": "string", "minLength": 1, "maxLength": MAX_FIELD},
            "object": {"type": "string", "minLength": 1, "maxLength": MAX_FIELD},
            "object_kind": {"type": "string", "enum": list(OBJECT_TYPES)},
            "force": {"type": "string", "enum": list(FORCES)},
            "quote": {"type": "string", "minLength": 3, "maxLength": MAX_QUOTE},
            "segment": {"type": "string", "pattern": r"^§?\d+$"},
        }}}},
}

_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "‚": "'",
                             "–": "-", "—": "-", " ": " "})


def _per_comparison(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().lower()


def present_quote(quote: str, segment: str) -> bool:
    """La citazione compare nel testo del segmento (spazi, virgolette, trattini e maiuscole normalizzati)."""
    searched = _per_comparison(quote).strip(" .…")
    return len(searched) >= 3 and searched in _per_comparison(segment)


def segment_number(value: str) -> int | None:
    match = re.fullmatch(r"§?(\d+)", str(value).strip())
    return int(match.group(1)) if match else None


@dataclass
class Outcome:
    valid: list[dict]
    discarded: list[dict]  # ognuna con il campo "motivo"

    @property
    def discarded_share(self) -> float:
        total = len(self.valid) + len(self.discarded)
        return len(self.discarded) / total if total else 0.0


def is_valid(triples: list[dict[str, Any]], segments: dict[int, str]) -> Outcome:
    """Controlla le triple del lettore contro i segmenti letti ({numero: testo}). Le valide ricevono origine 'estratta'
    e il numero di segmento; al massimo 8 per segmento (le altre si scartano)."""
    valid: list[dict] = []
    discarded: list[dict] = []
    per_segment: dict[int, int] = {}
    for triple in triples:
        k = segment_number(triple.get("segment", ""))
        reason = ""
        if k is None or k not in segments:
            reason = "segmento inesistente"
        elif triple.get("force") not in FORCES or triple.get("object_kind") not in OBJECT_TYPES:
            reason = "forza o tipo non validi"
        elif not all(str(triple.get(c, "")).strip() for c in ("subject", "relation", "object")):
            reason = "campo vuoto"
        elif not present_quote(triple.get("quote", ""), segments[k]):
            reason = "citazione non trovata nel segmento"
        elif per_segment.get(k, 0) >= MAX_PER_SEGMENT:
            reason = "oltre 8 triple per segmento"
        if reason:
            discarded.append({**triple, "reason": reason})
            continue
        per_segment[k] = per_segment.get(k, 0) + 1
        valid.append({**{c: str(triple[c]).strip()[:MAX_FIELD] for c in ("subject", "relation", "object")},
                       "object_kind": triple["object_kind"], "force": triple["force"],
                       "quote": str(triple["quote"]).strip()[:MAX_QUOTE], "segment": k,
                       "origin": "extracted"})
    return Outcome(valid, discarded)
