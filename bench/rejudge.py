"""New judgement of saved runs, with the corrected yardstick and without redoing the questions.

The value judged is the one found by the program (`value`), not the one extracted by a model; the comparison takes the
sign into account. An old judge verdict stays if it concerned the same value. The rest is "pending judgement": with
`--judge` Claude Haiku decides it (cents), without it stays out of the count.

Usage: venv/bin/python bench/rejudge.py <run> [<run> ...] [--judge] [--questions bench/questions_holdout.json]
Writes results/bench/<run>/rejudged_outcomes.json (read by stats.py instead of outcomes.json).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research.web import validation  # noqa: E402


def rejudge(outcomes: dict[str, dict], with_judge: bool = False, statuses: dict[str, str] | None = None) -> dict[str, dict]:
    new_items = {}
    for key, old in outcomes.items():
        article = dict(old)
        new_items[key] = article
        if "error" in article:
            continue
        if statuses and key in statuses:
            article["reference_status"] = statuses[key]
        # The status is recomputed from the saved check outcomes, with the current rule: a changed rule can be tried
        # on runs already done without redoing them. The status written by the run stays in `run_status`.
        checks = (article.get("chain") or {}).get("checks")
        # with a second research the status comes from comparing the two (web/retry.py): not recomputed from one
        if checks and "exams" in checks and not article.get("retry"):
            article.setdefault("run_status", article.get("status"))
            article["status"], reasons = validation.status(checks)
            article["confirmed"] = article["status"] in ("verified", "supported")
        if article.get("without_answer"):  # right only if the chain gave no value
            article["correct"], article["decided_by"] = not article.get("found"), "unanswerable"
            continue
        value = " ".join(str(article.get("value") or "").split())
        if not value:  # no value from the program: the old judgement on the written answer stands
            continue
        final_old = judge.normalize(article.get("final") or "").strip()
        value_n = judge.normalize(value).strip()
        article["final"] = value
        if judge.normalized_equal(article["expected"], value):
            article["correct"], article["decided_by"] = True, "comparison"
        elif article.get("decided_by") == "judge" and final_old and (
                value_n in final_old or final_old in value_n):
            pass  # the judge had already seen this value
        elif with_judge:
            correct, article["decided_by"] = judge.assess(article.get("question", ""), article["expected"], value)
            if correct is None:
                article.pop("correct", None)
            else:
                article["correct"] = correct
        else:
            article.pop("correct", None)
            article["decided_by"] = "pending judgement"
    return new_items


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("runs", nargs="+")
    a.add_argument("--judge", action="store_true", help="Claude Haiku decides the doubtful cases")
    a.add_argument("--questions", default="", help="questions file, for the reference_status field")
    arg = a.parse_args()
    statuses = {}
    if arg.questions:
        for d in json.loads(Path(arg.questions).read_text(encoding="utf-8"))["questions"]:
            statuses[f"{d['theme']}/{d['seed']}"] = d.get("reference_status", "ok")
    for run in arg.runs:
        folder = Path(run) if Path(run).is_dir() else ROOT / "results" / "bench" / run
        outcomes = json.loads((folder / "outcomes.json").read_text(encoding="utf-8"))
        new_items = rejudge(outcomes, arg.judge, statuses)
        (folder / "rejudged_outcomes.json").write_text(json.dumps(new_items, ensure_ascii=False, indent=1), encoding="utf-8")
        for key, article in new_items.items():
            first, after = outcomes[key].get("correct"), article.get("correct", "pending judgement")
            if first != after and "error" not in article:
                print(f"{run} {key}: {first} → {after} (expected \"{article.get('wait')}\", value \"{article.get('value')}\")")
        judged = [v for v in new_items.values() if "correct" in v]
        print(f"{run}: {sum(v['correct'] for v in judged)}/{len(judged)} right, "
              f"{sum(1 for v in new_items.values() if v.get('decided_by') == 'pending judgement')} pending judgement")


if __name__ == "__main__":
    main()
