"""Al posto di `python -m ricerca_catena.domanda`: stesso protocollo, comportamento scelto dal testo della domanda."""
import json
import sys
import time


def scrivi(oggetto: dict) -> None:
    print(json.dumps(oggetto), flush=True)


domanda = json.loads(sys.stdin.readline())["domanda"]
if "muore" in domanda:
    scrivi({"evento": {"tipo": "costo", "speso_usd": 0.12}})
    sys.exit(1)
if "limite" in domanda:
    scrivi({"errore": "Limite di sessione di Claude raggiunto.", "limite_sessione": 600.0, "costo_usd": 0.03})
    sys.exit(3)
if "lenta" in domanda:
    scrivi({"evento": {"tipo": "costo", "speso_usd": 0.05}})
    time.sleep(60)
scrivi({"evento": {"tipo": "costo", "speso_usd": 0.02}})
scrivi({"esito": {"risposta": "**Risposta:** 1973 [1].\n\n**Fiducia:** alta", "valore": "1973", "trovata": True,
                  "confermata": True, "fonti": [{"numero": 1, "titolo": "A", "url": "https://esempio.org/a"}],
                  "costo_usd": 0.02, "secondi": 1, "pagine_lette": 1, "motivo_fine": "catena completa",
                  "stato": "sostenuta", "catena": {"controlli": {"motivi": ["il valore viene da una sola fonte"]},
                             "prove": [{"url": "https://esempio.org/a", "riga": "Nato nel 1973"}]},
                  "pensatore": {"modelli": ["claude-finto-1"]}}})
