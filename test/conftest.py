"""Configurazione comune dei test: impostazioni, segreti e registri in una cartella temporanea; nessun Claude vero
se il test non è marcato "claude"."""
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "banco"))


@pytest.fixture
def cartella_temporanea(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _isolato(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory,
             monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Registri, impostazioni e segreti dei test in una cartella a parte; un `claude` finto in testa al PATH che
    risponde sempre con un errore (il pensatore usa i predefiniti), tranne nei test marcati "claude"."""
    cartella = tmp_path_factory.mktemp("isolato")
    monkeypatch.setenv("RICERCA_LOG", str(cartella / "log"))
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(cartella / "impostazioni.json"))
    monkeypatch.setenv("RICERCA_SEGRETI", str(cartella / "segreti.json"))
    if not request.node.get_closest_marker("claude"):
        finto = cartella / "bin" / "claude"
        finto.parent.mkdir(parents=True)
        finto.write_text('#!/bin/sh\necho \'{"type": "result", "subtype": "vietato_nei_test", "is_error": true}\'\n')
        finto.chmod(0o755)
        monkeypatch.setenv("PATH", f"{finto.parent}{os.pathsep}{os.environ['PATH']}")
    yield
