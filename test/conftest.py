"""Shared test configuration: settings, secrets and logs in a temporary folder; no real Claude unless the test is
marked "claude"."""
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
    """Logs, settings and secrets of the tests in a separate folder; a fake `claude` at the head of PATH that always
    answers with an error (the thinker uses the defaults), except in tests marked "claude"."""
    folder = tmp_path_factory.mktemp("isolated")
    monkeypatch.setenv("CHAIN_LOG", str(folder / "log"))
    monkeypatch.setenv("CHAIN_SETTINGS", str(folder / "settings.json"))
    monkeypatch.setenv("CHAIN_SECRETS", str(folder / "secrets.json"))
    if not request.node.get_closest_marker("claude"):
        fake = folder / "bin" / "claude"
        fake.parent.mkdir(parents=True)
        fake.write_text('#!/bin/sh\necho \'{"type": "result", "subtype": "forbidden_in_tests", "is_error": true}\'\n')
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    yield
