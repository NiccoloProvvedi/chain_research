"""Configurazione delle chiamate a Claude: tetti, attese, modello per punto e cartella dei registri.

Il modello di ogni punto, se non indicato da chi chiama, è `modello_catena` delle impostazioni. La cartella dei
registri si cambia con RICERCA_LOG (usato dai test)."""
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .. import RADICE, configurazione

PUNTI = ("lettura", "interpreta", "catena", "passo", "candidati", "verifica", "valore", "voce", "finale",
         "scrittura", "regia", "documento", "giudice", "revisione", "documento_controllo")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pensatore(_Base):
    # Tetto di ogni singola chiamata (--max-budget-usd), oltre al budget della domanda.
    max_chiamata_usd: float = Field(0.50, gt=0)
    attesa_s: float = Field(180, gt=0)


class Query(_Base):
    # Una query troppo simile (Jaccard) a una già fatta non parte.
    soglia_doppia: float = Field(0.6, ge=0, le=1)


class ConfigModello(_Base):
    pensatore: Pensatore = Pensatore()
    query: Query = Query()

    def modello_claude(self, punto: str) -> str:
        return str(configurazione.carica().get("modello_catena") or "haiku")


def carica() -> ConfigModello:
    return ConfigModello()


def cartella_log() -> Path:
    da_env = os.environ.get("RICERCA_LOG")
    cartella = Path(da_env).expanduser() if da_env else RADICE / "log"
    cartella.mkdir(parents=True, exist_ok=True)
    return cartella
