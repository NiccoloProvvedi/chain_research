"""Stands in for `python -m chain_research.question`: same protocol, behaviour chosen by the text of the question."""
import json
import sys
import time


def write(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


question = json.loads(sys.stdin.readline())["question"]
if "dies" in question:
    write({"event": {"kind": "cost", "spent_usd": 0.12}})
    sys.exit(1)
if "limit" in question:
    write({"error": "Claude session limit reached.", "session_limit": 600.0, "cost_usd": 0.03})
    sys.exit(3)
if "slow" in question:
    write({"event": {"kind": "cost", "spent_usd": 0.05}})
    time.sleep(60)
write({"event": {"kind": "cost", "spent_usd": 0.02}})
write({"outcome": {"answer": "**Answer:** 1973 [1].\n\n**Confidence:** high", "value": "1973", "found": True,
                  "confirmed": True, "sources": [{"number": 1, "title": "A", "url": "https://example.org/a"}],
                  "cost_usd": 0.02, "seconds": 1, "read_pages": 1, "end_reason": "chain complete",
                  "status": "supported", "chain": {"checks": {"reasons": ["the value comes from a single source"]},
                             "proofs": [{"url": "https://example.org/a", "row": "Born in 1973"}]},
                  "thinker": {"models": ["claude-fake-1"]}}})
