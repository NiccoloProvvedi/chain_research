"""Second independent search for uncertain answers (docs/validation.md, 16 Nov): no network, no Claude."""
import pytest

from chain_research.web import retry


def _outcome(value, status, cost=0.10, seconds=300, reasons=None) -> dict:
    confidence = {"verified": "high", "supported": "medium"}.get(status, "low")
    return {"question": "d", "value": value, "found": bool(value), "status": status, "confirmed": status in ("verified", "supported"),
            "answer": f"**Answer:** {value} [1].\n\n**Confidence:** {confidence} — {status} answer: x." if value else "**Answer:** Not found.",
            "cost_usd": cost, "seconds": seconds, "sources": [{"number": 1, "url": f"https://e.org/{value}"}],
            "read_pages": 4, "searches": ["q"],
            "chain": {"checks": {"status": status, "reasons": list(reasons or ["1 chain links not confirmed"]), "confirmed": False}}}


def test_same_value_twice_makes_answer_supported() -> None:
    merged = retry.merge(_outcome("56 m s.l.m.", "uncertain"), _outcome("56 metri", "uncertain", cost=0.2, seconds=100))
    assert (merged["status"], merged["confirmed"], merged["value"]) == ("supported", True, "56 m s.l.m.")
    assert merged["retry"] == {"outcome": "same value", "value": "56 metri", "status": "uncertain", "cost_usd": 0.2, "seconds": 100}
    assert merged["cost_usd"] == pytest.approx(0.3) and merged["seconds"] == 400
    reasons = merged["chain"]["checks"]["reasons"]
    assert reasons[0].startswith("two independent searches give the same value") and "1 chain links" in reasons[1]
    assert merged["chain"]["checks"]["status"] == "supported" and merged["chain"]["checks"]["confirmed"]
    assert "**Confidence:** medium — supported answer: two independent searches" in merged["answer"]
    assert "uncertain answer" not in merged["answer"]


def test_better_second_search_becomes_main_answer() -> None:
    """Same value and the second chain is fully confirmed: its answer counts, with its sources and its status."""
    merged = retry.merge(_outcome("56 m", "uncertain"), _outcome("56 m s.l.m.", "verified", reasons=[]))
    assert (merged["status"], merged["value"], merged["sources"][0]["url"]) == ("verified", "56 m s.l.m.", "https://e.org/56 m s.l.m.")
    assert merged["retry"]["outcome"] == "same value" and merged["retry"]["status"] == "uncertain"   # the other one is the first


@pytest.mark.parametrize("status_second,main", [("uncertain", "120 m"), ("supported", "138 m")])
def test_different_value_makes_answer_contested(status_second: str, main: str) -> None:
    merged = retry.merge(_outcome("120 m", "uncertain"), _outcome("138 m", status_second))
    assert (merged["status"], merged["confirmed"], merged["value"]) == ("contested", False, main)
    other = "138 m" if main == "120 m" else "120 m"
    assert merged["retry"]["outcome"] == "other value" and merged["retry"]["value"] == other
    assert f"another independent search gives {other}" in merged["chain"]["checks"]["reasons"][0]
    assert "**Confidence:** low — contested answer" in merged["answer"]


def test_second_search_without_value_stays_uncertain() -> None:
    merged = retry.merge(_outcome("56 m", "uncertain"), _outcome(None, "not found"))
    assert (merged["status"], merged["value"], merged["retry"]["outcome"]) == ("uncertain", "56 m", "no value")
    assert "a second search found no value" in merged["chain"]["checks"]["reasons"][-1]


class _Fake:
    create: list[dict] = []
    outcomes: list[dict] = []

    def __init__(self, options: dict) -> None:
        _Fake.create.append(options)

    def run(self) -> dict:
        return _Fake.outcomes.pop(0)


@pytest.fixture
def fake():
    _Fake.create, _Fake.outcomes = [], []
    return _Fake


def test_only_uncertain_answers_are_searched_twice(fake) -> None:
    for status in ("verified", "supported", "contested", "not found"):
        fake.outcomes = [_outcome("56 m" if status != "not found" else None, status)]
        outcome = retry.with_retry(fake, {"budget_claude": 0.5, "chain_minutes": 35}, lambda _and: None)
        assert outcome["status"] == status and "retry" not in outcome
    assert len(fake.create) == 4


def test_second_search_gets_remaining_budget_and_time(fake) -> None:
    fake.outcomes = [_outcome("56 m", "uncertain", cost=0.12, seconds=600), _outcome("56 m", "uncertain", cost=0.2)]
    events: list[dict] = []
    outcome = retry.with_retry(fake, {"budget_claude": 0.5, "chain_minutes": 35, "x": 1}, events.append)
    assert outcome["status"] == "supported" and len(fake.create) == 2
    second = fake.create[1]
    assert second["budget_claude"] == pytest.approx(0.38) and second["chain_minutes"] == pytest.approx(25) and second["x"] == 1
    assert any("second" in e.get("text", "") for e in events)


@pytest.mark.parametrize("options,cost,seconds", [
    ({"budget_claude": 0.5, "chain_minutes": 35}, 0.40, 300),                        # too little budget left
    ({"budget_claude": 0.5, "chain_minutes": 35}, 0.10, 31 * 60),                    # too little time left
    ({"budget_claude": 0.5, "chain_minutes": 35, "second_research": False}, 0.10, 300),
])
def test_single_search_without_budget_time_or_permission(fake, options: dict, cost: float, seconds: int) -> None:
    fake.outcomes = [_outcome("56 m", "uncertain", cost=cost, seconds=seconds)]
    outcome = retry.with_retry(fake, options, lambda _and: None)
    assert outcome["status"] == "uncertain" and len(fake.create) == 1 and "retry" not in outcome


def test_question_process_searches_twice_and_sums_costs(monkeypatch, capsys) -> None:
    import io
    import json
    from types import SimpleNamespace

    from chain_research import question
    outcomes = [_outcome("120 m", "uncertain", cost=0.1), _outcome("138 m", "supported", cost=0.2)]
    create: list[dict] = []

    class FakeCollector:
        def __init__(self, _question: str, *, options: dict, **_other) -> None:
            create.append(options)
            self.thinker = SimpleNamespace(ledger=SimpleNamespace(spent=0.1 * len(create)))

        def run(self) -> dict:
            return outcomes.pop(0)

    monkeypatch.setattr(question, "Collector", FakeCollector)
    monkeypatch.setattr(question.signal, "signal", lambda *_to: None)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"question": "d", "options": {"budget_claude": 0.5}}) + "\n"))
    assert question.main() == 0
    rows = [json.loads(r) for r in capsys.readouterr().out.splitlines()]
    outcome = rows[-1]["outcome"]
    assert len(create) == 2 and create[1]["budget_claude"] == pytest.approx(0.4)
    assert (outcome["status"], outcome["value"], outcome["retry"]["value"]) == ("contested", "138 m", "120 m")
    assert outcome["cost_usd"] == pytest.approx(0.3) and any("second time" in r.get("event", {}).get("text", "") for r in rows)
