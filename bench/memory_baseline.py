"""Baseline: the model answers the 40 questions from memory (without research); same judge as the bench.

Usage: venv/bin/python bench/memory_baseline.py haiku|sonnet|opus (2026-11-01: 9, 18 and 24 out of 40)."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research.model import claude_json  # noqa: E402

INSTRUCTIONS = ("Answer the QUESTION from memory, without tools. Reason briefly if needed, then end with a line "
                "\"**Answer:** <a single value>\". If you are not sure, give the most likely answer anyway.")


def one(d: dict, model: str) -> dict:
    try:
        text = str(claude_json.ask(INSTRUCTIONS, f"QUESTION: {d['question']}", None, "from_memory", model=model)[0])
    except claude_json.ClaudeError as error:
        text = f"ERROR {error}"
    final = judge.extract(d["question"], judge.short_answer(text))
    correct, _ = judge.assess(d["question"], d["answer"], final)
    return {"seed": d["seed"], "final": final, "wait": d["answer"], "correct": correct, "text": text[-600:]}


def main() -> None:
    model = sys.argv[1]
    questions = json.loads((ROOT / "bench" / "questions.json").read_text(encoding="utf-8"))["questions"][:40]
    with ThreadPoolExecutor(4) as executor:
        outcomes = list(executor.map(lambda d: one(d, model), questions))
    folder = ROOT / "results" / "bench"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"from_memory_{model}.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    print(model, sum(e["correct"] for e in outcomes), "/", len(outcomes))
    for e in outcomes:
        print("✓" if e["correct"] else "✗", e["seed"], "|", e["final"][:60], "| expected:", e["wait"])


if __name__ == "__main__":
    main()
