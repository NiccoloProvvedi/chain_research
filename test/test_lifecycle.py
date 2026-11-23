"""Life cycle of an investigation with real processes (fake question, fake `claude`): stop, resume, die, limits."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from chain_research import ROOT, document, investigation as module
from chain_research.investigation import Investigation, acquire_lock
from test_server import environment  # noqa: F401 - fixture

FAKE = Path(__file__).parent / "fixtures" / "fake_question.py"
SOLID = {"sections": [{"id": "s1", "coverage": 3, "note": ""}], "solid_knowledge": True, "reason": "enough",
          "new_questions": [], "new_sections": []}
PLAN = {"title": "Title", "goal": "Goal", "sections": [{"id": "s1", "title": "One", "purpose": "x"}]}


@pytest.fixture
def ind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Investigation:
    Path(os.environ["CHAIN_SETTINGS"]).write_text(json.dumps({"profiles_folder": str(tmp_path / "profiles")}))
    monkeypatch.setattr(Investigation, "_question_command", staticmethod(lambda: [sys.executable, str(FAKE)]))
    monkeypatch.setattr(module.Director, "ask", lambda self, point, *_to, **_k: SOLID)
    monkeypatch.setattr(document, "write", lambda _i: {"pdf": "x"})
    i = Investigation.create("test topic", base_folder=tmp_path / "investigations")
    i.status.update(plan=PLAN, round_no=1)
    return i


def _questions(i: Investigation, *texts: str, status: str = "waiting") -> None:
    i.status["questions"] = [{"id": f"d{n}", "round": 1, "section": "s1", "question": t, "reason": "", "status": status}
                          for n, t in enumerate(texts, 1)]


def _wait(condition, seconds: float = 15) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "condition not reached"
        time.sleep(0.02)


def test_question_finished_with_model_and_confirmation(ind: Investigation) -> None:
    _questions(ind, "normal outcome")
    ind.run()
    d = ind.status["questions"][0]
    assert d["status"] == "answer" and d["confirmed"] and d["models"] == ["claude-fake-1"] and ind.spent() == 0.02
    assert (d["answer_status"], d["status_reasons"]) == ("supported", ["the value comes from a single source"])
    assert d["proofs"] == [{"url": "https://example.org/a", "row": "Born in 1973"}]


def test_dead_question_counts_cost(ind: Investigation) -> None:
    _questions(ind, "this one dies")
    ind.run()
    d = ind.status["questions"][0]
    assert d["status"] == "error" and "process closed" in d["error"] and ind.spent() == pytest.approx(0.12)


def test_resume_redoes_failed_questions(ind: Investigation) -> None:
    _questions(ind, "normal outcome", status="error")
    ind.status["questions"][0]["error"] = "old"
    ind.run()
    assert ind.status["questions"][0]["status"] == "answer" and "error" not in ind.status["questions"][0]


def test_session_limit_stops_without_not_found(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    written = []
    monkeypatch.setattr(document, "write", lambda _i: written.append(1))
    _questions(ind, "limit here")
    ind.run()
    assert ind.status["status"] == "stopped" and "session limit" in ind.status["end_reason"] and not written
    assert ind.status["questions"][0]["status"] == "waiting" and ind.spent() == pytest.approx(0.03)


def test_limit_during_director_call(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    def limit(*_to, **_k):
        raise module.claude_json.SessionLimit(600)
    monkeypatch.setattr(module.Director, "ask", limit)
    ind.status["plan"] = None
    ind.run()
    assert ind.status["status"] == "stopped" and "Resume" in ind.status["end_reason"]


def test_stop_closes_slow_question(ind: Investigation) -> None:
    _questions(ind, "this one is slow")
    t = threading.Thread(target=ind.run)
    t.start()
    _wait(lambda: ind._processes and ind.spent() == 0 and ind.status["questions"][0]["status"] == "in_progress")
    time.sleep(0.5)  # the question has written its cost
    ind.stop()
    t.join(15)
    assert not t.is_alive() and ind.status["status"] == "stopped" and ind.status["questions"][0]["status"] == "waiting"
    assert ind.spent() == pytest.approx(0.05)


def test_stop_interrupts_director_call(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "claude").write_text("#!/bin/sh\nexec sleep 60\n")
    (folder / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    ind.status["plan"] = None
    t = threading.Thread(target=ind.run)
    t.start()
    _wait(lambda: module.claude_json._in_progress)
    start = time.monotonic()
    ind.stop()
    t.join(10)
    assert not t.is_alive() and time.monotonic() - start < 5 and ind.status["status"] == "stopped"


def test_guard_closes_stuck_question(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "GUARD_MARGIN_S", 0)
    monkeypatch.setattr(Investigation, "_question_minutes", lambda _s: 0.01)
    _questions(ind, "this one is slow")
    ind.run()
    assert ind.status["questions"][0]["status"] == "error" and "maximum time" in ind.status["questions"][0]["error"]


def test_thread_error_does_not_leave_question_in_progress(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_to):
        raise KeyError("broken")
    monkeypatch.setattr(Investigation, "_one_question", broken)
    _questions(ind, "normal outcome")
    ind.run()
    assert ind.status["questions"][0]["status"] == "error" and "KeyError" in ind.status["questions"][0]["error"]


def test_question_stderr_goes_to_log(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Investigation, "_question_command",
                        staticmethod(lambda: [sys.executable, "-c", "import sys; sys.stderr.write('error-trace')"]))
    _questions(ind, "normal outcome")
    ind.run()
    assert "error-trace" in (ind.folder / "errors.log").read_text()


# --- new questions ----------------------------------------------------------------------------------------------

def _evaluation(coverages: dict, questions: list[str] | None = None, solid: bool = False) -> dict:
    return {"sections": [{"id": k, "coverage": v, "note": ""} for k, v in coverages.items()], "solid_knowledge": solid,
            "reason": "m", "new_sections": [],
            "new_questions": [{"section": s, "question": f"Question number {i} about section {s}?", "reason": ""}
                              for i, s in enumerate(questions or [])]}


def test_least_covered_sections_first_and_only_as_many_as_fit(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ind, "_how_many_more", lambda: 2)
    choices = ind._new_choices(_evaluation({"s1": 3, "s2": 0, "s3": 1}, ["s1", "s3", "s2"]))
    assert [d["section"] for d in choices] == ["s2", "s3"]


def test_how_many_more_from_budget(ind: Investigation) -> None:
    ind.status["limits"].update(budget_usd=1.0, max_minutes=1440, without_limits=False)
    ind.status["questions"] = [{"cost_usd": 0.10, "seconds": 60} for _ in range(5)]
    ind.status["director_spent_usd"] = 0.0  # spent 0.50; reserve 0.18 (sonnet, 1 section); 0.32 left → 3 questions
    assert ind._how_many_more() == 3
    ind.status["limits"]["without_limits"] = True
    assert ind._how_many_more() is None


def test_coverage_stop_closes_cycle(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([_evaluation({"s1": 1}, ["s1"]), _evaluation({"s1": 1}, ["s1", "s1"])])
    monkeypatch.setattr(module.Director, "ask", lambda *_to, **_k: next(answers))
    assert ind._evaluate() is False
    assert ind._evaluate() is True and ind.status["end_reason"].startswith("coverage no longer grows")


def test_settings_fixed_at_creation(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    Path(os.environ["CHAIN_SETTINGS"]).write_text(json.dumps({"question_minutes": 90}))
    ind.status["limits"]["without_limits"] = True
    assert ind._question_minutes() == 35


# --- document ---------------------------------------------------------------------------------------------------

def _for_document(ind: Investigation) -> None:
    ind.status["plan"] = {**PLAN, "sections": [{"id": "s1", "title": "One", "purpose": "x"},
                                               {"id": "s2", "title": "Two", "purpose": "y"}]}
    ind.status["questions"] = [
        {"id": f"d{n}", "round": 1, "section": s, "question": f"Question {n}", "status": "answer", "confirmed": True,
         "answer": "**Answer:** fact [1].\n\n**Confidence:** high", "cost_usd": 0.1,
         "sources": [{"number": 1, "url": f"https://example.org/{n}", "title": f"F{n}"},
                   {"number": 2, "url": f"https://other.org/{n}", "title": f"G{n}"}]}
        for n, s in ((1, "s1"), (2, "s2"))]


def test_document_does_not_redo_paid_sections(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _for_document(ind)
    calls: list[str] = []
    fault = {"active": True}

    def ask(self, point, obj, schema, **_k):
        calls.append(obj.get("section", {}).get("id", point))
        if point == "document_section":
            if obj["section"]["id"] == "s2" and fault["active"]:
                raise module.claude_json.ClaudeError("fault")
            return "Text of the section [1]."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "A summary long enough to pass the fifty-character check [1].",
                "key_points": ["Point [1]."], "conclusions": "End [1].", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    with pytest.raises(module.claude_json.ClaudeError):
        document.write(ind)
    assert sorted(calls) == ["document_check", "s1", "s2"]   # s1 written and reread, s2 failed
    calls.clear()
    fault["active"] = False
    outcome = document.write(ind)
    assert calls == ["s2", "document_check", "document_frame"] and outcome["sections"] == 2
    calls.clear()
    document.write(ind)
    assert calls == []  # nothing changed, nothing is rewritten
    page = Path(outcome["html"]).read_text()
    # answers from before the model checks: "supported", never "verified"
    assert "2 answered (2 supported)" in page and ">verified<" not in page
    assert "Cited sources" in page and "Other sources consulted" in page and "</p></li>" not in page
    assert 'value="3"' in page


def _answer(n: int, section: str, value: str, **other) -> dict:
    return {"id": f"d{n}", "round": 1, "section": section, "question": f"Question {n}", "status": "answer", "cost_usd": 0.1,
            "value": value, "answer": f"**Answer:** {value} [1].\n\n**Confidence:** high — all good.",
            "sources": [{"number": 1, "url": f"https://example.org/{n}", "title": f"F{n}"}], **other}


def test_document_follows_status_of_answers(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T10 (docs/validation.md): an "uncertain" answer never reaches the writer of the sections nor the summary, it
    stays in a box written by the program; a "contested" one arrives with its reasons; the cover counts the statuses."""
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _for_document(ind)
    ind.status["questions"] = [
        _answer(1, "s1", "VERIFIED-YEAR", answer_status="verified", status_reasons=[], confirmed=True),
        _answer(2, "s1", "UNCERTAIN-NAME", answer_status="uncertain", confirmed=False,
                  status_reasons=["1 chain links not confirmed"]),
        _answer(3, "s2", "CONTESTED-DATE", answer_status="contested", confirmed=False,
                  status_reasons=["another source gives 21 June 1885"]),
        _answer(4, "s2", "OLD-WEAK", confirmed=False),          # older investigation: chain not confirmed
        {"id": "d5", "round": 1, "section": "s2", "question": "Question 5", "status": "not_found", "sources": [],
         "answer": "**Answer:** Not found: chain broken."},
    ]
    received: list[tuple[str, dict]] = []

    def ask(self, point, obj, schema, **_k):
        received.append((point, obj))
        if point == "document_section":
            return "Section: " + " | ".join(r["answer"].splitlines()[0] for r in obj["answers"])
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "Summary from the sections: " + " ".join(x["text"] for x in obj["sections"]),
                "key_points": ["Point [1]."], "conclusions": "End.", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()

    to_model = json.dumps([o for _p, o in received], ensure_ascii=False)
    assert "UNCERTAIN-NAME" not in to_model and "OLD-WEAK" not in to_model  # not even in the short facts
    s1 = next(o for p, o in received if p == "document_section" and o["section"]["id"] == "s1")
    s2 = next(o for p, o in received if p == "document_section" and o["section"]["id"] == "s2")
    assert [r["status"] for r in s1["answers"]] == ["verified"]
    contested = next(r for r in s2["answers"] if r["status"] == "contested")
    assert contested["checks_limits"] == ["another source gives 21 June 1885"]
    assert any(r["status"] == "not found" for r in s2["answers"])

    def piece(start: str, end: str) -> str:
        return page[page.index(start):page.index(end, page.index(start))]
    synthesis, one, two = piece('<section id="summary"', "</section>"), piece('<section id="s1"', "</section>"), piece('<section id="s2"', "</section>")
    assert "VERIFIED-YEAR" in synthesis and "UNCERTAIN-NAME" not in synthesis and "OLD-WEAK" not in synthesis
    pending = one[one.index('class="open-points"'):]
    assert "Open points" in pending and "UNCERTAIN-NAME" in pending and "1 chain links not confirmed" in pending
    assert 'href="#source-' in pending and "UNCERTAIN-NAME" not in one[:one.index('class="open-points"')]
    assert "OLD-WEAK" in two[two.index('class="open-points"'):] and "CONTESTED-DATE" in two[:two.index('class="open-points"')]
    assert "4 answered (1 verified, 1 contested, 2 uncertain)" in page
    table = piece('<section id="questions"', "</section>")
    assert all(x in table for x in ("<th>Status</th>", ">verified<", ">contested<", ">uncertain<")) and "all good" not in table


def test_section_with_only_uncertain_answers(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _for_document(ind)
    ind.status["questions"] = [_answer(1, "s1", "A", answer_status="supported"),
                            _answer(2, "s2", "B-UNCERTAIN", answer_status="uncertain", status_reasons=["m"])]
    written_sections: list[str] = []

    def ask(self, point, obj, schema, **_k):
        if point == "document_section":
            written_sections.append(obj["section"]["id"])
            return "Text."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "A summary long enough to pass the fifty-character check.",
                "key_points": ["Point."], "conclusions": "End.", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert written_sections == ["s1"] and "No confirmed answer" in page and "B-UNCERTAIN" in page


def test_director_receives_status(ind: Investigation) -> None:
    short = Investigation.in_short(_answer(1, "s1", "X", answer_status="contested", status_reasons=["another source gives Y"]))
    assert short["status"] == "contested" and short["checks_limits"] == ["another source gives Y"]
    assert "all good" not in json.dumps(short, ensure_ascii=False)
    old = Investigation.in_short(_answer(2, "s1", "X", confirmed=False))
    assert old["status"] == "uncertain"
    assert Investigation.in_short({"id": "d3", "section": "s1", "question": "Q", "status": "not_found", "answer": ""})["status"] == "not found"


# --- check of the section sentences (14 Nov: 15 sentences out of 120 with a detail added or changed) -------------

def test_numbers_without_support() -> None:
    support = "**Answer:** 2005 [1]. Sold 1 234 pieces; price 20,65 euro; born on 24 July 1973."
    assert document.numbers_without_support("Until 1964 the presidency was held by Schiavo [3].", support) == ["1964"]
    assert document.numbers_without_support("Born in 2005 [1, 12], sold 1234 pieces at 20.65 euro [33].", support) == []
    assert document.numbers_without_support("The 3 offices and the 7 members [45].", support) == []          # single digits: not counted
    assert document.numbers_without_support("Born on 24 July 1973 and died in 2011 [2].", support) == ["2011"]


def test_markdown_sentences() -> None:
    text = "### Title\n\nFirst sentence [1]. Second sentence of 1999 [2].\n\n- item one [3];\n- item two.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert document.markdown_sentences(text) == ["First sentence [1].", "Second sentence of 1999 [2].", "item one [3];", "item two."]
    assert document.remove_sentences(text, ["Second sentence of 1999 [2].", "item one [3];"]) == (
        "### Title\n\nFirst sentence [1].\n\n- item two.\n\n| a | b |\n|---|---|\n| 1 | 2 |")


def _investigation_for_check(ind: Investigation) -> None:
    _for_document(ind)
    ind.status["plan"]["sections"] = [{"id": "s1", "title": "One", "purpose": "x"}]
    ind.status["questions"] = [_answer(1, "s1", "born in 2005", answer_status="verified", status_reasons=[])]


def test_section_checked_sentence_by_sentence(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A sentence with a number the answers do not have and one flagged by the rereader: a rewrite is asked once;
    what stays unsupported is removed by the program."""
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_for_check(ind)
    first = "The project was born in 2005 [1]. In 1999 it sold 3000 pieces [1]. It was loved by everyone [1]."
    after = "The project was born in 2005 [1]. In 1999 it sold 3000 pieces [1]. It was a success [1]."
    calls: list[tuple[str, dict]] = []

    def ask(self, point, obj, schema, **_k):
        calls.append((point, obj))
        if point == "document_section":
            return after if "sentences_to_fix" in obj else first
        if point == "document_check":
            number = next((f["number"] for f in obj["sentences"] if "loved by everyone" in f["sentence"]), None)
            return {"sentences": [{"number": number, "outcome": "unsupported", "reason": "a judgement the answers do not give"}]
                    if number else [{"number": 99, "outcome": "unsupported", "reason": "number that does not exist"}]}
        return {"synthesis": "Summary: " + " ".join(x["text"] for x in obj["sections"]) + " " * 50,
                "key_points": ["Point [1]."], "conclusions": "End.", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert [p for p, _ in calls] == ["document_section", "document_check", "document_section",
                                        "document_check", "document_frame"]
    from_correct = calls[2][1]["sentences_to_fix"]
    assert [x["sentence"] for x in from_correct] == ["In 1999 it sold 3000 pieces [1].", "It was loved by everyone [1]."]
    assert "1999" in from_correct[0]["reason"] and calls[2][1]["previous_text"] == first
    assert "born in 2005" in page and "It was a success" in page
    assert "sold 3000 pieces" not in page and "loved by everyone" not in page     # not even in the summary
    assert ind.status["sections_check"]["s1"] == {"sentences": 3, "flagged": 2, "rewritten": True, "removed": 1}
    assert "1 removed by the program" in page
    calls.clear()
    document.write(ind)
    assert calls == []        # section checked and already paid for: not redone


def test_section_without_flags_not_rewritten(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_for_check(ind)
    points: list[str] = []

    def ask(self, point, obj, schema, **_k):
        points.append(point)
        if point == "document_section":
            return "The project was born in 2005 [1]."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "A summary long enough to pass the fifty-character check [1].",
                "key_points": ["Point [1]."], "conclusions": "End.", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    document.write(ind)
    assert points == ["document_section", "document_check", "document_frame"]
    assert ind.status["sections_check"]["s1"] == {"sentences": 1, "flagged": 0, "rewritten": False, "removed": 0}


def test_program_check_remains_if_rereader_fails(ind: Investigation, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_for_check(ind)

    def ask(self, point, obj, schema, **_k):
        if point == "document_section":
            return "The project was born in 2005 [1]. In 1999 it sold 3000 pieces [1]."
        if point == "document_check":
            raise module.claude_json.ClaudeError("fault")
        return {"synthesis": "Summary: " + " ".join(x["text"] for x in obj["sections"]) + " " * 50,
                "key_points": ["Point [1]."], "conclusions": "End.", "limits": "None."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert "born in 2005" in page and "sold 3000 pieces" not in page
    assert ind.status["sections_check"]["s1"]["removed"] == 1


# --- processes and server -----------------------------------------------------------------------------------------

def test_lock_excludes_second_process(tmp_path: Path) -> None:
    first_one = acquire_lock(tmp_path)
    assert first_one is not None and acquire_lock(tmp_path) is None
    first_one.close()
    assert acquire_lock(tmp_path) is not None


def test_process_surviving_previous_server(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("test topic", base_folder=handler.folder)
    ind.status["status"] = "in_progress"
    ind.save()
    ident = ind.folder.name
    assert client.get(f"/api/investigations/{ident}").json()["status"] == "interrupted"
    code = ("import sys, time; from pathlib import Path; from chain_research.investigation import acquire_lock; "
              "b = acquire_lock(Path(sys.argv[1])); print('ready', flush=True); time.sleep(60)")
    alive = subprocess.Popen([sys.executable, "-c", code, str(ind.folder)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert alive.stdout.readline().strip() == "ready"
        assert client.get(f"/api/investigations/{ident}").json()["status"] == "in_progress" and handler.active()
        assert client.post(f"/api/investigations/{ident}/resume", json={}).status_code == 400
        assert client.post(f"/api/investigations/{ident}/stop", json={}).status_code == 200
        alive.wait(10)
    finally:
        alive.kill()
        alive.wait()
    assert client.get(f"/api/investigations/{ident}").json()["status"] == "interrupted" and not handler.active()


def test_stream_closed_by_server_and_wrong_id(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("test topic", base_folder=handler.folder)
    ind.event({"kind": "end", "status": "stopped"})
    ind.event({"kind": "start"})
    ind.event({"kind": "end", "status": "finished"})
    with client.stream("GET", f"/api/investigations/{ind.folder.name}/events", headers={"last-event-id": "abc"}) as stream:
        rows = list(stream.iter_lines())
    assert sum(r.startswith("data: {\"when") for r in rows) == 3 and rows.count("event: closed") == 1


def test_resume_with_new_limits(environment) -> None:  # noqa: F811
    client, handler, started, _ = environment
    ind = Investigation.create("test topic", base_folder=handler.folder)
    ident = ind.folder.name
    assert client.post(f"/api/investigations/{ident}/resume", json={"budget_usd": "9", "max_minutes": 300}).status_code == 200
    limits = client.get(f"/api/investigations/{ident}").json()["limits"]
    assert limits["budget_usd"] == 9 and limits["max_minutes"] == 300 and started == [(ident, False)]
    assert client.post(f"/api/investigations/{ident}/resume", json={"budget_usd": 0}).status_code == 400


def test_clean_frees_space(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("test topic", base_folder=handler.folder)
    (ind.folder / "pieces").mkdir()
    assert client.post(f"/api/investigations/{ind.folder.name}/clean", json={}).status_code == 200
    assert not (ind.folder / "pieces").exists() and (ind.folder / "status.json").exists()


def test_advanced_settings(environment) -> None:  # noqa: F811
    client, *_ = environment
    r = client.post("/api/settings", json={"chain_model": "sonnet", "question_budget_usd": "0.8", "question_pages": 12,
                                               "question_minutes": 20}).json()
    assert (r["chain_model"], r["question_budget_usd"], r["question_pages"], r["question_minutes"]) == ("sonnet", 0.8, 12, 20)
    for wrong in ({"chain_model": "gpt"}, {"question_pages": 0}, {"question_minutes": 999}):
        assert client.post("/api/settings", json=wrong).status_code == 400
