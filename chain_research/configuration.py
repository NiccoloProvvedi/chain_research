"""User settings: `config/settings.json` (outside git; the path can be changed with CHAIN_SETTINGS, used by the
tests). The file holds only what the user changes: the rest are the defaults.
An invalid value (hand-written file) counts as the default."""
import json
import logging
import os
from pathlib import Path
from typing import Any

from . import ROOT

log = logging.getLogger("chain_research.configuration")
MODELS = ("haiku", "sonnet", "opus")
DEFAULTS: dict[str, Any] = {
    # Who leads the investigation (questions, evaluation, document) and who reads for the chain (bench: Haiku, 36/40).
    "director_model": "sonnet",
    "chain_model": "haiku",
    # Who rereads the finished chain looking for the error: a model different from the one that followed it.
    "review_model": "sonnet",
    # Limits of an investigation (1 Nov, Niccolò: $5 and 2 hours, or no limit).
    "budget_usd": 5.0,
    "max_minutes": 120,
    "without_limits": False,
    "parallel_questions": 3,
    # Limits of a single question of the chain (those of the test bench).
    "question_budget_usd": 0.50,
    "question_pages": 30,
    "question_minutes": 35,
    "investigations_folder": "investigations",
    "engines_folder": "browser/engines",
    "profiles_folder": "browser/profiles",
}
# Key → (min, max) for the numbers; the same intervals as the server.
INTERVALS: dict[str, tuple[float, float]] = {"budget_usd": (0.5, 500), "max_minutes": (10, 24 * 60),
                                              "parallel_questions": (1, 4), "question_budget_usd": (0.05, 5),
                                              "question_pages": (1, 200), "question_minutes": (5, 180)}


def settings_path() -> Path:
    from_env = os.environ.get("CHAIN_SETTINGS")
    return Path(from_env).expanduser() if from_env else ROOT / "config" / "settings.json"


def _is_valid_setting(key: str, value: Any) -> bool:
    if key in INTERVALS:
        minimum, maximum = INTERVALS[key]
        return isinstance(value, (int, float)) and not isinstance(value, bool) and minimum <= value <= maximum
    if key in ("director_model", "chain_model", "review_model"):
        return value in MODELS
    if key == "without_limits":
        return isinstance(value, bool)
    return isinstance(value, str) and bool(value)


def _saved() -> dict[str, Any]:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        log.warning("%s ignored: not valid JSON", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def load() -> dict[str, Any]:
    """The settings, read every time (changes show immediately)."""
    saved = _saved()
    not_valid = [k for k, v in saved.items() if k in DEFAULTS and not _is_valid_setting(k, v)]
    if not_valid:
        log.warning("invalid settings, using the defaults: %s", ", ".join(not_valid))
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k not in not_valid}}


def save(changes: dict[str, Any]) -> None:
    """Updates some settings, keeping the others (atomic write: a reader never sees a half-written file)."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({**_saved(), **changes}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def folder(key: str) -> Path:
    """A folder from the settings (relative to the project root, or absolute), created if missing."""
    p = Path(str(load()[key])).expanduser()
    p = p if p.is_absolute() else ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p
