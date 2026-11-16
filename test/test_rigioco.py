"""Le catene registrate dal banco, ripercorse senza rete né Claude: risposta, valore e azioni devono restare uguali.

Registrazione: `banco/banco.py --nome X --registra risultati/rigioco`. La cartella è fuori da git (decine di MB): senza
registrazioni questi test si saltano. Un prompt cambiato cambia le richieste: allora si registra di nuovo.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ricerca_catena import RADICE
from ricerca_catena.modello import rigioco
from ricerca_catena.prove.concetti import Concetti
from ricerca_catena.web.raccolta import BLOCCO, Raccolta

CATENE = sorted((RADICE / "risultati" / "rigioco").glob("*/atteso.json"))


@pytest.mark.rigioco
@pytest.mark.parametrize("file", CATENE or [None], ids=[f.parent.name for f in CATENE] or ["nessuna"])
def test_catena_ripercorsa_uguale(file: Path | None, tmp_path: Path) -> None:
    if file is None:
        pytest.skip("nessuna catena registrata")
    superata = file.parent / "da_registrare.txt"
    if superata.is_file():  # il codice è cambiato apposta su questa catena: va registrata di nuovo (costa)
        pytest.skip(f"da registrare di nuovo: {superata.read_text(encoding='utf-8').strip()[:200]}")
    atteso = json.loads(file.read_text(encoding="utf-8"))
    # Le catene registrate prima dei controlli con il modello (11/11) non ne hanno le richieste.
    atteso["opzioni"].setdefault("controlli_modello", False)
    atteso["opzioni"]["seconda_ricerca"] = False   # le registrazioni sono di una ricerca sola
    richiesta = {"domanda": atteso["domanda"], "opzioni": atteso["opzioni"], "cartella_pezzi": str(tmp_path / "pezzi"),
                 "profilo": str(tmp_path / "profilo")}
    uscita = subprocess.run([sys.executable, "-m", "ricerca_catena.domanda"], cwd=RADICE, capture_output=True, text=True,
                            input=json.dumps(richiesta, ensure_ascii=False) + "\n", timeout=300,
                            env={**os.environ, "RICERCA_RIGIOCA": str(file.parent), "RICERCA_OGGI": atteso["oggi"],
                                 "PYTHONHASHSEED": "0"})
    messaggi = [json.loads(r) for r in uscita.stdout.splitlines()]
    esito = next((m["esito"] for m in messaggi if "esito" in m), None)
    assert esito is not None, [m for m in messaggi if "errore" in m] or uscita.stderr[-800:]
    assert (esito["valore"], esito["trovata"], esito["motivo_fine"]) == (atteso["valore"], atteso["trovata"],
                                                                        atteso["motivo_fine"])
    # La riga «Fiducia» la scrive il programma dallo stato dei controlli: una regola dello stato cambiata la cambia,
    # e ha i suoi test (test_validazione.py). Qui conta che la catena e il testo scritto restino uguali.
    senza_fiducia = lambda testo: re.sub(r"\*\*Fiducia:?\*\*:?[^\n]*", "", testo or "")  # noqa: E731
    assert esito["catena"]["azioni"] == atteso["azioni"]
    assert senza_fiducia(esito["risposta"]) == senza_fiducia(atteso["risposta"])


def test_registra_e_rigioca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RICERCA_REGISTRA", str(tmp_path))
    risposte = iter(["prima", "seconda"])
    assert [rigioco.attraverso("t", "x", lambda: next(risposte)) for _ in range(2)] == ["prima", "seconda"]

    def rotta():
        raise ValueError("guasto")
    with pytest.raises(ValueError):
        rigioco.attraverso("t", "y", rotta)
    monkeypatch.delenv("RICERCA_REGISTRA")
    monkeypatch.setenv("RICERCA_RIGIOCA", str(tmp_path))
    monkeypatch.setattr(rigioco, "_letture", type(rigioco._letture)())
    vietato = lambda: pytest.fail("nel rigioco non si esce")  # noqa: E731
    assert [rigioco.attraverso("t", "x", vietato) for _ in range(2)] == ["prima", "seconda"]
    with pytest.raises(rigioco.FallitaAllaRegistrazione, match="guasto"):
        rigioco.attraverso("t", "y", vietato)
    with pytest.raises(rigioco.RichiestaNonRegistrata):
        rigioco.attraverso("t", "x", vietato)  # una volta in più di quelle registrate
    with pytest.raises(rigioco.RichiestaNonRegistrata):
        rigioco.attraverso("t", "mai vista", vietato)


def test_oggi_fissato(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RICERCA_OGGI", "2026-01-02")
    assert rigioco.oggi() == "2026-01-02" and Raccolta("domanda").oggi == "2026-01-02"


def test_blocchi_salvati_in_ordine_di_lettura(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il primo blocco finisce per ultimo: il grafo deve comunque averlo per primo."""
    r = Raccolta("domanda", opzioni={"modello_claude": "haiku"})
    r.concetti = Concetti(tmp_path / "c.sqlite")

    def chiedi(_punto, _regole, testo, _schema=None):
        if "parte 1 di" in testo:
            time.sleep(0.3)
        return {"appunti": "- fatto", "pertinenza": 3, "concetti": [{"nome": "Alfa", "frase": "Alfa"}]}
    monkeypatch.setattr(r.pensatore, "chiedi", chiedi)
    r._annota(1, "https://esempio.org/a", "A", "Alfa " * (BLOCCO // 2))
    r._valuta_completati(attendi=True)
    numeri = [x[0] for x in r.concetti.grafo.db.execute("SELECT numero FROM segmento ORDER BY id")]
    assert numeri == sorted(numeri) and len(numeri) >= 2


def test_la_scrittura_si_rigioca_anche_con_un_ingresso_cambiato(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ricerca_catena.modello import claude_json
    riga = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "result": "TESTO"})
    monkeypatch.setenv("RICERCA_REGISTRA", str(tmp_path))
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: riga)
    assert claude_json.chiedi("regole", "ingresso di allora", None, "scrittura", modello="haiku")[0] == "TESTO"
    assert claude_json.chiedi("regole", "una lettura", None, "lettura", modello="haiku")[0] == "TESTO"
    monkeypatch.delenv("RICERCA_REGISTRA")
    monkeypatch.setenv("RICERCA_RIGIOCA", str(tmp_path))
    monkeypatch.setattr(rigioco, "_letture", type(rigioco._letture)())
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: pytest.fail("nel rigioco non si esce"))
    assert claude_json.chiedi("regole", "ingresso con lo stato di adesso", None, "scrittura", modello="haiku")[0] == "TESTO"
    with pytest.raises(rigioco.RichiestaNonRegistrata):          # solo scrittura e revisione: una lettura cambiata resta un errore
        claude_json.chiedi("regole", "un'altra lettura", None, "lettura", modello="haiku")
    assert claude_json.chiedi("regole", "ingresso di allora", None, "scrittura", modello="haiku")[0] == "TESTO"
    monkeypatch.setenv("RICERCA_REGISTRA", str(tmp_path))        # due scritture registrate: non si sa quale, errore
    monkeypatch.delenv("RICERCA_RIGIOCA")
    monkeypatch.setattr(claude_json, "_esegui", lambda *_a: riga)
    claude_json.chiedi("regole", "seconda scrittura", None, "scrittura", modello="haiku")
    monkeypatch.delenv("RICERCA_REGISTRA")
    monkeypatch.setenv("RICERCA_RIGIOCA", str(tmp_path))
    with pytest.raises(rigioco.RichiestaNonRegistrata, match="2 risposte"):
        claude_json.chiedi("regole", "terzo ingresso", None, "scrittura", modello="haiku")
