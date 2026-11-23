"""Sabotage: with one check switched off at a time, at least one test must fail (docs/validation.md, T1).

Copies the code and the tests to a temporary folder, changes one line and runs the tests without network: if they all
pass, that check is protected by no test. Does not touch the project and does not call Claude.

Usage: venv/bin/python bench/sabotage.py        (exits with 1 if a sabotage goes unnoticed)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ["test/test_validation.py", "test/test_chain.py", "test/test_bench.py", "test/test_regressions.py",
        "test/test_collector.py", "test/test_lifecycle.py", "test/test_investigation.py", "test/test_retry.py"]
# (name, file, text to find exactly once, text that replaces it)
SABOTAGES = [
    ("G1 the value is no longer compared with the row", "chain_research/web/chain.py",
     "if proof is not None and not validation.value_in_row(outcome.get(\"value\", \"\"), proof.text):",
     "if False:"),
    ("G1 numbers do not count", "chain_research/web/validation.py",
     "            if whole not in its and not to_groups:", "            if False:"),
    ("G1 the month does not count", "chain_research/web/validation.py",
     "return not months or not of_row or bool(months & of_row)", "return True"),
    ("G3 an ancestor can come back", "chain_research/web/validation.py",
     "        if my & _identity(node):", "        if False:"),
    ("G3 the check is not wired to the frontier", "chain_research/web/chain.py",
     "            returned = [a for a in children if validation.repeats_ancestor(a, node)]", "            returned = []"),
    ("G4 the check is not wired to the verification", "chain_research/web/chain.py",
     " and not validation.tie_only_from_snippets(a)]", "]"),
    ("G7 disagreements are not recorded", "chain_research/web/chain.py",
     "            if article not in self.conflicts:", "            if False:"),
    ("G4 a snippet counts as a page read", "chain_research/web/validation.py",
     "    if str(proof.text).startswith(SNIPPET):", "    if False:"),
    ("G4 confirmed even with the value from a snippet", "chain_research/web/validation.py",
     '    if c.get("value_source") == "C":', '    if False:'),
    ("G7 disagreements do not remove the seal", "chain_research/web/validation.py",
     '    for d in c.get("disagreements") or []:', '    for d in []:'),
    ("G7 every Wikipedia row is a disagreement", "chain_research/web/validation.py",
     " or not _from_infobox(a):", ":"),
    ("G7 siblings are disagreements too", "chain_research/web/validation.py",
     "if a.parent is not None and not _strong_tie(a)}", "if a.parent is not None}"),
    ("weak link confirmed anyway", "chain_research/web/validation.py",
     '    elif c.get("weak_links"):', '    elif False:'),
    ("weak link that passes even without a review that holds", "chain_research/web/validation.py",
     '    if c.get("weak_links") and holds:', '    if c.get("weak_links"):'),
    ("weak link with a review that holds taken as verified", "chain_research/web/validation.py",
     '    if c.get("weak_links") and holds:\n        limits.append(', '    if c.get("weak_links") and holds:\n        ('),
    ("G2/G5 two different values pass as equal", "chain_research/web/validation.py",
     "        return (numbers_to == numbers_b and", "        return (True and"),
    ("G2 the second reading does not check the row", "chain_research/web/chain.py",
     "                or not validation.value_in_row(value, proof.text) \\\n", ""),
    ("G6 a \"no\" counts even without the quote in the row", "chain_research/web/validation.py",
     "        if outcome == \"no\" and is_valid:", "        if outcome == \"no\":"),
    ("status: the review's objections do not count", "chain_research/web/validation.py",
     "    for o in review.get(\"objections\") or []:", "    for o in []:"),
    ("status: another source with another value does not count", "chain_research/web/validation.py",
     "    if corroboration.get(\"outcome\") == \"disagree\":", "    if False:"),
    ("status: a single source is verified", "chain_research/web/validation.py",
     "    if corroboration.get(\"outcome\") != \"agree\":", "    if False:"),
    ("the confidence is still written by the model", "chain_research/web/chain.py",
     "        if not self.value or not self.exams:\n            return text", "        if True:\n            return text"),
    ("the exams never start", "chain_research/web/collector.py",
     "            if self.o.get(\"model_checks\", True):", "            if False:"),
    ("the exams start even without budget", "chain_research/web/chain.py",
     "        if self.r.thinker.ledger.available(\"review\") < self.MIN_BUDGET_FOR_EXAMS:", "        if False:"),
    ("the review uses the chain's model", "chain_research/web/chain.py",
     "lambda: {}, model=model)", "lambda: {})"),
    ("document: uncertain answers reach the writer", "chain_research/document.py",
     "    resolved = [d for d in with_text if status_of(d) != \"uncertain\"]", "    resolved = list(with_text)"),
    ("document: no open-points box", "chain_research/document.py",
     "</ul></aside>\") if articles else \"\"", "</ul></aside>\") if False else \"\""),
    ("document: an old unconfirmed chain counts as supported", "chain_research/document.py",
     "    return \"uncertain\" if d.get(\"confirmed\") is False else \"supported\"", "    return \"supported\""),
    ("director: gets the model's confidence instead of the status", "chain_research/investigation.py",
     "                \"status\": status_of(d) or outcome,", "                \"status\": row(answer, \"Confidence\"),"),
    ("investigation: the question's status is not saved", "chain_research/investigation.py",
     "answer_status=outcome.get(\"status\"),", "answer_status=None,"),
    ("G6 the review does not see the other articles of the links", "chain_research/web/chain.py",
     "        rows = self._route_rows() + self._rows_of_other_articles(route)",
     "        rows = self._route_rows()"),
    ("bench: an invented answer to an unanswerable question counts as right", "bench/bench.py",
     "article[\"correct\"], article[\"decided_by\"] = not article.get(\"found\"), \"unanswerable\"",
     "article[\"correct\"], article[\"decided_by\"] = True, \"unanswerable\""),
    ("investigation: the source rows are not saved", "chain_research/investigation.py",
     "proofs=(outcome.get(\"chain\") or {}).get(\"proofs\") or [],", "proofs=[],"),
    ("premise refuted even without the words in the row", "chain_research/web/validation.py",
     "        if row is not None and present_quote(str(said.get(\"quote\") or \"\"), row.text):", "        if True:"),
    ("premise refuted but the value is given anyway", "chain_research/web/chain.py",
     "        if premise:", "        if False:"),
    ("chain rejected but the value is given anyway", "chain_research/web/chain.py",
     "        if objection:\n            weak_ones = self._weak_links()", "        if False:\n            weak_ones = self._weak_links()"),
    ("chain rejected even with a premise that holds", "chain_research/web/validation.py",
     " or (review.get(\"premise\") or {}).get(\"outcome\") != \"unproven\":", ":"),
    ("chain rejected even without weak links", "chain_research/web/validation.py",
     "    if not weak_links or not objections or", "    if not objections or"),
    ("document: sections are not reread sentence by sentence", "chain_research/document.py",
     "            return check_section(investigation, s, input_data, written)", "            return written"),
    ("document: a number the answers do not give passes", "chain_research/document.py",
     "            if missing:\n                found_items[sentence]", "            if False:\n                found_items[sentence]"),
    ("document: sentences still without support stay", "chain_research/document.py",
     "        from_remove = [f for f, (degree, _m) in anchor.items() if degree == \"unsupported\"]", "        from_remove = []"),
    ("document: sentences flagged by the rereader do not count", "chain_research/document.py",
     "            if isinstance(number, int) and 1 <= number <= len(sentences):", "            if False:"),
    ("a single review instead of two", "chain_research/web/chain.py",
     "        readings = max(1, int(self.o.get(\"review_readings\", 2)))", "        readings = 1"),
    ("of two reviews the more lenient wins", "chain_research/web/validation.py",
     "    worst = max(done, key=lambda r: _SEVERITY.get(r.get(\"holds\"), 1))",
     "    worst = min(done, key=lambda r: _SEVERITY.get(r.get(\"holds\"), 1))"),
    ("the starting point's name is not looked for in the pages read", "chain_research/web/chain.py",
     "        if missing:", "        if False:"),
    ("the starting point's name counts even if it is not in the question", "chain_research/web/validation.py",
     "    if not name or name not in \" \".join(str(question or \"\").split()):", "    if not name:"),
    ("the question is searched only once even if uncertain", "chain_research/question.py",
     "retry.with_retry(create, request.get(\"options\") or {}, publish)", "create(request.get(\"options\") or {}).run()"),
    ("second research also for answers that are not uncertain", "chain_research/web/retry.py",
     " or outcome.get(\"status\") != \"uncertain\" or not outcome.get(\"value\"):", " or not outcome.get(\"value\"):"),
    ("second research with another value: the answer stays as it was", "chain_research/web/retry.py",
     "            merged = _with_status(main, \"contested\", reasons)", "            merged = _with_status(main, main[\"status\"], reasons)"),
    ("second research without looking at the remaining budget", "chain_research/web/retry.py",
     "    if budget < MIN_BUDGET_USD or minutes < MIN_MINUTES:", "    if False:"),
    ("two identical researches but the answer stays uncertain", "chain_research/web/retry.py",
     "status = main[\"status\"] if ORDER.get(main[\"status\"], 0) >= ORDER[\"supported\"] else \"supported\"",
     "status = main[\"status\"]"),
    ("judge: the sign does not count", "bench/judge.py",
     "    return all((n in _negatives(wait)) == (n in under)", "    return True or all((n in _negatives(wait)) == (n in under)"),
    ("judge: an error counts as a wrong answer", "bench/judge.py",
     "return (None, \"judge error\") if errors == 2 else", "return"),
    ("bench: always extracted with the model", "bench/judge.py",
     "    return value or extract(", "    return extract("),
    ("duration including the session-limit waits", "chain_research/web/collector.py",
     "\"seconds\": round(self._elapsed())", "\"seconds\": round(time.monotonic() - self._start)"),
]


def main() -> int:
    unnoticed = []
    for name, file, old, new in SABOTAGES:
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "project"
            for part in ("chain_research", "test", "bench"):
                shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(ROOT / "pytest.ini", copy / "pytest.ini")
            target = copy / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"? {name}: the line to change is gone (or not unique): update bench/sabotage.py")
                unnoticed.append(name)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            outcome = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TEST],
                                   cwd=copy, capture_output=True, text=True)
            caught = outcome.returncode == 1
            print(f"{'✓' if caught else '✗'} {name}" + ("" if caught else "  ← no test notices"), flush=True)
            if not caught:
                unnoticed.append(name)
    print(f"{len(SABOTAGES) - len(unnoticed)}/{len(SABOTAGES)} sabotages caught by the tests")
    return 1 if unnoticed else 0


if __name__ == "__main__":
    sys.exit(main())
