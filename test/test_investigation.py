"""Indagine e documento senza Claude né rete: regia finta, catena finta, PDF vero con il Chromium del progetto."""
import json
from pathlib import Path

import pytest

from chain_research import document, investigation
from chain_research.investigation import Investigation

PLAN = {"title": "Arduino: storia e marchio", "goal": "Capire chi ha creato Arduino e come è finita.",
         "sections": [{"id": "s1", "title": "Origini", "purpose": "chi e quando"},
                     {"id": "s2", "title": "Il marchio", "purpose": "la disputa"}],
         "questions": [{"section": "s1", "question": "In che anno è nato il progetto Arduino a Ivrea?", "reason": "m"},
                     {"section": "s1", "question": "Chi ha guidato il progetto Arduino all'Interaction Design "
                                                  "Institute Ivrea?", "reason": "m"},
                     {"section": "s2", "question": "In che anno Arduino LLC e Arduino Srl hanno firmato l'accordo "
                                                  "sul marchio?", "reason": "m"},
                     {"section": "s2", "question": "In che anno è nato il progetto Arduino a Ivrea?", "reason": "doppia"}]}
ANSWERS = {
    "In che anno è nato il progetto Arduino a Ivrea?": ("2005", [("https://it.wikipedia.org/wiki/Arduino", "Arduino")]),
    "Chi ha guidato il progetto Arduino all'Interaction Design Institute Ivrea?":
        ("Massimo Banzi", [("https://example.org/banzi", "Banzi"), ("https://it.wikipedia.org/wiki/Arduino/", "Arduino")]),
    "In che anno Arduino LLC e Arduino Srl hanno firmato l'accordo sul marchio?": None,
    "Quale società ha acquisito Arduino AG nel 2017?": ("BCMI", [("https://example.org/bcmi", "BCMI")]),
}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CHAIN_SETTINGS", str(tmp_path / "settings.json"))
    calls: list[tuple[str, dict]] = []
    evaluations = iter([
        {"sections": [{"id": "s1", "coverage": 3, "note": ""}, {"id": "s2", "coverage": 1, "note": "manca"}],
         "solid_knowledge": False, "reason": "manca il marchio",
         "new_questions": [{"section": "s2", "question": "Quale società ha acquisito Arduino AG nel 2017?",
                            "reason": "proprietà del marchio"}], "new_sections": []},
        {"sections": [{"id": "s1", "coverage": 3, "note": ""}, {"id": "s2", "coverage": 3, "note": ""}],
         "solid_knowledge": True, "reason": "basta", "new_questions": [], "new_sections": []}])

    def ask(self, point, obj, schema, cap_usd=2.0, wait=900, rules=None):
        calls.append((point, obj))
        self.to_cost(0.01)
        if point == "plan_director":
            return PLAN
        if point == "director_evaluate":
            return next(evaluations)
        if point == "document_section":
            facts = " ".join(r["answer"].splitlines()[0] for r in obj["answers"])
            return f"## {obj['section']['title']}\n\nTesto della sezione: {facts}"
        return {"synthesis": "Arduino nasce nel 2005 [1] e il marchio passa a BCMI [3]. " * 3,
                "key_points": ["Nato nel 2005 [1].", "Guidato da Massimo Banzi [2]."],
                "conclusions": "Conclusione [1].", "limits": "L'accordo non è stato trovato."}

    def one_question(self, d, profile, parallel):
        found = ANSWERS[d["question"]]
        with self._mutex:
            if found is None:
                d.update(status="not_found", answer="**Risposta:** Non trovata: catena incompleta.", sources=[],
                         cost_usd=0.3, seconds=600)
            else:
                value, sources = found
                d.update(status="answer", value=value, cost_usd=0.1, seconds=300,
                         answer=f"**Risposta:** {value} [{len(sources)}].\n\n**Fiducia:** alta, fonte diretta.",
                         sources=[{"number": i + 1, "url": u, "title": t} for i, (u, t) in enumerate(sources)])
        self.save()

    monkeypatch.setattr(investigation.Director, "ask", ask)
    monkeypatch.setattr(Investigation, "_one_question", one_question)
    return calls


def test_complete_investigation_until_to_pdf(fake, tmp_path: Path) -> None:
    events: list[dict] = []
    ind = Investigation.create("Storia di Arduino e del suo marchio", director_model="sonnet", budget_usd=5,
                        max_minutes=120, base_folder=tmp_path)
    ind.publish = events.append
    ind.run()
    status = json.loads((ind.folder / "status.json").read_text())
    # la domanda doppia non entra; un secondo giro con la domanda nuova; poi la regia dice basta
    assert [d["question"] for d in status["questions"]].count("In che anno è nato il progetto Arduino a Ivrea?") == 1
    assert len(status["questions"]) == 4 and status["round"] == 3 and status["status"] == "finished"
    assert status["end_reason"].startswith("Claude ritiene la conoscenza solida")
    assert [p for p, _ in fake].count("director_evaluate") == 2 and [p for p, _ in fake].count("document_section") == 2
    page = Path(status["document"]["html"]).read_text()
    # fonti uniche per tutto il documento (stesso indirizzo con la barra finale = stessa fonte) e citazioni collegate
    assert status["document"]["sources"] == 3 and 'id="fonte-3"' in page and 'href="#fonte-1"' in page
    assert "Appendice A" in page and "not found" in page and "Massimo Banzi" in page
    assert Path(status["document"]["pdf"]).read_bytes()[:5] == b"%PDF-"
    assert [e["kind"] for e in events][-1] == "end" and any(e["kind"] == "evaluation" for e in events)
    # la regia riceve le risposte ripulite e in breve
    evaluated = next(o for p, o in fake if p == "director_evaluate")["questions"]
    assert {"id", "section", "question", "outcome", "answer", "status", "checks_limits", "sources"} <= set(evaluated[0])


def test_renumber_and_global_sources() -> None:
    questions = [{"id": "d1", "sources": [{"number": 1, "url": "https://a.org/x", "title": "A"},
                                      {"number": 2, "url": "https://b.org", "title": "B"}]},
               {"id": "d2", "sources": [{"number": 1, "url": "https://www.b.org/", "title": "B"}]}]
    sources, mappings = document.global_sources(questions)
    assert [f["url"] for f in sources] == ["https://a.org/x", "https://b.org"] and mappings["d2"] == {1: 2}
    assert document.renumber("Fatto [1, 2] e altro [1]; ignoto [9].", mappings["d2"]) == "Fatto [2] e altro [2]; ignoto ."


def test_limits_of_budget_and_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_SETTINGS", str(tmp_path / "settings.json"))
    ind = Investigation.create("argomento di prova", budget_usd=1.0, max_minutes=120, director_model="haiku",
                        base_folder=tmp_path)
    ind.status["plan"] = {"sections": [{"id": "s1"}] * 4}
    assert ind.can_start(0) == ""                     # 0,20 + riserva 0,18 ≤ 1 $
    ind.status["director_spent_usd"] = 0.4
    ind.status["director_spent_usd"] = 0.5
    assert "budget" in ind.can_start(1)               # 0,5 + 2×0,20 + 0,18 > 1 $
    ind.status["limits"]["without_limits"] = True
    assert ind.can_start(5) == ""
    ind.stop()
    assert ind.can_start(0) == "indagine fermata"


def test_topic_and_checked_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic"):
        Investigation.create("  ", base_folder=tmp_path)
    with pytest.raises(ValueError, match="Modello"):
        Investigation.create("argomento valido", director_model="gpt", base_folder=tmp_path)
