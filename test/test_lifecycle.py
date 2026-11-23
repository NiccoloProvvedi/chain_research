"""Ciclo di vita di un'indagine con processi veri (domanda finta, `claude` finto): fermare, riprendere, morire, limiti."""
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
SOLID = {"sections": [{"id": "s1", "coverage": 3, "note": ""}], "solid_knowledge": True, "reason": "basta",
          "new_questions": [], "new_sections": []}
PLAN = {"title": "Titolo", "goal": "Obiettivo", "sections": [{"id": "s1", "title": "Uno", "purpose": "x"}]}


@pytest.fixture
def ind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Investigation:
    Path(os.environ["CHAIN_SETTINGS"]).write_text(json.dumps({"profiles_folder": str(tmp_path / "profili")}))
    monkeypatch.setattr(Investigation, "_question_command", staticmethod(lambda: [sys.executable, str(FAKE)]))
    monkeypatch.setattr(module.Director, "ask", lambda self, point, *_to, **_k: SOLID)
    monkeypatch.setattr(document, "write", lambda _i: {"pdf": "x"})
    i = Investigation.create("argomento di prova", base_folder=tmp_path / "indagini")
    i.status.update(plan=PLAN, round_no=1)
    return i


def _questions(i: Investigation, *texts: str, status: str = "waiting") -> None:
    i.status["questions"] = [{"id": f"d{n}", "round": 1, "section": "s1", "question": t, "reason": "", "status": status}
                          for n, t in enumerate(texts, 1)]


def _wait(condition, seconds: float = 15) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "condizione non raggiunta"
        time.sleep(0.02)


def test_question_finished_with_model_and_confirmation(ind: Investigation) -> None:
    _questions(ind, "esito normale")
    ind.run()
    d = ind.status["questions"][0]
    assert d["status"] == "answer" and d["confirmed"] and d["models"] == ["claude-finto-1"] and ind.spent() == 0.02
    assert (d["answer_status"], d["status_reasons"]) == ("supported", ["il valore viene da una sola fonte"])
    assert d["proofs"] == [{"url": "https://esempio.org/a", "row": "Nato nel 1973"}]


def test_dead_question_count_cost(ind: Investigation) -> None:
    _questions(ind, "questa muore")
    ind.run()
    d = ind.status["questions"][0]
    assert d["status"] == "error" and "processo chiuso" in d["error"] and ind.spent() == pytest.approx(0.12)


def test_resume_redoes_questions_in_error(ind: Investigation) -> None:
    _questions(ind, "esito normale", status="error")
    ind.status["questions"][0]["error"] = "vecchio"
    ind.run()
    assert ind.status["questions"][0]["status"] == "answer" and "error" not in ind.status["questions"][0]


def test_limit_of_session_stop_without_say_not_found(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    written = []
    monkeypatch.setattr(document, "write", lambda _i: written.append(1))
    _questions(ind, "qui il limite")
    ind.run()
    assert ind.status["status"] == "stopped" and "Limite di sessione" in ind.status["end_reason"] and not written
    assert ind.status["questions"][0]["status"] == "waiting" and ind.spent() == pytest.approx(0.03)


def test_limit_during_director(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    def limit(*_to, **_k):
        raise module.claude_json.SessionLimit(600)
    monkeypatch.setattr(module.Director, "ask", limit)
    ind.status["plan"] = None
    ind.run()
    assert ind.status["status"] == "stopped" and "Riprendi" in ind.status["end_reason"]


def test_stop_closes_slow_question(ind: Investigation) -> None:
    _questions(ind, "questa è lenta")
    t = threading.Thread(target=ind.run)
    t.start()
    _wait(lambda: ind._processes and ind.spent() == 0 and ind.status["questions"][0]["status"] == "in_progress")
    time.sleep(0.5)  # la domanda ha scritto il suo costo
    ind.stop()
    t.join(15)
    assert not t.is_alive() and ind.status["status"] == "stopped" and ind.status["questions"][0]["status"] == "waiting"
    assert ind.spent() == pytest.approx(0.05)


def test_stop_interrupts_call_of_director(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_guard_closes_question_blocked(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "GUARD_MARGIN_S", 0)
    monkeypatch.setattr(Investigation, "_question_minutes", lambda _s: 0.01)
    _questions(ind, "questa è lenta")
    ind.run()
    assert ind.status["questions"][0]["status"] == "error" and "tempo massimo" in ind.status["questions"][0]["error"]


def test_error_in_thread_not_leaves_question_in_progress(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_to):
        raise KeyError("rotta")
    monkeypatch.setattr(Investigation, "_one_question", broken)
    _questions(ind, "esito normale")
    ind.run()
    assert ind.status["questions"][0]["status"] == "error" and "KeyError" in ind.status["questions"][0]["error"]


def test_stderr_of_question_ends_in_log(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Investigation, "_question_command",
                        staticmethod(lambda: [sys.executable, "-c", "import sys; sys.stderr.write('traccia-errore')"]))
    _questions(ind, "esito normale")
    ind.run()
    assert "traccia-errore" in (ind.folder / "errors.log").read_text()


# --- nuove domande ----------------------------------------------------------------------------------------------

def _evaluation(coverages: dict, questions: list[str] | None = None, solid: bool = False) -> dict:
    return {"sections": [{"id": k, "coverage": v, "note": ""} for k, v in coverages.items()], "solid_knowledge": solid,
            "reason": "m", "new_sections": [],
            "new_questions": [{"section": s, "question": f"Domanda numero {i} sulla sezione {s}?", "reason": ""}
                              for i, s in enumerate(questions or [])]}


def test_first_sections_covered_less_and_only_how_many_end(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ind, "_how_many_more", lambda: 2)
    choices = ind._new_choices(_evaluation({"s1": 3, "s2": 0, "s3": 1}, ["s1", "s3", "s2"]))
    assert [d["section"] for d in choices] == ["s2", "s3"]


def test_how_many_anchor_from_budget(ind: Investigation) -> None:
    ind.status["limits"].update(budget_usd=1.0, max_minutes=1440, without_limits=False)
    ind.status["questions"] = [{"cost_usd": 0.10, "seconds": 60} for _ in range(5)]
    ind.status["director_spent_usd"] = 0.0  # speso 0,50; riserva 0,18 (sonnet, 1 sezione); restano 0,32 → 3 domande
    assert ind._how_many_more() == 3
    ind.status["limits"]["without_limits"] = True
    assert ind._how_many_more() is None


def test_coverage_stop_closes_cycle(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([_evaluation({"s1": 1}, ["s1"]), _evaluation({"s1": 1}, ["s1", "s1"])])
    monkeypatch.setattr(module.Director, "ask", lambda *_to, **_k: next(answers))
    assert ind._evaluate() is False
    assert ind._evaluate() is True and ind.status["end_reason"].startswith("la copertura non cresce")


def test_settings_fixed_to_creation(ind: Investigation, monkeypatch: pytest.MonkeyPatch) -> None:
    Path(os.environ["CHAIN_SETTINGS"]).write_text(json.dumps({"question_minutes": 90}))
    ind.status["limits"]["without_limits"] = True
    assert ind._question_minutes() == 35


# --- documento --------------------------------------------------------------------------------------------------

def _per_document(ind: Investigation) -> None:
    ind.status["plan"] = {**PLAN, "sections": [{"id": "s1", "title": "Uno", "purpose": "x"},
                                               {"id": "s2", "title": "Due", "purpose": "y"}]}
    ind.status["questions"] = [
        {"id": f"d{n}", "round": 1, "section": s, "question": f"Domanda {n}", "status": "answer", "confirmed": True,
         "answer": "**Risposta:** dato [1].\n\n**Fiducia:** alta", "cost_usd": 0.1,
         "sources": [{"number": 1, "url": f"https://esempio.org/{n}", "title": f"F{n}"},
                   {"number": 2, "url": f"https://altro.org/{n}", "title": f"G{n}"}]}
        for n, s in ((1, "s1"), (2, "s2"))]


def test_document_not_redoes_sections_already_paid(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _per_document(ind)
    calls: list[str] = []
    fault = {"active": True}

    def ask(self, point, obj, schema, **_k):
        calls.append(obj.get("section", {}).get("id", point))
        if point == "document_section":
            if obj["section"]["id"] == "s2" and fault["active"]:
                raise module.claude_json.ClaudeError("guasto")
            return "Testo della sezione [1]."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri [1].",
                "key_points": ["Punto [1]."], "conclusions": "Fine [1].", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    with pytest.raises(module.claude_json.ClaudeError):
        document.write(ind)
    assert sorted(calls) == ["document_check", "s1", "s2"]   # s1 scritta e riletta, s2 fallita
    calls.clear()
    fault["active"] = False
    outcome = document.write(ind)
    assert calls == ["s2", "document_check", "document_frame"] and outcome["sections"] == 2
    calls.clear()
    document.write(ind)
    assert calls == []  # niente è cambiato, niente si riscrive
    page = Path(outcome["html"]).read_text()
    # risposte di prima dei controlli con il modello: «sostenute», mai «verificate»
    assert "2 con risposta (2 sostenute)" in page and ">verificata<" not in page
    assert "Fonti citate" in page and "Altre fonti consultate" in page and "</p></li>" not in page
    assert 'value="3"' in page


def _answer(n: int, section: str, value: str, **other) -> dict:
    return {"id": f"d{n}", "round": 1, "section": section, "question": f"Domanda {n}", "status": "answer", "cost_usd": 0.1,
            "value": value, "answer": f"**Risposta:** {value} [1].\n\n**Fiducia:** alta — tutto bene.",
            "sources": [{"number": 1, "url": f"https://esempio.org/{n}", "title": f"F{n}"}], **other}


def test_document_follows_status_of_answers(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T10 (docs/validation.md): una risposta «incerta» non arriva mai a chi scrive le sezioni né alla
    sintesi, sta in un riquadro scritto dal programma; una «contestata» arriva con i motivi; la copertina conta gli stati."""
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _per_document(ind)
    ind.status["questions"] = [
        _answer(1, "s1", "ANNO-VERIFICATO", answer_status="verified", status_reasons=[], confirmed=True),
        _answer(2, "s1", "NOME-INCERTO", answer_status="uncertain", confirmed=False,
                  status_reasons=["1 anelli della catena non confermati"]),
        _answer(3, "s2", "DATA-CONTESTATA", answer_status="contested", confirmed=False,
                  status_reasons=["un'altra fonte dà 21 giugno 1885"]),
        _answer(4, "s2", "VECCHIA-DEBOLE", confirmed=False),          # indagine di prima: catena non confermata
        {"id": "d5", "round": 1, "section": "s2", "question": "Domanda 5", "status": "not_found", "sources": [],
         "answer": "**Risposta:** Non trovata: catena interrotta."},
    ]
    received: list[tuple[str, dict]] = []

    def ask(self, point, obj, schema, **_k):
        received.append((point, obj))
        if point == "document_section":
            return "Sezione: " + " | ".join(r["answer"].splitlines()[0] for r in obj["answers"])
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "Sintesi dalle sezioni: " + " ".join(x["text"] for x in obj["sections"]),
                "key_points": ["Punto [1]."], "conclusions": "Fine.", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()

    to_model = json.dumps([o for _p, o in received], ensure_ascii=False)
    assert "NOME-INCERTO" not in to_model and "VECCHIA-DEBOLE" not in to_model  # nemmeno nei fatti brevi
    s1 = next(o for p, o in received if p == "document_section" and o["section"]["id"] == "s1")
    s2 = next(o for p, o in received if p == "document_section" and o["section"]["id"] == "s2")
    assert [r["status"] for r in s1["answers"]] == ["verified"]
    contested = next(r for r in s2["answers"] if r["status"] == "contested")
    assert contested["checks_limits"] == ["un'altra fonte dà 21 giugno 1885"]
    assert any(r["status"] == "not found" for r in s2["answers"])

    def piece(start: str, end: str) -> str:
        return page[page.index(start):page.index(end, page.index(start))]
    synthesis, one, two = piece('<section id="sintesi"', "</section>"), piece('<section id="s1"', "</section>"), piece('<section id="s2"', "</section>")
    assert "ANNO-VERIFICATO" in synthesis and "NOME-INCERTO" not in synthesis and "VECCHIA-DEBOLE" not in synthesis
    pending = one[one.index('class="aperti"'):]
    assert "Punti aperti" in pending and "NOME-INCERTO" in pending and "1 anelli della catena non confermati" in pending
    assert 'href="#fonte-' in pending and "NOME-INCERTO" not in one[:one.index('class="aperti"')]
    assert "VECCHIA-DEBOLE" in two[two.index('class="aperti"'):] and "DATA-CONTESTATA" in two[:two.index('class="aperti"')]
    assert "4 con risposta (1 verificata, 1 contestata, 2 incerte)" in page
    table = piece('<section id="domande"', "</section>")
    assert all(x in table for x in ("<th>Stato</th>", ">verificata<", ">contestata<", ">incerta<")) and "tutto bene" not in table


def test_section_with_sun_uncertain_answers(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _per_document(ind)
    ind.status["questions"] = [_answer(1, "s1", "A", answer_status="supported"),
                            _answer(2, "s2", "B-INCERTO", answer_status="uncertain", status_reasons=["m"])]
    written_sections: list[str] = []

    def ask(self, point, obj, schema, **_k):
        if point == "document_section":
            written_sections.append(obj["section"]["id"])
            return "Testo."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri.",
                "key_points": ["Punto."], "conclusions": "Fine.", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert written_sections == ["s1"] and "Nessuna risposta confermata" in page and "B-INCERTO" in page


def test_director_receives_status(ind: Investigation) -> None:
    short = Investigation.in_short(_answer(1, "s1", "X", answer_status="contested", status_reasons=["un'altra fonte dà Y"]))
    assert short["status"] == "contested" and short["checks_limits"] == ["un'altra fonte dà Y"]
    assert "tutto bene" not in json.dumps(short, ensure_ascii=False)
    old = Investigation.in_short(_answer(2, "s1", "X", confirmed=False))
    assert old["status"] == "uncertain"
    assert Investigation.in_short({"id": "d3", "section": "s1", "question": "D", "status": "not_found", "answer": ""})["status"] == "not found"


# --- controllo delle frasi delle sezioni (14/11: 15 frasi su 120 con un dettaglio aggiunto o cambiato) ------------

def test_numbers_without_support() -> None:
    support = "**Risposta:** 2005 [1]. Venduti 1 234 pezzi; prezzo 20,65 euro; nato il 24 luglio 1973."
    assert document.numbers_without_support("Fino al 1964 la presidenza fu di Schiavo [3].", support) == ["1964"]
    assert document.numbers_without_support("Nato nel 2005 [1, 12], vendette 1234 pezzi a 20.65 euro [33].", support) == []
    assert document.numbers_without_support("Le 3 sedi e i 7 soci [45].", support) == []          # cifre singole: non si contano
    assert document.numbers_without_support("Nato il 24 luglio 1973 e morto nel 2011 [2].", support) == ["2011"]


def test_sentences_of_markdown() -> None:
    text = "### Titolo\n\nPrima frase [1]. Seconda frase del 1999 [2].\n\n- voce uno [3];\n- voce due.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert document.markdown_sentences(text) == ["Prima frase [1].", "Seconda frase del 1999 [2].", "voce uno [3];", "voce due."]
    assert document.remove_sentences(text, ["Seconda frase del 1999 [2].", "voce uno [3];"]) == (
        "### Titolo\n\nPrima frase [1].\n\n- voce due.\n\n| a | b |\n|---|---|\n| 1 | 2 |")


def _investigation_per_check(ind: Investigation) -> None:
    _per_document(ind)
    ind.status["plan"]["sections"] = [{"id": "s1", "title": "Uno", "purpose": "x"}]
    ind.status["questions"] = [_answer(1, "s1", "nato nel 2005", answer_status="verified", status_reasons=[])]


def test_checked_section_sentence_per_sentence(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Una frase con un numero che nelle risposte non c'è e una segnalata da chi rilegge: si chiede una volta di
    riscrivere; quello che resta senza appoggio lo toglie il programma."""
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_per_check(ind)
    first = "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]. Fu amato da tutti [1]."
    after = "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]. Ebbe successo [1]."
    calls: list[tuple[str, dict]] = []

    def ask(self, point, obj, schema, **_k):
        calls.append((point, obj))
        if point == "document_section":
            return after if "sentences_to_fix" in obj else first
        if point == "document_check":
            number = next((f["number"] for f in obj["sentences"] if "amato da tutti" in f["sentence"]), None)
            return {"sentences": [{"number": number, "outcome": "unsupported", "reason": "un giudizio che le risposte non danno"}]
                    if number else [{"number": 99, "outcome": "unsupported", "reason": "numero che non esiste"}]}
        return {"synthesis": "Sintesi: " + " ".join(x["text"] for x in obj["sections"]) + " " * 50,
                "key_points": ["Punto [1]."], "conclusions": "Fine.", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert [p for p, _ in calls] == ["document_section", "document_check", "document_section",
                                        "document_check", "document_frame"]
    from_correct = calls[2][1]["sentences_to_fix"]
    assert [x["sentence"] for x in from_correct] == ["Nel 1999 vendette 3000 pezzi [1].", "Fu amato da tutti [1]."]
    assert "1999" in from_correct[0]["reason"] and calls[2][1]["previous_text"] == first
    assert "nato nel 2005" in page and "Ebbe successo" in page
    assert "vendette 3000 pezzi" not in page and "amato da tutti" not in page     # nemmeno nella sintesi
    assert ind.status["sections_check"]["s1"] == {"sentences": 3, "flagged": 2, "rewritten": True, "removed": 1}
    assert "1 tolte dal programma" in page
    calls.clear()
    document.write(ind)
    assert calls == []        # sezione controllata e già pagata: non si rifà


def test_section_without_flags_not_rewrites(ind: Investigation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_per_check(ind)
    points: list[str] = []

    def ask(self, point, obj, schema, **_k):
        points.append(point)
        if point == "document_section":
            return "Il progetto è nato nel 2005 [1]."
        if point == "document_check":
            return {"sentences": []}
        return {"synthesis": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri [1].",
                "key_points": ["Punto [1]."], "conclusions": "Fine.", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    document.write(ind)
    assert points == ["document_section", "document_check", "document_frame"]
    assert ind.status["sections_check"]["s1"] == {"sentences": 1, "flagged": 0, "rewritten": False, "removed": 0}


def test_if_who_rereads_not_answers_stays_check_of_program(ind: Investigation, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("CHAIN_LOG", str(tmp_path / "log"))
    _investigation_per_check(ind)

    def ask(self, point, obj, schema, **_k):
        if point == "document_section":
            return "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]."
        if point == "document_check":
            raise module.claude_json.ClaudeError("guasto")
        return {"synthesis": "Sintesi: " + " ".join(x["text"] for x in obj["sections"]) + " " * 50,
                "key_points": ["Punto [1]."], "conclusions": "Fine.", "limits": "Nessuno."}
    monkeypatch.setattr(module.Director, "ask", ask)
    monkeypatch.setattr(document, "print_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    page = Path(document.write(ind)["html"]).read_text()
    assert "nato nel 2005" in page and "vendette 3000 pezzi" not in page
    assert ind.status["sections_check"]["s1"]["removed"] == 1


# --- processi e server ------------------------------------------------------------------------------------------

def test_block_excludes_second_process(tmp_path: Path) -> None:
    first_one = acquire_lock(tmp_path)
    assert first_one is not None and acquire_lock(tmp_path) is None
    first_one.close()
    assert acquire_lock(tmp_path) is not None


def test_process_surviving_to_previous_server(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    ind.status["status"] = "in_progress"
    ind.save()
    ident = ind.folder.name
    assert client.get(f"/api/indagini/{ident}").json()["status"] == "interrupted"
    code = ("import sys, time; from pathlib import Path; from chain_research.investigation import blocca; "
              "b = blocca(Path(sys.argv[1])); print('pronto', flush=True); time.sleep(60)")
    alive = subprocess.Popen([sys.executable, "-c", code, str(ind.folder)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert alive.stdout.readline().strip() == "pronto"
        assert client.get(f"/api/indagini/{ident}").json()["status"] == "in_progress" and handler.active()
        assert client.post(f"/api/indagini/{ident}/riprendi", json={}).status_code == 400
        assert client.post(f"/api/indagini/{ident}/ferma", json={}).status_code == 200
        alive.wait(10)
    finally:
        alive.kill()
        alive.wait()
    assert client.get(f"/api/indagini/{ident}").json()["status"] == "interrupted" and not handler.active()


def test_stream_closed_from_server_and_wrong_id(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    ind.event({"kind": "end", "status": "stopped"})
    ind.event({"kind": "start"})
    ind.event({"kind": "end", "status": "finished"})
    with client.stream("GET", f"/api/indagini/{ind.folder.name}/eventi", headers={"last-event-id": "abc"}) as stream:
        rows = list(stream.iter_lines())
    assert sum(r.startswith("data: {\"quando") for r in rows) == 3 and rows.count("event: chiuso") == 1


def test_resume_with_new_limits(environment) -> None:  # noqa: F811
    client, handler, started, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    ident = ind.folder.name
    assert client.post(f"/api/indagini/{ident}/riprendi", json={"budget_usd": "9", "max_minutes": 300}).status_code == 200
    limits = client.get(f"/api/indagini/{ident}").json()["limits"]
    assert limits["budget_usd"] == 9 and limits["max_minutes"] == 300 and started == [(ident, False)]
    assert client.post(f"/api/indagini/{ident}/riprendi", json={"budget_usd": 0}).status_code == 400


def test_free_space(environment) -> None:  # noqa: F811
    client, handler, _, _ = environment
    ind = Investigation.create("argomento di prova", base_folder=handler.folder)
    (ind.folder / "pieces").mkdir()
    assert client.post(f"/api/indagini/{ind.folder.name}/pulisci", json={}).status_code == 200
    assert not (ind.folder / "pieces").exists() and (ind.folder / "status.json").exists()


def test_settings_advanced(environment) -> None:  # noqa: F811
    client, *_ = environment
    r = client.post("/api/impostazioni", json={"chain_model": "sonnet", "question_budget_usd": "0.8", "question_pages": 12,
                                               "question_minutes": 20}).json()
    assert (r["chain_model"], r["question_budget_usd"], r["question_pages"], r["question_minutes"]) == ("sonnet", 0.8, 12, 20)
    for wrong in ({"chain_model": "gpt"}, {"question_pages": 0}, {"question_minutes": 999}):
        assert client.post("/api/impostazioni", json=wrong).status_code == 400
