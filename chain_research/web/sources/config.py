"""Segreti delle fonti (`config/segreti.json`, fuori da git, permessi 600) e contatto per Wikipedia.

Il percorso si cambia con CHAIN_SECRETS (usato dai test). I segreti non vanno mai nei log."""
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from ... import ROOT

log = logging.getLogger("chain_research.sources")


def secrets_path() -> Path:
    from_env = os.environ.get("CHAIN_SECRETS")
    return Path(from_env).expanduser() if from_env else ROOT / "config" / "secrets.json"


def secrets() -> dict[str, Any]:
    """I segreti; {} se il file non c'è. Il file deve essere leggibile solo dall'utente (600): altrimenti è ignorato."""
    path = secrets_path()
    if not path.is_file():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("%s ignorato: permessi troppo aperti (servono 600)", path.name)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("%s ignorato: non è un JSON valido", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def save_secrets(changes: dict[str, Any]) -> None:
    """Aggiorna alcune chiavi dei segreti tenendo le altre; il file resta leggibile solo dall'utente (600)."""
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:  # anche con permessi troppo aperti: le altre chiavi non si perdono
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        data = {}
    data = {**(data if isinstance(data, dict) else {}), **changes}
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def email_account_claude() -> str:
    """L'email dell'account Claude con cui è fatto l'accesso, dal file del programma `claude` (con
    CLAUDE_CONFIG_DIR sta in quella cartella); "" se non si trova. Serve solo al contatto per Wikipedia: non va mai
    altrove."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    for path in ([Path(folder) / ".claude.json"] if folder else []) + [Path.home() / ".claude.json"]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        email = str(((data if isinstance(data, dict) else {}).get("oauthAccount") or {}).get("emailAddress") or "")
        if email.strip():
            return email.strip()
    return ""
