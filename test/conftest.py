"""Configurazione comune dei test: impostazioni, segreti e registri in una cartella temporanea; nessun Claude vero
se il test non è marcato "claude"."""
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))


@pytest.fixture
def temporary_folder(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory,
             monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Registri, impostazioni e segreti dei test in una cartella a parte; un `claude` finto in testa al PATH che
    risponde sempre con un errore (il pensatore usa i predefiniti), tranne nei test marcati "claude"."""
    folder = tmp_path_factory.mktemp("isolato")
    monkeypatch.setenv("CHAIN_LOG", str(folder / "log"))
    monkeypatch.setenv("CHAIN_SETTINGS", str(folder / "settings.json"))
    monkeypatch.setenv("CHAIN_SECRETS", str(folder / "secrets.json"))
    if not request.node.get_closest_marker("claude"):
        fake = folder / "bin" / "claude"
        fake.parent.mkdir(parents=True)
        fake.write_text('#!/bin/sh\necho \'{"type": "result", "subtype": "vietato_nei_test", "is_error": true}\'\n')
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    yield
