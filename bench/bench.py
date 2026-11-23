"""Test bench: the 40 chain questions (bench/questions.json) solved with the same executor as the app.

Usage: venv/bin/python bench/bench.py --name test-1 [--seeds "computing/Linux kernel,..."] [--parallel 3]
       [--note "what is being tested"] [--budget 12]
Questions run in parallel, each in its own process (`python -m chain_research.question`) with its own browser
profile; Wikidata is forbidden (the questions come from there). Outcomes in results/bench/<name>/outcomes.json (a rerun
resumes from the questions not done yet) and a row in bench/LOG.md.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import judge  # noqa: E402

from chain_research import configuration  # noqa: E402
from chain_research.model import claude_json  # noqa: E402
from chain_research.web.sources.wikipedia_graph import interval_per  # noqa: E402


MAX_WAIT_S = 14 * 3600


def one_question(d: dict, profile: str, parallel: int, record: Path | None = None) -> dict:
    # A recording for replay covers a single research: the second research for uncertain answers stays off.
    options = {"without_wikidata": True, "wikipedia_interval": interval_per(parallel), "model_checks": True,
               "second_research": record is None}
    request = {"question": d["question"], "profile": str(configuration.folder("profiles_folder") / profile),
                 "pieces_folder": str(ROOT / "results" / "bench" / ".pieces"), "options": options}
    environment = dict(os.environ)
    folder, today = None, f"{datetime.now():%Y-%m-%d}"
    if record is not None:  # each question in its own folder; same PYTHONHASHSEED and date in the replay too
        folder = record / re.sub(r"\W+", "-", f"{d['theme']}-{d['seed']}")
        environment.update(CHAIN_RECORD=str(folder), CHAIN_TODAY=today, PYTHONHASHSEED="0")
    output = subprocess.run([str(ROOT / "venv" / "bin" / "python"), "-m", "chain_research.question"], cwd=ROOT,
                            input=json.dumps(request, ensure_ascii=False) + "\n", capture_output=True, text=True,
                            # the wait for the session limit (up to 6 hours) fits within this time: 12 Nov, three
                            # control questions lost at 3 hours while waiting for the reset
                            timeout=MAX_WAIT_S, env=environment)
    for row in reversed(output.stdout.splitlines()):
        message = json.loads(row)
        if "outcome" in message:
            outcome = message["outcome"]
            if folder is not None:
                (folder / "expected.json").write_text(json.dumps({
                    "question": d["question"], "options": options, "today": today, "value": outcome.get("value"),
                    "found": outcome.get("found"), "end_reason": outcome.get("end_reason"),
                    "actions": outcome.get("chain", {}).get("actions"), "answer": outcome.get("answer")},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            return outcome
        if "error" in message:
            raise RuntimeError(message["error"])
    raise RuntimeError(f"no outcome (exit code {output.returncode})")


def assess_article(article: dict) -> None:
    """Adds `final`, `correct` and `decided_by`. If the judge does not answer, the answer found stays, marked
    `pending_judgement`: on resume only the judgement is redone, not the question."""
    article.pop("pending_judgement", None)
    if article.get("without_answer"):  # the question has no answer: right is to give none (no model judges)
        article["final"] = " ".join(str(article.get("value") or "").split())
        article["correct"], article["decided_by"] = not article.get("found"), "unanswerable"
        return
    try:
        article["final"] = judge.final_of(article, article["question"])
        correct, article["decided_by"] = judge.assess(article["question"], article["wait"], article["final"])
    except claude_json.ClaudeError as error:
        article.setdefault("final", "")
        correct, article["decided_by"] = None, f"judge error: {error}"[:200]
    if correct is None:
        article["pending_judgement"] = True
    else:
        article["correct"] = correct


def counted(outcomes: list[dict]) -> list[dict]:
    """The judged questions with a good expected answer (`reference_status`: docs/validation.md §3)."""
    return [e for e in outcomes if "correct" in e and e.get("reference_status", "ok") == "ok"]


def main() -> None:
    a = argparse.ArgumentParser(description="Test bench for the chain questions")
    a.add_argument("--name", required=True)
    a.add_argument("--seeds", default="", help="only these questions: theme/seed, comma-separated")
    a.add_argument("--parallel", type=int, default=3)
    a.add_argument("--budget", type=float, default=12.0, help="maximum Claude dollars for the whole run")
    a.add_argument("--note", default="")
    a.add_argument("--questions", default="", help="questions file other than bench/questions.json")
    a.add_argument("--record", default="", help="folder where everything is recorded for replay (test_replay.py)")
    arg = a.parse_args()
    folder = ROOT / "results" / "bench" / arg.name
    folder.mkdir(parents=True, exist_ok=True)
    outcomes_file = folder / "outcomes.json"
    outcomes = json.loads(outcomes_file.read_text(encoding="utf-8")) if outcomes_file.is_file() else {}
    questions_file = Path(arg.questions) if arg.questions else ROOT / "bench" / "questions.json"
    questions = json.loads(questions_file.read_text(encoding="utf-8"))["questions"][:None if arg.questions else 40]
    record = Path(arg.record).resolve() if arg.record else None
    if arg.seeds:
        wanted = {s.strip() for s in arg.seeds.split(",") if s.strip()}
        questions = [d for d in questions if f"{d['theme']}/{d['seed']}" in wanted]
    for article in outcomes.values():  # judgements left pending (session limit, judge error)
        if article.get("pending_judgement"):
            assess_article(article)
    from_do = [d for d in questions if not {"correct", "pending_judgement"} & set(outcomes.get(f"{d['theme']}/{d['seed']}", {}))]
    print(f"{len(from_do)} questions to do out of {len(questions)}; results in {folder}", flush=True)
    mutex = threading.Lock()
    profiles = [f"bench_{i + 1}" for i in range(arg.parallel)]

    def work(d: dict) -> None:
        key = f"{d['theme']}/{d['seed']}"
        with mutex:
            if sum(e.get("cost_usd", 0) for e in outcomes.values()) >= arg.budget:
                return
            profile = profiles.pop(0)
        try:
            outcome = one_question(d, profile, arg.parallel, record)
            article = {"theme": d["theme"], "seed": d["seed"], "question": d["question"], "wait": d["answer"],
                    "reference_status": d.get("reference_status", "ok"),
                    "without_answer": bool(d.get("without_answer")), **outcome}
            assess_article(article)
            print(f"{'?' if 'correct' not in article else '✓' if article['correct'] else '✗'} {key}: expected "
                  f"\"{d['answer']}\", final \"{article['final'][:80]}\" ({outcome['seconds']} s, {outcome['cost_usd']} $)",
                  flush=True)
        except Exception as error:  # noqa: BLE001 - a failed question does not stop the run
            article = {"theme": d["theme"], "seed": d["seed"], "error": f"{type(error).__name__}: {error}"[:500]}
            print(f"ERROR {key}: {error}", flush=True)
        finally:
            with mutex:
                profiles.append(profile)
        with mutex:
            outcomes[key] = article
            outcomes_file.write_text(json.dumps(outcomes, ensure_ascii=False, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=arg.parallel) as executor:
        list(executor.map(work, from_do))
    done = counted([outcomes.get(f"{d['theme']}/{d['seed']}", {}) for d in questions])
    right = sum(e["correct"] for e in done)
    confirmed = [e for e in done if e.get("confirmed")]
    from_judge = sum(e.get("decided_by") == "judge" for e in done)
    pending = sum(1 for e in outcomes.values() if e.get("pending_judgement"))
    minutes = round(sum(e["seconds"] for e in done) / max(1, len(done)) / 60, 1)
    cost = round(sum(e["cost_usd"] for e in done), 3)
    errors = sum(1 for d in questions if "error" in outcomes.get(f"{d['theme']}/{d['seed']}", {}))
    print(f"SUMMARY {right}/{len(done)} right, {minutes} minutes on average, {cost} $, {errors} errors, confirmed right "
          f"{sum(e['correct'] for e in confirmed)}/{len(confirmed)}, decided by the judge {from_judge}, pending judgement "
          f"{pending}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip() or "?"
    with (ROOT / "bench" / "LOG.md").open("a", encoding="utf-8") as f:
        f.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {arg.note or arg.name} | {commit} | {len(questions)} questions | "
                f"{right}/{len(done)} | {minutes} | {cost} | {errors} | "
                f"{sum(e['correct'] for e in confirmed)}/{len(confirmed)} | {from_judge} |\n")


if __name__ == "__main__":
    main()
