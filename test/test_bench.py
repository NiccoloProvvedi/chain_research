"""Giudice del banco senza Claude: confronto normalizzato e risposta breve."""
from judge import assess, normalize, short_answer, normalized_equal


def test_normalized_comparison() -> None:
    assert normalize("Città  d'Aosta!") == "citta d aosta "
    assert normalized_equal("24 luglio 1973", "**24 luglio 1973** [6].")
    assert not normalized_equal("1973", "19731")
    assert assess("d", "Mitchell Baker", "Mitchell Baker [2]") == (True, "comparison")
    assert assess("d", "x", "") == (False, "empty")


def test_short_answer() -> None:
    assert short_answer("intro\n**Risposta:** 21 giugno 1976 [16].\n\n**Fiducia:** alta") == "21 giugno 1976 [16]."
    assert short_answer("\n\nsolo testo\naltro") == "solo testo"


# --- revisione dell'11/11/2026: il metro del banco ----------------------------------------------------------------

import pytest  # noqa: E402

import judge  # noqa: E402
from chain_research.model import claude_json  # noqa: E402


@pytest.mark.parametrize("wait,date", [("2 m", "−2 m s.l.m."), ("2 m", "-2 m s.l.m. (2 metri sotto il livello del mare)"),
                                         ("-5 °C", "5 °C")])
def test_mark_count(wait: str, date: str) -> None:
    assert not normalized_equal(wait, date)


@pytest.mark.parametrize("wait,date", [("00118–00199", "00118–00199"), ("1973-07-24", "1973-07-24 [2]"),
                                         ("−2 m", "-2 m s.l.m."), ("312 m", "312 m s.l.m."),
                                         ("21 giugno 1868", "21 giugno 1868 - fondazione")])
def test_dashes_that_not_are_mark(wait: str, date: str) -> None:
    assert normalized_equal(wait, date)


def test_value_of_program_comes_first_of_extraction(monkeypatch) -> None:
    monkeypatch.setattr(judge, "extract", lambda *_to: pytest.fail("estrazione inutile"))
    assert judge.final_of({"value": "00118–00199", "answer": "**Risposta:** 00118–00199 [18]."}, "d") == "00118–00199"


def test_without_value_extracts_from_answer(monkeypatch) -> None:
    monkeypatch.setattr(judge, "extract", lambda _d, text: f"estratto da {text}")
    assert judge.final_of({"value": None, "answer": "**Risposta:** Non trovata."}, "d") == "estratto da Non trovata."


def test_limit_of_session_not_and_one_wrong_answer(monkeypatch) -> None:
    def limit(*_to, **_k):
        raise claude_json.SessionLimit(60)
    monkeypatch.setattr(claude_json, "ask", limit)
    with pytest.raises(claude_json.SessionLimit):
        assess("d", "Mitchell Baker", "Brendan Eich")


def test_judge_in_error_not_decides(monkeypatch) -> None:
    def error(*_to, **_k):
        raise claude_json.ClaudeError("rotto")
    monkeypatch.setattr(claude_json, "ask", error)
    assert assess("d", "Mitchell Baker", "Brendan Eich") == (None, "judge error")
    with pytest.raises(claude_json.ClaudeError):
        judge.extract("d", "text")


def new_test_judgement_without_claude() -> None:
    import rejudge
    outcomes = {
        "a": {"wait": "2 m", "value": "−2 m s.l.m.", "final": "−2 m s.l.m.", "correct": True, "decided_by": "comparison"},
        "b": {"wait": "00118–00199", "value": "00118–00199", "final": "", "correct": False, "decided_by": "empty"},
        "c": {"wait": "68 m", "value": "56 m s.l.m.", "final": "56 m s.l.m.", "correct": False, "decided_by": "judge"},
        "d": {"wait": "27 gennaio 1861", "value": None, "found": False, "final": "", "correct": False, "decided_by": "empty"},
        "e": {"error": "boh"},
    }
    new_items = rejudge.rejudge(outcomes)
    assert new_items["a"]["decided_by"] == "pending judgement" and "correct" not in new_items["a"]
    assert (new_items["b"]["correct"], new_items["b"]["decided_by"]) == (True, "comparison")
    assert (new_items["c"]["correct"], new_items["c"]["decided_by"]) == (False, "judge")
    assert (new_items["d"]["correct"], new_items["d"]["decided_by"]) == (False, "empty")
    assert new_items["e"] == {"error": "boh"}


def test_reference_not_ok_outside_from_count() -> None:
    import stats
    outcomes = [{"correct": True}, {"correct": False, "reference_status": "contested"}, {"correct": False}]
    assert stats.summarize(outcomes).startswith("1/2 ")


def _outcome(correct: bool, status: str, value: str = "v", **checks) -> dict:
    return {"correct": correct, "status": status, "value": value, "chain": {"checks": checks}}


def test_report_signals_and_rule() -> None:
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
    assert table["revisione: obiezione con riga"] == (0, 3) and table["anello debole"] == (1, 3)
    assert table["altre fonti: discordi"] == (0, 1) and table["altre fonti: concordi"] == (19, 4)
    assert table["esami non fatti"] == (1, 0) and table["revisione: regge sì"] == (19, 2)
    first_one, second = report.rule(outcomes)
    assert not first_one[1] and "19/20" in first_one[2]            # 95% ma il limite inferiore è sotto l'85%
    assert second[1] and "19/20 contro 1/5" in second[2]
    assert report.rule([_outcome(True, "verified", exams=good)] * 40)[0][1]
    a = {"x": _outcome(True, "verified", "1 m"), "y": _outcome(True, "uncertain", "2 m")}
    b = {"x": _outcome(True, "verified", "1  M"), "y": _outcome(False, "supported", "3 m")}
    assert report.repetitions([a, b]) == "2 domande in tutte le corse: stesso valore 1, stesso stato 1"
    c = {"x": _outcome(True, "verified", "1 metro (s.l.m.)"), "y": _outcome(True, "uncertain", "2 m s.l.m.")}
    assert report.repetitions([a, c]) == "2 domande in tutte le corse: stesso valore 2, stesso stato 2"


def test_question_without_right_answer_only_if_not_found(monkeypatch) -> None:
    """T4: per una domanda che non ha risposta, giusto è dire «non trovata»; un valore qualunque è inventato."""
    import bench
    monkeypatch.setattr(judge, "extract", lambda *_to: pytest.fail("non serve il modello"))
    monkeypatch.setattr(judge, "assess", lambda *_to: pytest.fail("non serve il giudice"))
    honest = {"question": "d", "wait": "", "without_answer": True, "found": False, "value": None, "answer": "Non trovata"}
    invented = {"question": "d", "wait": "", "without_answer": True, "found": True, "value": "12 marzo 1850", "answer": "x"}
    bench.assess_article(honest)
    bench.assess_article(invented)
    assert (honest["correct"], honest["decided_by"]) == (True, "unanswerable")
    assert (invented["correct"], invented["final"]) == (False, "12 marzo 1850")


def test_questions_without_answer_are_ben_done() -> None:
    import json
    from pathlib import Path
    questions = json.loads((Path(__file__).resolve().parents[1] / "bench" / "domande_senza_risposta.json").read_text())["questions"]
    assert len(questions) == 20 and len({d["seed"] for d in questions}) == 20
    assert all(d["without_answer"] is True and d["answer"] == "" and d["why"] and d["question"].endswith("?") for d in questions)
    assert {d["theme"] for d in questions} == {"invented_entity", "nonexistent_relation", "fact_never_recorded"}


def test_faithfulness_proofs_per_source() -> None:
    """Secondo modo di fedelta.py: la frase si confronta con le righe delle fonti che cita, non con le risposte."""
    import faithfulness
    questions = [
        {"id": "d1", "question": "D1", "answer": "**Risposta:** 1973 [2].",
         "sources": [{"number": 1, "url": "https://a.org/x", "title": "A"}, {"number": 2, "url": "https://b.org/y", "title": "B"}],
         "proofs": [{"url": "https://b.org/y", "row": "Nato il 24 luglio 1973"}, {"url": "https://a.org/x", "row": "Fondatore di X"},
                   {"url": "https://mai.vista/z", "row": "riga di una fonte non in elenco"}]},
        {"id": "d2", "question": "D2", "answer": "**Risposta:** Roma [1].",
         "sources": [{"number": 1, "url": "https://b.org/y/", "title": "B"}], "proofs": [{"url": "https://b.org/y", "row": "Vive a Roma"}]},
        {"id": "d3", "question": "D3", "answer": "**Risposta:** vecchia [1].", "sources": [{"number": 1, "url": "https://c.org", "title": "C"}]},
    ]
    per_source = faithfulness.texts_per_source(questions, "sources")
    assert per_source == {1: ["Fondatore di X"], 2: ["Nato il 24 luglio 1973", "Vive a Roma"]}   # stesso indirizzo, stessa fonte
    per_answer = faithfulness.texts_per_source(questions, "answers")
    assert set(per_answer) == {1, 2, 3} and "DOMANDA: D1" in per_answer[2][0] and "DOMANDA: D2" in per_answer[2][1]


def test_corroboration_of_answers_waits() -> None:
    import check_references as vr
    assert vr.corroboration("68 m", {"Reggio (it)": ["Altitudine: 58 m s.l.m."], "Reggio (en)": ["Elevation: 58 m (190 ft)"]}) == "diversa"
    assert vr.corroboration("21 giugno 1885", {"H (en)": ["Died: 21 June 1886"], "H (it)": ["morta il 21 giugno 1885"]}) == "uguale"
    assert vr.corroboration("68 m", {"X (it)": [], "X (en)": []}) == "senza righe" and vr.corroboration("68 m", {}) == "senza voce"

    class V:
        start = "Reggio nell'Emilia è un comune italiano."

        def infobox(self):
            from chain_research.web.sources.wikipedia_graph import Row
            return [Row("Altitudine: 58 m s.l.m.", [], "riquadro"), Row("Superficie: 230 km²", [], "riquadro"),
                    Row("Nome abitanti: reggiani (altitudine media)", [], "riquadro")]
    assert vr.datum_rows(V(), "altitudine") == ["Altitudine: 58 m s.l.m."]
    assert vr.datum_rows(V(), "data di fondazione") == [V.start]


def test_time_of_one_question_covers_l_wait_of_limit() -> None:
    import bench
    assert bench.MAX_WAIT_S >= 2 * 6 * 3600   # due rinnovi del limite di sessione (6 ore al massimo ciascuno)


def test_new_judgement_recomputes_status_with_rule_of_now() -> None:
    import rejudge
    good = {"second_reading": {"outcome": "agree"}, "corroboration": {"outcome": "agree"},
             "review": {"outcome": "done", "holds": "yes", "objections": []}}
    article = {"wait": "56 m", "value": "56 m", "final": "56 m", "correct": True, "decided_by": "comparison", "status": "uncertain",
            "chain": {"checks": {"value": "56 m", "weak_links": 1, "value_source": "A", "disagreements": [], "exams": good}}}
    fresh = rejudge.rejudge({"a": article, "b": {"wait": "x", "value": "x", "status": "v", "chain": {}}})
    assert (fresh["a"]["status"], fresh["a"]["run_status"], fresh["a"]["confirmed"]) == ("supported", "uncertain", True)
    assert fresh["b"]["status"] == "v" and article["status"] == "uncertain"   # corse di prima: niente da ricalcolare
    two = {**article, "status": "contested", "retry": {"outcome": "other value", "value": "58 m"}}
    assert rejudge.rejudge({"a": two})["a"]["status"] == "contested"   # deciso da due ricerche: resta
    invented = {"wait": "", "without_answer": True, "found": True, "value": "1850", "correct": False, "decided_by": "unanswerable"}
    honest = {"wait": "", "without_answer": True, "found": False, "value": None, "correct": True, "decided_by": "unanswerable"}
    outcome = rejudge.rejudge({"i": invented, "o": honest})
    assert (outcome["i"]["correct"], outcome["o"]["correct"]) == (False, True)


def test_l_set_of_confirmation_and_ben_fact() -> None:
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "bench"
    questions = json.loads((root / "domande_conferma.json").read_text())["questions"]
    conn, without = [d for d in questions if not d.get("without_answer")], [d for d in questions if d.get("without_answer")]
    assert (len(conn), len(without)) == (13, 10) and len({d["seed"] for d in questions}) == 23
    assert all(d["answer"] and d["answer_source"] and d["question"].endswith("?") for d in conn)
    assert all(d["answer"] == "" and d["why"] for d in without)
    # la risposta e l'entità finale non stanno nella domanda; nessun seme già usato negli altri insiemi
    assert all(d["answer"] not in d["question"] and d["path"][-1]["name"] not in d["question"] for d in conn)
    used = {d["seed"] for f in ("questions.json", "questions_holdout.json", "domande_senza_risposta.json")
             for d in json.loads((root / f).read_text())["questions"]}
    assert not used & {d["seed"] for d in questions}
