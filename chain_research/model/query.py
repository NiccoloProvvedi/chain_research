"""Filtro delle query doppie e indice di ridondanza (Fase C, Parte 7.1 del piano; DeepDive, sezione 2.2 e formula 7).

Due query sono "doppie" quando la somiglianza di Jaccard tra le loro parole (normalizzate, senza parole vuote) è almeno
la soglia (0,6). Versioni e identificativi tecnici restano interi (`b4000`, `gfx1200`, `3.8`, `llama.cpp`).
"""
import re
import unicodedata
from itertools import permutations

STOP_WORDS = frozenset("""
a ad al alla alle allo ai agli all anche che chi ci come con contro cui da dal dalla dalle dai dagli del della delle
dello dei degli di dove e ed fra gli i il in la le lo ma ne nei nel nella nelle nello non o per piu quale quali quando
quanto se si sono su sua sue suo suoi sul sulla tra un una uno qual cosa cos
an and are as at be by for from how is it its of on or that the this to was what when where which who why with
vs versus
""".split())
_WORD = re.compile(r"[a-z0-9]+(?:[.\-_+][a-z0-9]+)*")


def words(query: str) -> frozenset[str]:
    """Parole significative di una query: minuscole, senza accenti né punteggiatura, senza parole vuote."""
    text = unicodedata.normalize("NFKD", str(query).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return frozenset(p for p in _WORD.findall(text) if p not in STOP_WORDS)


def jaccard(a: str | frozenset[str], b: str | frozenset[str]) -> float:
    pa = a if isinstance(a, frozenset) else words(a)
    pb = b if isinstance(b, frozenset) else words(b)
    if not pa and not pb:
        return 1.0
    return len(pa & pb) / len(pa | pb)


MONTHS = frozenset("""gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre
january february march april may june july august september october november december""".split())


def essential(word: str) -> bool:
    """Numeri, versioni, identificativi con cifre e mesi: se cambiano, la query cerca un'altra cosa."""
    return any(c.isdigit() for c in word) or word in MONTHS


def similarity(a: str | frozenset[str], b: str | frozenset[str]) -> float:
    """Jaccard, ma 0 se le due query differiscono per un numero, una versione o un mese («llama.cpp agosto 2024» e
    «llama.cpp ottobre 2024» non sono doppie, anche se la Jaccard è 0,75)."""
    pa = a if isinstance(a, frozenset) else words(a)
    pb = b if isinstance(b, frozenset) else words(b)
    if any(essential(p) for p in pa ^ pb):
        return 0.0
    return jaccard(pa, pb)


def most_similar(query: str, executed: list[str]) -> tuple[float, str | None]:
    """La query già eseguita più simile e la sua somiglianza (0 e None se non ce ne sono)."""
    pq = words(query)
    best: tuple[float, str | None] = (0.0, None)
    for another in executed:
        value = similarity(pq, words(another))
        if value > best[0]:
            best = (value, another)
    return best


def duplicate(query: str, executed: list[str], threshold: float) -> bool:
    return most_similar(query, executed)[0] >= threshold


def redundancy(query: list[str]) -> float:
    """Indice di ridondanza S(T): media della Jaccard su tutte le coppie ordinate di query diverse (formula 7)."""
    if len(query) < 2:
        return 0.0
    sets = [words(q) for q in query]
    pairs = list(permutations(range(len(sets)), 2))
    return round(sum(jaccard(sets[i], sets[j]) for i, j in pairs) / len(pairs), 4)
