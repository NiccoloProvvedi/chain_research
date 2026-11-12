"""Indagine e documento senza Claude né rete: regia finta, catena finta, PDF vero con il Chromium del progetto."""
import json
from pathlib import Path

import pytest

from ricerca_catena import documento, indagine
from ricerca_catena.indagine import Indagine

PIANO = {"titolo": "Arduino: storia e marchio", "obiettivo": "Capire chi ha creato Arduino e come è finita.",
         "sezioni": [{"id": "s1", "titolo": "Origini", "scopo": "chi e quando"},
                     {"id": "s2", "titolo": "Il marchio", "scopo": "la disputa"}],
         "domande": [{"sezione": "s1", "domanda": "In che anno è nato il progetto Arduino a Ivrea?", "motivo": "m"},
                     {"sezione": "s1", "domanda": "Chi ha guidato il progetto Arduino all'Interaction Design "
                                                  "Institute Ivrea?", "motivo": "m"},
                     {"sezione": "s2", "domanda": "In che anno Arduino LLC e Arduino Srl hanno firmato l'accordo "
                                                  "sul marchio?", "motivo": "m"},
                     {"sezione": "s2", "domanda": "In che anno è nato il progetto Arduino a Ivrea?", "motivo": "doppia"}]}
RISPOSTE = {
    "In che anno è nato il progetto Arduino a Ivrea?": ("2005", [("https://it.wikipedia.org/wiki/Arduino", "Arduino")]),
    "Chi ha guidato il progetto Arduino all'Interaction Design Institute Ivrea?":
        ("Massimo Banzi", [("https://example.org/banzi", "Banzi"), ("https://it.wikipedia.org/wiki/Arduino/", "Arduino")]),
    "In che anno Arduino LLC e Arduino Srl hanno firmato l'accordo sul marchio?": None,
    "Quale società ha acquisito Arduino AG nel 2017?": ("BCMI", [("https://example.org/bcmi", "BCMI")]),
}


@pytest.fixture
def finti(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(tmp_path / "impostazioni.json"))
    chiamate: list[tuple[str, dict]] = []
    valutazioni = iter([
        {"sezioni": [{"id": "s1", "copertura": 3, "nota": ""}, {"id": "s2", "copertura": 1, "nota": "manca"}],
         "conoscenza_solida": False, "motivo": "manca il marchio",
         "nuove_domande": [{"sezione": "s2", "domanda": "Quale società ha acquisito Arduino AG nel 2017?",
                            "motivo": "proprietà del marchio"}], "nuove_sezioni": []},
        {"sezioni": [{"id": "s1", "copertura": 3, "nota": ""}, {"id": "s2", "copertura": 3, "nota": ""}],
         "conoscenza_solida": True, "motivo": "basta", "nuove_domande": [], "nuove_sezioni": []}])

    def chiedi(self, punto, oggetto, schema, tetto_usd=2.0, attesa=900, regole=None):
        chiamate.append((punto, oggetto))
        self.al_costo(0.01)
        if punto == "regia_piano":
            return PIANO
        if punto == "regia_valuta":
            return next(valutazioni)
        if punto == "documento_sezione":
            fatti = " ".join(r["risposta"].splitlines()[0] for r in oggetto["risposte"])
            return f"## {oggetto['sezione']['titolo']}\n\nTesto della sezione: {fatti}"
        return {"sintesi": "Arduino nasce nel 2005 [1] e il marchio passa a BCMI [3]. " * 3,
                "punti_chiave": ["Nato nel 2005 [1].", "Guidato da Massimo Banzi [2]."],
                "conclusioni": "Conclusione [1].", "limiti": "L'accordo non è stato trovato."}

    def una_domanda(self, d, profilo, parallele):
        trovata = RISPOSTE[d["domanda"]]
        with self._serratura:
            if trovata is None:
                d.update(stato="non_trovata", risposta="**Risposta:** Non trovata: catena incompleta.", fonti=[],
                         costo_usd=0.3, secondi=600)
            else:
                valore, fonti = trovata
                d.update(stato="risposta", valore=valore, costo_usd=0.1, secondi=300,
                         risposta=f"**Risposta:** {valore} [{len(fonti)}].\n\n**Fiducia:** alta, fonte diretta.",
                         fonti=[{"numero": i + 1, "url": u, "titolo": t} for i, (u, t) in enumerate(fonti)])
        self.salva()

    monkeypatch.setattr(indagine.Regia, "chiedi", chiedi)
    monkeypatch.setattr(Indagine, "_una_domanda", una_domanda)
    return chiamate


def test_indagine_completa_fino_al_pdf(finti, tmp_path: Path) -> None:
    eventi: list[dict] = []
    ind = Indagine.crea("Storia di Arduino e del suo marchio", modello_regia="sonnet", budget_usd=5,
                        max_minuti=120, cartella_base=tmp_path)
    ind.pubblica = eventi.append
    ind.esegui()
    stato = json.loads((ind.cartella / "stato.json").read_text())
    # la domanda doppia non entra; un secondo giro con la domanda nuova; poi la regia dice basta
    assert [d["domanda"] for d in stato["domande"]].count("In che anno è nato il progetto Arduino a Ivrea?") == 1
    assert len(stato["domande"]) == 4 and stato["giro"] == 3 and stato["stato"] == "finita"
    assert stato["motivo_fine"].startswith("Claude ritiene la conoscenza solida")
    assert [p for p, _ in finti].count("regia_valuta") == 2 and [p for p, _ in finti].count("documento_sezione") == 2
    pagina = Path(stato["documento"]["html"]).read_text()
    # fonti uniche per tutto il documento (stesso indirizzo con la barra finale = stessa fonte) e citazioni collegate
    assert stato["documento"]["fonti"] == 3 and 'id="fonte-3"' in pagina and 'href="#fonte-1"' in pagina
    assert "Appendice A" in pagina and "non trovata" in pagina and "Massimo Banzi" in pagina
    assert Path(stato["documento"]["pdf"]).read_bytes()[:5] == b"%PDF-"
    assert [e["tipo"] for e in eventi][-1] == "fine" and any(e["tipo"] == "valutazione" for e in eventi)
    # la regia riceve le risposte ripulite e in breve
    valutate = next(o for p, o in finti if p == "regia_valuta")["domande"]
    assert {"id", "sezione", "domanda", "esito", "risposta", "stato", "limiti_dei_controlli", "fonti"} <= set(valutate[0])


def test_rinumera_e_fonti_globali() -> None:
    domande = [{"id": "d1", "fonti": [{"numero": 1, "url": "https://a.org/x", "titolo": "A"},
                                      {"numero": 2, "url": "https://b.org", "titolo": "B"}]},
               {"id": "d2", "fonti": [{"numero": 1, "url": "https://www.b.org/", "titolo": "B"}]}]
    fonti, mappe = documento.fonti_globali(domande)
    assert [f["url"] for f in fonti] == ["https://a.org/x", "https://b.org"] and mappe["d2"] == {1: 2}
    assert documento.rinumera("Fatto [1, 2] e altro [1]; ignoto [9].", mappe["d2"]) == "Fatto [2] e altro [2]; ignoto ."


def test_limiti_di_budget_e_tempo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RICERCA_IMPOSTAZIONI", str(tmp_path / "impostazioni.json"))
    ind = Indagine.crea("argomento di prova", budget_usd=1.0, max_minuti=120, modello_regia="haiku",
                        cartella_base=tmp_path)
    ind.stato["piano"] = {"sezioni": [{"id": "s1"}] * 4}
    assert ind.puo_partire(0) == ""                     # 0,20 + riserva 0,18 ≤ 1 $
    ind.stato["speso_regia_usd"] = 0.4
    ind.stato["speso_regia_usd"] = 0.5
    assert "budget" in ind.puo_partire(1)               # 0,5 + 2×0,20 + 0,18 > 1 $
    ind.stato["limiti"]["senza_limiti"] = True
    assert ind.puo_partire(5) == ""
    ind.ferma()
    assert ind.puo_partire(0) == "indagine fermata"


def test_argomento_e_modello_controllati(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="argomento"):
        Indagine.crea("  ", cartella_base=tmp_path)
    with pytest.raises(ValueError, match="Modello"):
        Indagine.crea("argomento valido", modello_regia="gpt", cartella_base=tmp_path)
