"""Errori delle fonti (Wikipedia e client HTTP): le uniche eccezioni che una fonte solleva verso la ricerca."""


class ErroreFonte(Exception):
    """Errore di una fonte, con un messaggio in italiano. Una fonte che fallisce non ferma mai la ricerca."""


class FonteNonDisponibile(ErroreFonte):
    """La fonte non si può usare (accesso negato, rete assente...)."""


class LimiteRaggiunto(ErroreFonte):
    """Troppe richieste: la fonte va lasciata riposare."""


class ContenutoNonLeggibile(ErroreFonte):
    """Questo contenuto non si può leggere (privato, senza testo, troppo grande, dominio non consentito...)."""
