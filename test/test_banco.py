"""Giudice del banco senza Claude: confronto normalizzato e risposta breve."""
from giudice import giudica, normalizza, risposta_breve, uguale_normalizzata


def test_confronto_normalizzato() -> None:
    assert normalizza("Città  d'Aosta!") == "citta d aosta "
    assert uguale_normalizzata("24 luglio 1973", "**24 luglio 1973** [6].")
    assert not uguale_normalizzata("1973", "19731")
    assert giudica("d", "Mitchell Baker", "Mitchell Baker [2]") == (True, "confronto")
    assert giudica("d", "x", "") == (False, "vuota")


def test_risposta_breve() -> None:
    assert risposta_breve("intro\n**Risposta:** 21 giugno 1976 [16].\n\n**Fiducia:** alta") == "21 giugno 1976 [16]."
    assert risposta_breve("\n\nsolo testo\naltro") == "solo testo"
