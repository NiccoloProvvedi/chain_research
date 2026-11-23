"""Source secrets (`config/secrets.json`, outside git, permissions 600) and contact for Wikipedia.

The path is changed with CHAIN_SECRETS (used by the tests). Secrets never go into the logs."""
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
    """The secrets; {} if the file is missing. The file must be readable only by the user (600), otherwise it is
    ignored."""
    path = secrets_path()
    if not path.is_file():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("%s ignored: permissions too open (600 required)", path.name)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("%s ignored: not valid JSON", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def save_secrets(changes: dict[str, Any]) -> None:
    """Update some secret keys keeping the others; the file stays readable only by the user (600)."""
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:  # even with permissions too open: the other keys are not lost
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
    """The email of the Claude account that is logged in, from the `claude` program's file (with
    CLAUDE_CONFIG_DIR it is in that folder); "" if not found. Used only for the Wikipedia contact: it never goes
    anywhere else."""
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
