"""Segreti delle fonti (`config/segreti.json`, fuori da git, permessi 600) e contatto per Wikipedia.

Il percorso si cambia con RICERCA_SEGRETI (usato dai test). I segreti non vanno mai nei log."""
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from ... import RADICE

log = logging.getLogger("ricerca_catena.fonti")


def percorso_segreti() -> Path:
    da_env = os.environ.get("RICERCA_SEGRETI")
    return Path(da_env).expanduser() if da_env else RADICE / "config" / "segreti.json"


def segreti() -> dict[str, Any]:
    """I segreti; {} se il file non c'è. Il file deve essere leggibile solo dall'utente (600): altrimenti è ignorato."""
    percorso = percorso_segreti()
    if not percorso.is_file():
        return {}
    if percorso.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("%s ignorato: permessi troppo aperti (servono 600)", percorso.name)
        return {}
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("%s ignorato: non è un JSON valido", percorso.name)
        return {}
    return dati if isinstance(dati, dict) else {}


def salva_segreti(modifiche: dict[str, Any]) -> None:
    """Aggiorna alcune chiavi dei segreti tenendo le altre; il file resta leggibile solo dall'utente (600)."""
    percorso = percorso_segreti()
    percorso.parent.mkdir(parents=True, exist_ok=True)
    try:  # anche con permessi troppo aperti: le altre chiavi non si perdono
        dati = json.loads(percorso.read_text(encoding="utf-8")) if percorso.is_file() else {}
    except ValueError:
        dati = {}
    dati = {**(dati if isinstance(dati, dict) else {}), **modifiche}
    temporaneo = percorso.with_suffix(".tmp")
    descrittore = os.open(temporaneo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descrittore, "w", encoding="utf-8") as file:
        file.write(json.dumps(dati, indent=2, ensure_ascii=False) + "\n")
    os.chmod(temporaneo, 0o600)
    temporaneo.replace(percorso)


def email_account_claude() -> str:
    """L'email dell'account Claude con cui è fatto l'accesso, dal file del programma `claude` (con
    CLAUDE_CONFIG_DIR sta in quella cartella); "" se non si trova. Serve solo al contatto per Wikipedia: non va mai
    altrove."""
    cartella = os.environ.get("CLAUDE_CONFIG_DIR")
    for percorso in ([Path(cartella) / ".claude.json"] if cartella else []) + [Path.home() / ".claude.json"]:
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        email = str(((dati if isinstance(dati, dict) else {}).get("oauthAccount") or {}).get("emailAddress") or "")
        if email.strip():
            return email.strip()
    return ""
