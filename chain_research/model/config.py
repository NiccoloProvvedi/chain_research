"""Configuration of the Claude calls: caps, waits, model per point and log folder.

The model of each point, unless the caller gives one, is `chain_model` from the settings. The log folder can be
changed with CHAIN_LOG (used by the tests)."""
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .. import ROOT, configuration

POINTS = ("reading", "interpret", "chain", "step", "candidates", "verification", "value", "article", "final",
         "writing", "regia", "document", "judge", "review", "document_check")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Thinker(_Base):
    # Cap of each single call (--max-budget-usd), on top of the question budget.
    max_call_usd: float = Field(0.50, gt=0)
    wait_s: float = Field(180, gt=0)


class Query(_Base):
    # A query too similar (Jaccard) to one already made is not sent.
    duplicate_threshold: float = Field(0.6, ge=0, le=1)


class ModelConfig(_Base):
    thinker: Thinker = Thinker()
    query: Query = Query()

    def claude_model(self, point: str) -> str:
        return str(configuration.load().get("chain_model") or "haiku")


def load() -> ModelConfig:
    return ModelConfig()


def folder_log() -> Path:
    from_env = os.environ.get("CHAIN_LOG")
    folder = Path(from_env).expanduser() if from_env else ROOT / "log"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
