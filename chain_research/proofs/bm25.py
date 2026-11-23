"""BM25 (Okapi) per la prima scrematura delle triple candidate (Fase D, Parte 8.5 punto 3): scritto qui, niente
dipendenze (decisione B5). Le parole sono quelle del filtro delle query (minuscole, senza accenti né parole vuote,
identificativi tecnici interi)."""
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
        """Indici dei documenti migliori, in ordine (a parità l'ordine originale)."""
        scores = self.scores(question)
        return sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:how_many]


def _items_words(text: str) -> list[str]:
    # `parole` restituisce un insieme: per BM25 servono le ripetizioni, quindi si contano parola per parola.
    return [p for piece in str(text).split() for p in words(piece)]
