"""Bench judge without Claude: normalized comparison and short answer."""
from judge import assess, normalize, short_answer, normalized_equal


def test_normalized_comparison() -> None:
    assert normalize("Città  d'Aosta!") == "citta d aosta "
    assert normalized_equal("24 July 1973", "**24 July 1973** [6].")
    assert not normalized_equal("1973", "19731")
    assert assess("d", "Mitchell Baker", "Mitchell Baker [2]") == (True, "comparison")
    assert assess("d", "x", "") == (False, "empty")


def test_short_answer() -> None:
    assert short_answer("intro\n**Answer:** 21 June 1976 [16].\n\n**Confidence:** high") == "21 June 1976 [16]."
    assert short_answer("**Risposta:** 21 giugno 1976 [16].") == "21 giugno 1976 [16]."   # runs saved before
    assert short_answer("\n\njust text\nother") == "just text"


# --- review of 2026-11-11: the bench yardstick --------------------------------------------------------------------

import pytest  # noqa: E402

import judge  # noqa: E402
from chain_research.model import claude_json  # noqa: E402


@pytest.mark.parametrize("wait,date", [("2 m", "−2 m a.s.l."), ("2 m", "-2 m a.s.l. (2 metres below sea level)"),
                                         ("-5 °C", "5 °C")])
def test_sign_counts(wait: str, date: str) -> None:
    assert not normalized_equal(wait, date)


@pytest.mark.parametrize("wait,date", [("00118–00199", "00118–00199"), ("1973-07-24", "1973-07-24 [2]"),
                                         ("−2 m", "-2 m a.s.l."), ("312 m", "312 m a.s.l."),
                                         ("21 June 1868", "21 June 1868 - founding")])
def test_dashes_that_are_not_a_sign(wait: str, date: str) -> None:
    assert normalized_equal(wait, date)


def test_program_value_comes_before_extraction(monkeypatch) -> None:
    monkeypatch.setattr(judge, "extract", lambda *_to: pytest.fail("needless extraction"))
    assert judge.final_of({"value": "00118–00199", "answer": "**Answer:** 00118–00199 [18]."}, "d") == "00118–00199"


def test_without_value_extracts_from_answer(monkeypatch) -> None:
    monkeypatch.setattr(judge, "extract", lambda _d, text: f"extracted from {text}")
    assert judge.final_of({"value": None, "answer": "**Answer:** Not found."}, "d") == "extracted from Not found."


def test_session_limit_is_not_a_wrong_answer(monkeypatch) -> None:
    def limit(*_to, **_k):
        raise claude_json.SessionLimit(60)
    monkeypatch.setattr(claude_json, "ask", limit)
    with pytest.raises(claude_json.SessionLimit):
        assess("d", "Mitchell Baker", "Brendan Eich")


def test_judge_error_does_not_decide(monkeypatch) -> None:
    def error(*_to, **_k):
        raise claude_json.ClaudeError("broken")
    monkeypatch.setattr(claude_json, "ask", error)
    assert assess("d", "Mitchell Baker", "Brendan Eich") == (None, "judge error")
    with pytest.raises(claude_json.ClaudeError):
        judge.extract("d", "text")


def test_rejudge_without_claude() -> None:
    import rejudge
    outcomes = {
        "a": {"expected": "2 m", "value": "−2 m a.s.l.", "final": "−2 m a.s.l.", "correct": True, "decided_by": "comparison"},
        "b": {"expected": "00118–00199", "value": "00118–00199", "final": "", "correct": False, "decided_by": "empty"},
        "c": {"expected": "68 m", "value": "56 m a.s.l.", "final": "56 m a.s.l.", "correct": False, "decided_by": "judge"},
        "d": {"expected": "27 January 1861", "value": None, "found": False, "final": "", "correct": False, "decided_by": "empty"},
        "e": {"error": "boh"},
    }
    new_items = rejudge.rejudge(outcomes)
    assert new_items["a"]["decided_by"] == "pending judgement" and "correct" not in new_items["a"]
    assert (new_items["b"]["correct"], new_items["b"]["decided_by"]) == (True, "comparison")
    assert (new_items["c"]["correct"], new_items["c"]["decided_by"]) == (False, "judge")
    assert (new_items["d"]["correct"], new_items["d"]["decided_by"]) == (False, "empty")
    assert new_items["e"] == {"error": "boh"}


def test_bad_reference_is_out_of_the_count() -> None:
    import stats
    outcomes = [{"correct": True}, {"correct": False, "reference_status": "contested"}, {"correct": False}]
    assert stats.summarize(outcomes).startswith("1/2 ")


def _outcome(correct: bool, status: str, value: str = "v", **checks) -> dict:
    return {"correct": correct, "status": status, "value": value, "chain": {"checks": checks}}


def test_report_signals_and_decision_rule() -> None:
    import report
    good = {"second_reading": {"outcome": "agree"}, "corroboration": {"outcome": "agree"},
             "review": {"outcome": "done", "holds": "yes", "objections": []}}
    objection = {**good, "review": {"outcome": "done", "holds": "no", "objections": [{"clue": "x"}]}}
    outcomes = ([_outcome(True, "verified", exams=good)] * 19 + [_outcome(False, "verified", exams=good)]
             + [_outcome(False, "uncertain", exams=objection, weak_links=1)] * 3 + [_outcome(True, "uncertain", weak_links=1)]
             + [_outcome(False, "contested", exams={**good, "corroboration": {"outcome": "disagree"}})]
             + [{"correct": False, "status": "not found", "value": None, "chain": {"checks": {}}}]
             + [_outcome(True, "verified", exams=good) | {"reference_status": "contested"}])
    assert report.per_status(outcomes) == {"verified": (19, 20), "supported": (0, 0), "contested": (0, 1), "uncertain": (1, 4)}
    table = report.signals_table(outcomes)
    assert table["review: objection with row"] == (0, 3) and table["weak link"] == (1, 3)
    assert table["other sources: disagree"] == (0, 1) and table["other sources: agree"] == (19, 4)
    assert table["exams not done"] == (1, 0) and table["review: holds yes"] == (19, 2)
    first_one, second = report.rule(outcomes)
    assert not first_one[1] and "19/20" in first_one[2]            # 95% but the lower bound is below 85%
    assert second[1] and "19/20 vs 1/5" in second[2]
    assert report.rule([_outcome(True, "verified", exams=good)] * 40)[0][1]
    a = {"x": _outcome(True, "verified", "1 m"), "y": _outcome(True, "uncertain", "2 m")}
    b = {"x": _outcome(True, "verified", "1  M"), "y": _outcome(False, "supported", "3 m")}
    assert report.repetitions([a, b]) == "2 questions in all runs: same value 1, same status 1"
    c = {"x": _outcome(True, "verified", "1 metro (s.l.m.)"), "y": _outcome(True, "uncertain", "2 m s.l.m.")}
    assert report.repetitions([a, c]) == "2 questions in all runs: same value 2, same status 2"


def test_unanswerable_question_is_right_only_if_not_found(monkeypatch) -> None:
    """T4: for a question that has no answer, "not found" is right; any value is invented."""
    import bench
    monkeypatch.setattr(judge, "extract", lambda *_to: pytest.fail("no model needed"))
    monkeypatch.setattr(judge, "assess", lambda *_to: pytest.fail("no judge needed"))
    honest = {"question": "d", "expected": "", "without_answer": True, "found": False, "value": None, "answer": "Not found"}
    invented = {"question": "d", "expected": "", "without_answer": True, "found": True, "value": "12 March 1850", "answer": "x"}
    bench.assess_article(honest)
    bench.assess_article(invented)
    assert (honest["correct"], honest["decided_by"]) == (True, "unanswerable")
    assert (invented["correct"], invented["final"]) == (False, "12 March 1850")


def test_unanswerable_questions_are_well_formed() -> None:
    import json
    from pathlib import Path
    questions = json.loads((Path(__file__).resolve().parents[1] / "bench" / "questions_unanswerable.json").read_text())["questions"]
    assert len(questions) == 20 and len({d["seed"] for d in questions}) == 20
    assert all(d["without_answer"] is True and d["answer"] == "" and d["why"] and d["question"].endswith("?") for d in questions)
    assert {d["theme"] for d in questions} == {"invented_entity", "nonexistent_relation", "fact_never_recorded"}


def test_faithfulness_proofs_per_source() -> None:
    """Second mode of faithfulness.py: the sentence is compared with the rows of the sources it cites, not the answers."""
    import faithfulness
    questions = [
        {"id": "d1", "question": "D1", "answer": "**Answer:** 1973 [2].",
         "sources": [{"number": 1, "url": "https://a.org/x", "title": "A"}, {"number": 2, "url": "https://b.org/y", "title": "B"}],
         "proofs": [{"url": "https://b.org/y", "row": "Nato il 24 luglio 1973"}, {"url": "https://a.org/x", "row": "Fondatore di X"},
                   {"url": "https://never.seen/z", "row": "row of a source not in the list"}]},
        {"id": "d2", "question": "D2", "answer": "**Answer:** Roma [1].",
         "sources": [{"number": 1, "url": "https://b.org/y/", "title": "B"}], "proofs": [{"url": "https://b.org/y", "row": "Vive a Roma"}]},
        {"id": "d3", "question": "D3", "answer": "**Answer:** old [1].", "sources": [{"number": 1, "url": "https://c.org", "title": "C"}]},
    ]
    per_source = faithfulness.texts_per_source(questions, "sources")
    assert per_source == {1: ["Fondatore di X"], 2: ["Nato il 24 luglio 1973", "Vive a Roma"]}   # same address, same source
    per_answer = faithfulness.texts_per_source(questions, "answers")
    assert set(per_answer) == {1, 2, 3} and "QUESTION: D1" in per_answer[2][0] and "QUESTION: D2" in per_answer[2][1]


def test_corroboration_of_expected_answers() -> None:
    import check_references as vr
    assert vr.corroboration("68 m", {"Reggio (it)": ["Altitudine: 58 m s.l.m."], "Reggio (en)": ["Elevation: 58 m (190 ft)"]}) == "different"
    assert vr.corroboration("21 June 1885", {"H (en)": ["Died: 21 June 1886"], "H (it)": ["morta il 21 giugno 1885"]}) == "equal"
    assert vr.corroboration("68 m", {"X (it)": [], "X (en)": []}) == "no rows" and vr.corroboration("68 m", {}) == "no article"

    class V:
        start = "Reggio nell'Emilia è un comune italiano."

        def infobox(self):
            from chain_research.web.sources.wikipedia_graph import Row
            return [Row("Altitudine: 58 m s.l.m.", [], "infobox"), Row("Superficie: 230 km²", [], "infobox"),
                    Row("Nome abitanti: reggiani (altitudine media)", [], "infobox")]
    assert vr.datum_rows(V(), "elevation") == ["Altitudine: 58 m s.l.m."]
    assert vr.datum_rows(V(), "founding date") == [V.start]


def test_question_timeout_covers_session_limit_wait() -> None:
    import bench
    assert bench.MAX_WAIT_S >= 2 * 6 * 3600   # two session-limit resets (6 hours at most each)


def test_rejudge_recomputes_status_with_current_rule() -> None:
    import rejudge
    good = {"second_reading": {"outcome": "agree"}, "corroboration": {"outcome": "agree"},
             "review": {"outcome": "done", "holds": "yes", "objections": []}}
    article = {"expected": "56 m", "value": "56 m", "final": "56 m", "correct": True, "decided_by": "comparison", "status": "uncertain",
            "chain": {"checks": {"value": "56 m", "weak_links": 1, "value_source": "A", "disagreements": [], "exams": good}}}
    fresh = rejudge.rejudge({"a": article, "b": {"expected": "x", "value": "x", "status": "v", "chain": {}}})
    assert (fresh["a"]["status"], fresh["a"]["run_status"], fresh["a"]["confirmed"]) == ("supported", "uncertain", True)
    assert fresh["b"]["status"] == "v" and article["status"] == "uncertain"   # older runs: nothing to recompute
    two = {**article, "status": "contested", "retry": {"outcome": "other value", "value": "58 m"}}
    assert rejudge.rejudge({"a": two})["a"]["status"] == "contested"   # decided by two researches: stays
    invented = {"expected": "", "without_answer": True, "found": True, "value": "1850", "correct": False, "decided_by": "unanswerable"}
    honest = {"expected": "", "without_answer": True, "found": False, "value": None, "correct": True, "decided_by": "unanswerable"}
    outcome = rejudge.rejudge({"i": invented, "o": honest})
    assert (outcome["i"]["correct"], outcome["o"]["correct"]) == (False, True)


def test_confirmation_set_is_well_formed() -> None:
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "bench"
    questions = json.loads((root / "questions_confirmation.json").read_text())["questions"]
    conn, without = [d for d in questions if not d.get("without_answer")], [d for d in questions if d.get("without_answer")]
    assert (len(conn), len(without)) == (13, 10) and len({d["seed"] for d in questions}) == 23
    assert all(d["answer"] and d["answer_source"] and d["question"].endswith("?") for d in conn)
    assert all(d["answer"] == "" and d["why"] for d in without)
    # the answer and the final entity are not in the question; no seed already used in the other sets
    assert all(d["answer"] not in d["question"] and d["path"][-1]["name"] not in d["question"] for d in conn)
    used = {d["seed"] for f in ("questions.json", "questions_holdout.json", "questions_unanswerable.json")
             for d in json.loads((root / f).read_text())["questions"]}
    assert not used & {d["seed"] for d in questions}
