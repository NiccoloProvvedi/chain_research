"""Server dell'app senza processi veri: protezione, impostazioni, indagini, azioni ed eventi."""
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from chain_research import server
from chain_research.investigation import Investigation

TOKEN = "t" * 64


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHAIN_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CHAIN_SECRETS", str(tmp_path / "secrets.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "casa"))
    (tmp_path / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "persona@esempio.org"}}')
    handler = server.Manager(tmp_path / "indagini")
    started: list[tuple[str, bool]] = []
    monkeypatch.setattr(handler, "launch", lambda ident, document_only=False: started.append((ident, document_only)))
    app = server.create_app(TOKEN, lambda: 8123, handler)
    client = TestClient(app, base_url="http://127.0.0.1:8123")
    client.cookies.set("token", TOKEN)
    return client, handler, started, tmp_path


def test_protection(environment) -> None:
    client, *_ = environment
    assert client.get("/api/indagini").status_code == 200
    foreign = TestClient(client.app, base_url="http://127.0.0.1:8123")
    assert foreign.get("/api/indagini").status_code == 403  # senza chiave
    other_host = TestClient(client.app, base_url="http://esempio.org")
    other_host.cookies.set("token", TOKEN)
    assert other_host.get("/api/indagini").status_code == 403
    assert client.post("/api/impostazioni", json={}, headers={"origin": "http://evil.org"}).status_code == 403
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]


def test_settings_and_contact_wikipedia(environment) -> None:
    client, _, _, folder = environment
    imp = client.get("/api/impostazioni").json()
    assert imp["director_model"] == "sonnet" and imp["budget_usd"] == 5.0 and imp["max_minutes"] == 120
    assert imp["wikipedia"] == {"active": True, "contact": "", "in_use": "persona@esempio.org", "origin": "account"}
    r = client.post("/api/impostazioni", json={"director_model": "opus", "without_limits": True, "budget_usd": 12,
                                               "wikipedia": {"active": True, "contact": "io@esempio.org"}})
    assert r.status_code == 200 and r.json()["director_model"] == "opus" and r.json()["without_limits"] is True
    assert r.json()["wikipedia"]["origin"] == "settings"
    assert (folder / "secrets.json").stat().st_mode & 0o777 == 0o600
    for wrong in ({"director_model": "gpt"}, {"budget_usd": 0}, {"wikipedia": {"contact": "a b"}}):
        assert client.post("/api/impostazioni", json=wrong).status_code == 400, wrong


def new_test_investigation_list_and_actions(environment) -> None:
    client, handler, started, _ = environment
    r = client.post("/api/indagini", json={"topic": "Storia di Arduino", "director_model": "haiku",
                                           "budget_usd": "3", "max_minutes": "", "without_limits": False})
    assert r.status_code == 200
    ident = r.json()["id"]
    assert started == [(ident, False)]
    status = client.get(f"/api/indagini/{ident}").json()
    assert status["director_model"] == "haiku" and status["limits"]["budget_usd"] == 3 and status["limits"]["max_minutes"] == 120
    article = client.get("/api/indagini").json()[0]
    assert article["id"] == ident and article["topic"] == "Storia di Arduino" and article["status"] == "waiting"
    assert client.post(f"/api/indagini/{ident}/documento", json={}).status_code == 200 and started[-1] == (ident, True)
    assert client.post(f"/api/indagini/{ident}/ferma", json={}).status_code == 400  # non è in corso
    assert client.post(f"/api/indagini/{ident}/apri", json={}).json()["error"] == "Il PDF non c'è ancora."
    for mistaken in ("../../etc", "2026-01-01-0000-non-esiste"):
        assert client.get(f"/api/indagini/{mistaken}").status_code in (400, 404)
    assert client.post("/api/indagini", json={"topic": "x"}).status_code == 400


def test_investigation_in_progress_without_process_interrupted_results(environment) -> None:
    client, handler, _, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    ind.status["status"] = "in_progress"
    ind.save()
    assert client.get(f"/api/indagini/{ind.folder.name}").json()["status"] == "interrupted"


def test_events_from_file(environment) -> None:
    client, handler, _, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    ind.event({"kind": "phase", "text": "uno"})
    ind.event({"kind": "end", "status": "finished", "reason": "basta"})

    rows = []
    with client.stream("GET", f"/api/indagini/{ind.folder.name}/eventi") as stream:
        for row in stream.iter_lines():
            if row.startswith("data: "):
                rows.append(json.loads(row[6:]))
            if len(rows) == 2:
                break
    assert [r["kind"] for r in rows] == ["phase", "end"]
