"""Chiamate a Claude (claude_json), con registro, senza il Claude vero.

Le risposte finte sono copie di risposte reali (13/10): test/fixtures/orchestratore.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from chain_research.model import claude_json, config, event_log

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orchestratore"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["choice", "reason"],
          "properties": {"choice": {"type": "string", "enum": ["fattura", "contract", "email"]},
                         "reason": {"type": "string", "maxLength": 120}}}


# --- Claude finto -------------------------------------------------------------------------------------------------

@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un eseguibile `claude` che salva gli argomenti e risponde con la riga `result` reale (o con un errore)."""
    folder = config.folder_log().parent / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "claude"
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
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_FINTO_ARGOMENTI", str(folder / "arguments.json"))
    return folder


MODE_SCHEMA = {"type": "object", "properties": {"mode": {"type": "string"}, "reason": {"type": "string"}},
               "required": ["mode", "reason"]}


def test_isolated_claude_with_cap_and_log(fake_claude: Path) -> None:
    data, usage = claude_json.ask("Scegli la modalità.", "DOMANDA", MODE_SCHEMA, "start", max_budget_usd=0.2,
                                       plan="p-abc123")
    assert data["mode"] == "fatto_veloce"
    assert (usage.input_tokens, usage.output_tokens, usage.cache_write_tokens) == (2, 123, 1199)
    assert usage.cost_usd == pytest.approx(0.00603)
    arguments = json.loads((fake_claude / "arguments.json").read_text())
    assert arguments[arguments.index("--tools") + 1] == ""
    assert json.loads(arguments[arguments.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert "--strict-mcp-config" in arguments and "--no-session-persistence" in arguments
    assert arguments[arguments.index("--max-budget-usd") + 1] == "0.2000"
    assert arguments[arguments.index("--model") + 1] == "haiku"  # predefinito: modello_catena
    row = event_log.read("thinker")[-1]
    assert row["point"] == "start" and row["plan"] == "p-abc123" and row["outcome"] == "ok"
    assert row["output_tokens"] == 123 and row["cap_usd"] == 0.2


def test_claude_recorded_errors(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_FINTO_MODO", "budget")
    with pytest.raises(claude_json.ClaudeError, match="error_max_budget_usd"):
        claude_json.ask("i", "t", MODE_SCHEMA, "espansione")
    monkeypatch.setenv("CLAUDE_FINTO_MODO", "non_collegato")
    with pytest.raises(claude_json.ClaudeError, match="non è collegato"):
        claude_json.ask("i", "t", MODE_SCHEMA, "espansione")
    rows = event_log.read("thinker")
    assert rows[0]["cost_usd"] == pytest.approx(0.31) and "error_max_budget_usd" in rows[0]["outcome"]
    assert "non è collegato" in rows[1]["outcome"]
