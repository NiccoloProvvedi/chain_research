"""Probe of the review instructions on saved runs: only the review is redone, not the question.

For each answer with a value in a saved run (since 12 Nov the outcome has the rows of the solution) the review is asked
again, with the current instructions, to see what it would say about the premise. It costs about 2 cents per answer;
it does not measure the rows of the other articles (they are not saved): a real run is needed before trusting it.

Usage: venv/bin/python bench/review_probe.py <run> [<run> ...] [--parallel 3]
The reviews are kept in results/bench/<run>/review_probe.json.
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from stats import load  # noqa: E402

from chain_research import configuration  # noqa: E402
from chain_research.model import claude_json, cleaning  # noqa: E402
from chain_research.model.thinker import SCHEMAS, errors_schema, instructions  # noqa: E402
from chain_research.web import validation  # noqa: E402


def request(article: dict) -> tuple[dict, list]:
    """The review data rebuilt from the saved outcome: the route is the "Explanation" line of the answer ("Spiegazione"
    in runs saved before the translation)."""
    rows = [SimpleNamespace(text=p["row"], url=p["url"]) for p in article["chain"]["proofs"]]
    explanation = re.search(r"\*\*(?:Explanation|Spiegazione):?\*\*:?\s*(.+)", article.get("answer") or "")
    value = article.get("value") or article["chain"].get("discarded_value")
    return {"question": article["question"][:1500], "route": (explanation.group(1) if explanation else "")[:800],
            "answer": str(value), "rows": "\n".join(f"[{i}] {r.text[:700]}" for i, r in enumerate(rows, start=1))}, rows


def _one(data: dict, rows: list) -> dict:
    try:
        outcome, _ = claude_json.ask(instructions("review"), cleaning.data(data), SCHEMAS["review"], "review",
                                      model=configuration.load()["review_model"], max_budget_usd=0.2)
    except claude_json.SessionLimit:
        raise
    except claude_json.ClaudeError:
        return {"outcome": "not done"}
    return {"outcome": "not done"} if errors_schema(outcome, SCHEMAS["review"]) else validation.examine_review(outcome, rows)


def review(article: dict) -> dict | None:
    """Two reviews, as in the chain: the merged one (the strictest) and, in `single`, the verdict of each."""
    data, rows = request(article)
    single = [_one(data, rows) for _ in range(2)]
    unit = validation.merge_reviews(single)
    return {**unit, "single": [r.get("holds") for r in single]} if unit.get("outcome") == "done" else None


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("runs", nargs="+")
    a.add_argument("--parallel", type=int, default=3)
    arg = a.parse_args()
    for run in arg.runs:
        articles = {k: v for k, v in load(run).items()
                if "correct" in v and (v.get("chain") or {}).get("proofs")
                and (v.get("value") or v["chain"].get("discarded_value"))}
        with ThreadPoolExecutor(max_workers=arg.parallel) as executor:
            outcomes = dict(zip(articles, executor.map(review, articles.values()), strict=True))
        folder = Path(run) if Path(run).is_dir() else ROOT / "results" / "bench" / run
        (folder / "review_probe.json").write_text(json.dumps(
            {k: {"correct": bool(articles[k]["correct"]) and bool(articles[k].get("value")), "review": e,
                 "weak_links": articles[k]["chain"]["checks"].get("weak_links")} for k, e in outcomes.items()},
            ensure_ascii=False, indent=1), encoding="utf-8")
        counts: dict[tuple, int] = {}
        for key, exam in outcomes.items():
            premise = (exam or {}).get("premise", {}).get("outcome", "review failed")
            right = bool(articles[key]["correct"]) and bool(articles[key].get("value"))
            counts[(premise, right)] = counts.get((premise, right), 0) + 1
            if premise == "refuted":
                print(f"  {'RIGHT ANSWER LOST' if right else 'wrong answer stopped'} {key}: \"{exam['premise']['what']}\" "
                      f"(\"{exam['premise']['quote'][:90]}\")", flush=True)
        print(f"{run}: " + ", ".join(f"premise {p}, answer {'right' if g else 'wrong'}: {n}"
                                        for (p, g), n in sorted(counts.items(), key=str)), flush=True)


if __name__ == "__main__":
    main()
