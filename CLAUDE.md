# Chain Research

Web investigations guided by Claude: see `README.md` for how it works and how it is organised.

## Commands

```bash
venv/bin/python -m pytest -q -m "not network and not claude"   # tests without Internet and without Claude
venv/bin/python -m pytest -q -m browser                         # only those using Chromium
venv/bin/python -m pytest -q -m replay                          # recorded chains (if present), no network, no Claude
venv/bin/python bench/sabotage.py                               # every disabled check must make a test fail
venv/bin/python bench/rejudge.py <run>                          # re-judge a saved run, without Claude
```

The bench (`bench/bench.py`) and every run with the real Claude use the subscription: ask before launching them.
Long runs start with `bench/run.sh` from a frozen copy of the code (`git worktree add --detach`, with `venv`,
`browser`, `python`, `results`, `log`, `investigations` linked): detached from the terminal, they resume where they were.
The control, confirmation and unanswerable question sets are never used to tune the chain: status rules are tuned on
the 40 questions of `bench/questions.json` and tested on saved runs with `bench/rejudge.py` and `bench/report.py`.

## Rules

- Code, comments and messages in English. The tool always writes its output in English.
- Browser traffic goes through `web/proxy.py`, HTTP requests through `web/sources/http.py`: no other way to the
  network, otherwise the block on the local computer and home network does not hold.
- `web/chain.py` is tuned on the 40 questions of `bench/questions.json`: results on those questions do not measure new
  questions.
- Every fixed defect has its own test, written before the fix.
- Checks on the results live in `web/validation.py` (plan and status in `docs/validation.md`): a new check goes into
  `bench/sabotage.py` and, if it changes a recorded chain, the reason goes into the chain's `to_record.txt`.
- Commits carry no Claude co-author line.
