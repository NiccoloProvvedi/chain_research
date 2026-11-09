"""Impostazioni dell'utente: `config/impostazioni.json` (fuori da git; il percorso si cambia con
RICERCA_IMPOSTAZIONI, usato dai test). Il file contiene solo ciò che l'utente cambia: il resto sono i predefiniti.
Un valore non valido (file scritto a mano) vale come il predefinito."""
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import RADICE

log = logging.getLogger("ricerca_catena.configurazione")
MODELLI = ("haiku", "sonnet", "opus")
PREDEFINITE: dict[str, Any] = {
    # Chi guida l'indagine (domande, valutazione, documento) e chi legge per la catena (banco: Haiku, 36/40).
    "modello_regia": "sonnet",
    "modello_catena": "haiku",
    # Limiti di un'indagine (1/11, Niccolò: 5 $ e 2 ore, oppure nessun limite).
    "budget_usd": 5.0,
    "max_minuti": 120,
    "senza_limiti": False,
    "domande_in_parallelo": 3,
    # Limiti di una singola domanda della catena (quelli del banco di prova).
    "budget_domanda_usd": 0.50,
    "pagine_domanda": 30,
    "minuti_domanda": 35,
    "cartella_indagini": "indagini",
    "cartella_motori": "browser/motori",
    "cartella_profili": "browser/profili",
}
# Chiave → (minimo, massimo) per i numeri; gli stessi intervalli del server.
INTERVALLI: dict[str, tuple[float, float]] = {"budget_usd": (0.5, 500), "max_minuti": (10, 24 * 60),
                                              "domande_in_parallelo": (1, 4), "budget_domanda_usd": (0.05, 5),
                                              "pagine_domanda": (1, 200), "minuti_domanda": (5, 180)}


def percorso_impostazioni() -> Path:
    da_env = os.environ.get("RICERCA_IMPOSTAZIONI")
    return Path(da_env).expanduser() if da_env else RADICE / "config" / "impostazioni.json"


def _valido(chiave: str, valore: Any) -> bool:
    if chiave in INTERVALLI:
        minimo, massimo = INTERVALLI[chiave]
        return isinstance(valore, (int, float)) and not isinstance(valore, bool) and minimo <= valore <= massimo
    if chiave in ("modello_regia", "modello_catena"):
        return valore in MODELLI
    if chiave == "senza_limiti":
        return isinstance(valore, bool)
    return isinstance(valore, str) and bool(valore)


def _salvate() -> dict[str, Any]:
    percorso = percorso_impostazioni()
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8")) if percorso.is_file() else {}
    except ValueError:
        log.warning("%s ignorato: non è un JSON valido", percorso.name)
        return {}
    return dati if isinstance(dati, dict) else {}


def carica() -> dict[str, Any]:
    """Le impostazioni, lette ogni volta (le modifiche si vedono subito)."""
    salvate = _salvate()
    non_valide = [k for k, v in salvate.items() if k in PREDEFINITE and not _valido(k, v)]
    if non_valide:
        log.warning("impostazioni non valide, uso i predefiniti: %s", ", ".join(non_valide))
    return {**PREDEFINITE, **{k: v for k, v in salvate.items() if k not in non_valide}}


def salva(modifiche: dict[str, Any]) -> None:
    """Aggiorna alcune impostazioni, tenendo le altre (scrittura atomica: chi legge non vede mai il file a metà)."""
    percorso = percorso_impostazioni()
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_text(json.dumps({**_salvate(), **modifiche}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporaneo.replace(percorso)


def cartella(chiave: str) -> Path:
    """Una cartella delle impostazioni (relativa alla radice del progetto, o assoluta), creata se manca."""
    p = Path(str(carica()[chiave])).expanduser()
    p = p if p.is_absolute() else RADICE / p
    p.mkdir(parents=True, exist_ok=True)
    return p
