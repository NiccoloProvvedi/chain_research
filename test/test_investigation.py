"""Investigation and document without Claude or network: fake director, fake chain, real PDF with the project's Chromium."""
import json
from pathlib import Path

import pytest

from chain_research import document, investigation
from chain_research.investigation import Investigation

PLAN = {"title": "Arduino: history and trademark", "goal": "Understand who created Arduino and how it ended.",
         "sections": [{"id": "s1", "title": "Origins", "purpose": "who and when"},
                     {"id": "s2", "title": "The trademark", "purpose": "the dispute"}],
         "questions": [{"section": "s1", "question": "In which year was the Arduino project born in Ivrea?", "reason": "m"},
                     {"section": "s1", "question": "Who led the Arduino project at the Interaction Design "
                                                  "Institute Ivrea?", "reason": "m"},
                     {"section": "s2", "question": "In which year did Arduino LLC and Arduino Srl sign the "
                                                  "trademark agreement?", "reason": "m"},
                     {"section": "s2", "question": "In which year was the Arduino project born in Ivrea?", "reason": "duplicate"}]}
ANSWERS = {
    "In which year was the Arduino project born in Ivrea?": ("2005", [("https://it.wikipedia.org/wiki/Arduino", "Arduino")]),
    "Who led the Arduino project at the Interaction Design Institute Ivrea?":
        ("Massimo Banzi", [("https://example.org/banzi", "Banzi"), ("https://it.wikipedia.org/wiki/Arduino/", "Arduino")]),
    "In which year did Arduino LLC and Arduino Srl sign the trademark agreement?": None,
    "Which company acquired Arduino AG in 2017?": ("BCMI", [("https://example.org/bcmi", "BCMI")]),
}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CHAIN_SETTINGS", str(tmp_path / "settings.json"))
    calls: list[tuple[str, dict]] = []
    evaluations = iter([
        {"sections": [{"id": "s1", "coverage": 3, "note": ""}, {"id": "s2", "coverage": 1, "note": "missing"}],
         "solid_knowledge": False, "reason": "the trademark is missing",
         "new_questions": [{"section": "s2", "question": "Which company acquired Arduino AG in 2017?",
                            "reason": "ownership of the trademark"}], "new_sections": []},
        {"sections": [{"id": "s1", "coverage": 3, "note": ""}, {"id": "s2", "coverage": 3, "note": ""}],
         "solid_knowledge": True, "reason": "enough", "new_questions": [], "new_sections": []}])

    def ask(self, point, obj, schema, cap_usd=2.0, wait=900, rules=None):
        calls.append((point, obj))
        self.to_cost(0.01)
        if point == "plan_director":
            return PLAN
        if point == "director_evaluate":
            return next(evaluations)
        if point == "document_section":
            facts = " ".join(r["answer"].splitlines()[0] for r in obj["answers"])
            return f"## {obj['section']['title']}\n\nText of the section: {facts}"
        return {"synthesis": "Arduino was born in 2005 [1] and the trademark passed to BCMI [3]. " * 3,
                "key_points": ["Born in 2005 [1].", "Led by Massimo Banzi [2]."],
                "conclusions": "Conclusion [1].", "limits": "The agreement was not found."}

    def one_question(self, d, profile, parallel):
        found = ANSWERS[d["question"]]
        with self._mutex:
            if found is None:
                d.update(status="not_found", answer="**Answer:** Not found: incomplete chain.", sources=[],
                         cost_usd=0.3, seconds=600)
            else:
                value, sources = found
                d.update(status="answer", value=value, cost_usd=0.1, seconds=300,
                         answer=f"**Answer:** {value} [{len(sources)}].\n\n**Confidence:** high, direct source.",
                         sources=[{"number": i + 1, "url": u, "title": t} for i, (u, t) in enumerate(sources)])
        self.save()

    monkeypatch.setattr(investigation.Director, "ask", ask)
    monkeypatch.setattr(Investigation, "_one_question", one_question)
    return calls


def test_complete_investigation_to_pdf(fake, tmp_path: Path) -> None:
    events: list[dict] = []
    ind = Investigation.create("History of Arduino and its trademark", director_model="sonnet", budget_usd=5,
                        max_minutes=120, base_folder=tmp_path)
    ind.publish = events.append
    ind.run()
    status = json.loads((ind.folder / "status.json").read_text())
    # the duplicate question does not enter; a second round with the new question; then the director says enough
    assert [d["question"] for d in status["questions"]].count("In which year was the Arduino project born in Ivrea?") == 1
    assert len(status["questions"]) == 4 and status["round"] == 3 and status["status"] == "finished"
    assert status["end_reason"].startswith("Claude deems the knowledge solid")
    assert [p for p, _ in fake].count("director_evaluate") == 2 and [p for p, _ in fake].count("document_section") == 2
    page = Path(status["document"]["html"]).read_text()
    # unique sources for the whole document (same address with the trailing slash = same source) and linked citations
    assert status["document"]["sources"] == 3 and 'id="source-3"' in page and 'href="#source-1"' in page
    assert "Appendix A" in page and "not found" in page and "Massimo Banzi" in page
    assert Path(status["document"]["pdf"]).read_bytes()[:5] == b"%PDF-"
    assert [e["kind"] for e in events][-1] == "end" and any(e["kind"] == "evaluation" for e in events)
    # the director receives the answers cleaned and in short
    evaluated = next(o for p, o in fake if p == "director_evaluate")["questions"]
    assert {"id", "section", "question", "outcome", "answer", "status", "checks_limits", "sources"} <= set(evaluated[0])


def test_renumber_and_global_sources() -> None:
    questions = [{"id": "d1", "sources": [{"number": 1, "url": "https://a.org/x", "title": "A"},
                                      {"number": 2, "url": "https://b.org", "title": "B"}]},
               {"id": "d2", "sources": [{"number": 1, "url": "https://www.b.org/", "title": "B"}]}]
    sources, mappings = document.global_sources(questions)
    assert [f["url"] for f in sources] == ["https://a.org/x", "https://b.org"] and mappings["d2"] == {1: 2}
    assert document.renumber("Fact [1, 2] and more [1]; unknown [9].", mappings["d2"]) == "Fact [2] and more [2]; unknown ."


def test_budget_and_time_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_SETTINGS", str(tmp_path / "settings.json"))
    ind = Investigation.create("test topic", budget_usd=1.0, max_minutes=120, director_model="haiku",
                        base_folder=tmp_path)
    ind.status["plan"] = {"sections": [{"id": "s1"}] * 4}
    assert ind.can_start(0) == ""                     # 0.20 + reserve 0.18 ≤ $1
    ind.status["director_spent_usd"] = 0.4
    ind.status["director_spent_usd"] = 0.5
    assert "budget" in ind.can_start(1)               # 0.5 + 2×0.20 + 0.18 > $1
    ind.status["limits"]["without_limits"] = True
    assert ind.can_start(5) == ""
    ind.stop()
    assert ind.can_start(0) == "investigation stopped"


def test_topic_and_model_checked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="topic"):
        Investigation.create("  ", base_folder=tmp_path)
    with pytest.raises(ValueError, match="Invalid model"):
        Investigation.create("valid topic", director_model="gpt", base_folder=tmp_path)
