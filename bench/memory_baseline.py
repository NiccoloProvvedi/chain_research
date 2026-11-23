"""Linea di base: il modello risponde a memoria (senza ricerca) alle 40 domande; stesso giudice del banco.

Uso: venv/bin/python bench/memory_baseline.py haiku|sonnet|opus (1/11/2026: 9, 18 e 24 su 40)."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research.model import claude_json  # noqa: E402

INSTRUCTIONS = ("Rispondi alla DOMANDA a memoria, senza strumenti. Ragiona brevemente se serve, poi chiudi con una riga "
              "«**Risposta:** <un solo valore>». Se non sei sicuro, dai comunque la risposta più probabile.")


def one(d: dict, model: str) -> dict:
    try:
        text = str(claude_json.ask(INSTRUCTIONS, f"DOMANDA: {d['question']}", None, "from_memory", model=model)[0])
    except claude_json.ClaudeError as error:
        text = f"ERRORE {error}"
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
        print("✓" if e["correct"] else "✗", e["seed"], "|", e["final"][:60], "| attesa:", e["wait"])


if __name__ == "__main__":
    main()
