"""Errori delle fonti (Wikipedia e client HTTP): le uniche eccezioni che una fonte solleva verso la ricerca."""


class SourceError(Exception):
    """Errore di una fonte, con un messaggio in italiano. Una fonte che fallisce non ferma mai la ricerca."""


class SourceUnavailable(SourceError):
    """La fonte non si può usare (accesso negato, rete assente...)."""


class ReachedLimit(SourceError):
    """Troppe richieste: la fonte va lasciata riposare."""


class UnreadableContent(SourceError):
    """Questo contenuto non si può leggere (privato, senza testo, troppo grande, dominio non consentito...)."""
