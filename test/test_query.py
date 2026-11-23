"""Phase C, Part 7.1: duplicate query filter (Jaccard) and redundancy index S(T)."""
import pytest

from chain_research.web import collector as research
from chain_research.model import query


def test_words_are_normalized_and_identifiers_kept_whole() -> None:
    assert query.words("Compilare llama.cpp con Vulkan su gfx1200, build b4000 (Qwen 3.8)") == \
        {"compilare", "llama.cpp", "vulkan", "gfx1200", "build", "b4000", "qwen", "3.8"}
    assert query.words("La città di Forlì") == {"citta", "forli"}
    assert query.words("Python vs Rust") == {"python", "rust"}


def test_jaccard_and_duplicates() -> None:
    assert query.jaccard("Guerra dei Trent'anni cause", "cause della guerra dei trent'anni") == 1.0
    assert query.jaccard("pace di Vestfalia 1648", "pace di Vestfalia trattati") == 0.5
    assert query.duplicate("llama.cpp vulkan rx 9060", ["llama.cpp vulkan rx 9060 xt"], 0.6)
    assert not query.duplicate("whisper.cpp vulkan", ["llama.cpp vulkan"], 0.6)
    assert query.most_similar("x", []) == (0.0, None)


def test_redundancy_index() -> None:
    assert query.redundancy(["a b", "a b"]) == 1.0
    assert query.redundancy(["alfa beta", "gamma delta"]) == 0.0
    assert query.redundancy(["alfa beta", "alfa gamma", "delta"]) == round((1 / 3 * 2) / 6, 4)
    assert query.redundancy(["sola"]) == 0.0


def test_collector_skips_duplicate_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    r = research.Collector("topic")
    events = []
    r.publish = events.append
    rephrased = iter(['{"query": "pace di Vestfalia conseguenze territoriali"}', '{"query": "trattati Vestfalia 1648"}'])
    monkeypatch.setattr(r, "_ask", lambda instructions, text: next(rephrased))
    kept = r._filter_query(["trattati Vestfalia 1648", "Vestfalia 1648 trattati", "Vestfalia trattati 1648",
                              "Guerra dei Trent'anni cause"])
    # the second is a duplicate and Claude rephrases it well; the third is a duplicate and its rephrasing is still one
    assert kept == ["trattati Vestfalia 1648", "pace di Vestfalia conseguenze territoriali",
                      "Guerra dei Trent'anni cause"]
    assert r.executed_query == kept
    duplicates = [e for e in events if e["kind"] == "duplicate_query"]
    assert [e["discarded"] for e in duplicates] == [False, True]


def test_different_numbers_versions_or_months_make_different_queries() -> None:
    assert query.jaccard("llama.cpp ottobre 2024 nuove funzioni", "llama.cpp agosto 2024 nuove funzioni") > 0.6
    assert not query.duplicate("llama.cpp ottobre 2024 nuove funzioni", ["llama.cpp agosto 2024 nuove funzioni"], 0.6)
    assert not query.duplicate("Qwen 3.8 benchmark vulkan", ["Qwen 3.5 benchmark vulkan"], 0.6)
    assert query.duplicate("llama.cpp vulkan rx 9060 arch linux driver",
                        ["llama.cpp vulkan rx 9060 arch linux compilazione"], 0.6)
