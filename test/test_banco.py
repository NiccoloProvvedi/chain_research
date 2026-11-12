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


def _esito(corretta: bool, stato: str, valore: str = "v", **controlli) -> dict:
    return {"corretta": corretta, "stato": stato, "valore": valore, "catena": {"controlli": controlli}}


def test_rapporto_segnali_e_regola() -> None:
    import rapporto
    buoni = {"seconda_lettura": {"esito": "concordi"}, "riscontro": {"esito": "concordi"},
             "revisione": {"esito": "fatta", "regge": "sì", "obiezioni": []}}
    obiezione = {**buoni, "revisione": {"esito": "fatta", "regge": "no", "obiezioni": [{"indizio": "x"}]}}
    esiti = ([_esito(True, "verificata", esami=buoni)] * 19 + [_esito(False, "verificata", esami=buoni)]
             + [_esito(False, "incerta", esami=obiezione, anelli_deboli=1)] * 3 + [_esito(True, "incerta", anelli_deboli=1)]
             + [_esito(False, "contestata", esami={**buoni, "riscontro": {"esito": "discordi"}})]
             + [{"corretta": False, "stato": "non trovata", "valore": None, "catena": {"controlli": {}}}]
             + [_esito(True, "verificata", esami=buoni) | {"stato_riferimento": "contestata"}])
    assert rapporto.per_stato(esiti) == {"verificata": (19, 20), "sostenuta": (0, 0), "contestata": (0, 1), "incerta": (1, 4)}
    tabella = rapporto.tabella_segnali(esiti)
    assert tabella["revisione: obiezione con riga"] == (0, 3) and tabella["anello debole"] == (1, 3)
    assert tabella["altre fonti: discordi"] == (0, 1) and tabella["altre fonti: concordi"] == (19, 4)
    assert tabella["esami non fatti"] == (1, 0) and tabella["revisione: regge sì"] == (19, 2)
    primo, secondo = rapporto.regola(esiti)
    assert not primo[1] and "19/20" in primo[2]            # 95% ma il limite inferiore è sotto l'85%
    assert secondo[1] and "19/20 contro 1/5" in secondo[2]
    assert rapporto.regola([_esito(True, "verificata", esami=buoni)] * 40)[0][1]
    a = {"x": _esito(True, "verificata", "1 m"), "y": _esito(True, "incerta", "2 m")}
    b = {"x": _esito(True, "verificata", "1  M"), "y": _esito(False, "sostenuta", "3 m")}
    assert rapporto.ripetizioni([a, b]) == "2 domande in tutte le corse: stesso valore 1, stesso stato 1"


def test_domanda_senza_risposta_giusta_solo_se_non_trovata(monkeypatch) -> None:
    """T4: per una domanda che non ha risposta, giusto è dire «non trovata»; un valore qualunque è inventato."""
    import banco
    monkeypatch.setattr(giudice, "estrai", lambda *_a: pytest.fail("non serve il modello"))
    monkeypatch.setattr(giudice, "giudica", lambda *_a: pytest.fail("non serve il giudice"))
    onesta = {"domanda": "d", "attesa": "", "senza_risposta": True, "trovata": False, "valore": None, "risposta": "Non trovata"}
    inventata = {"domanda": "d", "attesa": "", "senza_risposta": True, "trovata": True, "valore": "12 marzo 1850", "risposta": "x"}
    banco.giudica_voce(onesta)
    banco.giudica_voce(inventata)
    assert (onesta["corretta"], onesta["deciso_da"]) == (True, "senza risposta")
    assert (inventata["corretta"], inventata["finale"]) == (False, "12 marzo 1850")


def test_le_domande_senza_risposta_sono_ben_fatte() -> None:
    import json
    from pathlib import Path
    domande = json.loads((Path(__file__).resolve().parents[1] / "banco" / "domande_senza_risposta.json").read_text())["domande"]
    assert len(domande) == 20 and len({d["seme"] for d in domande}) == 20
    assert all(d["senza_risposta"] is True and d["risposta"] == "" and d["perche"] and d["domanda"].endswith("?") for d in domande)
    assert {d["tema"] for d in domande} == {"entita_inventata", "relazione_inesistente", "dato_mai_esistito"}
