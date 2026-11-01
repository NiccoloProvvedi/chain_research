"""Linea di base: il modello risponde a memoria (senza ricerca) alle 40 domande; stesso giudice del banco.
Uso: venv/bin/python test/prova_a_memoria.py haiku|sonnet|opus (1/11: 9, 18 e 24 su 40)."""
import json, sys
from pathlib import Path
PROGETTO = Path(__file__).resolve().parents[1]
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(PROGETTO)); sys.path.insert(0, str(PROGETTO / "ponte"))
sys.path.insert(0, str(PROGETTO / "test")); sys.path.insert(0, str(PROGETTO / "banco_prova"))
import prova_banco, filtra_domande
from orchestratore import claude_json
prova_banco.GIUDICE.update(tipo="claude", modello="haiku")
modello = sys.argv[1]
D = json.load(open(PROGETTO / "banco_prova" / "domande.json"))["domande"][:40]
ISTR = ("Rispondi alla DOMANDA a memoria, senza strumenti. Ragiona brevemente se serve, poi chiudi con una riga "
        "«**Risposta:** <un solo valore>». Se non sei sicuro, dai comunque la risposta più probabile.")
def una(d):
    testo, costo = "", 0.0
    try:
        r = claude_json.chiedi(ISTR, f"DOMANDA: {d['domanda']}", None, "a_memoria", modello=modello)
        testo = str(r[0])
    except claude_json.ErroreClaude as e:
        testo = f"ERRORE {e}"
    finale = prova_banco.estrai_risposta(d["domanda"], prova_banco.risposta_breve(testo), "")
    ok, deciso = filtra_domande.giudica(d["domanda"], d["risposta"], finale, prova_banco.giudice())
    return {"seme": d["seme"], "finale": finale, "attesa": d["risposta"], "corretta": ok, "testo": testo[-600:]}
with ThreadPoolExecutor(4) as ex:
    esiti = list(ex.map(una, D))
json.dump(esiti, open(PROGETTO / "risultati-test" / "banco" / f"a_memoria_{modello.replace('/', '_')}.json", "w"), ensure_ascii=False, indent=1)
print(modello, sum(e["corretta"] for e in esiti), "/", len(esiti))
for e in esiti: print(("✓" if e["corretta"] else "✗"), e["seme"], "|", e["finale"][:60], "| attesa:", e["attesa"])
