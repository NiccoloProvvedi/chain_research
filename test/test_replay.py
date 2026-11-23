"""The chains recorded by the bench, replayed without network or Claude: answer, value and actions must stay the same.

Recording: `bench/bench.py --name X --record results/replay`. The folder is outside git (tens of MB): without
recordings these tests are skipped. A changed prompt changes the requests: then record again.
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

CHAINS = sorted((ROOT / "results" / "replay").glob("*/expected.json"))


@pytest.mark.replay
@pytest.mark.parametrize("file", CHAINS or [None], ids=[f.parent.name for f in CHAINS] or ["none"])
def test_recorded_chain_replays_unchanged(file: Path | None, tmp_path: Path) -> None:
    if file is None:
        pytest.skip("no recorded chain")
    outdated = file.parent / "to_record.txt"
    if outdated.is_file():  # the code changed on purpose for this chain: record it again (costs)
        pytest.skip(f"to record again: {outdated.read_text(encoding='utf-8').strip()[:200]}")
    expected = json.loads(file.read_text(encoding="utf-8"))
    # Chains recorded before the model checks (11 Nov) do not have their requests.
    expected["options"].setdefault("model_checks", False)
    expected["options"]["second_research"] = False   # recordings cover a single research
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
    # The "Confidence" line is written by the program from the status of the checks: a changed status rule changes it,
    # and has its own tests (test_validation.py). What matters here is that the chain and the written text stay the same.
    without_confidence = lambda text: re.sub(r"\*\*Confidence:?\*\*:?[^\n]*", "", text or "")  # noqa: E731
    assert outcome["chain"]["actions"] == expected["actions"]
    assert without_confidence(outcome["answer"]) == without_confidence(expected["answer"])


def test_record_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))
    answers = iter(["first", "second"])
    assert [replay.through("t", "x", lambda: next(answers)) for _ in range(2)] == ["first", "second"]

    def broken():
        raise ValueError("fault")
    with pytest.raises(ValueError):
        replay.through("t", "y", broken)
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    monkeypatch.setattr(replay, "_readings", type(replay._readings)())
    forbidden = lambda: pytest.fail("no outside calls in replay")  # noqa: E731
    assert [replay.through("t", "x", forbidden) for _ in range(2)] == ["first", "second"]
    with pytest.raises(replay.FailedWhenRecorded, match="fault"):
        replay.through("t", "y", forbidden)
    with pytest.raises(replay.UnrecordedRequest):
        replay.through("t", "x", forbidden)  # one more time than recorded
    with pytest.raises(replay.UnrecordedRequest):
        replay.through("t", "never seen", forbidden)


def test_fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_TODAY", "2026-01-02")
    assert replay.today() == "2026-01-02" and Collector("question").today == "2026-01-02"


def test_blocks_saved_in_reading_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first block finishes last: the graph must still have it first."""
    r = Collector("question", options={"claude_model": "haiku"})
    r.concepts = Concepts(tmp_path / "c.sqlite")

    def ask(_point, _rules, text, _schema=None):
        if "part 1 of" in text:
            time.sleep(0.3)
        return {"notes": "- fact", "relevance": 3, "concepts": [{"name": "Alfa", "sentence": "Alfa"}]}
    monkeypatch.setattr(r.thinker, "ask", ask)
    r._annotate(1, "https://example.org/a", "A", "Alfa " * (BLOCK // 2))
    r._evaluate_completed(wait_for=True)
    numbers = [x[0] for x in r.concepts.graph.db.execute("SELECT number FROM segment ORDER BY id")]
    assert numbers == sorted(numbers) and len(numbers) >= 2


def test_writing_replays_even_with_changed_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chain_research.model import claude_json
    row = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "result": "TEXT"})
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))
    monkeypatch.setattr(claude_json, "_run", lambda *_to: row)
    assert claude_json.ask("rules", "input of then", None, "writing", model="haiku")[0] == "TEXT"
    assert claude_json.ask("rules", "a reading", None, "reading", model="haiku")[0] == "TEXT"
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    monkeypatch.setattr(replay, "_readings", type(replay._readings)())
    monkeypatch.setattr(claude_json, "_run", lambda *_to: pytest.fail("no outside calls in replay"))
    assert claude_json.ask("rules", "input with the current status", None, "writing", model="haiku")[0] == "TEXT"
    with pytest.raises(replay.UnrecordedRequest):          # only writing and review: a changed reading stays an error
        claude_json.ask("rules", "another reading", None, "reading", model="haiku")
    assert claude_json.ask("rules", "input of then", None, "writing", model="haiku")[0] == "TEXT"
    monkeypatch.setenv("CHAIN_RECORD", str(tmp_path))        # two writings recorded: which one is unknown, error
    monkeypatch.delenv("CHAIN_REPLAY")
    monkeypatch.setattr(claude_json, "_run", lambda *_to: row)
    claude_json.ask("rules", "second writing", None, "writing", model="haiku")
    monkeypatch.delenv("CHAIN_RECORD")
    monkeypatch.setenv("CHAIN_REPLAY", str(tmp_path))
    with pytest.raises(replay.UnrecordedRequest, match="2 answers"):
        claude_json.ask("rules", "third input", None, "writing", model="haiku")
