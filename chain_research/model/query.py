"""Duplicate query filter and redundancy index (Phase C, Part 7.1 of the plan; DeepDive, section 2.2 and formula 7).

Two queries are "duplicates" when the Jaccard similarity of their words (normalized, without stop words) is at least
the threshold (0.6). Versions and technical identifiers stay whole (`b4000`, `gfx1200`, `3.8`, `llama.cpp`).
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
    """Meaningful words of a query: lowercase, without accents, punctuation or stop words."""
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
    """Numbers, versions, identifiers with digits, and months: if they change, the query looks for something else."""
    return any(c.isdigit() for c in word) or word in MONTHS


def similarity(a: str | frozenset[str], b: str | frozenset[str]) -> float:
    """Jaccard, but 0 if the two queries differ by a number, a version or a month («llama.cpp agosto 2024» and
    «llama.cpp ottobre 2024» are not duplicates, even though the Jaccard is 0.75)."""
    pa = a if isinstance(a, frozenset) else words(a)
    pb = b if isinstance(b, frozenset) else words(b)
    if any(essential(p) for p in pa ^ pb):
        return 0.0
    return jaccard(pa, pb)


def most_similar(query: str, executed: list[str]) -> tuple[float, str | None]:
    """The most similar query already executed and its similarity (0 and None if there are none)."""
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
    """Redundancy index S(T): mean Jaccard over all ordered pairs of different queries (formula 7)."""
    if len(query) < 2:
        return 0.0
    sets = [words(q) for q in query]
    pairs = list(permutations(range(len(sets)), 2))
    return round(sum(jaccard(sets[i], sets[j]) for i, j in pairs) / len(pairs), 4)
