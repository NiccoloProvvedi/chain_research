"""Entities of the proof graph (Phase D, Part 8.3 of the plan).

- Normalization: lowercase, without accents, spaces and punctuation at the edges removed; the original form is kept
  for display.
- Two entities with the same normalized name are the same, **unless** their names have different digit sequences
  (numbers rule, idea from graphify `_numeric_tokens_differ`): "Qwen 2.5" and "Qwen 3", "b4000" and "b4100" stay
  distinct. With the normalization below two equal names have the same digits; the rule is for aliases.
- Aliases (from Wikidata) are other names of the same entity.
- No merging by similarity in this phase.
"""
import re
import unicodedata

_BORDERS = " \t\n\r.,;:!?\"'«»“”‘’()[]{}-–—/"


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip(_BORDERS)


def digits(name: str) -> tuple[str, ...]:
    """Digit sequences of a name, in order ("Qwen 2.5 27B" → ("2", "5", "27"))."""
    return tuple(re.findall(r"\d+", str(name)))


def same(a: str, b: str) -> bool:
    """Two names denote the same entity (same normalized name and same digits)."""
    return normalize(a) == normalize(b) and digits(a) == digits(b)


def alias_compatibile(name: str, alias: str) -> bool:
    """An alias joins an entity only if it has the same numbers as the name: "Qwen 3" is not an alias of "Qwen 2.5",
    and "Pace di Vestfalia 1650" is not one of "Pace di Vestfalia" (cautious: better a lost alias than two merged
    entities)."""
    return digits(alias) == digits(name)
