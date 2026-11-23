"""Triples extracted from pages (Phase D, Part 8.2 of the plan): validation and quote check.

For each segment the reader returns at most 8 triples
`{subject, relation, object, object_kind, force, quote, segment}`. The most important check against the small
model's inventions: the `quote` must **really** appear in the given segment (after normalizing spaces, quotation marks
and dashes). If not, the triple is discarded and counted. Triples that pass have origin `extracted`; only these count
as proofs.
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
    """The quote appears in the segment text (spaces, quotation marks, dashes and case normalized)."""
    searched = _per_comparison(quote).strip(" .…")
    return len(searched) >= 3 and searched in _per_comparison(segment)


def segment_number(value: str) -> int | None:
    match = re.fullmatch(r"§?(\d+)", str(value).strip())
    return int(match.group(1)) if match else None


@dataclass
class Outcome:
    valid: list[dict]
    discarded: list[dict]  # each with the "reason" field

    @property
    def discarded_share(self) -> float:
        total = len(self.valid) + len(self.discarded)
        return len(self.discarded) / total if total else 0.0


def is_valid(triples: list[dict[str, Any]], segments: dict[int, str]) -> Outcome:
    """Check the reader's triples against the segments read ({number: text}). Valid ones get origin 'extracted'
    and the segment number; at most 8 per segment (the others are discarded)."""
    valid: list[dict] = []
    discarded: list[dict] = []
    per_segment: dict[int, int] = {}
    for triple in triples:
        k = segment_number(triple.get("segment", ""))
        reason = ""
        if k is None or k not in segments:
            reason = "segment does not exist"
        elif triple.get("force") not in FORCES or triple.get("object_kind") not in OBJECT_TYPES:
            reason = "invalid force or kind"
        elif not all(str(triple.get(c, "")).strip() for c in ("subject", "relation", "object")):
            reason = "empty field"
        elif not present_quote(triple.get("quote", ""), segments[k]):
            reason = "quote not found in the segment"
        elif per_segment.get(k, 0) >= MAX_PER_SEGMENT:
            reason = "more than 8 triples per segment"
        if reason:
            discarded.append({**triple, "reason": reason})
            continue
        per_segment[k] = per_segment.get(k, 0) + 1
        valid.append({**{c: str(triple[c]).strip()[:MAX_FIELD] for c in ("subject", "relation", "object")},
                       "object_kind": triple["object_kind"], "force": triple["force"],
                       "quote": str(triple["quote"]).strip()[:MAX_QUOTE], "segment": k,
                       "origin": "extracted"})
    return Outcome(valid, discarded)
