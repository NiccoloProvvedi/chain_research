"""Report on the result checks: how much an answer's status is worth and what each check flags.

Reads the saved runs (the new judgement of `rejudge.py`, if present) and calls neither the network nor Claude.

Usage: venv/bin/python bench/report.py <base run> <run> [<run> ...]
       The first run is the comparison (previous code); the others are repetitions of the same test with the checks.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stats import _equal, load, counted, mcnemar, summarize, wilson  # noqa: E402

STATUSES = ("verified", "supported", "contested", "uncertain")


def signals(e: dict) -> list[str]:
    """The checks that flagged something on this answer (chain.checks of the outcome)."""
    c = (e.get("chain") or {}).get("checks") or {}
    exams = c.get("exams") or {}
    reading, corroboration, review = (exams.get(k) or {} for k in ("second_reading", "corroboration", "review"))
    hits = []
    if c.get("weak_links"):
        hits.append("weak link")
    if c.get("value_source") == "C":
        hits.append("value from a snippet")
    if c.get("disagreements"):
        hits.append("infobox pointing to another candidate")
    if reading.get("outcome") in ("disagree", "not found again"):
        hits.append(f"second reading: {reading['outcome']}")
    if corroboration.get("outcome") in ("disagree", "single source", "agree"):
        hits.append(f"other sources: {corroboration['outcome']}")
    if review.get("objections"):
        hits.append("review: objection with row")
    elif review.get("outcome") == "done":
        hits.append(f"review: holds {review.get('holds')}")
    if not exams or all(x.get("outcome") == "not done" for x in (reading, corroboration, review)):
        hits.append("exams not done")
    return hits


def signals_table(outcomes: list[dict]) -> dict[str, tuple[int, int]]:
    """Signal → (right answers, wrong answers) among those with a value."""
    counts: dict[str, list[int]] = {}
    for e in counted(outcomes):
        if not e.get("value"):
            continue
        for s in signals(e):
            counts.setdefault(s, [0, 0])[0 if e["correct"] else 1] += 1
    return {s: (g, b) for s, (g, b) in sorted(counts.items())}


def per_status(outcomes: list[dict]) -> dict[str, tuple[int, int]]:
    """Status → (right, total)."""
    done = counted(outcomes)
    return {s: (sum(e["correct"] for e in done if e.get("status") == s), sum(e.get("status") == s for e in done))
            for s in STATUSES}


def rule(outcomes: list[dict]) -> list[tuple[str, bool, str]]:
    """The decision rule fixed before the runs (docs/validation.md): (criterion, passed, numbers)."""
    statuses = per_status(outcomes)
    right, total = statuses["verified"]
    low = wilson(right, total)[0]
    good = tuple(map(sum, zip(statuses["verified"], statuses["supported"], strict=True)))
    doubtful = tuple(map(sum, zip(statuses["contested"], statuses["uncertain"], strict=True)))
    share = lambda x: x[0] / x[1] if x[1] else 0.0  # noqa: E731
    return [
        ("verified: at least 95% right (lower bound at least 85%)",
         total > 0 and right / total >= 0.95 and low >= 0.85, f"{right}/{total}, lower bound {low:.0%}"),
        ("verified and supported more often right than contested and uncertain",
         good[1] > 0 and doubtful[1] > 0 and share(good) - share(doubtful) >= 0.15,
         f"{good[0]}/{good[1]} vs {doubtful[0]}/{doubtful[1]}"),
    ]


def repetitions(runs: list[dict[str, dict]]) -> str:
    common = [k for k in runs[0] if all("value" in c.get(k, {}) for c in runs)]
    value = sum(all(_equal(c[k]["value"], runs[0][k]["value"]) for c in runs) for k in common)
    status = sum(len({c[k].get("status") for c in runs}) == 1 for k in common)
    return f"{len(common)} questions in all runs: same value {value}, same status {status}"


def main() -> None:
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("base")
    a.add_argument("runs", nargs="+")
    arg = a.parse_args()
    base = load(arg.base)
    print(f"BASE {arg.base}: {summarize(list(base.values()))}")
    all_entries: list[dict] = []
    loaded = []
    for name in arg.runs:
        run = load(name)
        loaded.append(run)
        outcomes = list(run.values())
        all_entries += outcomes
        done = counted(outcomes)
        common = [k for k in base if "correct" in base[k] and "correct" in run.get(k, {})]
        base_only = sum(base[k]["correct"] and not run[k]["correct"] for k in common)
        run_only = sum(run[k]["correct"] and not base[k]["correct"] for k in common)
        print(f"\n{name}: {summarize(outcomes)}; {sum(e['cost_usd'] for e in done):.2f} $, "
              f"{sum(e['seconds'] for e in done) / max(1, len(done)) / 60:.1f} min on average; pending judgement "
              f"{sum(1 for e in outcomes if 'correct' not in e and 'error' not in e)}, errors {sum('error' in e for e in outcomes)}")
        print(f"  vs the base ({len(common)} in common): only the base right {base_only}, only this one {run_only}, "
              f"McNemar p = {mcnemar(base_only, run_only):.3f}")
        for status, (right, total) in per_status(outcomes).items():
            if total:
                print(f"  {status}: {right}/{total}")
    print(f"\nALL RUNS WITH THE CHECKS ({len(arg.runs)}; the repetitions are not independent)")
    for status, (right, total) in per_status(all_entries).items():
        low, high = wilson(right, total)
        print(f"  {status}: {right}/{total}" + (f" = {right / total:.0%} (95%: {low:.0%}-{high:.0%})" if total else ""))
    print("  signal: right / wrong")
    for signal, (right, wrong) in signals_table(all_entries).items():
        print(f"    {signal}: {right} / {wrong}")
    if len(loaded) > 1:
        print("  " + repetitions(loaded))
    print("\nDECISION RULE")
    for criterion, passed, numbers in rule(all_entries):
        print(f"  {'PASS' if passed else 'FAIL'}: {criterion} — {numbers}")


if __name__ == "__main__":
    main()
