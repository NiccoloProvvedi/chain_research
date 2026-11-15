"""Ciclo di vita di un'indagine con processi veri (domanda finta, `claude` finto): fermare, riprendere, morire, limiti."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ricerca_catena import RADICE, documento, indagine as modulo
from ricerca_catena.indagine import Indagine, blocca
from test_server import ambiente  # noqa: F401 - fixture

FINTA = Path(__file__).parent / "fixtures" / "domanda_finta.py"
SOLIDA = {"sezioni": [{"id": "s1", "copertura": 3, "nota": ""}], "conoscenza_solida": True, "motivo": "basta",
          "nuove_domande": [], "nuove_sezioni": []}
PIANO = {"titolo": "Titolo", "obiettivo": "Obiettivo", "sezioni": [{"id": "s1", "titolo": "Uno", "scopo": "x"}]}


@pytest.fixture
def ind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Indagine:
    Path(os.environ["RICERCA_IMPOSTAZIONI"]).write_text(json.dumps({"cartella_profili": str(tmp_path / "profili")}))
    monkeypatch.setattr(Indagine, "_comando_domanda", staticmethod(lambda: [sys.executable, str(FINTA)]))
    monkeypatch.setattr(modulo.Regia, "chiedi", lambda self, punto, *_a, **_k: SOLIDA)
    monkeypatch.setattr(documento, "scrivi", lambda _i: {"pdf": "x"})
    i = Indagine.crea("argomento di prova", cartella_base=tmp_path / "indagini")
    i.stato.update(piano=PIANO, giro=1)
    return i


def _domande(i: Indagine, *testi: str, stato: str = "in_attesa") -> None:
    i.stato["domande"] = [{"id": f"d{n}", "giro": 1, "sezione": "s1", "domanda": t, "motivo": "", "stato": stato}
                          for n, t in enumerate(testi, 1)]


def _aspetta(condizione, secondi: float = 15) -> None:
    fine = time.monotonic() + secondi
    while not condizione():
        assert time.monotonic() < fine, "condizione non raggiunta"
        time.sleep(0.02)


def test_domanda_finita_con_modello_e_conferma(ind: Indagine) -> None:
    _domande(ind, "esito normale")
    ind.esegui()
    d = ind.stato["domande"][0]
    assert d["stato"] == "risposta" and d["confermata"] and d["modelli"] == ["claude-finto-1"] and ind.speso() == 0.02
    assert (d["stato_risposta"], d["motivi_stato"]) == ("sostenuta", ["il valore viene da una sola fonte"])
    assert d["prove"] == [{"url": "https://esempio.org/a", "riga": "Nato nel 1973"}]


def test_domanda_morta_conta_il_costo(ind: Indagine) -> None:
    _domande(ind, "questa muore")
    ind.esegui()
    d = ind.stato["domande"][0]
    assert d["stato"] == "errore" and "processo chiuso" in d["errore"] and ind.speso() == pytest.approx(0.12)


def test_riprendere_rifa_le_domande_in_errore(ind: Indagine) -> None:
    _domande(ind, "esito normale", stato="errore")
    ind.stato["domande"][0]["errore"] = "vecchio"
    ind.esegui()
    assert ind.stato["domande"][0]["stato"] == "risposta" and "errore" not in ind.stato["domande"][0]


def test_limite_di_sessione_ferma_senza_dire_non_trovata(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    scritto = []
    monkeypatch.setattr(documento, "scrivi", lambda _i: scritto.append(1))
    _domande(ind, "qui il limite")
    ind.esegui()
    assert ind.stato["stato"] == "fermata" and "Limite di sessione" in ind.stato["motivo_fine"] and not scritto
    assert ind.stato["domande"][0]["stato"] == "in_attesa" and ind.speso() == pytest.approx(0.03)


def test_limite_durante_la_regia(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    def limite(*_a, **_k):
        raise modulo.claude_json.LimiteSessione(600)
    monkeypatch.setattr(modulo.Regia, "chiedi", limite)
    ind.stato["piano"] = None
    ind.esegui()
    assert ind.stato["stato"] == "fermata" and "Riprendi" in ind.stato["motivo_fine"]


def test_fermare_chiude_la_domanda_lenta(ind: Indagine) -> None:
    _domande(ind, "questa è lenta")
    t = threading.Thread(target=ind.esegui)
    t.start()
    _aspetta(lambda: ind._processi and ind.speso() == 0 and ind.stato["domande"][0]["stato"] == "in_corso")
    time.sleep(0.5)  # la domanda ha scritto il suo costo
    ind.ferma()
    t.join(15)
    assert not t.is_alive() and ind.stato["stato"] == "fermata" and ind.stato["domande"][0]["stato"] == "in_attesa"
    assert ind.speso() == pytest.approx(0.05)


def test_fermare_interrompe_la_chiamata_della_regia(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    cartella = tmp_path / "bin"
    cartella.mkdir()
    (cartella / "claude").write_text("#!/bin/sh\nexec sleep 60\n")
    (cartella / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{cartella}{os.pathsep}{os.environ['PATH']}")
    ind.stato["piano"] = None
    t = threading.Thread(target=ind.esegui)
    t.start()
    _aspetta(lambda: modulo.claude_json._in_corso)
    inizio = time.monotonic()
    ind.ferma()
    t.join(10)
    assert not t.is_alive() and time.monotonic() - inizio < 5 and ind.stato["stato"] == "fermata"


def test_guardia_chiude_la_domanda_bloccata(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo, "MARGINE_GUARDIA_S", 0)
    monkeypatch.setattr(Indagine, "_minuti_domanda", lambda _s: 0.01)
    _domande(ind, "questa è lenta")
    ind.esegui()
    assert ind.stato["domande"][0]["stato"] == "errore" and "tempo massimo" in ind.stato["domande"][0]["errore"]


def test_errore_nel_thread_non_lascia_la_domanda_in_corso(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    def rotta(*_a):
        raise KeyError("rotta")
    monkeypatch.setattr(Indagine, "_una_domanda", rotta)
    _domande(ind, "esito normale")
    ind.esegui()
    assert ind.stato["domande"][0]["stato"] == "errore" and "KeyError" in ind.stato["domande"][0]["errore"]


def test_stderr_della_domanda_finisce_nel_registro(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Indagine, "_comando_domanda",
                        staticmethod(lambda: [sys.executable, "-c", "import sys; sys.stderr.write('traccia-errore')"]))
    _domande(ind, "esito normale")
    ind.esegui()
    assert "traccia-errore" in (ind.cartella / "errori.log").read_text()


# --- nuove domande ----------------------------------------------------------------------------------------------

def _valutazione(coperture: dict, domande: list[str] | None = None, solida: bool = False) -> dict:
    return {"sezioni": [{"id": k, "copertura": v, "nota": ""} for k, v in coperture.items()], "conoscenza_solida": solida,
            "motivo": "m", "nuove_sezioni": [],
            "nuove_domande": [{"sezione": s, "domanda": f"Domanda numero {i} sulla sezione {s}?", "motivo": ""}
                              for i, s in enumerate(domande or [])]}


def test_prima_le_sezioni_meno_coperte_e_solo_quante_si_finiscono(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ind, "_quante_ancora", lambda: 2)
    scelte = ind._nuove_scelte(_valutazione({"s1": 3, "s2": 0, "s3": 1}, ["s1", "s3", "s2"]))
    assert [d["sezione"] for d in scelte] == ["s2", "s3"]


def test_quante_ancora_dal_budget(ind: Indagine) -> None:
    ind.stato["limiti"].update(budget_usd=1.0, max_minuti=1440, senza_limiti=False)
    ind.stato["domande"] = [{"costo_usd": 0.10, "secondi": 60} for _ in range(5)]
    ind.stato["speso_regia_usd"] = 0.0  # speso 0,50; riserva 0,18 (sonnet, 1 sezione); restano 0,32 → 3 domande
    assert ind._quante_ancora() == 3
    ind.stato["limiti"]["senza_limiti"] = True
    assert ind._quante_ancora() is None


def test_copertura_ferma_chiude_il_ciclo(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    risposte = iter([_valutazione({"s1": 1}, ["s1"]), _valutazione({"s1": 1}, ["s1", "s1"])])
    monkeypatch.setattr(modulo.Regia, "chiedi", lambda *_a, **_k: next(risposte))
    assert ind._valuta() is False
    assert ind._valuta() is True and ind.stato["motivo_fine"].startswith("la copertura non cresce")


def test_impostazioni_fissate_alla_creazione(ind: Indagine, monkeypatch: pytest.MonkeyPatch) -> None:
    Path(os.environ["RICERCA_IMPOSTAZIONI"]).write_text(json.dumps({"minuti_domanda": 90}))
    ind.stato["limiti"]["senza_limiti"] = True
    assert ind._minuti_domanda() == 35


# --- documento --------------------------------------------------------------------------------------------------

def _per_documento(ind: Indagine) -> None:
    ind.stato["piano"] = {**PIANO, "sezioni": [{"id": "s1", "titolo": "Uno", "scopo": "x"},
                                               {"id": "s2", "titolo": "Due", "scopo": "y"}]}
    ind.stato["domande"] = [
        {"id": f"d{n}", "giro": 1, "sezione": s, "domanda": f"Domanda {n}", "stato": "risposta", "confermata": True,
         "risposta": "**Risposta:** dato [1].\n\n**Fiducia:** alta", "costo_usd": 0.1,
         "fonti": [{"numero": 1, "url": f"https://esempio.org/{n}", "titolo": f"F{n}"},
                   {"numero": 2, "url": f"https://altro.org/{n}", "titolo": f"G{n}"}]}
        for n, s in ((1, "s1"), (2, "s2"))]


def test_documento_non_rifa_le_sezioni_gia_pagate(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _per_documento(ind)
    chiamate: list[str] = []
    guasto = {"attivo": True}

    def chiedi(self, punto, oggetto, schema, **_k):
        chiamate.append(oggetto.get("sezione", {}).get("id", punto))
        if punto == "documento_sezione":
            if oggetto["sezione"]["id"] == "s2" and guasto["attivo"]:
                raise modulo.claude_json.ErroreClaude("guasto")
            return "Testo della sezione [1]."
        if punto == "documento_controllo":
            return {"frasi": []}
        return {"sintesi": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri [1].",
                "punti_chiave": ["Punto [1]."], "conclusioni": "Fine [1].", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    with pytest.raises(modulo.claude_json.ErroreClaude):
        documento.scrivi(ind)
    assert sorted(chiamate) == ["documento_controllo", "s1", "s2"]   # s1 scritta e riletta, s2 fallita
    chiamate.clear()
    guasto["attivo"] = False
    esito = documento.scrivi(ind)
    assert chiamate == ["s2", "documento_controllo", "documento_cornice"] and esito["sezioni"] == 2
    chiamate.clear()
    documento.scrivi(ind)
    assert chiamate == []  # niente è cambiato, niente si riscrive
    pagina = Path(esito["html"]).read_text()
    # risposte di prima dei controlli con il modello: «sostenute», mai «verificate»
    assert "2 con risposta (2 sostenute)" in pagina and ">verificata<" not in pagina
    assert "Fonti citate" in pagina and "Altre fonti consultate" in pagina and "</p></li>" not in pagina
    assert 'value="3"' in pagina


def _risposta(n: int, sezione: str, valore: str, **altro) -> dict:
    return {"id": f"d{n}", "giro": 1, "sezione": sezione, "domanda": f"Domanda {n}", "stato": "risposta", "costo_usd": 0.1,
            "valore": valore, "risposta": f"**Risposta:** {valore} [1].\n\n**Fiducia:** alta — tutto bene.",
            "fonti": [{"numero": 1, "url": f"https://esempio.org/{n}", "titolo": f"F{n}"}], **altro}


def test_il_documento_segue_lo_stato_delle_risposte(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T10 (docs/solidita-e-validazione.md): una risposta «incerta» non arriva mai a chi scrive le sezioni né alla
    sintesi, sta in un riquadro scritto dal programma; una «contestata» arriva con i motivi; la copertina conta gli stati."""
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _per_documento(ind)
    ind.stato["domande"] = [
        _risposta(1, "s1", "ANNO-VERIFICATO", stato_risposta="verificata", motivi_stato=[], confermata=True),
        _risposta(2, "s1", "NOME-INCERTO", stato_risposta="incerta", confermata=False,
                  motivi_stato=["1 anelli della catena non confermati"]),
        _risposta(3, "s2", "DATA-CONTESTATA", stato_risposta="contestata", confermata=False,
                  motivi_stato=["un'altra fonte dà 21 giugno 1885"]),
        _risposta(4, "s2", "VECCHIA-DEBOLE", confermata=False),          # indagine di prima: catena non confermata
        {"id": "d5", "giro": 1, "sezione": "s2", "domanda": "Domanda 5", "stato": "non_trovata", "fonti": [],
         "risposta": "**Risposta:** Non trovata: catena interrotta."},
    ]
    ricevuti: list[tuple[str, dict]] = []

    def chiedi(self, punto, oggetto, schema, **_k):
        ricevuti.append((punto, oggetto))
        if punto == "documento_sezione":
            return "Sezione: " + " | ".join(r["risposta"].splitlines()[0] for r in oggetto["risposte"])
        if punto == "documento_controllo":
            return {"frasi": []}
        return {"sintesi": "Sintesi dalle sezioni: " + " ".join(x["testo"] for x in oggetto["sezioni"]),
                "punti_chiave": ["Punto [1]."], "conclusioni": "Fine.", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    pagina = Path(documento.scrivi(ind)["html"]).read_text()

    al_modello = json.dumps([o for _p, o in ricevuti], ensure_ascii=False)
    assert "NOME-INCERTO" not in al_modello and "VECCHIA-DEBOLE" not in al_modello  # nemmeno nei fatti brevi
    s1 = next(o for p, o in ricevuti if p == "documento_sezione" and o["sezione"]["id"] == "s1")
    s2 = next(o for p, o in ricevuti if p == "documento_sezione" and o["sezione"]["id"] == "s2")
    assert [r["stato"] for r in s1["risposte"]] == ["verificata"]
    contestata = next(r for r in s2["risposte"] if r["stato"] == "contestata")
    assert contestata["limiti_dei_controlli"] == ["un'altra fonte dà 21 giugno 1885"]
    assert any(r["stato"] == "non trovata" for r in s2["risposte"])

    def pezzo(inizio: str, fine: str) -> str:
        return pagina[pagina.index(inizio):pagina.index(fine, pagina.index(inizio))]
    sintesi, uno, due = pezzo('<section id="sintesi"', "</section>"), pezzo('<section id="s1"', "</section>"), pezzo('<section id="s2"', "</section>")
    assert "ANNO-VERIFICATO" in sintesi and "NOME-INCERTO" not in sintesi and "VECCHIA-DEBOLE" not in sintesi
    aperti = uno[uno.index('class="aperti"'):]
    assert "Punti aperti" in aperti and "NOME-INCERTO" in aperti and "1 anelli della catena non confermati" in aperti
    assert 'href="#fonte-' in aperti and "NOME-INCERTO" not in uno[:uno.index('class="aperti"')]
    assert "VECCHIA-DEBOLE" in due[due.index('class="aperti"'):] and "DATA-CONTESTATA" in due[:due.index('class="aperti"')]
    assert "4 con risposta (1 verificata, 1 contestata, 2 incerte)" in pagina
    tabella = pezzo('<section id="domande"', "</section>")
    assert all(x in tabella for x in ("<th>Stato</th>", ">verificata<", ">contestata<", ">incerta<")) and "tutto bene" not in tabella


def test_sezione_con_sole_risposte_incerte(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _per_documento(ind)
    ind.stato["domande"] = [_risposta(1, "s1", "A", stato_risposta="sostenuta"),
                            _risposta(2, "s2", "B-INCERTO", stato_risposta="incerta", motivi_stato=["m"])]
    sezioni_scritte: list[str] = []

    def chiedi(self, punto, oggetto, schema, **_k):
        if punto == "documento_sezione":
            sezioni_scritte.append(oggetto["sezione"]["id"])
            return "Testo."
        if punto == "documento_controllo":
            return {"frasi": []}
        return {"sintesi": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri.",
                "punti_chiave": ["Punto."], "conclusioni": "Fine.", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    pagina = Path(documento.scrivi(ind)["html"]).read_text()
    assert sezioni_scritte == ["s1"] and "Nessuna risposta confermata" in pagina and "B-INCERTO" in pagina


def test_la_regia_riceve_lo_stato(ind: Indagine) -> None:
    breve = Indagine.in_breve(_risposta(1, "s1", "X", stato_risposta="contestata", motivi_stato=["un'altra fonte dà Y"]))
    assert breve["stato"] == "contestata" and breve["limiti_dei_controlli"] == ["un'altra fonte dà Y"]
    assert "tutto bene" not in json.dumps(breve, ensure_ascii=False)
    vecchia = Indagine.in_breve(_risposta(2, "s1", "X", confermata=False))
    assert vecchia["stato"] == "incerta"
    assert Indagine.in_breve({"id": "d3", "sezione": "s1", "domanda": "D", "stato": "non_trovata", "risposta": ""})["stato"] == "non trovata"


# --- controllo delle frasi delle sezioni (14/11: 15 frasi su 120 con un dettaglio aggiunto o cambiato) ------------

def test_numeri_senza_appoggio() -> None:
    appoggio = "**Risposta:** 2005 [1]. Venduti 1 234 pezzi; prezzo 20,65 euro; nato il 24 luglio 1973."
    assert documento.numeri_senza_appoggio("Fino al 1964 la presidenza fu di Schiavo [3].", appoggio) == ["1964"]
    assert documento.numeri_senza_appoggio("Nato nel 2005 [1, 12], vendette 1234 pezzi a 20.65 euro [33].", appoggio) == []
    assert documento.numeri_senza_appoggio("Le 3 sedi e i 7 soci [45].", appoggio) == []          # cifre singole: non si contano
    assert documento.numeri_senza_appoggio("Nato il 24 luglio 1973 e morto nel 2011 [2].", appoggio) == ["2011"]


def test_frasi_del_markdown() -> None:
    testo = "### Titolo\n\nPrima frase [1]. Seconda frase del 1999 [2].\n\n- voce uno [3];\n- voce due.\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert documento.frasi_del_markdown(testo) == ["Prima frase [1].", "Seconda frase del 1999 [2].", "voce uno [3];", "voce due."]
    assert documento.togli_frasi(testo, ["Seconda frase del 1999 [2].", "voce uno [3];"]) == (
        "### Titolo\n\nPrima frase [1].\n\n- voce due.\n\n| a | b |\n|---|---|\n| 1 | 2 |")


def _indagine_per_il_controllo(ind: Indagine) -> None:
    _per_documento(ind)
    ind.stato["piano"]["sezioni"] = [{"id": "s1", "titolo": "Uno", "scopo": "x"}]
    ind.stato["domande"] = [_risposta(1, "s1", "nato nel 2005", stato_risposta="verificata", motivi_stato=[])]


def test_sezione_controllata_frase_per_frase(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Una frase con un numero che nelle risposte non c'è e una segnalata da chi rilegge: si chiede una volta di
    riscrivere; quello che resta senza appoggio lo toglie il programma."""
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _indagine_per_il_controllo(ind)
    prima = "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]. Fu amato da tutti [1]."
    dopo = "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]. Ebbe successo [1]."
    chiamate: list[tuple[str, dict]] = []

    def chiedi(self, punto, oggetto, schema, **_k):
        chiamate.append((punto, oggetto))
        if punto == "documento_sezione":
            return dopo if "frasi_da_correggere" in oggetto else prima
        if punto == "documento_controllo":
            numero = next((f["numero"] for f in oggetto["frasi"] if "amato da tutti" in f["frase"]), None)
            return {"frasi": [{"numero": numero, "esito": "non sostenuta", "motivo": "un giudizio che le risposte non danno"}]
                    if numero else [{"numero": 99, "esito": "non sostenuta", "motivo": "numero che non esiste"}]}
        return {"sintesi": "Sintesi: " + " ".join(x["testo"] for x in oggetto["sezioni"]) + " " * 50,
                "punti_chiave": ["Punto [1]."], "conclusioni": "Fine.", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    pagina = Path(documento.scrivi(ind)["html"]).read_text()
    assert [p for p, _ in chiamate] == ["documento_sezione", "documento_controllo", "documento_sezione",
                                        "documento_controllo", "documento_cornice"]
    da_correggere = chiamate[2][1]["frasi_da_correggere"]
    assert [x["frase"] for x in da_correggere] == ["Nel 1999 vendette 3000 pezzi [1].", "Fu amato da tutti [1]."]
    assert "1999" in da_correggere[0]["motivo"] and chiamate[2][1]["testo_precedente"] == prima
    assert "nato nel 2005" in pagina and "Ebbe successo" in pagina
    assert "vendette 3000 pezzi" not in pagina and "amato da tutti" not in pagina     # nemmeno nella sintesi
    assert ind.stato["controllo_sezioni"]["s1"] == {"frasi": 3, "segnalate": 2, "riscritta": True, "tolte": 1}
    assert "1 tolte dal programma" in pagina
    chiamate.clear()
    documento.scrivi(ind)
    assert chiamate == []        # sezione controllata e già pagata: non si rifà


def test_sezione_senza_segnalazioni_non_si_riscrive(ind: Indagine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _indagine_per_il_controllo(ind)
    punti: list[str] = []

    def chiedi(self, punto, oggetto, schema, **_k):
        punti.append(punto)
        if punto == "documento_sezione":
            return "Il progetto è nato nel 2005 [1]."
        if punto == "documento_controllo":
            return {"frasi": []}
        return {"sintesi": "Sintesi abbastanza lunga da passare il controllo dei cinquanta caratteri [1].",
                "punti_chiave": ["Punto [1]."], "conclusioni": "Fine.", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    documento.scrivi(ind)
    assert punti == ["documento_sezione", "documento_controllo", "documento_cornice"]
    assert ind.stato["controllo_sezioni"]["s1"] == {"frasi": 1, "segnalate": 0, "riscritta": False, "tolte": 0}


def test_se_chi_rilegge_non_risponde_resta_il_controllo_del_programma(ind: Indagine, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("RICERCA_LOG", str(tmp_path / "log"))
    _indagine_per_il_controllo(ind)

    def chiedi(self, punto, oggetto, schema, **_k):
        if punto == "documento_sezione":
            return "Il progetto è nato nel 2005 [1]. Nel 1999 vendette 3000 pezzi [1]."
        if punto == "documento_controllo":
            raise modulo.claude_json.ErroreClaude("guasto")
        return {"sintesi": "Sintesi: " + " ".join(x["testo"] for x in oggetto["sezioni"]) + " " * 50,
                "punti_chiave": ["Punto [1]."], "conclusioni": "Fine.", "limiti": "Nessuno."}
    monkeypatch.setattr(modulo.Regia, "chiedi", chiedi)
    monkeypatch.setattr(documento, "stampa_pdf", lambda _h, pdf: pdf.write_bytes(b"%PDF-"))
    pagina = Path(documento.scrivi(ind)["html"]).read_text()
    assert "nato nel 2005" in pagina and "vendette 3000 pezzi" not in pagina
    assert ind.stato["controllo_sezioni"]["s1"]["tolte"] == 1


# --- processi e server ------------------------------------------------------------------------------------------

def test_blocco_esclude_un_secondo_processo(tmp_path: Path) -> None:
    primo = blocca(tmp_path)
    assert primo is not None and blocca(tmp_path) is None
    primo.close()
    assert blocca(tmp_path) is not None


def test_processo_sopravvissuto_a_un_server_precedente(ambiente) -> None:  # noqa: F811
    client, gestore, _, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    ind.stato["stato"] = "in_corso"
    ind.salva()
    ident = ind.cartella.name
    assert client.get(f"/api/indagini/{ident}").json()["stato"] == "interrotta"
    codice = ("import sys, time; from pathlib import Path; from ricerca_catena.indagine import blocca; "
              "b = blocca(Path(sys.argv[1])); print('pronto', flush=True); time.sleep(60)")
    vivo = subprocess.Popen([sys.executable, "-c", codice, str(ind.cartella)], cwd=RADICE, stdout=subprocess.PIPE, text=True)
    try:
        assert vivo.stdout.readline().strip() == "pronto"
        assert client.get(f"/api/indagini/{ident}").json()["stato"] == "in_corso" and gestore.attiva()
        assert client.post(f"/api/indagini/{ident}/riprendi", json={}).status_code == 400
        assert client.post(f"/api/indagini/{ident}/ferma", json={}).status_code == 200
        vivo.wait(10)
    finally:
        vivo.kill()
        vivo.wait()
    assert client.get(f"/api/indagini/{ident}").json()["stato"] == "interrotta" and not gestore.attiva()


def test_flusso_chiuso_dal_server_e_id_sbagliato(ambiente) -> None:  # noqa: F811
    client, gestore, _, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    ind.evento({"tipo": "fine", "stato": "fermata"})
    ind.evento({"tipo": "inizio"})
    ind.evento({"tipo": "fine", "stato": "finita"})
    with client.stream("GET", f"/api/indagini/{ind.cartella.name}/eventi", headers={"last-event-id": "abc"}) as flusso:
        righe = list(flusso.iter_lines())
    assert sum(r.startswith("data: {\"quando") for r in righe) == 3 and righe.count("event: chiuso") == 1


def test_riprendere_con_limiti_nuovi(ambiente) -> None:  # noqa: F811
    client, gestore, avviate, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    ident = ind.cartella.name
    assert client.post(f"/api/indagini/{ident}/riprendi", json={"budget_usd": "9", "max_minuti": 300}).status_code == 200
    limiti = client.get(f"/api/indagini/{ident}").json()["limiti"]
    assert limiti["budget_usd"] == 9 and limiti["max_minuti"] == 300 and avviate == [(ident, False)]
    assert client.post(f"/api/indagini/{ident}/riprendi", json={"budget_usd": 0}).status_code == 400


def test_libera_spazio(ambiente) -> None:  # noqa: F811
    client, gestore, _, _ = ambiente
    ind = Indagine.crea("argomento di prova", cartella_base=gestore.cartella)
    (ind.cartella / "pezzi").mkdir()
    assert client.post(f"/api/indagini/{ind.cartella.name}/pulisci", json={}).status_code == 200
    assert not (ind.cartella / "pezzi").exists() and (ind.cartella / "stato.json").exists()


def test_impostazioni_avanzate(ambiente) -> None:  # noqa: F811
    client, *_ = ambiente
    r = client.post("/api/impostazioni", json={"modello_catena": "sonnet", "budget_domanda_usd": "0.8", "pagine_domanda": 12,
                                               "minuti_domanda": 20}).json()
    assert (r["modello_catena"], r["budget_domanda_usd"], r["pagine_domanda"], r["minuti_domanda"]) == ("sonnet", 0.8, 12, 20)
    for sbagliata in ({"modello_catena": "gpt"}, {"pagine_domanda": 0}, {"minuti_domanda": 999}):
        assert client.post("/api/impostazioni", json=sbagliata).status_code == 400
