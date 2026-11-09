"""Un test per ogni difetto trovato nella revisione del 9/11/2026 (moduli singoli, senza processi né rete)."""
import json
from pathlib import Path

import pytest

from ricerca_catena import configurazione, documento
from ricerca_catena.indagine import Indagine
from ricerca_catena.modello import claude_json, registro
from ricerca_catena.modello.pensatore import Bilancio, Pensatore
from ricerca_catena.web import browser
from ricerca_catena.web.catena import Catena, valore_ammesso
from ricerca_catena.web.fonti import wikipedia_grafo as wg

ERRORE_LIMITE = json.dumps({"type": "result", "subtype": "success", "is_error": True, "total_cost_usd": 0,
                            "result": "You've hit your session limit · resets 3pm"})
RISPOSTA_NORMALE = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                               "result": "Il piano prevede un usage limit di 10 GB.", "modelUsage": {"claude-x-1": {}}})


# --- Wikipedia: frequenza e fallimenti temporanei ---------------------------------------------------------------

@pytest.mark.parametrize("contatto,parallele,atteso", [("a@b.org", 1, 0.7), ("a@b.org", 3, 0.9), ("a@b.org", 4, 1.2),
                                                       ("", 1, 6.5), ("", 3, 19.5)])
def test_intervallo_wikipedia(monkeypatch, contatto: str, parallele: int, atteso: float) -> None:
    monkeypatch.setattr(wg, "contatto_wikipedia", lambda: (contatto, "impostazioni" if contatto else ""))
    assert wg.intervallo_per(parallele) == pytest.approx(atteso)


def test_errore_temporaneo_di_wikipedia_non_resta_in_memoria() -> None:
    chiamate = []

    def ottieni(_url: str, _params: dict):
        chiamate.append(1)
        if len(chiamate) == 1:
            raise RuntimeError("rete giù")
        return {"parse": {"title": "Voce", "text": "<p>Una frase qualsiasi della voce.</p>"}}, None

    wiki = wg.WikipediaGrafo(ottieni=ottieni, intervallo_s=0.0)
    assert wiki.voce("Voce", "it") is None
    assert wiki.voce("Voce", "it") is not None and len(chiamate) == 2 and wiki.lette == 1


def test_voce_inesistente_resta_in_memoria() -> None:
    chiamate = []

    def ottieni(_url: str, _params: dict):
        chiamate.append(1)
        return {"error": {"code": "missingtitle"}}, None

    wiki = wg.WikipediaGrafo(ottieni=ottieni, intervallo_s=0.0)
    assert wiki.voce("Niente", "it") is None and wiki.voce("Niente", "it") is None and len(chiamate) == 1


# --- limite di sessione e chiamate a Claude ----------------------------------------------------------------------

def test_limite_di_sessione_e_un_errore_a_parte(monkeypatch) -> None:
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: ERRORE_LIMITE)
    with pytest.raises(claude_json.LimiteSessione) as e:
        claude_json.chiedi("r", "t", None, "passo")
    assert e.value.secondi > 0


def test_una_risposta_che_parla_di_limiti_non_e_il_limite() -> None:
    assert claude_json.secondi_al_rinnovo(RISPOSTA_NORMALE) is None
    assert claude_json.secondi_al_rinnovo(ERRORE_LIMITE) is not None


@pytest.mark.parametrize("metodo", ["decidi", "chiedi"])
def test_il_pensatore_non_nasconde_il_limite(monkeypatch, metodo: str) -> None:
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: ERRORE_LIMITE)
    p = Pensatore("p", 1.0)
    with pytest.raises(claude_json.LimiteSessione):
        p.decidi("passo", {}, lambda: {}) if metodo == "decidi" else p.chiedi("lettura", "r", "t")


def test_modello_esatto_registrato(monkeypatch) -> None:
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: RISPOSTA_NORMALE)
    p = Pensatore("p", 1.0)
    assert p.chiedi("lettura", "r", "t") and p.riepilogo()["modelli"] == ["claude-x-1"]
    assert registro.leggi("pensatore")[-1]["modello_esatto"] == "claude-x-1"


def test_budget_finito_in_decidi_ferma_la_catena() -> None:
    p = Pensatore("p", 0.0)
    assert p.decidi("passo", {}, lambda: {"x": 1}) == ({"x": 1}, "locale") and p.esaurito


def test_stima_legge_solo_lo_stesso_modello() -> None:
    registro.scrivi("pensatore", {"punto": "passo", "esito": "ok", "costo_usd": 0.9, "modello": "opus"})
    registro.scrivi("pensatore", {"punto": "passo", "esito": "ok", "costo_usd": 0.01, "modello": "haiku"})
    assert Bilancio.stima("passo", 10, "haiku") == pytest.approx(0.012)


def test_registro_ruota_e_si_legge_la_coda(monkeypatch) -> None:
    monkeypatch.setattr(registro, "DIMENSIONE_MASSIMA", 400)
    for i in range(30):
        registro.scrivi("prova", {"n": i})
    assert registro.leggi("prova")[-1]["n"] == 29
    assert len(list(registro.config.cartella_log().glob("prova.jsonl*"))) == 2
    assert registro.leggi("prova", ultimi_byte=120)[-1]["n"] == 29


# --- valore ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("valore,tipo", [
    ("476", "anno"), ("800 d.C.", "anno"), ("44 a.C.", "anno"), ("2150", "anno"),
    ("14 marzo 44 a.C.", "data completa"), ("July 24th, 1973", "data completa"), ("24th July 1973", "data completa"),
    ("15. Dezember 1941", "data completa"), ("12 de mayo de 1950", "data completa"),
    ("1er janvier 1999", "data completa"), ("24 luglio 1973", "data completa"), ("1973-07-24", "data completa"),
    ("Dec. 5, 1990", "data completa"), ("5 maggio", "giorno e mese")])
def test_valore_ammesso_date(valore: str, tipo: str) -> None:
    assert valore_ammesso(valore, {"tipo_valore": tipo}) == ""


@pytest.mark.parametrize("valore,tipo", [("ottobre 1994", "data completa"), ("sconosciuto", "anno"),
                                         ("1994", "giorno e mese")])
def test_valore_ammesso_rifiuti(valore: str, tipo: str) -> None:
    assert valore_ammesso(valore, {"tipo_valore": tipo})


def test_valore_avanti_cristo_e_intervallo() -> None:
    assert valore_ammesso("44 a.C.", {"tipo_valore": "anno", "minimo": -50, "massimo": -40}) == ""
    assert valore_ammesso("44 a.C.", {"tipo_valore": "anno", "minimo": 40, "massimo": 50})


def test_tetto_delle_pagine_cresce_al_massimo_di_sei() -> None:
    c = Catena.__new__(Catena)
    c.o, c._pagine_aggiunte = {"max_pagine": 30}, 0
    for _ in range(10):
        c._aggiungi_pagine_valore()
    assert c.o["max_pagine"] == 36


# --- impostazioni ------------------------------------------------------------------------------------------------

def test_impostazioni_rovinate(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "imp.json"
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(file))
    file.write_text("{non json")
    assert configurazione.carica()["budget_usd"] == 5.0
    configurazione.salva({"budget_usd": 7.0})
    assert configurazione.carica()["budget_usd"] == 7.0 and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("chiave,valore", [("budget_usd", "tanti"), ("domande_in_parallelo", 99), ("modello_regia", "gpt"),
                                           ("minuti_domanda", -1), ("pagine_domanda", None)])
def test_valori_non_validi_tornano_ai_predefiniti(tmp_path: Path, monkeypatch, chiave: str, valore) -> None:
    file = tmp_path / "imp.json"
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(file))
    file.write_text(json.dumps({chiave: valore}))
    assert configurazione.carica()[chiave] == configurazione.PREDEFINITE[chiave]


# --- piccoli ------------------------------------------------------------------------------------------------------

def test_stale_page_esiste() -> None:
    assert issubclass(browser.StalePage, Exception)


def test_punti_chiave_senza_p_spaiati() -> None:
    assert documento._in_linea("punto [1]", {1}) == 'punto [<a href="#fonte-1">1</a>]'


def test_sezione_sconosciuta_va_nella_prima_del_piano(tmp_path: Path) -> None:
    ind = Indagine.crea("argomento di prova", cartella_base=tmp_path)
    ind.stato["piano"] = {"sezioni": [{"id": f"s{i}"} for i in range(1, 30)]}
    voce = ind._aggiungi_domande([{"sezione": "boh", "domanda": "Una domanda abbastanza lunga?", "motivo": ""}], 1)[0]
    assert voce["sezione"] == "s1"


def test_due_indagini_uguali_nello_stesso_minuto(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ricerca_catena.indagine.nome_cartella", lambda _a: "2026-01-01-0000-uguale")
    Indagine.crea("argomento di prova", cartella_base=tmp_path)
    with pytest.raises(ValueError, match="stesso argomento"):
        Indagine.crea("argomento di prova", cartella_base=tmp_path)


def test_stima_della_domanda_dai_costi_osservati(tmp_path: Path) -> None:
    ind = Indagine.crea("argomento di prova", cartella_base=tmp_path)
    ind.stato["domande"] = [{"costo_usd": c} for c in (0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.30, 0.04)]
    assert ind._stima_domanda() == pytest.approx(0.10)
    ind.stato["domande"] = [{"costo_usd": 0.05}]
    assert ind._stima_domanda() == pytest.approx(0.20)


def test_minuti_della_domanda_e_ultimo_lancio(tmp_path: Path, monkeypatch) -> None:
    ind = Indagine.crea("argomento di prova", max_minuti=120, cartella_base=tmp_path)
    monkeypatch.setattr(ind, "trascorsi", lambda: 104 * 60)
    assert ind.puo_partire(0) == "" and ind._minuti_domanda() == pytest.approx(11)
    monkeypatch.setattr(ind, "trascorsi", lambda: 106 * 60)
    assert "tempo" in ind.puo_partire(0)
