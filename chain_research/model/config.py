"""Configurazione delle chiamate a Claude: tetti, attese, modello per punto e cartella dei registri.

Il modello di ogni punto, se non indicato da chi chiama, è `modello_catena` delle impostazioni. La cartella dei
registri si cambia con CHAIN_LOG (usato dai test)."""
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .. import ROOT, configuration

POINTS = ("reading", "interpret", "chain", "step", "candidates", "verification", "value", "article", "final",
         "writing", "regia", "document", "judge", "review", "document_check")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Thinker(_Base):
    # Tetto di ogni singola chiamata (--max-budget-usd), oltre al budget della domanda.
    max_call_usd: float = Field(0.50, gt=0)
    wait_s: float = Field(180, gt=0)


class Query(_Base):
    # Una query troppo simile (Jaccard) a una già fatta non parte.
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
