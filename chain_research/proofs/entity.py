"""Entità del grafo delle prove (Fase D, Parte 8.3 del piano).

- Normalizzazione: minuscole, senza accenti, spazi e punteggiatura ai bordi tolti; la forma originale si tiene per la
  visualizzazione.
- Due entità con lo stesso nome normalizzato sono la stessa, **tranne** se i loro nomi hanno sequenze di cifre diverse
  (regola dei numeri, idea di graphify `_numeric_tokens_differ`): "Qwen 2.5" e "Qwen 3", "b4000" e "b4100" restano
  distinte. Con la normalizzazione qui sotto due nomi uguali hanno le stesse cifre; la regola serve agli alias.
- Gli alias (da Wikidata) sono altri nomi della stessa entità.
- Nessuna fusione per somiglianza in questa fase.
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
    """Sequenze di cifre di un nome, in ordine ("Qwen 2.5 27B" → ("2", "5", "27"))."""
    return tuple(re.findall(r"\d+", str(name)))


def same(a: str, b: str) -> bool:
    """Due nomi indicano la stessa entità (stesso nome normalizzato e stesse cifre)."""
    return normalize(a) == normalize(b) and digits(a) == digits(b)


def alias_compatibile(name: str, alias: str) -> bool:
    """Un alias si unisce a un'entità solo se ha gli stessi numeri del nome: "Qwen 3" non è un alias di "Qwen 2.5", e
    "Pace di Vestfalia 1650" non lo è di "Pace di Vestfalia" (prudente: meglio un alias perso che due entità fuse)."""
    return digits(alias) == digits(name)
