# Robustness and validation of the results

> All measurements in this document were taken on the Italian version of the tool (before 24 Nov 2026); the English
> version is being re-measured.

Review of 11 Nov 2026 (branch `revisione-correzioni-e-test`). The previous review fixed the lifecycle, security,
settings and tests. This document is about the next layer: what the tool *claims* and what it has actually
*checked*. The central point is **validation of the results**: once a fact has been found, being sure that it is valid
and correct.

**Evidence used:** the code (`web/chain.py`, `web/collector.py`, `bench/judge.py`, `bench/bench.py`,
`bench/faithfulness.py`, `proofs/triples.py`, the prompts in `model/prompts/`) and the data of the runs: holdout
questions run 1 (36 questions, complete) and run 2 (14 questions, interrupted; run 3 never started), the 40 questions
(`after-review-2`).

Labels: **D** = seen in the run data, **R** = seen by reading the code, **I** = hypothesis to be checked.

---

## 1. In short: validation is weaker than the numbers suggested

| Measure | 40 tuned questions | Holdout questions (never seen) | Target |
|---|---|---|---|
| Correct answers | 34/40 = 85% | 25/36 = 69% (95%: 53–82%) | lower bound ≥ 70% |
| “Confirmed” answers that are correct | 13/13 | run 1: 10/12 = 83%; run 2: 4/7 | ≥ 95% |
| Same value on repetition | — | 11/14 = 79% | ≥ 85% |
| Same “confirmed” on repetition | — | **8/14 = 57%** | ≥ 85% |

With the corrected yardstick (phase A, done): holdout run 1 25/35 = 71% (95%: 55–84%), one still to be judged; run 2
10/12. Over the two runs together, the “confirmed” answers are correct 13/17 = 76% of the time, the unconfirmed ones
22/30 = 73%: **today the seal says nothing.**

1. **“Confirmed” is not a reliable seal.** Perfect on the tuned questions, 83% and 57% on the new ones; repeating the
   run, it changes almost one time in two. Today it only means “no link of the winning route is marked weak”
   (`collector.py`).
2. **The measuring instrument errs in both directions** (§3). The true percentage on the holdout questions is not
   known to better than about ±4 questions until the judge and the expected answers are fixed.
3. Hence two jobs, in this order: straighten the yardstick, then build the real checks.

---

## 2. What is checked today and what is not

**Checked by the program (to keep):**
- Checks on every link: two independent readings, “yes” only if they agree (`_outcomes`, `_merge_outcomes`).
- The quoted words really are in the row; the row belongs to the candidate; the exact year of the check is in the
  quote (`_apply_outcomes`, `triples.present_quote`).
- Type and range of the value (`allowed_value`); the value's row is about the final entity (`_entity_value`).
- Several routes to the same value (“confirmed by N routes”).

**Not checked:**

| # | Gap | Evidence | |
|---|---|---|---|
| V1 | **The value is never compared with its row.** The model gives the value and the row number; the program checks that the row is about the entity, not that it contains the value. | `chain.py`, `_entity_value` | R |
| V2 | **The value is read only once.** Links get two readings, the final value (the answer) gets one. | `_entity_value` | R |
| V3 | **No comparison of the value across sources.** One row from one page is enough. Aida: en.wiki 21 June 1886, expected 1885. 30 values out of 35 come from a single Wikipedia article. | holdout 1 | D |
| V4 | **A search-result snippet counts as strong evidence for a link.** PostgreSQL: the Wikipedia infobox (“Newburyport, Massachusetts”) discarded, the snippet of a page never read (“born in Milton, New Hampshire”) at 0.9 “strong”, chain “confirmed”, wrong answer. The two runs gave two different wrong cities. | holdout 1 and 2 | D |
| V5 | **Disagreement between sources is lost.** A discarded candidate that had a strong source leaves no trace: “Sources in disagreement: none”. | same case | D |
| V6 | **No check for cycles or direction along the route.** C++ run 2: Stroustrup → Wheeler → … → Burrows → **Wheeler again** (relation walked backwards: student instead of advisor), “strong” 0.88, “confirmed”, wrong. | holdout 2 | D |
| V7 | **No check of the answer against the whole question.** C++ run 1: one step short (“British physicist” never checked on the final entity). Duchy of Savoy: route “Duchy of Savoy → French First Republic → First Empire → Kingdom of Holland → Rome” called “complete chain”; value correct by chance. | holdout 1 | D |
| V8 | **Weak links inside a “complete” chain, and the writer has to answer anyway.** 14 unconfirmed answers correct out of 24, 9 wrong: a 60/40 coin shown as an answer. | `write`, data | D |
| V9 | **“Confidence” is free text from the writing model.** It does not depend on the checks. Wrong answers with “high” (Reggio Emilia) and “medium” (4). Inconsistent capitalisation, sometimes missing. The director and the sections use it as if it were a measurement. | data, `investigation.in_short` | D |
| V10 | **Sign, unit and meaning of the value are not normalised.** −2 m and 2 m; date of establishment or of entry into force; Julian or Gregorian calendar (16 and 27 May 1703). | data | D |
| V11 | **Document:** `faithfulness.py` compares the sentence with the *answers*, never with the *page*. Unconfirmed answers enter the sections with just a warning. No check that the facts in the summary are confirmed. | `faithfulness.py`, `document_section.md` | R |
| V12 | **Stale data:** the value has no date (“the passages do not say when it is from”) and is never re-read. | “Limits” rows | D |
| V13 | **“Not found” is not verified:** it may be the session limit, a blocked page or a genuine absence; today only the limit is told apart. | `collector.py` | R |

---

## 3. The yardstick is crooked (bench and judge)

| # | Problem | Evidence | |
|---|---|---|---|
| M1 | **The normalised comparison ignores the sign.** “2 m” is contained in “−2 m a.s.l.” → counted correct. | `judge.normalized_equal`, Python question in both runs | D |
| M2 | **The bench has a model extract the answer instead of using the value the program already has.** `outcome["value"]` exists; `bench.py` asks Haiku to derive it from the “Answer” row. Empty extraction = “empty” = wrong. Two questions with exactly the expected value counted wrong (Duchy of Savoy run 1, Capture of Rome run 2). | data | D |
| M3 | **Judge errors disappear.** `judge._ask` turns every `ClaudeError` (including the session limit) into "", i.e. a wrong answer. | `judge.py` | R |
| M4 | **The expected answers from Wikidata are not the truth.** At least 5 out of 36 do not match Wikipedia and need to be looked at by a person: Reggio Emilia 68 and 56 m, Aida 1885 and 1886, United Kingdom 1921 and 1801, Gorizia 2020 and 2019, Amsterdam 2 and −2 m; in addition, the premise of the PostgreSQL question (state of birth). | data | D/I |
| M5 | **Durations include the waits for the session limit** (145, 152, 159, 160 minutes): “19.9 minutes on average” is false. | `collector.py`: `seconds` uses the raw clock | D |
| M6 | **The log has no quality columns:** no precision of the confirmed answers, no share decided by the judge. | `bench.py` | R |
| M7 | Questions generated with two defensible readings (“island state… founding”); no field marks them. | data | D |

Until M1–M4 are fixed, no percentage should be quoted.

---

## 4. The design: seven checks between “found” and “shown as fact”

Principle: **the program decides, the model proposes.** Every check belongs to the program wherever possible; where a
model is needed, it is a second, independent call that tries to refute, not the one that made the proposal.

```
proposed value
  G1 value in the row        digits, date or name of the value written in the cited row       (program)
  G2 second reading          a second call must give the same normalised value                (model ×2)
  G3 sound route             no entity twice, steps = plan, direction of the relation         (program)
  G4 source quality          every link and the value proven by a page read, level ≥ B        (program)
  G5 corroboration           value in ≥ 2 independent sources, or level A and none against    (program + network)
  G6 adversarial review      another model sees question, route and rows: “which clue does not fit?”
  G7 disagreement register   every refutation from a strong source recorded and shown         (program)
→ status and score computed by the program
```

- **G1 Value in the row** (`_entity_value`). Value and row normalised (digits, months via `_MONTHS`, sign, decimal
  comma). Dates: day, month and year in the row. Numbers: same digits with the sign. Names: `present_quote`.
  If missing: value rejected, “value not in the row” in `history`. The cheapest and most useful check.
- **G2 Second reading of the value.** Like `_outcomes`: two readings, merged in a fixed order. If they disagree, move
  on to the next attempt; if they remain split, both go into the disagreement register and the status is “contested”.
- **G3 Sound route.** In `_walk_frontier`: a child with the same key (`_canonical_key`, `_entity_key`) as an ancestor
  is discarded. At the solution: route length = plan steps + 1. The plan states the `direction` of every step and the
  row of the tie must respect it (advisor *of*, not student *of*).
- **G4 Source quality.** New module `proofs/source.py`: A = Wikipedia article or infobox, official or institutional
  site, the entity's own site; B = a page read on another site; C = search-result snippet, page not read, aggregator.
  A C-level proof can propose a candidate; it cannot make a link “strong” nor carry a value.
- **G5 Corroboration.** After G1–G2 the same fact is looked for (a) in the Wikipedia article in another language
  (interlanguage links, already in `WikipediaGraph`), (b) in the pages already read, (c) in at most 2 new pages.
  Outcome: `agree`, `single source`, `disagree`. Optional (setting, off by default): the Wikidata claim on the final
  entity.
- **G6 Adversarial review.** A call with a model different from the chain's (Haiku chain → Sonnet review). It receives
  the question, the route with one row per link, and the value's row. For every clue in the question:
  `{clue, satisfied: yes / no / not proven, row}`. The program decides: a “no” with a valid row and quote → route
  “refuted”, and the chain tries the next candidate. It would have stopped C++ (the final entity is not a physicist)
  and the Duchy of Savoy.
- **G7 Disagreement register.** `Chain.conflicts`: every discarded candidate or value that had a level A or B proof,
  with the source. The “Sources in disagreement” row is written by the program.

### Status and score

In place of `confirmed` (yes/no) and the free-text “Confidence”:

| Status | Rule | Shown as |
|---|---|---|
| `verified` | G1–G4 passed, G5 agree, G6 no objections, no weak link | fact |
| `supported` | G1–G4 passed, G5 single source (level A), G6 no objections | fact, “single source” |
| `contested` | G5 disagree or register not empty | both versions |
| `uncertain` | a weak link, or G6 “not proven” on a clue | “not confirmed”, never in the summary |
| `not found` | no value, with the cause: absent, limit, blocked page | gap |

The `score` (0–1) comes from the outcomes of the checks; the weights are tuned **only** on a calibration set, never on
the holdout questions. The “Confidence” row is written by the program from the status. The director and the document
receive the status.

### Document

- The sections give as fact only `verified` and `supported` answers; `contested` with both versions; `uncertain` in
  an “open points” box. This is enforced by `document.py`, which passes the answers split by status.
- Cover page: how many answers per status.
- `faithfulness.py`, second mode: sentence → **source row** (text of the saved proof), not only → answer.
- Every value carries “read on <date>” and, if the row gives it, the date of the fact.

---

## 5. Other defects and improvements

| # | Where | Problem | |
|---|---|---|---|
| B1 | `bench/judge.py` | M1: the sign is lost in `normalize`. | D |
| B2 | `bench/bench.py` | M2: use `outcome["value"]`; extraction only if it is missing. | D |
| B3 | `bench/judge.py` | M3: the session limit propagates; other errors are “judge in error”, left out of the count. | R |
| B4 | `web/collector.py` | `seconds` without the waits for the limit (`_elapsed`). | D |
| B5 | `chain._apply_outcomes` | The candidate is matched to the model's answer by a contained name key: two candidates with the same surname can swap outcomes. | R/I |
| B6 | `chain._merge_outcomes` | Checks are recognised by their first 50 characters: two checks that start the same way are merged. | R/I |
| B7 | `chain._walk_frontier` | “Confirmed by N routes” counts routes with the same final entity and the same page. Distinct sources should be counted. | R |
| B8 | `chain.walk` | The best chain is chosen by weak links and quality; corroboration does not count. | R |
| B9 | writing prompts | “Confidence” with inconsistent capitalisation or missing. Disappears once the program writes it. | D |
| B10 | bench | Holdout run 3 did not start: the loop died with the session. | D |
| O1 | cost and time | Holdout questions with more than 20 pages read: 9 out of 36, 4 wrong. A long search is a signal: it goes into the score. | D |
| O2 | `chain.py` | 2,015 lines. The checks go into `web/validation.py`: first step of the split. | R |
| O3 | replay | G2, G5 and G6 add calls: the 39 chains have to be recorded again once (about $6). | R |

Still open from before: the cause of the Blender replay failure; `test_contracts.py` and the CI never run; dead code
(`Bm25`, `_walk_plan`, methods of `Concepts`); the Claude account e-mail sent to Wikipedia enabled by default.

---

## 6. Order of work

**Phase A — straighten the yardstick (no cost):** B1–B4 and M6 with tests first; re-judging of the saved runs
(`bench/rejudge.py`); review sheet of the expected answers (`bench/check_references.py`) with the field
`reference_status` in the holdout questions (`ok`, `contested`, `wrong`, `ambiguous`): only `ok` counts.

**Phase B — program checks (free, protected by the replay):** G1, G3, G4, G7, then B5–B7. Every check has its own
test, written first, built on the real cases from the holdout questions.

**Phase C — model checks (cost goes up, measured):** G2, G5, G6; status and score in place of `confirmed` and
“Confidence” in `collector.py`, `investigation.py`, `document.py` and in the interface.

**Phase D — document and measurement:** document rules by status, `faithfulness.py` on the source row, new replay
recording, test campaign (§7).

Rule: a check stays only if, on the holdout questions, the precision of `verified` goes up and the correct answers do
not drop beyond the noise (McNemar, `bench/stats.py`).

---

## 7. Test campaign

| Round | What | Criterion | Cost |
|---|---|---|---|
| 0 Snapshot | commit, exact models, settings of every run | — | 0 |
| 1 Integrity | offline tests, replay, ruff, coverage ≥ 80% | all green | 0 |
| 2 Yardstick | judge tests; re-judging of the runs; review sheet of the expected answers; 50 judge verdicts re-read by hand | judge errors ≤ 2%; every expected answer `ok` or excluded | cents and one person's time |
| 3 Baseline | holdout questions ×3 with the corrected yardstick; answers from memory (haiku, sonnet, opus) | numbers only | about $14 |
| 4 Calibration | set B (36 questions, other seeds) for the score weights; 20 unanswerable questions | invented answers ≤ 10% | about $5 |
| 5 A/B checks | every check on and off on the holdout questions, paired | `verified` correct ≥ 95% (lower bound ≥ 85%); correct answers not dropping | about $5 per check |
| 6 Stability | same question ×3 | same value ≥ 85%; same status ≥ 85% | in the data of rounds 3 and 5 |
| 7 Sources | 150 proofs of links and values re-read live | ≥ 90% supported | cents |
| 8 Documents | 6 complete investigations; `faithfulness.py` in both modes; key facts recovered, by hand | sentences ≥ 95%, no invented key fact, recovered ≥ 80% | about $30 |
| 9 Confirmation | final code on a third, never-seen set, once | lower bound ≥ 70%; `verified` ≥ 95% | about $5 |

The holdout questions are used in rounds 3 and 5 to *accept or reject* a check, never to write rules for a question.
After round 5 they are used up: round 9 needs a new set.

## 8. Test plan for the checks

Every check goes through six levels, from the cheapest to the most expensive. A check enters the code after levels
1–4 (free); it stays in the code only if it passes 5 and 6.

| Level | What it tests | How | Criterion | Status as of 11 Nov |
|---|---|---|---|---|
| 1 Tables | the rule, case by case | `test/test_validation.py`: values and rows written by hand, correct and wrong | all | done for G1, G3, G4, G7 |
| 2 Exhaustive | the rule on hundreds of generated cases | every date (192) in 6 languages and in numeric form is found; the same date with day, month or year changed by one is rejected; every changed number is rejected | 0 errors | done for G1 (2,300 correct dates, 4,600 altered) |
| 3 Real cases | the defects seen in the runs | one test per case, with the real rows: PostgreSQL (snippet against infobox), Aida (1885 and 1886), C++ (return to Wheeler), Python (−2 and 2 m), Duchy of Savoy and Capture of Rome (correct value counted wrong) | all | done |
| 4 Replay | the check does not change chains it must not change | the 39 recorded chains, without network or Claude: every chain that changes must be inspected and explained | every difference explained and written in `to_record.txt` | 37 identical, 2 changed on purpose (below) |
| 5 Saved runs | how many answers it touches, correct and wrong | the check recomputed on the saved outcomes (`rejudged_outcomes.json`) | removes the seal from more wrong answers than correct ones | G4: 1 wrong and 1 correct out of 87 answers |
| 6 Live, paired | the real effect | same holdout questions with the check on and off; McNemar | `confirmed` correct going up, total correct not dropping | to do (paid) |

**What level 4 has already found** (three wrong rules, fixed before any paid run):
- G7 in its first form flagged “disagreement” in 33 chains out of 39: every discarded candidate with a Wikipedia row
  (the siblings of a one-to-many relation: the students, the derivatives). Now only the infobox of the previous
  article counts, against a candidate chosen with a weaker source.
- G3 without the parentheses took “I promessi sposi (Petrella)” for the novel and “Solférino (Landes)” for Solferino.
  Now it counts the full name or the same Wikipedia article.
- G1 rejected “26 Ιουλίου 1985” (month in Greek). Now a row with no recognised months cannot be refuted: day and year
  are enough.

The two chains changed on purpose: Divine Comedy (the recording used up the budget going round between Kyparissia and
Tripyla: G3 stops the loop) and Penicillin (a link strong only because of a snippet: G4 makes it weak and the chain
keeps searching).

**Tests still to be written, in order:**

| # | Test | What it shows | Cost |
|---|---|---|---|
| T1 | **Sabotage (done):** with one check switched off at a time, at least one test must fail (`bench/sabotage.py`, on a copy of the code). 17 sabotages, 17 caught; on the first run one was missing (the disagreement register not wired in): test added | no check is without a test | 0 |
| T2 | **Poisoned page** (`test_browser.py`, local server): a page that gives a value different from the infobox; a snippet that contradicts the page read; a row that names the value of another entity | G1, G4 and G7 hold with real pages, not just with fake objects | 0 |
| T3 | **Whole fake chain (done)** (`test_chain.py`, fake model): the model gives a value that is not in the row; returns to the starting point; a tie from a snippet; the infobox candidate discarded; in every case the final answer is not “confirmed” or the candidate is discarded with a trace | the checks are wired to the answer, not just written | 0 |
| T4 | **Unanswerable questions** (20, written by hand: invented entities, relations that do not exist, value never published) | the tool says “not found” instead of inventing | about $2 |
| T5 | **Second reading (G2):** fake model giving two different values in the two readings → `contested`; equal → passes | G2 | 0 |
| T6 | **Adversarial review (G6):** real wrong routes from the runs (C++, Duchy of Savoy, ENIAC, Marignano) and 10 correct ones, given to the reviewer | how many wrong ones it stops and how many correct ones it lets through, before wiring it in | cents |
| T7 | **Corroboration (G5):** articles recorded in two languages that agree, disagree (Aida) or are missing | G5 | 0 with the replay |
| T8 | **Judge:** 50 judge verdicts re-read by hand; the expected/answer pairs become tests of the comparison | judge errors ≤ 2% | one person's time |
| T9 | **Expected answers:** every expected answer compared with the it.wiki and en.wiki infoboxes; those that do not match are flagged | the yardstick measures the tool, not Wikidata | 0 and one person's time |
| T10 | **Status and document:** an `uncertain` answer never appears as fact in the summary; `contested` has both versions; the cover page counts the statuses | the document layer | 0 |
| T11 | **Stability:** same question three times: same value and same status | the seal does not depend on chance | in the data of round 3 |
| T12 | **Two people** re-read 40 `verified` answers with the sources open; agreement between them (kappa) | the final measurement does not depend on a single reader or on the judge | two people's time |

Test rules:
- The test is written before the check and must fail on the previous code.
- Every rejection by a check leaves a row in `history` (`value not in the row`, `already on the route`) and the
  outcome in `chain.checks`: without a trace a check cannot be measured.
- The real cases stay in the tests even after the defect is fixed.
- A check that, on the replay, changes a correct chain without explanation does not go in.

## 9. Open decisions

1. **Wikidata** as optional corroboration (G5) and for the new question sets (rounds 4 and 9): it needs a contact
   address. Options: the Claude account e-mail, another address, or no Wikidata (G5 only with Wikipedia in other
   languages, sets written by hand).
2. **Answers with a weak link:** shown as `uncertain` with the value (recommended) or hidden as “not found”.
3. **Review model (G6):** Sonnet (recommended, about +$0.02 per question) or the same as the chain.
4. **Spending:** phases A and B cents; C, D and the campaign about $60–70 of subscription over several windows. Every
   paid round starts only after a yes.

## 10. Status of the work

Updated 12 Nov 2026. The results of the paid runs are added below as they come in.

**Done and protected by tests** (455 offline tests, 35 sabotages out of 35 caught):
- Phase A, the yardstick: judge with the sign, value from the program, judge errors left out of the count, clean
  durations, `rejudge.py`, `report.py` (correct answers by status, what each check flags, decision rule).
- Phase B, program checks: G1 value in the row, G3 route without returns, G4 a snippet does not confirm,
  G7 disagreement register (only the infobox of the previous article against a candidate with a weaker source).
- Phase C, model checks (`Chain.final_review`): G2 second reading, G5 corroboration in other articles and pages, G6
  adversarial review with Sonnet, which also receives the rows of the other articles of every link. They do not
  change the answer: they decide its status (`validation.status`). The “Confidence” row is written by the program.
- Phase D: status and reasons saved with every question; the director receives the status; in the document,
  `uncertain` answers appear only in the “Open points” boxes written by the program; interface with the status next
  to every question; `faithfulness.py --on sources` on the rows of the cited pages.
- Defects B5 and B6 fixed (outcomes assigned to the right candidate and the right check), with the tests that
  reproduced them.
- T1, T3, T4 (questions written and scored), T5, T7, T10 of the test plan.

**Choices made along the way:**
- No Wikidata: corroboration uses the other Wikipedia articles; the new question sets are written by hand.
- The `direction` of the relation does not go into the chain's plan: direction is checked by the review (G6), without
  changing the prompts that guide the search.
- The model checks neither stop nor divert the chain (first version): they only decide the status. Making the chain
  look for another candidate after an objection is the next step, to be measured separately.
- Bench runs are started with `bench/run.sh`, detached from the session, from a frozen copy of the code.

**Not done:**
- T2 (poisoned page with the real browser): it would need a fake search engine and a fake reader for the whole
  collection step; the checks are tested on the chain's objects and live.
- T6, T8, T9, T11, T12 and rounds 4, 8 and 9 of the campaign: they depend on paid runs or on a person's time.
- B7 (“confirmed by N routes” with distinct sources): it changes the final reason written in the recorded chains, so
  it will be done together with the new recording.
- Dead code still to be removed; `test_contracts.py` and the CI never run.

### Run results (12 Nov 2026)

**Expected answers** (`bench/check_references.py`, then by hand): out of 36 holdout questions, 27 good, 6 contested
(Wikipedia gives another value or another step: for Guido van Rossum's birthplace, Haarlem according to Wikidata,
Amsterdam according to it.wiki, The Hague according to en.wiki), 3 ambiguous (Julian or Gregorian calendar;
established or operational). The notes are in `bench/questions_holdout.json`. The numbers are always given both ways:
removing questions after seeing the errors can make the tool look better.

| Run | Code | Correct, all | Correct, good expected answers |
|---|---|---|---|
| `holdout-1` | before the checks | 25/35 = 71% | 24/27 = 89% (95%: 72–96%) |
| `control-c1` | all checks | 24/33 | 22/26 = 85% (95%: 67–94%) |
| `control-d1` | the review sees the other articles | 24/36 | 22/27 = 81% (95%: 63–92%) |

The correct answers do not change beyond the noise (McNemar p = 1.0 in both comparisons): the checks do not steer the
search, they assign the status. The drop on new questions seen at the start (69%) came mostly from wrong expected
answers, not from the tool.

**Answer status, c1 and d1 together, good expected answers:**

| Status | Correct | Wrong |
|---|---|---|
| verified | 9 | 0 |
| supported | 11 | 0 |
| contested | 2 | 0 |
| uncertain | 22 | 9 |

- **No wrong answer was given as fact**: 20 out of 20 among verified and supported. With the old “confirmed” it was
  13 out of 17.
- Decision rule: “verified and supported more often correct than contested and uncertain” passes (20/20 against
  24/33); “verified ≥ 95% with lower bound ≥ 85%” does not pass yet: 9 out of 9 is too few (lower bound 70%).
- **`uncertain` is too broad**: 22 correct answers. The cause is the “weak link”, which on its own leads to
  `uncertain` and is a poor signal (21 correct, 7 wrong). The strong signal is the review: “holds: yes” 29 correct
  and 0 wrong; objection with a row 4 and 4.
- On repetition (c1 and d1, 33 questions): same value 25, same status 19. Of the 8 questions that change value, 7 are
  `uncertain` in both runs: the instability is where the status already warns.
- The case that led to d1 (contested birthplace, answer `verified` in c1) is `uncertain` in d1, with the other
  article's row as the reason.
- Cost: $4.4 and $5.4 per run, about 8 minutes per question (16 before).

**Defects found by the runs and fixed:** three questions lost by the bench at 3 hours while waiting for the session
limit to reset; two equal values marked “disagree” because one had a note after it.

**Next step:** narrow `uncertain` (weak link but review “holds: yes” and corroboration agrees) using only the
calibration run on the 40 questions, then a confirmation run on the holdout questions.

### Calibration, narrowed rule and unanswerable questions (13 Nov 2026)

**Calibration on the 40 tuned questions** (`calibration-1`, 33/40 correct, $6.6): the weak link on its own was a poor
signal here too (21 correct, 6 wrong). With the review holding and no objections: 6 correct and 0 wrong.
New rule, decided on this run only: **a weak link leads to `uncertain` only if the review does not hold**; if it holds,
the answer is at most `supported`, with the limit written down.

**The new rule on the runs already done** (`rejudge.py` recomputes the status from the saved outcomes, without
redoing anything):

| Status | Holdout questions, c1 and d1 (good expected answers) | Calibration, 40 questions |
|---|---|---|
| verified | 9 correct, 0 wrong | 1, 0 |
| supported | 23 correct, 0 wrong | 15, 0 |
| contested | 3 correct, 0 wrong | 1, 0 |
| uncertain | 10 correct, 9 wrong | 16, 6 |

- On the holdout questions **32 answers out of 32 given as fact are correct** (95% lower bound: 89%), and
  `uncertain` now really separates: 10 correct out of 19. All the wrong answers are in `uncertain`.
- The criterion “`verified` ≥ 95% with lower bound ≥ 85%” remains unproven for the `verified` status on its own
  (9 out of 9, lower bound 70%); for `verified` and `supported` together it is met. The two runs are repetitions of
  the same questions: the independent confirmation is the third question set.
- **The status is not as stable as the value**: on repetition, same value in 28 questions out of 36, same status in
  19. The review's verdict switches between “holds” and “not proven” from one run to the next. It switches between
  `supported` and `uncertain`, not between correct-given-as-fact and wrong-given-as-fact; to be improved (two
  reviews, the stricter one counts).

**Twenty unanswerable questions** (`unanswerable-1`, $3.1; invented entities, relations that do not exist, facts
that never existed):

| What the chain did | Questions |
|---|---|
| “Not found” (correct) | 5 |
| A value, marked `uncertain` | 13 |
| A value given as fact (`supported`) | 2 |

- **The chain on its own invents**: in 15 cases out of 20 it finds a “similar enough” route and gives a value
  (target: at most 2). It is the most serious remaining defect, and it is not in the checks: it is in the search,
  which accepts the question's premise.
- The checks stop 13 of the 15 (`uncertain`, in 10 cases with a review objection that says precisely that the premise
  is false: “Newton had no wife”, “they never won the prize”). In the document they end up in the “Open points”, not
  in the text.
- 2 out of 20 pass as fact: the advisor of Leonardo da Vinci's “doctorate” and the postcode of Spartacus's home
  village. The review did not object.
- All the invented entities (8 out of 8) got a value: the chain replaces the non-existent entity with a nearby one.
  Remedy to try: a check on the starting point (does the entity named by the question exist, with that name and those
  traits?) before searching, and “not found” when the review refutes the premise.

**Replay**: the 40 chains have been recorded again with the checks; 39 repeat identically without network or Claude,
one (ended by budget) is not repeatable. The old recordings are in `results/replay-old` (70 MB, can be deleted).

**Spending on runs** since 11 Nov: about $20 (c1 5.1; d1 5.4; calibration 6.6; unanswerable 3.1).

**B7** (“confirmed by N routes”) stays as it is: it would change the final reason written in all the recordings for a
sentence that does not enter the status.

### Invented answers: refuted premise and rejected chain (13 Nov 2026)

Two new rules in `Chain.final_review`, both with the words of a row checked by the program:
- **refuted premise**: the review states what the question takes for granted and, if a row denies it (or shows that
  the route started from something else), the answer is “not found” with the reason; the value is kept in
  `discarded_value`;
- **rejected chain**: objection with a row, premise not proven and weak link together.

Before the real run, `bench/review_probe.py` (redoes only the review on the saved runs, about $1.5): on the 40
calibration questions, no correct answer lost out of 33.

| | Before (`unanswerable-1`) | After (`unanswerable-2`) |
|---|---|---|
| “Not found” (correct) | 5 out of 20 | **14 out of 20** |
| Value given as fact | 2 | **0** |
| Value marked `uncertain` or `contested` | 13 | 6 |

On the holdout questions (`control-f1`, good expected answers): 25 correct out of 27 = 93% (95%: 77–98%), against 24
out of 27 before the checks (McNemar p = 0.69: no difference shown, and no drop); **20 answers out of 20 given as fact
are correct**; only one answer rejected, and it was wrong. No correct answer lost.

There remain 6 unanswerable questions out of 20 with a value, all flagged: two invented entities (the chain takes a
nearby entity and the review has no row to say so) and four in which the route found “looks similar”. The 10% target
is not met (30%); none, however, reaches the document as fact. The missing step is in the search, not in the checks:
checking that the entity named by the question exists under that name before searching (it changes the prompts of the
chain's plan: it needs a new recording).

**Confirmation set** (`bench/questions_confirmation.json`, written by hand on 13 Nov): 13 questions with the answer
checked against the Italian and English articles (only those where the two languages agree were kept; 10 discarded
for differing altitudes) and 10 new unanswerable questions. It is used only once, on the final code.

### Confirmation on never-seen questions (13 Nov 2026, `confirmation-1`, once only, $1.9)

| | Result |
|---|---|
| Questions with an answer (13) | 12 correct = 92% (95%: 67–99%) |
| Answers given as fact (`verified` 8, `supported` 4) | **12 correct out of 12** |
| The wrong answer | `uncertain`: the review objected, quoting the row with the right name (“had Oswald Veblen as advisor”) |
| Unanswerable questions (10) | 6 “not found”; 4 with a value, all `uncertain`; **none given as fact** |

This is the independent number: these questions never decided anything. They are few and, those with an answer,
easier than the holdout questions (2 or 3 steps, very well-known starting points): it confirms the direction, it does
not tighten the intervals. Together with the holdout questions on the final code (`control-f1`): 32 answers out of 32
given as fact are correct (95% lower bound: 89%); the unanswerable questions give a value in 10 cases out of 30, never
as fact.

### Three complete investigations on the final code (13–14 Nov 2026)

| Investigation | Questions | verified | supported | contested | uncertain | Cost |
|---|---|---|---|---|---|---|
| Suez Canal | 28 | 8 | 16 | 0 | 4 | $3.0 |
| Olivetti | 40 | 5 | 24 | 4 | 7 | $3.7 |
| James Webb Space Telescope | 36 (30 with an answer) | 2 | 16 | 4 | 8 | $3.7 |

- **The document rule holds live**: 19 uncertain answers in the three documents, all in the “Open points” boxes;
  none enters the text on its own (8 values also appear in the text because another answer, not uncertain, carries the
  same value with its source).
- The Olivetti investigation stopped under the new rule (“coverage no longer grows: many key facts are uncertain or
  contested”): the director reacts to the status.
- A note on the test, not on the product: the investigations were started with the session-limit wait switched on;
  the investigation time and the question time also include that wait (Suez stopped at 2 cycles, 5 James Webb
  questions timed out). In the application the limit stops the investigation, which can be resumed.

**Sentence faithfulness** (`bench/faithfulness.py`, 40 cited sentences at random per document, Sonnet judge):

| Investigation | Against the answers: supported / partly / no | Against the source rows: supported / partly / no |
|---|---|---|
| Suez Canal | 38 / 2 / 0 | 28 / 7 / 5 |
| Olivetti | 31 / 9 / 0 | 27 / 8 / 5 |
| James Webb Space Telescope | 36 / 3 / 1 | 23 / 10 / 7 |

- Against the answers: 105 sentences out of 120 supported (88%), 1 not supported. **The 95% target is met in one
  document out of three.** The “partly” ones are sentences that add or change a detail relative to the answer.
- Against the source rows the number is lower (65%), but it measures less than it seems: only the rows of the chain's
  route are saved, not everything the writer of the answer had in front of them; and many “not supported” sentences
  are about the sources themselves (“comes from a single source”, “the date is not given”), not about facts.
- Among the unsupported ones there is at least one fact that seems invented by the section writer (“until 1964 the
  presidency was held by Elio Schiavo”): the target “no invented key fact” is not shown.
- **The weakest layer now is section writing**: the checks protect the answers, not the sentences Claude writes from
  them. Next step: check every sentence of the section against the answer it cites while the document is being written
  (same logic as the review: a sentence without support is removed or rewritten), and save all the rows seen by the
  writer, not just those of the route.

### Assessment (14 Nov 2026)

| Target set at the start | Result |
|---|---|
| Correct answers on new questions, lower bound ≥ 70% | 25/27 = 93% (lower bound 77%) on the holdout questions; 12/13 on the confirmation set: **met** |
| Answers given as fact correct ≥ 95%, lower bound ≥ 85% | 32 out of 32 (lower bound 89%) across holdout and confirmation: **met** for `verified` and `supported` together; not demonstrable for `verified` alone with these numbers |
| Same value on repetition ≥ 85% | 28 out of 36 = 78%: **not met**; the unstable questions are almost all `uncertain` |
| Same status on repetition ≥ 85% | 19 out of 36: **not met** (the review's verdict swings between “holds” and “not proven”) |
| Invented answers ≤ 10% | value given in 10 cases out of 30 unanswerable questions: **not met**; given as fact: 0 out of 30 |
| Document sentences supported ≥ 95% | 88% against the answers: **not met** |
| Budget and time limits respected | yes in all three investigations |

Total spending on runs from 11 to 14 Nov: about $47 of subscription.

### Checking the section sentences (15 Nov 2026)

`document.check_section`: the program flags sentences with a number that the answers do not give, the director
re-reads the sentences against the answers, flagged sentences go back once to the writer, and those still without
support are removed by the program. The three documents have been rewritten (the old ones are kept alongside as
`document_before_check.*`):

| Investigation | Sentences flagged and rewritten | Removed by the program | Supported before | Supported after | Director, before and after |
|---|---|---|---|---|---|
| Suez Canal | 3 | 0 | 38 / 40 | 39 / 40 | $0.42 → $1.18 |
| Olivetti | 8 | 0 | 31 / 40 | 35 / 40 | $0.61 → $1.75 |
| James Webb Space Telescope | 3 | 0 | 36 / 40 | 33 / 40 | $0.59 → $1.33 |

- “Not supported” sentences in the sample: from 1 to **0**. The invented sentence about the Olivetti presidency
  “until 1964” is gone (it now says what the sources say, without the date).
- Fully supported sentences: 105 out of 120 before, 107 out of 120 after (88% and 89%): **no measurable improvement**,
  and the 95% target remains unmet. The remaining “partly” ones are almost all nuances (“a second source”, “in the
  world”, “maximum”) or sentences about the sources; the judge and the re-reader are the same model and do not agree
  sentence by sentence.
- The director's cost grows by about $0.75 per document (rewriting the sections is included in this figure; the
  re-reading alone costs less).
- What it certainly gives: no number or date in the text that is not in the answers (program check, at no cost) and
  no sentence that the re-reader judges unsupported. What it does not give: sentences without added nuances. For that,
  the sections would have to be written by composing already-checked sentences, not by free rewriting.

### Two reviews and the starting-point name (15–16 Nov 2026)

Two changes, both after the chain (they do not change the search):
- **two independent reviews, the stricter one counts** (`validation.merge_reviews`);
- **the name by which the question calls its starting point** (the review copies it from the question, the program
  checks that it really is in the question) **must appear in at least one page read** (`validation.missing_name`):
  otherwise “not found”.

Low-priority runs, one question at a time (`LOW_PRIORITY=1 bench/run.sh … --parallel 1`):

| | Before (code of 13 Nov) | After (code of 15 Nov) |
|---|---|---|
| 20 unanswerable questions: “not found” | 14 | **17** (3 with a value, all `uncertain`) |
| 10 unanswerable questions from the confirmation set | 6 | **8** |
| Invented values given as fact | 0 | 0 |
| Holdout questions, good expected answers | 25 out of 27 | **19 out of 27** |
| Confirmation set, 13 questions with an answer | 12 correct | 11 correct |

- On the unanswerable questions the invented value remains in 5 cases out of 30 (17%; the target was 10%), never as
  fact.
- **The drop on the holdout questions does not come from the checks but from the chain, which changes from one run to
  the next**: in 5 of the 8 wrong answers the search found another value before any review (C++, ENIAC, Louvre
  Museum, Madama Butterfly, Isaac Newton: the same unstable questions as in the other runs). In the four runs done
  with the checks the correct answers were 23, 22, 25 and 19 out of 27: 82% on average, with three questions more or
  fewer from one run to the next. The 93% of 13 Nov was a lucky run; the honest number is “between 70 and 93%, 82% on
  average”.
- There were 2 rejections on the holdout questions: one of a wrong answer, **one of a correct answer** (Decameron:
  50 m, rejected chain). It is the measured price of the rules that stop invented answers.
- Answers given as fact, runs of 13 Nov and 15 Nov together: `verified` 15 correct out of 15, `supported` 22 out of
  23. The only wrong one is a step on which the sources disagree (where Newton's mother died: Stamford according to
  Wikidata, Colsterworth according to the page read), which the review did not see.
- The stability of the status did not improve measurably: between the two runs, same value in 26 questions out of
  36, same status in 21 (previously 28 and 19). With a chain that changes value one time in four, the status cannot be
  more stable than the value.

**What remains, in order:** the chain is not repeatable on hard questions (same question, different route and
different value): this is the biggest remaining limit, and it lies in the search, not in validation. Then: the steps
on which the sources disagree and that the review does not see; the section sentences with added nuances.

### Second search for uncertain answers (16–17 Nov 2026)

`web/retry.py`: an `uncertain` answer is searched a second time, from scratch, with the budget and time left for the
question. Same value: `supported` (“two independent searches”); different value: `contested`, with both; no value:
stays `uncertain`. If the second search has a better status, the main answer is its answer.

Before building it, on the saved runs (pairs of runs on the same holdout questions): an `uncertain` answer found again
with the same value was correct 28 times out of 30; with a different value 14 out of 51; on the unanswerable questions,
40 invented values out of 42 did not repeat.

Low-priority runs (`control-h1`, `unanswerable-4`):

| | Run of 15 Nov | With the second search |
|---|---|---|
| Holdout questions, good expected answers | 19 out of 27 | **26 out of 27** (the other: “not found”) |
| Answers given as fact | 17 correct out of 18 | **17 correct out of 17** |
| `contested` | 1 | 5, all with the correct value as the main one |
| `uncertain` | 5 (1 correct) | 4 (all correct) |
| Unanswerable questions: “not found” | 17 out of 20 | **19 out of 20** (1 value, `uncertain`) |
| Cost, holdout questions | $4.6 (36 questions) | $7.1 |

- Second searches on the holdout questions with a good expected answer: 6. Two gave the same value again (correct, now
  `supported`), two a different value (`contested`), two no value.
- **No wrong answer among the 27**, and none wrongly given as fact; no invented value given as fact.
- With caution: the chain changes from one run to the next (23, 22, 25, 19 and now 26 correct out of 27). A single run
  does not show that the second search raises the correct answers; it shows that it does not lower them and that the
  status holds. The cost grows by about half.
- On the questions with a contested expected answer, the second search gives the same value again (4 out of 5): two
  searches that read the same pages agree even when the source disagrees with Wikidata. The second search measures the
  repeatability of the chain, not the truth of the source.

**Updated assessment (17 Nov):** answers given as fact that are correct in the last three runs on the holdout
questions: 54 out of 55; invented values on the unanswerable questions 1 out of 20, never as fact (10% target: met in
this run); document sentences supported 89% (95% target: not met); same status on repetition: to be measured again
with the second search. Total spending on runs: about $75.
