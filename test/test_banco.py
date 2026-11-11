"""Giudice del banco senza Claude: confronto normalizzato e risposta breve."""
from giudice import giudica, normalizza, risposta_breve, uguale_normalizzata


def test_confronto_normalizzato() -> None:
    assert normalizza("Città  d'Aosta!") == "citta d aosta "
    assert uguale_normalizzata("24 luglio 1973", "**24 luglio 1973** [6].")
    assert not uguale_normalizzata("1973", "19731")
    assert giudica("d", "Mitchell Baker", "Mitchell Baker [2]") == (True, "confronto")
    assert giudica("d", "x", "") == (False, "vuota")


def test_risposta_breve() -> None:
    assert risposta_breve("intro\n**Risposta:** 21 giugno 1976 [16].\n\n**Fiducia:** alta") == "21 giugno 1976 [16]."
    assert risposta_breve("\n\nsolo testo\naltro") == "solo testo"


# --- revisione dell'11/11/2026: il metro del banco ----------------------------------------------------------------

import pytest  # noqa: E402

import giudice  # noqa: E402
from ricerca_catena.modello import claude_json  # noqa: E402


@pytest.mark.parametrize("attesa,data", [("2 m", "−2 m s.l.m."), ("2 m", "-2 m s.l.m. (2 metri sotto il livello del mare)"),
                                         ("-5 °C", "5 °C")])
def test_il_segno_conta(attesa: str, data: str) -> None:
    assert not uguale_normalizzata(attesa, data)


@pytest.mark.parametrize("attesa,data", [("00118–00199", "00118–00199"), ("1973-07-24", "1973-07-24 [2]"),
                                         ("−2 m", "-2 m s.l.m."), ("312 m", "312 m s.l.m."),
                                         ("21 giugno 1868", "21 giugno 1868 - fondazione")])
def test_trattini_che_non_sono_un_segno(attesa: str, data: str) -> None:
    assert uguale_normalizzata(attesa, data)


def test_il_valore_del_programma_viene_prima_dell_estrazione(monkeypatch) -> None:
    monkeypatch.setattr(giudice, "estrai", lambda *_a: pytest.fail("estrazione inutile"))
    assert giudice.finale_di({"valore": "00118–00199", "risposta": "**Risposta:** 00118–00199 [18]."}, "d") == "00118–00199"


def test_senza_valore_si_estrae_dalla_risposta(monkeypatch) -> None:
    monkeypatch.setattr(giudice, "estrai", lambda _d, testo: f"estratto da {testo}")
    assert giudice.finale_di({"valore": None, "risposta": "**Risposta:** Non trovata."}, "d") == "estratto da Non trovata."


def test_il_limite_di_sessione_non_e_una_risposta_sbagliata(monkeypatch) -> None:
    def limite(*_a, **_k):
        raise claude_json.LimiteSessione(60)
    monkeypatch.setattr(claude_json, "chiedi", limite)
    with pytest.raises(claude_json.LimiteSessione):
        giudica("d", "Mitchell Baker", "Brendan Eich")


def test_il_giudice_in_errore_non_decide(monkeypatch) -> None:
    def errore(*_a, **_k):
        raise claude_json.ErroreClaude("rotto")
    monkeypatch.setattr(claude_json, "chiedi", errore)
    assert giudica("d", "Mitchell Baker", "Brendan Eich") == (None, "giudice in errore")
    with pytest.raises(claude_json.ErroreClaude):
        giudice.estrai("d", "testo")


def test_nuovo_giudizio_senza_claude() -> None:
    import rigiudica
    esiti = {
        "a": {"attesa": "2 m", "valore": "−2 m s.l.m.", "finale": "−2 m s.l.m.", "corretta": True, "deciso_da": "confronto"},
        "b": {"attesa": "00118–00199", "valore": "00118–00199", "finale": "", "corretta": False, "deciso_da": "vuota"},
        "c": {"attesa": "68 m", "valore": "56 m s.l.m.", "finale": "56 m s.l.m.", "corretta": False, "deciso_da": "giudice"},
        "d": {"attesa": "27 gennaio 1861", "valore": None, "trovata": False, "finale": "", "corretta": False, "deciso_da": "vuota"},
        "e": {"errore": "boh"},
    }
    nuovi = rigiudica.rigiudica(esiti)
    assert nuovi["a"]["deciso_da"] == "da giudicare" and "corretta" not in nuovi["a"]
    assert (nuovi["b"]["corretta"], nuovi["b"]["deciso_da"]) == (True, "confronto")
    assert (nuovi["c"]["corretta"], nuovi["c"]["deciso_da"]) == (False, "giudice")
    assert (nuovi["d"]["corretta"], nuovi["d"]["deciso_da"]) == (False, "vuota")
    assert nuovi["e"] == {"errore": "boh"}


def test_riferimento_non_ok_fuori_dal_conto() -> None:
    import statistiche
    esiti = [{"corretta": True}, {"corretta": False, "stato_riferimento": "contestata"}, {"corretta": False}]
    assert statistiche.riassunto(esiti).startswith("1/2 ")
