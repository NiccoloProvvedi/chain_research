# Chain Research

[![test](https://github.com/NiccoloProvvedi/chain_research/actions/workflows/test.yml/badge.svg)](https://github.com/NiccoloProvvedi/chain_research/actions/workflows/test.yml)

**Web investigations guided by Claude, with verified answers and a PDF report citing every source.**

You type a topic. Claude splits it into sections and precise questions; each question is answered by a program that
follows the evidence from one page to the next (the *chain search*) and checks every quotation; each answer gets a
**status** that says how reliable it is; at the end you get a complete document in which only confirmed answers are
stated as facts.

- **Evidence checked by the program**: every value must be written in the row of the page that is cited for it.
- **A status for every answer**: verified, supported, contested, uncertain or not found, decided by the program, not
  by the model that writes the text.
- **A cautious document**: uncertain answers stay out of the main text, in "Open points" boxes; every sentence of the
  sections is checked again against the answers.
- **Local application**: desktop window, investigations that can be stopped and resumed, spending and time limits.

## Contents

- [How it works](#how-it-works)
- [How reliable the answers are](#how-reliable-the-answers-are)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Security and privacy](#security-and-privacy)
- [Project structure](#project-structure)
- [Development and tests](#development-and-tests)
- [Known limitations](#known-limitations)
- [Documentation](#documentation)

## How it works

```
topic ─► plan ─► questions ─► chain search ─► checks ─► status ─┐
           ▲                                                     │
           └──────────── coverage evaluation ◄───────────────────┘
                                   │
                                   ▼
                  document (sentences re-checked) ─► PDF
```

1. **Plan.** Claude (Haiku, Sonnet or Opus, your choice) prepares the title, the sections and the first questions.
   Each question asks for a single verifiable datum: a date, a number, a name, a place, an event.
2. **Chain search.** For each question a program starts from what is certain, follows the links between Wikipedia
   articles and web pages, and uses Claude Haiku only to read a few numbered rows at a time. Questions run in
   parallel (three by default), each with its own browser.
3. **Checks and status.** The program verifies the chain it found and a second model re-reads it looking for the
   error (see [How reliable the answers are](#how-reliable-the-answers-are)). An uncertain answer is searched a second
   time, from scratch.
4. **Evaluation.** After each round Claude rates the coverage of every section, taking the status of the answers into
   account, and adds questions on the gaps and on the facts to confirm. The cycle stops when the knowledge is solid,
   when coverage stops growing, when the budget or the time is used up, or after 12 rounds.
5. **Document.** Claude writes each section from the answers; every sentence is checked against the answers and the
   unsupported ones are rewritten or removed. Then come the summary, key points, conclusions, limitations, an appendix
   with every question and its status, the method and the list of sources. The PDF is printed by the project's
   browser.

The tool always writes in English, whatever language the topic is written in.

## How reliable the answers are

Before an answer becomes a fact in the document, it goes through program checks and an independent review:

- the value must be written in the cited row, and the row must be about the right entity;
- the chain cannot come back to an entity it has already passed through;
- a search-result snippet (a page that was not read) confirms nothing;
- the value is read a second time and looked for in another source;
- a second model reviews, twice, the question, the chain and the rows, looking for the clue that does not fit, and
  checks the premise of the question: if the premise is false (a person or a work that does not exist) the answer is
  "not found".

| Status | Meaning | In the document |
|---|---|---|
| **verified** | read twice, found again in another source, chain reviewed without objections | as a fact |
| **supported** | no objection, but a single source or a check not done | as a fact, with the limitation stated |
| **contested** | the sources, or two independent searches, give different values | with both versions |
| **uncertain** | an unconfirmed link or an objection from the review | only in the "Open points" boxes |
| **not found** | no value, or a false premise in the question | among the limitations |

### Measurements

Measured in November 2026 on questions never used to tune the tool, with expected answers checked against
Wikipedia. The English version was measured once on each question set (24 Nov); the Italian figures are the last runs
before the translation.

| Measure | English version | Italian version |
|---|---|---|
| Answers stated as fact (verified or supported) that are correct | **33 of 33** (holdout and confirmation) | 54 of 55 over three holdout runs |
| Correct answers, holdout questions | 23 of 27 (85%) | 26 of 27; 70% to 96% across runs |
| Unanswerable questions (invented entities or relations) ending in "not found" | 15 of 20; one sourced but wrong value stated as fact | 19 of 20; none stated as fact |
| Document sentences fully supported by the answers | not measured yet | 89% |

The differences between the two versions are within the run-to-run variation of the search (none is statistically
significant on these samples).

Method, test plan and the results of every run are in [`docs/validation.md`](docs/validation.md); each run has a line
in [`bench/LOG.md`](bench/LOG.md).

## Requirements

- Linux (the only platform tested);
- [Claude Code](https://claude.com/claude-code) (`claude`) logged in to your account: the calls to Claude use that
  account's subscription;
- [uv](https://docs.astral.sh/uv/) and Python 3.12;
- Node.js 22 or later, with npm.

## Installation

```bash
git clone https://github.com/NiccoloProvvedi/chain_research.git
cd chain_research

uv python install 3.12
uv venv venv --python 3.12
VIRTUAL_ENV=venv uv pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=browser/engines venv/bin/playwright install chromium

cd app && npm install
```

## Usage

```bash
./start.sh
```

1. In the left column, type the topic, choose the model and set budget and time (or "No spending or time limit"),
   then **Start the investigation**.
2. On the right you follow the work live: sections with their coverage, questions with answer and status, activity.
3. At the end, **Open the PDF**.

A stopped or interrupted investigation can be resumed with **Resume**, also with new limits; **Write the document with
what is there** produces the PDF before the end. Each investigation lives in `investigations/<date>-<topic>/`
(`status.json`, `events.jsonl`, `document.html`, `document.pdf`).

**Indicative costs and times** (real investigations, November 2026): $0.08–0.20 and 6–9 minutes per question; a
complete investigation of 30–40 questions costs about $4–5 of subscription usage and takes one to four hours.

## Configuration

From the **Settings** button (saved in `config/settings.json`, excluded from git):

| Setting | Default | Description |
|---|---|---|
| Director model | Sonnet | plan, evaluation and document |
| Budget and time | $5, 2 hours | default limits of an investigation; alternatively no limit |
| Questions in parallel | 3 | from 1 to 4 |
| Chain model | Haiku | reads the pages in the chain search |
| Review model | Sonnet | re-reads the chain looking for errors |
| Budget, pages and minutes per question | $0.50, 30, 35 | limits of a single question |
| Wikipedia contact | Claude account email | see [Security and privacy](#security-and-privacy) |

## Security and privacy

- **Claude without tools.** Every call uses `claude -p --tools ""`, with no MCP servers and no memory: the model
  cannot browse, read files or run commands. It receives text and answers in a fixed format.
- **Page text is data.** Sentences addressed to a model are removed and the text travels between delimiters.
- **Isolated browser.** The research browser does not use your accounts, starts empty for every question, and all
  its traffic goes through a local proxy that refuses the local computer and the home network, also after a redirect
  or through a WebSocket.
- **Local server.** The application server listens only on `127.0.0.1`, with a key known only to the window.
- **Wikipedia contact.** Wikipedia asks API users for a contact (200 requests per minute with a contact, 10 without).
  If the field is empty, the email of the Claude account is used; in the settings you can give another address or
  switch it off. The contact is sent only in requests to `wikipedia.org` and is stored in `config/secrets.json` with
  permissions 600.

## Project structure

| Path | Contents |
|---|---|
| `chain_research/investigation.py` | investigation cycle: plan, parallel questions, evaluation, limits |
| `chain_research/question.py` | one question in a separate process, with a second search for uncertain answers |
| `chain_research/document.py` | document: sections with sentence checks, frame, sources, HTML and PDF |
| `chain_research/web/chain.py` | chain search: plan, frontier, candidates, verification, value, review |
| `chain_research/web/validation.py` | checks on the results and answer status |
| `chain_research/web/retry.py` | independent second search for uncertain answers |
| `chain_research/web/collector.py` | web searches, page reading, limits of a question |
| `chain_research/web/sources/` | Wikipedia as a graph of linked rows; safe HTTP client |
| `chain_research/web/proxy.py`, `browser.py` | network proxy and isolated browser |
| `chain_research/model/` | calls to Claude, budget, text cleaning, prompts, recording and replay |
| `chain_research/server.py`, `app/` | local server and Electron window |
| `bench/` | test bench: question sets, judge, statistics, reports, run log |
| `test/` | automated tests |
| `docs/` | validation of the results and paper on the method |

## Development and tests

```bash
VIRTUAL_ENV=venv uv pip install -r requirements-dev.txt

venv/bin/python -m pytest -q -m "not network and not claude"   # full suite, no Internet and no Claude
venv/bin/python -m pytest -q -m browser                         # only the tests with the local browser
venv/bin/ruff check chain_research bench test                   # static analysis
venv/bin/python bench/sabotage.py                               # every disabled check must make a test fail
```

The suite (over 500 tests) runs on every push on GitHub Actions. The chains recorded in `results/replay/` are replayed
without network or Claude with `pytest -m replay`.

**Test bench.** Four question sets with separate roles: `questions.json` (40, used to tune the chain),
`questions_holdout.json` (36, control), `questions_confirmation.json` (23, final confirmation) and
`questions_unanswerable.json` (20, questions with no answer). Runs use Claude and the subscription:

```bash
venv/bin/python bench/bench.py --name trial --questions bench/questions_holdout.json --parallel 1
venv/bin/python bench/report.py <baseline run> trial   # comparison, precision per status, decision rule
```

## Known limitations

- **Search variability.** The same question can follow a different path and give a different value from one run to
  the next; the answer status takes this into account, but the share of correct answers varies between runs.
- **Kind of questions.** The measurements concern chains of facts (dates, places, numbers) that can be reconstructed
  mostly from Wikipedia, on samples of a few dozen questions.
- **Document writing.** About one sentence in ten adds nuances that the answers do not contain.
- **External dependencies.** Web search reads the result pages of DuckDuckGo and Bing, which can change.
- **Cost and duration.** A complete investigation takes hours and a few dollars of subscription usage.

## Documentation

- [`docs/validation.md`](docs/validation.md): how the results are checked, test plan, results of every run.
- [`docs/paper/paper.pdf`](docs/paper/paper.pdf): the chain-search method as it was on 1 November 2026 (Italian
  version). Its figures are measured on the questions used to tune it and are therefore higher than those on new
  questions.
- [`bench/LOG.md`](bench/LOG.md): log of every test bench run.
