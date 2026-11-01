"""Fase C, Parte 7.1: filtro delle query doppie (Jaccard) e indice di ridondanza S(T)."""
import pytest

from ricerca_catena.web import raccolta as ricerca
from ricerca_catena.modello import query


def test_parole_normalizzate_e_identificativi_interi() -> None:
    assert query.parole("Compilare llama.cpp con Vulkan su gfx1200, build b4000 (Qwen 3.8)") == \
        {"compilare", "llama.cpp", "vulkan", "gfx1200", "build", "b4000", "qwen", "3.8"}
    assert query.parole("La città di Forlì") == {"citta", "forli"}
    assert query.parole("Python vs Rust") == {"python", "rust"}


def test_jaccard_e_doppie() -> None:
    assert query.jaccard("Guerra dei Trent'anni cause", "cause della guerra dei trent'anni") == 1.0
    assert query.jaccard("pace di Vestfalia 1648", "pace di Vestfalia trattati") == 0.5
    assert query.doppia("llama.cpp vulkan rx 9060", ["llama.cpp vulkan rx 9060 xt"], 0.6)
    assert not query.doppia("whisper.cpp vulkan", ["llama.cpp vulkan"], 0.6)
    assert query.piu_simile("x", []) == (0.0, None)


def test_indice_di_ridondanza() -> None:
    assert query.ridondanza(["a b", "a b"]) == 1.0
    assert query.ridondanza(["alfa beta", "gamma delta"]) == 0.0
    assert query.ridondanza(["alfa beta", "alfa gamma", "delta"]) == round((1 / 3 * 2) / 6, 4)
    assert query.ridondanza(["sola"]) == 0.0


def test_la_ricerca_salta_le_query_doppie(monkeypatch: pytest.MonkeyPatch) -> None:
    r = ricerca.Raccolta("argomento")
    eventi = []
    r.pubblica = eventi.append
    riformulate = iter(['{"query": "pace di Vestfalia conseguenze territoriali"}', '{"query": "trattati Vestfalia 1648"}'])
    monkeypatch.setattr(r, "_chiedi", lambda istruzioni, testo: next(riformulate))
    tenute = r._filtra_query(["trattati Vestfalia 1648", "Vestfalia 1648 trattati", "Vestfalia trattati 1648",
                              "Guerra dei Trent'anni cause"])
    # la seconda è doppia e Claude la riformula bene; la terza è doppia e la riformulazione è ancora doppia
    assert tenute == ["trattati Vestfalia 1648", "pace di Vestfalia conseguenze territoriali",
                      "Guerra dei Trent'anni cause"]
    assert r.query_eseguite == tenute
    doppie = [e for e in eventi if e["tipo"] == "query_doppia"]
    assert [e["scartata"] for e in doppie] == [False, True]


def test_numeri_versioni_e_mesi_rendono_diverse_le_query() -> None:
    assert query.jaccard("llama.cpp ottobre 2024 nuove funzioni", "llama.cpp agosto 2024 nuove funzioni") > 0.6
    assert not query.doppia("llama.cpp ottobre 2024 nuove funzioni", ["llama.cpp agosto 2024 nuove funzioni"], 0.6)
    assert not query.doppia("Qwen 3.8 benchmark vulkan", ["Qwen 3.5 benchmark vulkan"], 0.6)
    assert query.doppia("llama.cpp vulkan rx 9060 arch linux driver",
                        ["llama.cpp vulkan rx 9060 arch linux compilazione"], 0.6)
