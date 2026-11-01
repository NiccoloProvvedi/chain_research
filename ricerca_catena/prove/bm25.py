"""BM25 (Okapi) per la prima scrematura delle triple candidate (Fase D, Parte 8.5 punto 3): scritto qui, niente
dipendenze (decisione B5). Le parole sono quelle del filtro delle query (minuscole, senza accenti né parole vuote,
identificativi tecnici interi)."""
import math
from collections import Counter

from ..modello.query import parole


class Bm25:
    def __init__(self, documenti: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.documenti = [Counter(_parole_lista(d)) for d in documenti]
        self.lunghezze = [sum(c.values()) for c in self.documenti]
        self.media = (sum(self.lunghezze) / len(self.lunghezze)) if self.lunghezze else 0.0
        frequenze: Counter[str] = Counter()
        for documento in self.documenti:
            frequenze.update(documento.keys())
        n = len(self.documenti)
        self.idf = {p: math.log(1 + (n - f + 0.5) / (f + 0.5)) for p, f in frequenze.items()}

    def punteggi(self, domanda: str) -> list[float]:
        termini = parole(domanda)
        risultato = []
        for documento, lunghezza in zip(self.documenti, self.lunghezze, strict=True):
            valore = 0.0
            for termine in termini:
                frequenza = documento.get(termine, 0)
                if frequenza:
                    norma = 1 - self.b + self.b * (lunghezza / self.media if self.media else 0)
                    valore += self.idf.get(termine, 0.0) * frequenza * (self.k1 + 1) / (frequenza + self.k1 * norma)
            risultato.append(valore)
        return risultato

    def migliori(self, domanda: str, quanti: int) -> list[int]:
        """Indici dei documenti migliori, in ordine (a parità l'ordine originale)."""
        punteggi = self.punteggi(domanda)
        return sorted(range(len(punteggi)), key=lambda i: (-punteggi[i], i))[:quanti]


def _parole_lista(testo: str) -> list[str]:
    # `parole` restituisce un insieme: per BM25 servono le ripetizioni, quindi si contano parola per parola.
    return [p for pezzo in str(testo).split() for p in parole(pezzo)]
