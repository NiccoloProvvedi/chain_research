"""Le catene registrate dal banco, ripercorse senza rete né Claude: risposta, valore e azioni devono restare uguali.

Registrazione: `bench/bench.py --nome X --registra risultati/rigioco`. La cartella è fuori da git (decine di MB): senza
registrazioni questi test si saltano. Un prompt cambiato cambia le richieste: allora si registra di nuovo.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chain_research import ROOT
from chain_research.model import replay
from chain_research.proofs.concepts import Concepts
from chain_research.web.collector import BLOCK, Collector

CHAINS = sorted((ROOT / "results" / "rigioco").glob("*/atteso.json"))


@pytest.mark.replay
@pytest.mark.parametrize("file", CHAINS or [None], ids=[f.parent.name for f in CHAINS] or ["nessuna"])
def test_chain_replayed_equal(file: Path | None, tmp_path: Path) -> None:
    if file is None:
        pytest.skip("nessuna catena registrata")
    outdated = file.parent / "da_registrare.txt"
    if outdated.is_file():  # il codice è cambiato apposta su questa catena: va registrata di nuovo (costa)
        pytest.skip(f"da registrare di nuovo: {outdated.read_text(encoding='utf-8').strip()[:200]}")
    expected = json.loads(file.read_text(encoding="utf-8"))
    # Le catene registrate prima dei controlli con il modello (11/11) non ne hanno le richieste.
    expected["options"].setdefault("model_checks", False)
    expected["options"]["second_research"] = False   # le registrazioni sono di una ricerca sola
    request = {"question": expected["question"], "options": expected["options"], "pieces_folder": str(tmp_path / "pieces"),
                 "profile": str(tmp_path / "profile")}
    output = subprocess.run([sys.executable, "-m", "chain_research.question"], cwd=ROOT, capture_output=True, text=True,
                            input=json.dumps(request, ensure_ascii=False) + "\n", timeout=300,
                            env={**os.environ, "CHAIN_REPLAY": str(file.parent), "CHAIN_TODAY": expected["today"],
                                 "PYTHONHASHSEED": "0"})
    messages = [json.loads(r) for r in output.stdout.splitlines()]
    outcome = next((m["outcome"] for m in messages if "outcome" in m), None)
    assert outcome is not None, [m for m in messages if "error" in m] or output.stderr[-800:]
    assert (outcome["value"], outcome["found"], outcome["end_reason"]) == (expected["value"], expected["found"],
                                                                        expected["end_reason"])
    # La riga «Fiducia» la scrive il programma dallo stato dei controlli: una regola dello stato cambiata la cambia,
    # e ha i suoi test (test_validazione.py). Qui conta che la catena e il testo scritto restino uguali.
    without_confidence = lambda text: re.sub(r"\*\*Fiducia:?\*\*:?[^\n]*", "", text or "")  # noqa: E731
    assert outcome["chain"]["actions"] == expected["actions"]
    assert without_confidence(outcome["answer"]) == without_confidence(expected["answer"])


def test_record_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))
    answers = iter(["first", "second"])
    assert [replay.through("t", "x", lambda: next(answers)) for _ in range(2)] == ["first", "second"]

    def broken():
        raise ValueError("guasto")
    with pytest.raises(ValueError):
        replay.through("t", "y", broken)
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    monkeypatch.setattr(replay, "_readings", type(replay._readings)())
    forbidden = lambda: pytest.fail("nel rigioco non si esce")  # noqa: E731
    assert [replay.through("t", "x", forbidden) for _ in range(2)] == ["first", "second"]
    with pytest.raises(replay.FailedWhenRecorded, match="guasto"):
        replay.through("t", "y", forbidden)
    with pytest.raises(replay.UnrecordedRequest):
        replay.through("t", "x", forbidden)  # una volta in più di quelle registrate
    with pytest.raises(replay.UnrecordedRequest):
        replay.through("t", "mai vista", forbidden)


def test_today_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_TODAY", "2026-01-02")
    assert replay.today() == "2026-01-02" and Collector("question").today == "2026-01-02"


def test_saved_blocks_in_order_of_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il primo blocco finisce per ultimo: il grafo deve comunque averlo per primo."""
    r = Collector("question", options={"claude_model": "haiku"})
    r.concepts = Concepts(tmp_path / "c.sqlite")

    def ask(_point, _rules, text, _schema=None):
        if "parte 1 di" in text:
            time.sleep(0.3)
        return {"notes": "- fatto", "relevance": 3, "concepts": [{"name": "Alfa", "sentence": "Alfa"}]}
    monkeypatch.setattr(r.thinker, "ask", ask)
    r._annotate(1, "https://esempio.org/a", "A", "Alfa " * (BLOCK // 2))
    r._evaluate_completed(wait_for=True)
    numbers = [x[0] for x in r.concepts.graph.db.execute("SELECT numero FROM segmento ORDER BY id")]
    assert numbers == sorted(numbers) and len(numbers) >= 2


def test_writing_replay_also_with_input_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chain_research.model import claude_json
    row = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "result": "TESTO"})
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))
    monkeypatch.setattr(claude_json, "_run", lambda *_to: row)
    assert claude_json.ask("rules", "ingresso di allora", None, "writing", model="haiku")[0] == "TESTO"
    assert claude_json.ask("rules", "una lettura", None, "reading", model="haiku")[0] == "TESTO"
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    monkeypatch.setattr(replay, "_readings", type(replay._readings)())
    monkeypatch.setattr(claude_json, "_run", lambda *_to: pytest.fail("nel rigioco non si esce"))
    assert claude_json.ask("rules", "ingresso con lo stato di adesso", None, "writing", model="haiku")[0] == "TESTO"
    with pytest.raises(replay.UnrecordedRequest):          # solo scrittura e revisione: una lettura cambiata resta un errore
        claude_json.ask("rules", "un'altra lettura", None, "reading", model="haiku")
    assert claude_json.ask("rules", "ingresso di allora", None, "writing", model="haiku")[0] == "TESTO"
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))        # due scritture registrate: non si sa quale, errore
    monkeypatch.delenv("CHAIN_REPLAY")
    monkeypatch.setattr(claude_json, "_run", lambda *_to: row)
    claude_json.ask("rules", "seconda scrittura", None, "writing", model="haiku")
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    with pytest.raises(replay.UnrecordedRequest, match="2 risposte"):
        claude_json.ask("rules", "terzo ingresso", None, "writing", model="haiku")
