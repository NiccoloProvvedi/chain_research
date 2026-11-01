"""Server dell'app senza processi veri: protezione, impostazioni, indagini, azioni ed eventi."""
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ricerca_catena import server
from ricerca_catena.indagine import Indagine

TOKEN = "t" * 64


@pytest.fixture
def ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(tmp_path / "impostazioni.json"))
    monkeypatch.setenv("RICERCA_SEGRETI", str(tmp_path / "segreti.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "casa"))
    (tmp_path / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "persona@esempio.org"}}')
    gestore = server.Gestore(tmp_path / "indagini")
    avviate: list[tuple[str, bool]] = []
    monkeypatch.setattr(gestore, "avvia", lambda ident, solo_documento=False: avviate.append((ident, solo_documento)))
    app = server.crea_app(TOKEN, lambda: 8123, gestore)
    client = TestClient(app, base_url="http://127.0.0.1:8123")
    client.cookies.set("token", TOKEN)
    return client, gestore, avviate, tmp_path


def test_protezione(ambiente) -> None:
    client, *_ = ambiente
    assert client.get("/api/indagini").status_code == 200
    estraneo = TestClient(client.app, base_url="http://127.0.0.1:8123")
    assert estraneo.get("/api/indagini").status_code == 403  # senza chiave
    altro_host = TestClient(client.app, base_url="http://esempio.org")
    altro_host.cookies.set("token", TOKEN)
    assert altro_host.get("/api/indagini").status_code == 403
    assert client.post("/api/impostazioni", json={}, headers={"origin": "http://evil.org"}).status_code == 403
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]


def test_impostazioni_e_contatto_wikipedia(ambiente) -> None:
    client, _, _, cartella = ambiente
    imp = client.get("/api/impostazioni").json()
    assert imp["modello_regia"] == "sonnet" and imp["budget_usd"] == 5.0 and imp["max_minuti"] == 120
    assert imp["wikipedia"] == {"attivo": True, "contatto": "", "in_uso": "persona@esempio.org", "origine": "account"}
    r = client.post("/api/impostazioni", json={"modello_regia": "opus", "senza_limiti": True, "budget_usd": 12,
                                               "wikipedia": {"attivo": True, "contatto": "io@esempio.org"}})
    assert r.status_code == 200 and r.json()["modello_regia"] == "opus" and r.json()["senza_limiti"] is True
    assert r.json()["wikipedia"]["origine"] == "impostazioni"
    assert (cartella / "segreti.json").stat().st_mode & 0o777 == 0o600
    for sbagliata in ({"modello_regia": "gpt"}, {"budget_usd": 0}, {"wikipedia": {"contatto": "a b"}}):
        assert client.post("/api/impostazioni", json=sbagliata).status_code == 400, sbagliata


def test_nuova_indagine_elenco_e_azioni(ambiente) -> None:
    client, gestore, avviate, _ = ambiente
    r = client.post("/api/indagini", json={"argomento": "Storia di Arduino", "modello_regia": "haiku",
                                           "budget_usd": "3", "max_minuti": "", "senza_limiti": False})
    assert r.status_code == 200
    ident = r.json()["id"]
    assert avviate == [(ident, False)]
    stato = client.get(f"/api/indagini/{ident}").json()
    assert stato["modello_regia"] == "haiku" and stato["limiti"]["budget_usd"] == 3 and stato["limiti"]["max_minuti"] == 120
    voce = client.get("/api/indagini").json()[0]
    assert voce["id"] == ident and voce["argomento"] == "Storia di Arduino" and voce["stato"] == "in_attesa"
    assert client.post(f"/api/indagini/{ident}/documento", json={}).status_code == 200 and avviate[-1] == (ident, True)
    assert client.post(f"/api/indagini/{ident}/ferma", json={}).status_code == 400  # non è in corso
    assert client.post(f"/api/indagini/{ident}/apri", json={}).json()["errore"] == "Il PDF non c'è ancora."
    for sbagliato in ("../../etc", "2026-01-01-0000-non-esiste"):
        assert client.get(f"/api/indagini/{sbagliato}").status_code in (400, 404)
    assert client.post("/api/indagini", json={"argomento": "x"}).status_code == 400


def test_indagine_in_corso_senza_processo_risulta_interrotta(ambiente) -> None:
    client, gestore, _, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    ind.stato["stato"] = "in_corso"
    ind.salva()
    assert client.get(f"/api/indagini/{ind.cartella.name}").json()["stato"] == "interrotta"


def test_eventi_dal_file(ambiente) -> None:
    client, gestore, _, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    ind.evento({"tipo": "fase", "testo": "uno"})
    ind.evento({"tipo": "fine", "stato": "finita", "motivo": "basta"})

    righe = []
    with client.stream("GET", f"/api/indagini/{ind.cartella.name}/eventi") as flusso:
        for riga in flusso.iter_lines():
            if riga.startswith("data: "):
                righe.append(json.loads(riga[6:]))
            if len(righe) == 2:
                break
    assert [r["tipo"] for r in righe] == ["fase", "fine"]
