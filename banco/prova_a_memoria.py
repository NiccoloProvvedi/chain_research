"""Linea di base: il modello risponde a memoria (senza ricerca) alle 40 domande; stesso giudice del banco.

Uso: venv/bin/python banco/prova_a_memoria.py haiku|sonnet|opus (1/11/2026: 9, 18 e 24 su 40)."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RADICE))
sys.path.insert(0, str(RADICE / "banco"))

import giudice  # noqa: E402

from ricerca_catena.modello import claude_json  # noqa: E402

ISTRUZIONI = ("Rispondi alla DOMANDA a memoria, senza strumenti. Ragiona brevemente se serve, poi chiudi con una riga "
              "«**Risposta:** <un solo valore>». Se non sei sicuro, dai comunque la risposta più probabile.")


def una(d: dict, modello: str) -> dict:
    try:
        testo = str(claude_json.chiedi(ISTRUZIONI, f"DOMANDA: {d['domanda']}", None, "a_memoria", modello=modello)[0])
    except claude_json.ErroreClaude as errore:
        testo = f"ERRORE {errore}"
    finale = giudice.estrai(d["domanda"], giudice.risposta_breve(testo))
    corretta, _ = giudice.giudica(d["domanda"], d["risposta"], finale)
    return {"seme": d["seme"], "finale": finale, "attesa": d["risposta"], "corretta": corretta, "testo": testo[-600:]}


def main() -> None:
    modello = sys.argv[1]
    domande = json.loads((RADICE / "banco" / "domande.json").read_text(encoding="utf-8"))["domande"][:40]
    with ThreadPoolExecutor(4) as esecutore:
        esiti = list(esecutore.map(lambda d: una(d, modello), domande))
    cartella = RADICE / "risultati" / "banco"
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / f"a_memoria_{modello}.json").write_text(json.dumps(esiti, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    print(modello, sum(e["corretta"] for e in esiti), "/", len(esiti))
    for e in esiti:
        print("✓" if e["corretta"] else "✗", e["seme"], "|", e["finale"][:60], "| attesa:", e["attesa"])


if __name__ == "__main__":
    main()
