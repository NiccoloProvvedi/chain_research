"""Al posto di `python -m chain_research.question`: stesso protocollo, comportamento scelto dal testo della domanda."""
import json
import sys
import time


def write(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


question = json.loads(sys.stdin.readline())["question"]
if "muore" in question:
    write({"event": {"kind": "cost", "spent_usd": 0.12}})
    sys.exit(1)
if "limit" in question:
    write({"error": "Limite di sessione di Claude raggiunto.", "session_limit": 600.0, "cost_usd": 0.03})
    sys.exit(3)
if "lenta" in question:
    write({"event": {"kind": "cost", "spent_usd": 0.05}})
    time.sleep(60)
write({"event": {"kind": "cost", "spent_usd": 0.02}})
write({"outcome": {"answer": "**Risposta:** 1973 [1].\n\n**Fiducia:** alta", "value": "1973", "found": True,
                  "confirmed": True, "sources": [{"number": 1, "title": "A", "url": "https://esempio.org/a"}],
                  "cost_usd": 0.02, "seconds": 1, "read_pages": 1, "end_reason": "catena completa",
                  "status": "supported", "chain": {"checks": {"reasons": ["il valore viene da una sola fonte"]},
                             "proofs": [{"url": "https://esempio.org/a", "row": "Nato nel 1973"}]},
                  "thinker": {"models": ["claude-finto-1"]}}})
