"""Chiamate a Claude (claude_json), con registro, senza il Claude vero.

Le risposte finte sono copie di risposte reali (13/10): test/fixtures/orchestratore.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from ricerca_catena.modello import claude_json, config, registro

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orchestratore"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["scelta", "motivo"],
          "properties": {"scelta": {"type": "string", "enum": ["fattura", "contratto", "email"]},
                         "motivo": {"type": "string", "maxLength": 120}}}


# --- Claude finto -------------------------------------------------------------------------------------------------

@pytest.fixture
def claude_finto(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un eseguibile `claude` che salva gli argomenti e risponde con la riga `result` reale (o con un errore)."""
    cartella = config.cartella_log().parent / "bin"
    cartella.mkdir(parents=True, exist_ok=True)
    script = cartella / "claude"
    script.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
Path(os.environ["CLAUDE_FINTO_ARGOMENTI"]).write_text(json.dumps(sys.argv[1:]))
messaggio = json.loads(sys.stdin.readline())
Path(os.environ["CLAUDE_FINTO_ARGOMENTI"] + ".in").write_text(messaggio["message"]["content"][0]["text"])
modo = os.environ.get("CLAUDE_FINTO_MODO", "ok")
if modo == "non_collegato":
    print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "Not logged in"}}]}}}}))
elif modo == "budget":
    print(json.dumps({{"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.31,
                      "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
else:
    print(open({str(FIXTURE / "claude_result.jsonl")!r}).read().strip())
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{cartella}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_FINTO_ARGOMENTI", str(cartella / "argomenti.json"))
    return cartella


SCHEMA_MODO = {"type": "object", "properties": {"modalita": {"type": "string"}, "motivo": {"type": "string"}},
               "required": ["modalita", "motivo"]}


def test_claude_isolato_con_tetto_e_registro(claude_finto: Path) -> None:
    dati, consumo = claude_json.chiedi("Scegli la modalità.", "DOMANDA", SCHEMA_MODO, "inizio", max_budget_usd=0.2,
                                       piano="p-abc123")
    assert dati["modalita"] == "fatto_veloce"
    assert (consumo.token_ingresso, consumo.token_uscita, consumo.token_cache_scritti) == (2, 123, 1199)
    assert consumo.costo_usd == pytest.approx(0.00603)
    argomenti = json.loads((claude_finto / "argomenti.json").read_text())
    assert argomenti[argomenti.index("--tools") + 1] == ""
    assert json.loads(argomenti[argomenti.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--strict-mcp-config" in argomenti and "--no-session-persistence" in argomenti
    assert argomenti[argomenti.index("--max-budget-usd") + 1] == "0.2000"
    assert argomenti[argomenti.index("--model") + 1] == "haiku"  # predefinito: modello_catena
    riga = registro.leggi("pensatore")[-1]
    assert riga["punto"] == "inizio" and riga["piano"] == "p-abc123" and riga["esito"] == "ok"
    assert riga["token_uscita"] == 123 and riga["tetto_usd"] == 0.2


def test_claude_errori_registrati(claude_finto: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_FINTO_MODO", "budget")
    with pytest.raises(claude_json.ErroreClaude, match="error_max_budget_usd"):
        claude_json.chiedi("i", "t", SCHEMA_MODO, "espansione")
    monkeypatch.setenv("CLAUDE_FINTO_MODO", "non_collegato")
    with pytest.raises(claude_json.ErroreClaude, match="non è collegato"):
        claude_json.chiedi("i", "t", SCHEMA_MODO, "espansione")
    righe = registro.leggi("pensatore")
    assert righe[0]["costo_usd"] == pytest.approx(0.31) and "error_max_budget_usd" in righe[0]["esito"]
    assert "non è collegato" in righe[1]["esito"]
