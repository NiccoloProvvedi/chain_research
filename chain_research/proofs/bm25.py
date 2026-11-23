"""BM25 (Okapi) for the first screening of candidate triples (Phase D, Part 8.5 point 3): written here, no
dependencies (decision B5). The words are those of the query filter (lowercase, without accents or stop words,
technical identifiers kept whole)."""
import math
from collections import Counter

from ..model.query import words


class Bm25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.documents = [Counter(_items_words(d)) for d in documents]
        self.lengths = [sum(c.values()) for c in self.documents]
        self.media = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        frequencies: Counter[str] = Counter()
        for document in self.documents:
            frequencies.update(document.keys())
        n = len(self.documents)
        self.idf = {p: math.log(1 + (n - f + 0.5) / (f + 0.5)) for p, f in frequencies.items()}

    def scores(self, question: str) -> list[float]:
        terms = words(question)
        result = []
        for document, length in zip(self.documents, self.lengths, strict=True):
            value = 0.0
            for term in terms:
                frequency = document.get(term, 0)
                if frequency:
                    norm = 1 - self.b + self.b * (length / self.media if self.media else 0)
                    value += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / (frequency + self.k1 * norm)
            result.append(value)
        return result

    def best(self, question: str, how_many: int) -> list[int]:
        """Indices of the best documents, in order (ties keep the original order)."""
        scores = self.scores(question)
        return sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:how_many]


def _items_words(text: str) -> list[str]:
    # `words` returns a set: BM25 needs repetitions, so words are counted one by one.
    return [p for piece in str(text).split() for p in words(piece)]
