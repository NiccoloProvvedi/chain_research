"""Un test per ogni difetto trovato nella revisione del 9/11/2026 (moduli singoli, senza processi né rete)."""
import json
from pathlib import Path

import pytest

from chain_research import configuration, document
from chain_research.investigation import Investigation
from chain_research.model import claude_json, event_log
from chain_research.model.thinker import Ledger, Thinker
from chain_research.web import browser
from chain_research.web.chain import Chain, allowed_value
from chain_research.web.sources import wikipedia_graph as wg

LIMIT_ERROR = json.dumps({"type": "result", "subtype": "success", "is_error": True, "total_cost_usd": 0,
                            "result": "You've hit your session limit · resets 3pm"})
NORMAL_ANSWER = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                               "result": "Il piano prevede un usage limit di 10 GB.", "modelUsage": {"claude-x-1": {}}})


# --- Wikipedia: frequenza e fallimenti temporanei ---------------------------------------------------------------

@pytest.mark.parametrize("contact,parallel,expected", [("a@b.org", 1, 0.7), ("a@b.org", 3, 0.9), ("a@b.org", 4, 1.2),
                                                       ("", 1, 6.5), ("", 3, 19.5)])
def test_interval_wikipedia(monkeypatch, contact: str, parallel: int, expected: float) -> None:
    monkeypatch.setattr(wg, "wikipedia_contact", lambda: (contact, "settings" if contact else ""))
    assert wg.interval_per(parallel) == pytest.approx(expected)


def test_temporary_error_of_wikipedia_not_stays_in_memory() -> None:
    calls = []

    def fetch(_url: str, _params: dict):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("rete giù")
        return {"parse": {"title": "Article", "text": "<p>Una frase qualsiasi della voce.</p>"}}, None

    wiki = wg.WikipediaGraph(fetch=fetch, interval_s=0.0)
    assert wiki.article("Article", "it") is None
    assert wiki.article("Article", "it") is not None and len(calls) == 2 and wiki.read_count == 1


def test_article_missing_stays_in_memory() -> None:
    calls = []

    def fetch(_url: str, _params: dict):
        calls.append(1)
        return {"error": {"code": "missingtitle"}}, None

    wiki = wg.WikipediaGraph(fetch=fetch, interval_s=0.0)
    assert wiki.article("Niente", "it") is None and wiki.article("Niente", "it") is None and len(calls) == 1


# --- limite di sessione e chiamate a Claude ----------------------------------------------------------------------

def test_limit_of_session_and_error_to_part(monkeypatch) -> None:
    monkeypatch.setattr(claude_json, "_run", lambda *_to: LIMIT_ERROR)
    with pytest.raises(claude_json.SessionLimit) as e:
        claude_json.ask("r", "t", None, "step")
    assert e.value.seconds > 0


def test_one_answer_that_talks_of_limits_not_and_limit() -> None:
    assert claude_json.seconds_to_renewal(NORMAL_ANSWER) is None
    assert claude_json.seconds_to_renewal(LIMIT_ERROR) is not None


@pytest.mark.parametrize("text", ["You've hit your 5-hour limit · resets 3:10am (Europe/Rome)",
                                   "You've hit your session limit · resets 3pm", "Claude AI usage limit reached|1234",
                                   "You've hit your weekly limit"])
def test_all_i_messages_of_limit(text: str) -> None:
    row = json.dumps({"type": "result", "subtype": "success", "is_error": True, "total_cost_usd": 0, "result": text})
    assert claude_json.seconds_to_renewal(row) is not None


@pytest.mark.parametrize("method", ["decidi", "chiedi"])
def test_thinker_not_hides_limit(monkeypatch, method: str) -> None:
    monkeypatch.setattr(claude_json, "_run", lambda *_to: LIMIT_ERROR)
    p = Thinker("p", 1.0)
    with pytest.raises(claude_json.SessionLimit):
        p.decide("step", {}, lambda: {}) if method == "decidi" else p.ask("reading", "r", "t")


def test_exact_recorded_model(monkeypatch) -> None:
    monkeypatch.setattr(claude_json, "_run", lambda *_to: NORMAL_ANSWER)
    p = Thinker("p", 1.0)
    assert p.ask("reading", "r", "t") and p.summary()["models"] == ["claude-x-1"]
    assert event_log.read("thinker")[-1]["exact_model"] == "claude-x-1"


def test_budget_finished_in_decide_stop_chain() -> None:
    p = Thinker("p", 0.0)
    assert p.decide("step", {}, lambda: {"x": 1}) == ({"x": 1}, "local") and p.exhausted


def test_estimate_reads_only_same_model() -> None:
    event_log.write("thinker", {"point": "step", "outcome": "ok", "cost_usd": 0.9, "model": "opus"})
    event_log.write("thinker", {"point": "step", "outcome": "ok", "cost_usd": 0.01, "model": "haiku"})
    assert Ledger.estimate("step", 10, "haiku") == pytest.approx(0.012)


def test_log_rotates_and_reads_queue(monkeypatch) -> None:
    monkeypatch.setattr(event_log, "MAX_SIZE", 400)
    for i in range(30):
        event_log.write("proof", {"n": i})
    assert event_log.read("proof")[-1]["n"] == 29
    assert len(list(event_log.config.folder_log().glob("prova.jsonl*"))) == 2
    assert event_log.read("proof", last_byte=120)[-1]["n"] == 29


# --- valore ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,kind", [
    ("476", "year"), ("800 d.C.", "year"), ("44 a.C.", "year"), ("2150", "year"),
    ("14 marzo 44 a.C.", "full date"), ("July 24th, 1973", "full date"), ("24th July 1973", "full date"),
    ("15. Dezember 1941", "full date"), ("12 de mayo de 1950", "full date"),
    ("1er janvier 1999", "full date"), ("24 luglio 1973", "full date"), ("1973-07-24", "full date"),
    ("Dec. 5, 1990", "full date"), ("5 maggio", "day and month")])
def test_allowed_value_date(value: str, kind: str) -> None:
    assert allowed_value(value, {"value_kind": kind}) == ""


@pytest.mark.parametrize("value,kind", [("ottobre 1994", "full date"), ("sconosciuto", "year"),
                                         ("1994", "day and month")])
def test_allowed_value_rejections(value: str, kind: str) -> None:
    assert allowed_value(value, {"value_kind": kind})


def test_value_forward_christ_and_interval() -> None:
    assert allowed_value("44 a.C.", {"value_kind": "year", "min": -50, "max": -40}) == ""
    assert allowed_value("44 a.C.", {"value_kind": "year", "min": 40, "max": 50})


def test_cap_of_pages_grows_to_max_of_six() -> None:
    c = Chain.__new__(Chain)
    c.o, c._added_pages = {"max_pages": 30}, 0
    for _ in range(10):
        c._add_pages_value()
    assert c.o["max_pages"] == 36


# --- impostazioni ------------------------------------------------------------------------------------------------

def test_corrupted_settings(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "imp.json"
    monkeypatch.setenv("CHAIN_SETTINGS", str(file))
    file.write_text("{non json")
    assert configuration.load()["budget_usd"] == 5.0
    configuration.save({"budget_usd": 7.0})
    assert configuration.load()["budget_usd"] == 7.0 and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("key,value", [("budget_usd", "tanti"), ("parallel_questions", 99), ("director_model", "gpt"),
                                           ("question_minutes", -1), ("question_pages", None)])
def test_values_not_valid_return_to_defaults(tmp_path: Path, monkeypatch, key: str, value) -> None:
    file = tmp_path / "imp.json"
    monkeypatch.setenv("CHAIN_SETTINGS", str(file))
    file.write_text(json.dumps({key: value}))
    assert configuration.load()[key] == configuration.DEFAULTS[key]


# --- piccoli ------------------------------------------------------------------------------------------------------

def test_stale_page_exists() -> None:
    assert issubclass(browser.StalePage, Exception)


def test_points_key_without_p_unpaired() -> None:
    assert document._in_line("punto [1]", {1}) == 'punto [<a href="#fonte-1">1</a>]'


def test_section_unknown_goes_in_first_of_plan(tmp_path: Path) -> None:
    ind = Investigation.create("argomento di prova", base_folder=tmp_path)
    ind.status["plan"] = {"sections": [{"id": f"s{i}"} for i in range(1, 30)]}
    article = ind._add_questions([{"section": "boh", "question": "Una domanda abbastanza lunga?", "reason": ""}], 1)[0]
    assert article["section"] == "s1"


def test_two_investigations_equal_in_same_minute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("chain_research.investigation.folder_name", lambda _to: "2026-01-01-0000-uguale")
    Investigation.create("argomento di prova", base_folder=tmp_path)
    with pytest.raises(ValueError, match="stesso argomento"):
        Investigation.create("argomento di prova", base_folder=tmp_path)


def test_estimate_of_question_from_observed_costs(tmp_path: Path) -> None:
    ind = Investigation.create("argomento di prova", base_folder=tmp_path)
    ind.status["questions"] = [{"cost_usd": c} for c in (0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.30, 0.04)]
    assert ind._question_estimate() == pytest.approx(0.10)
    ind.status["questions"] = [{"cost_usd": 0.05}]
    assert ind._question_estimate() == pytest.approx(0.20)


def test_minutes_of_question_and_last_launch(tmp_path: Path, monkeypatch) -> None:
    ind = Investigation.create("argomento di prova", max_minutes=120, base_folder=tmp_path)
    monkeypatch.setattr(ind, "elapsed", lambda: 104 * 60)
    assert ind.can_start(0) == "" and ind._question_minutes() == pytest.approx(11)
    monkeypatch.setattr(ind, "elapsed", lambda: 106 * 60)
    assert "time" in ind.can_start(0)


def test_two_readings_give_same_outcome_in_any_order() -> None:
    from chain_research.web.chain import Link, Proof

    def reading(number: int) -> dict:
        return {"candidates": [{"name": "Mario Rossi", "checks": [
            {"check": "C1", "outcome": "yes", "proof": number, "quote": "Mario Rossi fu gesuita"}]}]}

    def choice_proof(answers: list[dict]) -> str:
        c = Chain.__new__(Chain)
        c.o = {}
        c._decide = lambda *_to: answers.pop(0)
        c._segments_on = lambda _n: []
        c._entity_proofs = lambda *_to, **_k: [Proof("Mario Rossi fu gesuita a Roma", "u1"), Proof("Mario Rossi fu gesuita a Parma", "u2")]
        a = Link("Mario Rossi")
        c._outcomes([a], [{"description": "gesuita", "readings": [], "weight": 2}])
        return a.outcomes[0]["proof"].text

    assert choice_proof([reading(1), reading(2)]) == choice_proof([reading(2), reading(1)])


def test_duration_not_count_l_wait_of_limit(monkeypatch) -> None:
    import inspect

    from chain_research.web import collector
    assert '"secondi": round(self._trascorsi())' in inspect.getsource(collector.Collector.run)
