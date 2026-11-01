"""Impostazioni dell'utente: `config/impostazioni.json` (fuori da git; il percorso si cambia con
RICERCA_IMPOSTAZIONI, usato dai test). Il file contiene solo ciò che l'utente cambia: il resto sono i predefiniti."""
import json
import os
from pathlib import Path
from typing import Any

from . import RADICE

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


def percorso_impostazioni() -> Path:
    da_env = os.environ.get("RICERCA_IMPOSTAZIONI")
    return Path(da_env).expanduser() if da_env else RADICE / "config" / "impostazioni.json"


def carica() -> dict[str, Any]:
    """Le impostazioni, lette ogni volta (le modifiche si vedono subito)."""
    percorso = percorso_impostazioni()
    try:
        salvate = json.loads(percorso.read_text(encoding="utf-8")) if percorso.is_file() else {}
    except ValueError:
        salvate = {}
    return {**PREDEFINITE, **(salvate if isinstance(salvate, dict) else {})}


def salva(modifiche: dict[str, Any]) -> None:
    """Aggiorna alcune impostazioni, tenendo le altre."""
    percorso = percorso_impostazioni()
    percorso.parent.mkdir(parents=True, exist_ok=True)
    attuali = json.loads(percorso.read_text(encoding="utf-8")) if percorso.is_file() else {}
    percorso.write_text(json.dumps({**attuali, **modifiche}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cartella(chiave: str) -> Path:
    """Una cartella delle impostazioni (relativa alla radice del progetto, o assoluta), creata se manca."""
    p = Path(str(carica()[chiave])).expanduser()
    p = p if p.is_absolute() else RADICE / p
    p.mkdir(parents=True, exist_ok=True)
    return p
