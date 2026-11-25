# Bench test log

| Date | What is tested | Code | Exam | Correct | Avg minutes | Claude $ | Errors | Confirmed correct | Decided by judge |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-14 14:47 | step 2: Claude Haiku reads and writes (from the notes) | dea145c | 1 questions | 0/1 | 3.1 | 0.026 | 0 |
| 2026-10-14 14:56 | steps 2-4: full graph with Claude Haiku | 82cf35f | 1 questions | 0/1 | 3.2 | 0.049 | 0 |
| 2026-10-14 15:24 | baseline of the day: Spark reads, Qwen writes (Haiku judge) | 82cf35f | quick (12) | 7/12 | 2.3 | 0.123 | 0 |
| 2026-10-14 16:08 | graph search, all Claude Haiku | 82cf35f | quick (12) | 8/12 | 3.7 | 0.493 | 0 |
| 2026-10-14 16:39 | graph v2 (concepts and ties), without Wikidata: single test | 6ee6039 | 1 questions | 0/0 | - | 0 | 1 |
| 2026-10-14 16:49 | graph v2 + relevance per search, without Wikidata: single test | 5de134d | 1 questions | 0/1 | 4.2 | 0.076 | 0 |
| 2026-10-14 18:06 | graph v2 (more concepts and ties), without Wikidata | 71a2a03 | quick (12) | 5/12 | 5.4 | 0.894 | 0 |
| 2026-10-14 20:50 | graph v3: 40 pages, 30 min, no stop for few new findings, translations (7 not found) | 8358edb | 7 questions | 1/7 | 14.4 | 1.589 | 0 |
| 2026-10-15 00:14 | graph v4: facts, candidates, constraints, reasons for ties, pieces for writing; 20 pages, 15 min | 84b7293 | 2 questions | 2/2 | 5.0 | 0.161 | 0 |
| 2026-10-15 01:01 | graph v4: facts, candidates, constraints, reasons for ties, pieces for writing; 20 pages, 15 min | c6a9c5d | quick (12) | 10/12 | 4.7 | 1.102 | 0 |
| 2026-10-15 02:29 | graph v5: answer only with a valid candidate, constraint check with Claude, lists completed | 0e3be2a | quick (12) | 8/12 | 6.4 | 1.524 | 0 |
| 2026-10-15 03:10 | graph v6 on the 40 (half A: computing and history) | f6d08f7 | 20 questions | 4/4 | 9.7 | 0.762 | 16 |
| 2026-10-15 03:10 | x | f6d08f7 | 1 questions | 0/0 | - | 0 | 1 |
| 2026-10-15 03:11 | graph v6 on the 40 (half A: computing and history) | f6d08f7 | 20 questions | 4/4 | 9.7 | 0.762 | 16 |
| 2026-10-15 03:43 | graph v6 on the 40 (half B: science and geography) | d4cfc41 | 20 questions | 1/4 | 6.6 | 0.502 | 16 |
| 2026-10-15 14:40 | graph v6 on the 40 (half A, latest; with budget reserve) | f7c6f7a | 20 questions | 14/20 | 12.5 | 4.043 | 0 |
| 2026-10-15 15:01 | graph v6 on the 40 (half B, latest; with budget reserve) | f7c6f7a | 20 questions | 14/20 | 11.1 | 3.768 | 0 |
| 2026-10-15 16:47 | guided chain v1: single test | 808558d | 1 questions | 0/1 | 6.7 | 0.106 | 0 |
| 2026-10-15 16:52 | guided chain v2: single test | 5d2a969 | 1 questions | 0/1 | 4.1 | 0.078 | 0 |
| 2026-10-15 16:59 | guided chain v3: single test | fdb5835 | 1 questions | 0/1 | 7.0 | 0.096 | 0 |
| 2026-10-15 17:19 | guided chain v4: single test | 43ad87a | 1 questions | 0/1 | 17.7 | 0.223 | 0 |
| 2026-10-15 18:30 | guided chain v4 on the 12 failed by v6 (half B) | 43ad87a | 6 questions | 3/6 | 11.8 | 0.9 | 0 |
| 2026-10-15 18:32 | guided chain v4 on the 12 failed by v6 (half A) | 43ad87a | 6 questions | 1/6 | 12.1 | 0.828 | 0 |
| 2026-10-15 20:00 | guided chain v5 on the 8 not recovered (half B) | 87b45d2 | 4 questions | 1/4 | 12.8 | 0.533 | 0 |
| 2026-10-15 20:12 | guided chain v5 on the 8 not recovered (half A) | 87b45d2 | 4 questions | 2/4 | 15.9 | 0.734 | 0 |
| 2026-10-16 04:21 | guided chain v6 on the 40 (half A: computing and history) | efff62d | 20 questions | 11/20 | 9.0 | 2.198 | 0 |
| 2026-10-16 04:34 | guided chain v6 on the 40 (half B: science and geography) | efff62d | 20 questions | 12/20 | 9.6 | 2.504 | 0 |
| 2026-10-16 04:52 | chain v7 (context in the searches): test on two regressions | 4881217 | 2 questions | 1/2 | 8.7 | 0.191 | 0 |
| 2026-10-16 16:05 | chain v8 (Wikipedia as a graph, 10 requests/min): test on 4 | 019a675 | 4 questions | 2/4 | 11.7 | 0.291 | 0 |
| 2026-10-16 18:20 | chain v8.1 (Wikipedia as a graph) on the 40 (X) | ec728ae | 14 questions | 2/14 | 5.6 | 0.721 | 0 |
| 2026-10-16 18:21 | chain v8.1 (Wikipedia as a graph) on the 40 (Z) | ec728ae | 13 questions | 4/13 | 6.1 | 0.736 | 0 |
| 2026-10-16 18:28 | chain v8.1 (Wikipedia as a graph) on the 40 (Y) | ec728ae | 13 questions | 7/13 | 6.7 | 0.766 | 0 |
| 2026-10-16 21:20 | chain v8.2 (Wikipedia as a graph, second plan) on the 40 (Z) | 3fc0f70 | 13 questions | 12/13 | 6.3 | 0.822 | 0 |
| 2026-10-16 21:55 | chain v8.2 (Wikipedia as a graph, second plan) on the 40 (Y) | 3fc0f70 | 13 questions | 8/13 | 9.1 | 1.012 | 0 |
| 2026-10-17 02:24 | chain v8.9 (verification read twice, answer written by the program) on the 40 (X) | 77ce57e | 14 questions | 7/11 | 11.0 | 1.482 | 3 |
| 2026-10-17 02:36 | chain v8.9 (verification read twice, answer written by the program) on the 40 (Y) | 77ce57e | 13 questions | 6/10 | 13.4 | 1.732 | 3 |
| 2026-10-17 02:43 | chain v8.9 (verification read twice, answer written by the program) on the 40 (Z) | 77ce57e | 13 questions | 5/10 | 14.0 | 1.46 | 3 |
| 2026-10-17 06:16 | chain v8.10 on the 40 (Y) | ac3fcaa | 13 questions | 7/11 | 11.7 | 1.176 | 2 |
| 2026-10-17 06:36 | chain v8.10 on the 40 (Z) | ac3fcaa | 13 questions | 6/11 | 13.6 | 1.602 | 2 |
| 2026-10-17 07:06 | chain v8.10 on the 40 (X) | ac3fcaa | 14 questions | 8/12 | 14.9 | 1.692 | 2 |
| 2026-10-17 15:44 | chain v8.12 on the 40 (Y) | 19a9376 | 13 questions | 8/13 | 9.6 | 1.785 | 0 |
| 2026-10-17 16:18 | chain v8.12 on the 40 (Z) | 19a9376 | 13 questions | 8/13 | 12.2 | 2.338 | 0 |
| 2026-10-17 16:39 | chain v8.14 (second chain if weak, strong origin first) on the 40 (X) | d8c8865 | 14 questions | 0/0 | - | 0 | 14 |
| 2026-10-17 20:38 | chain v8.15 on the 10 hard ones (Q) | 841137e | 5 questions | 0/5 | 16.6 | 1.257 | 0 |
| 2026-10-17 21:11 | chain v8.15 on the 10 hard ones (P) | 6070dad | 5 questions | 1/5 | 23.2 | 1.783 | 0 |
| 2026-10-18 05:04 | chain v9 (4-valued verification with weights, checked value, global frontier) on the 40 (Y) | 3bd4ef0 | 13 questions | 9/13 | 23.5 | 2.391 | 0 |
| 2026-10-18 05:24 | chain v9 (4-valued verification with weights, checked value, global frontier) on the 40 (Z) | 3bd4ef0 | 13 questions | 10/13 | 25.0 | 2.875 | 0 |
| 2026-10-18 06:19 | chain v9 (4-valued verification with weights, checked value, global frontier) on the 40 (X) | 3bd4ef0 | 14 questions | 10/14 | 27.1 | 3.559 | 0 |
| 2026-10-18 10:07 | chain v10 (+ anchors, two plans in parallel, decisive rows, weight 3, value before the web) on the 40 (Z) | c8fe529 | 13 questions | 9/13 | 17.5 | 1.874 | 0 |
| 2026-10-18 10:30 | chain v10 (+ anchors, two plans in parallel, decisive rows, weight 3, value before the web) on the 40 (X) | c8fe529 | 14 questions | 10/14 | 17.8 | 2.346 | 0 |
| 2026-10-18 10:50 | chain v10 (+ anchors, two plans in parallel, decisive rows, weight 3, value before the web) on the 40 (Y) | c8fe529 | 13 questions | 9/13 | 20.8 | 2.451 | 0 |
| 2026-10-18 13:42 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (Z) — INVALID: Claude weekly limit | bf5f6c2 | 13 questions | 0/2 | 0.9 | 0.003 | 11 |
| 2026-10-18 13:56 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (Y) — INVALID: Claude weekly limit | bf5f6c2 | 13 questions | 0/1 | 0.4 | 0.002 | 12 |
| 2026-10-18 14:12 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (X) — INVALID: Claude weekly limit | bf5f6c2 | 14 questions | 0/1 | 0.3 | 0.002 | 13 |
| 2026-10-22 13:45 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (Z) | 88b7c8e | 13 questions | 10/13 | 11.7 | 2.03 | 0 |
| 2026-10-22 17:30 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (X, resumed) | 88b7c8e | 14 questions | 9/14 | 18.3 | 2.916 | 0 |
| 2026-10-22 17:55 | chain v11 (+ articles in more languages for the value, priority, safe anchors, adjacent articles, tie) on the 40 (Y, resumed) | 88b7c8e | 13 questions | 9/13 | 14.9 | 2.443 | 0 |
| 2026-10-23 00:59 | Sonnet 5.5, reading off, 1 verification reading, 1 plan (test) | 03f9129 | 1 questions | 1/1 | 4.0 | 0.425 | 0 |
| 2026-10-23 01:30 | Sonnet 5.5, reading off, 1 verification reading, 1 plan: 5 hard ones from v11b | 2a9a252 | 5 questions | 3/5 | 6.2 | 4.011 | 0 |
| 2026-10-23 15:45 | entity-and-row plan (phases 1-5): Galileo with Haiku | d965a80 | 1 questions | 1/1 | 6.0 | 0.101 | 0 |
| 2026-10-23 16:09 | entity-and-row plan (phases 1-5): Arduino with Haiku | d965a80 | 1 questions | 0/1 | 20.9 | 0.28 | 0 |
| 2026-10-23 16:42 | name variants merged: Arduino with Haiku | 2f204d8 | 1 questions | 0/1 | 22.0 | 0.3 | 0 |
| 2026-10-23 19:20 | rows cited in the history, same article = same entity: Arduino with Haiku | 2f02d30 | 1 questions | 1/1 | 27.8 | 0.26 | 0 |
| 2026-10-23 20:08 | Arduino 5 times (2/5), commit c827041 | c827041 | 1 questions | 0/1 | 24.8 | 0.237 | 0 |
| 2026-10-23 20:09 | Arduino 5 times (3/5), commit c827041 | c827041 | 1 questions | 1/1 | 25.5 | 0.304 | 0 |
| 2026-10-23 20:11 | Arduino 5 times (1/5), commit c827041 | c827041 | 1 questions | 0/1 | 27.5 | 0.33 | 0 |
| 2026-10-23 20:38 | Arduino 5 times (5/5), commit c827041 | c827041 | 1 questions | 0/1 | 26.8 | 0.302 | 0 |
| 2026-10-23 20:46 | Arduino 5 times (4/5), commit c827041 | c827041 | 1 questions | 0/1 | 35.6 | 0.362 | 0 |
| 2026-10-24 14:28 | cheaper web without Wikipedia candidates (web-arduino-3) | bf77c91 | 1 questions | 0/1 | 19.2 | 0.253 | 0 |
| 2026-10-24 14:29 | cheaper web without Wikipedia candidates (web-arduino-1) | bf77c91 | 1 questions | 0/1 | 20.0 | 0.283 | 0 |
| 2026-10-24 14:29 | cheaper web without Wikipedia candidates (web-arduino-2) | bf77c91 | 1 questions | 0/1 | 20.2 | 0.265 | 0 |
| 2026-10-24 14:32 | cheaper web without Wikipedia candidates (web-duomo) | ccb43c3 | 1 questions | 1/1 | 3.0 | 0.045 | 0 |
| 2026-10-24 14:46 | cheaper web without Wikipedia candidates (web-arduino-5) | ccb43c3 | 1 questions | 0/1 | 16.6 | 0.212 | 0 |
| 2026-10-24 14:50 | cheaper web without Wikipedia candidates (web-arduino-4) | ccb43c3 | 1 questions | 0/1 | 20.6 | 0.26 | 0 |
| 2026-10-24 15:01 | cheaper web without Wikipedia candidates (web-sistina) | ccb43c3 | 1 questions | 0/1 | 11.6 | 0.209 | 0 |
| 2026-10-24 17:09 | Arduino 5 times, web fixes together (ccb43c3) (1/5) | ccb43c3 | 1 questions | 0/1 | 29.3 | 0.32 | 0 |
| 2026-10-24 17:11 | Arduino 5 times, web fixes together (ccb43c3) (2/5) | ccb43c3 | 1 questions | 0/1 | 30.6 | 0.351 | 0 |
| 2026-10-24 17:14 | Arduino 5 times, web fixes together (ccb43c3) (3/5) | ccb43c3 | 1 questions | 0/1 | 34.1 | 0.333 | 0 |
| 2026-10-24 17:29 | Arduino 5 times, web fixes together (ccb43c3) (4/5) | ccb43c3 | 1 questions | 1/1 | 15.1 | 0.159 | 0 |
| 2026-10-24 17:31 | Arduino 5 times, web fixes together (ccb43c3) (5/5) | ccb43c3 | 1 questions | 1/1 | 16.9 | 0.193 | 0 |
| 2026-10-24 19:18 | hard ones never/almost never solved, code ccb43c3 (C) | ccb43c3 | 2 questions | 1/2 | 11.1 | 0.289 | 0 |
| 2026-10-24 19:22 | hard ones never/almost never solved, code ccb43c3 (B) | ccb43c3 | 3 questions | 2/3 | 8.5 | 0.299 | 0 |
| 2026-10-24 19:55 | hard ones never/almost never solved, code ccb43c3 (A) | ccb43c3 | 3 questions | 0/3 | 19.7 | 0.776 | 0 |
| 2026-10-24 22:20 | round1 after fixes, code a11c4c2 (A) | 7f3af0b | 2 questions | 1/2 | 14.6 | 0.336 | 0 |
| 2026-10-24 22:23 | round1 after fixes, code a11c4c2 (C) | 7f3af0b | 2 questions | 1/2 | 16.4 | 0.348 | 0 |
| 2026-10-24 22:37 | round1 after fixes, code a11c4c2 (B) | 7f3af0b | 2 questions | 0/2 | 23.4 | 0.585 | 0 |
| 2026-10-24 23:25 | round2 after fixes, code 7cb5c60 (C) | 7cb5c60 | 1 questions | 0/1 | 5.2 | 0.096 | 0 |
| 2026-10-24 23:34 | round2 after fixes, code 7cb5c60 (B) | 0a97a11 | 1 questions | 1/1 | 13.8 | 0.176 | 0 |
| 2026-10-25 00:07 | round2 after fixes, code 7cb5c60 (A) | 9047f89 | 2 questions | 1/2 | 23.5 | 0.832 | 0 |
| 2026-10-25 00:24 | round3 after fixes, code 9047f89 (A) | 9047f89 | 2 questions | 1/2 | 8.1 | 0.215 | 0 |
| 2026-10-25 00:31 | round3 after fixes, code 9047f89 (C) | 9047f89 | 2 questions | 1/2 | 11.8 | 0.384 | 0 |
| 2026-10-25 00:53 | round3 after fixes, code 9047f89 (B) | 4936a51 | 2 questions | 2/2 | 22.9 | 0.575 | 0 |
| 2026-10-25 00:58 | round4 after fixes, code 4936a51 (A) | 4936a51 | 1 questions | 1/1 | 4.3 | 0.058 | 0 |
| 2026-10-25 01:20 | round4 after fixes, code 4936a51 (B) | 4936a51 | 1 questions | 0/1 | 26.7 | 0.408 | 0 |
| 2026-10-25 01:21 | round4 after fixes, code 4936a51 (C) | 4936a51 | 1 questions | 0/1 | 26.8 | 0.368 | 0 |
| 2026-10-25 01:37 | round5 after fixes, code 2e0548d (B) | 2e0548d | 1 questions | 1/1 | 14.0 | 0.239 | 0 |
| 2026-10-25 01:42 | round5 after fixes, code 2e0548d (A) | 2e0548d | 1 questions | 0/1 | 19.4 | 0.283 | 0 |
| 2026-10-25 01:49 | round5 after fixes, code 2e0548d (C) | 2e0548d | 1 questions | 0/1 | 25.8 | 0.353 | 0 |
| 2026-10-25 02:10 | Arduino 5 times (3/5), commit 2e0548d, web | 9b74f87 | 1 questions | 1/1 | 20.9 | 0.236 | 0 |
| 2026-10-25 02:22 | Arduino 5 times (1/5), commit 2e0548d, web | 9b74f87 | 1 questions | 0/1 | 32.5 | 0.304 | 0 |
| 2026-10-25 02:25 | Arduino 5 times (2/5), commit 2e0548d, web | 9b74f87 | 1 questions | 0/1 | 35.3 | 0.362 | 0 |
| 2026-10-25 02:45 | Arduino 5 times (4/5), commit 2e0548d, web | 9b74f87 | 1 questions | 1/1 | 20.6 | 0.236 | 0 |
| 2026-10-25 02:00 | Arduino 5 times (5/5), commit 2e0548d, web | 9b74f87 | 1 questions | 0/1 | 35.2 | 0.325 | 0 |
| 2026-10-25 02:13 | Arduino 5 times (2/5), commit 9b74f87, new web | 679ad7e | 1 questions | 1/1 | 12.9 | 0.118 | 0 |
| 2026-10-25 02:21 | Arduino 5 times (1/5), commit 9b74f87, new web | 9952624 | 1 questions | 1/1 | 20.5 | 0.218 | 0 |
| 2026-10-25 02:22 | Arduino 5 times (3/5), commit 9b74f87, new web | 9952624 | 1 questions | 1/1 | 21.3 | 0.259 | 0 |
| 2026-10-25 02:32 | Arduino 5 times (4/5), commit 9b74f87, new web | 9952624 | 1 questions | 1/1 | 10.2 | 0.123 | 0 |
| 2026-10-25 02:39 | Arduino 5 times (5/5), commit 9b74f87, new web | 9952624 | 1 questions | 1/1 | 17.2 | 0.193 | 0 |
| 2026-10-25 02:49 | round6 after fixes, code 9952624 (A) | 9952624 | 2 questions | 2/2 | 4.4 | 0.115 | 0 |
| 2026-10-25 03:05 | round6 after fixes, code 9952624 (C) | 9952624 | 1 questions | 0/1 | 25.2 | 0.321 | 0 |
| 2026-10-25 03:07 | round6 after fixes, code 9952624 (B) | 9952624 | 2 questions | 1/2 | 13.5 | 0.296 | 0 |
| 2026-10-25 08:46 | round7 after fixes, code 5bd2735 (B) | 5bd2735 | 1 questions | 1/1 | 13.5 | 0.199 | 0 |
| 2026-10-25 08:51 | round7 after fixes, code 5bd2735 (C) | 5bd2735 | 1 questions | 1/1 | 17.9 | 0.264 | 0 |
| 2026-10-25 08:55 | round7 after fixes, code 5bd2735 (A) | 5bd2735 | 1 questions | 1/1 | 22.4 | 0.249 | 0 |
| 2026-10-25 09:16 | round8 after fixes, code 5bd2735 (C) | 4d3b7e9 | 2 questions | 2/2 | 9.9 | 0.29 | 0 |
| 2026-10-25 09:16 | round8 after fixes, code 5bd2735 (A) | 4d3b7e9 | 2 questions | 1/2 | 10.2 | 0.276 | 0 |
| 2026-10-25 09:36 | round8 after fixes, code 5bd2735 (B) | 4d3b7e9 | 2 questions | 0/2 | 19.8 | 0.483 | 0 |
| 2026-10-25 09:50 | round9 after fixes, code 8c17ba8 (C) | 8c17ba8 | 2 questions | 2/2 | 6.3 | 0.131 | 0 |
| 2026-10-25 10:09 | round9 after fixes, code 8c17ba8 (B) | 8c17ba8 | 2 questions | 2/2 | 15.8 | 0.357 | 0 |
| 2026-10-25 10:13 | round9 after fixes, code 8c17ba8 (A) | 8c17ba8 | 2 questions | 2/2 | 17.8 | 0.434 | 0 |
| 2026-10-25 10:23 | round10 after fixes, code 8c17ba8 (A) | 8c17ba8 | 2 questions | 2/2 | 4.7 | 0.154 | 0 |
| 2026-10-25 10:39 | round10 after fixes, code 8c17ba8 (C) | 8c17ba8 | 2 questions | 2/2 | 12.4 | 0.385 | 0 |
| 2026-10-25 10:58 | round10 after fixes, code 8c17ba8 (B) | 8c17ba8 | 2 questions | 1/2 | 21.8 | 0.466 | 0 |
| 2026-10-25 11:22 | round11 after fixes, code 369f9ec (B) | 369f9ec | 1 questions | 0/1 | 22.6 | 0.216 | 0 |
| 2026-10-25 11:35 | round11 after fixes, code 369f9ec (A) | 369f9ec | 1 questions | 0/1 | 35.6 | 0.347 | 0 |
| 2026-10-25 11:36 | round11 after fixes, code 369f9ec (C) | 369f9ec | 1 questions | 0/1 | 36.5 | 0.366 | 0 |
| 2026-10-25 16:19 | 5 runs per question, code 48b7ab8 (sis3) | 48b7ab8 | 1 questions | 1/1 | 7.3 | 0.123 | 0 |
| 2026-10-25 16:35 | 5 runs per question, code 48b7ab8 (ard1) | 48b7ab8 | 1 questions | 1/1 | 23.5 | 0.293 | 0 |
| 2026-10-25 16:37 | 5 runs per question, code 48b7ab8 (sis4) | 48b7ab8 | 1 questions | 1/1 | 18.1 | 0.298 | 0 |
| 2026-10-25 16:39 | 5 runs per question, code 48b7ab8 (sis1) | 48b7ab8 | 1 questions | 0/1 | 27.4 | 0.391 | 0 |
| 2026-10-25 16:42 | 5 runs per question, code 48b7ab8 (mon3) | 48b7ab8 | 1 questions | 0/1 | 7.0 | 0.101 | 0 |
| 2026-10-25 16:46 | 5 runs per question, code 48b7ab8 (lib3) | 48b7ab8 | 1 questions | 1/1 | 3.8 | 0.053 | 0 |
| 2026-10-25 16:48 | 5 runs per question, code 48b7ab8 (sis2) | 48b7ab8 | 1 questions | 1/1 | 8.2 | 0.117 | 0 |
| 2026-10-25 16:49 | 5 runs per question, code 48b7ab8 (lib2) | 48b7ab8 | 1 questions | 1/1 | 12.0 | 0.173 | 0 |
| 2026-10-25 16:53 | 5 runs per question, code 48b7ab8 (lib1) | 48b7ab8 | 1 questions | 1/1 | 5.3 | 0.064 | 0 |
| 2026-10-25 16:53 | 5 runs per question, code 48b7ab8 (duc3) | 48b7ab8 | 1 questions | 1/1 | 6.5 | 0.116 | 0 |
| 2026-10-25 16:56 | 5 runs per question, code 48b7ab8 (duc2) | 48b7ab8 | 1 questions | 1/1 | 7.0 | 0.102 | 0 |
| 2026-10-25 16:58 | 5 runs per question, code 48b7ab8 (duc4) | 48b7ab8 | 1 questions | 1/1 | 5.3 | 0.08 | 0 |
| 2026-10-25 16:59 | 5 runs per question, code 48b7ab8 (duc1) | 48b7ab8 | 1 questions | 1/1 | 5.7 | 0.089 | 0 |
| 2026-10-25 17:08 | 5 runs per question, code 48b7ab8 (mon2) | 48b7ab8 | 1 questions | 0/1 | 11.6 | 0.202 | 0 |
| 2026-10-25 17:14 | 5 runs per question, code 48b7ab8 (mon1) | 48b7ab8 | 1 questions | 1/1 | 15.1 | 0.248 | 0 |
| 2026-10-25 17:37 | wide tiebreak, code 9336548 (mb3) | 9336548 | 1 questions | 1/1 | 5.5 | 0.087 | 0 |
| 2026-10-25 17:42 | wide tiebreak, code 9336548 (mb1) | 9336548 | 1 questions | 1/1 | 10.9 | 0.17 | 0 |
| 2026-10-25 17:46 | wide tiebreak, code 9336548 (mb2) | 9336548 | 1 questions | 1/1 | 14.1 | 0.176 | 0 |
| 2026-10-25 17:48 | wide tiebreak, code 9336548 (du1) | 9336548 | 1 questions | 1/1 | 5.7 | 0.086 | 0 |
| 2026-10-25 17:50 | wide tiebreak, code 9336548 (du2) | 9336548 | 1 questions | 1/1 | 4.7 | 0.073 | 0 |
| 2026-10-25 22:48 | full run 40 questions, code 9336548 (Y) | 9336548 | 13 questions | 11/13 | 17.3 | 2.002 | 0 |
| 2026-10-25 23:01 | full run 40 questions, code 9336548 (X) | 9336548 | 14 questions | 9/14 | 17.0 | 2.093 | 0 |
| 2026-10-25 23:16 | full run 40 questions, code 9336548 (Z) | 9336548 | 13 questions | 11/13 | 19.4 | 2.517 | 0 |
| 2026-10-29 11:55 | wide tiebreak, code 714b0f2 (du1) | 714b0f2 | 1 questions | 1/1 | 4.5 | 0.076 | 0 |
| 2026-10-29 12:06 | wide tiebreak, code 714b0f2 (mb1) | 714b0f2 | 1 questions | 1/1 | 15.6 | 0.255 | 0 |
| 2026-10-29 12:06 | wide tiebreak, code 714b0f2 (mb2) | 714b0f2 | 1 questions | 1/1 | 15.8 | 0.229 | 0 |
| 2026-10-29 12:09 | wide tiebreak, code 714b0f2 (em1) | 714b0f2 | 1 questions | 1/1 | 2.3 | 0.02 | 0 |
| 2026-10-29 13:07 | wide tiebreak, code 5af0892 (v2) | e1faf80 | 1 questions | 0/1 | 25.4 | 0.29 | 0 |
| 2026-10-29 13:14 | wide tiebreak, code 5af0892 (v1) | e1faf80 | 1 questions | 0/1 | 32.1 | 0.353 | 0 |
| 2026-10-29 15:49 | full run 40 questions, code e1faf80 (Z) | e1faf80 | 13 questions | 6/8 | 9.7 | 1.001 | 5 |
| 2026-10-29 15:53 | full run 40 questions, code e1faf80 (Y) | e1faf80 | 13 questions | 7/8 | 10.2 | 0.906 | 5 |
| 2026-10-29 16:54 | full run 40 questions, code e1faf80 (X) | e1faf80 | 14 questions | 6/7 | 16.0 | 1.176 | 7 |
| 2026-10-29 17:24 | full run 40 questions, code e1faf80 (resumed after the 5-hour limit) (Z) | e1faf80 | 13 questions | 11/13 | 8.2 | 1.377 | 0 |
| 2026-10-29 17:58 | full run 40 questions, code e1faf80 (resumed after the 5-hour limit) (X) | e1faf80 | 14 questions | 12/14 | 12.5 | 2.048 | 0 |
| 2026-10-29 18:06 | full run 40 questions, code e1faf80 (resumed after the 5-hour limit) (Y) | e1faf80 | 13 questions | 11/13 | 11.7 | 1.867 | 0 |
| 2026-10-29 18:06 | full run 40 questions, code e1faf80 (Hubble and Fermi redone: 5-hour limit) (X) | e1faf80 | 14 questions | 12/14 | 12.5 | 2.048 | 0 |
| 2026-10-29 18:13 | full run 40 questions, code e1faf80 (Hubble and Fermi redone: 5-hour limit) (Y) | e1faf80 | 13 questions | 12/13 | 11.6 | 1.963 | 0 |
| 2026-10-29 18:21 | full run 40 questions, code e1faf80 (Hubble and Fermi redone: 5-hour limit) (Z) | e1faf80 | 13 questions | 12/13 | 8.6 | 1.521 | 0 |
| 2026-10-30 03:28 | third full run 40 questions, code e1faf80 (Z) | e1faf80 | 13 questions | 13/13 | 10.7 | 1.761 | 0 |
| 2026-10-30 03:40 | third full run 40 questions, code e1faf80 (Y) | e1faf80 | 13 questions | 12/13 | 11.7 | 1.894 | 0 |
| 2026-10-30 04:23 | third full run 40 questions, code e1faf80 (X) | e1faf80 | 14 questions | 11/14 | 13.9 | 2.309 | 0 |
| 2026-11-10 00:46 | live test after the review | 0703055 | 1 questions | 1/1 | 11.6 | 0.072 | 0 |
| 2026-11-10 01:14 | replay test | a26b341 | 1 questions | 0/1 | 6.1 | 0.096 | 0 |
| 2026-11-10 03:03 | 40 questions after the review, with recording (SKEWED: 5-hour limit not recognised) | a26b341 | 40 questions | 22/40 | 8.1 | 4.378 | 0 |
| 2026-11-10 14:06 | 40 questions after the review, with recording | 46ae7e2 | 40 questions | 34/40 | 10.1 | 5.559 | 0 |
| 2026-11-10 18:17 | holdout questions, run 1 | 4f3ba92 | 36 questions | 25/36 | 19.9 | 4.452 | 0 |
| 2026-11-13 00:01 | holdout questions with all checks (3 questions redone after the session limit) | 513a90e | 36 questions | 25/36 | 8.4 | 5.065 | 0 | 12/13 | 13 |
| 2026-11-12 23:37 | holdout questions, the review sees the other articles of the links | 05db7f3 | 36 questions | 24/36 | 7.8 | 5.409 | 0 | 9/9 | 15 |
| 2026-11-13 04:59 | 40 questions with all checks: status calibration and new recording | 51d22d6 | 40 questions | 33/40 | 9.8 | 6.605 | 0 | 10/10 | 9 |
| 2026-11-13 06:17 | 20 unanswerable questions (correct = “not found”) | 51d22d6 | 20 questions | 5/20 | 11.1 | 3.082 | 0 | 0/2 | 0 |
| 2026-11-13 17:10 | 20 unanswerable questions, refuted premise and rejected chain (b1f5366) | b1f5366 | 20 questions | 14/20 | 12.1 | 3.397 | 0 | 0/0 | 0 |
| 2026-11-13 22:06 | holdout questions, refuted premise and rejected chain (b1f5366) | b1f5366 | 36 questions | 25/27 | 7.1 | 3.432 | 0 | 20/20 | 4 |
| 2026-11-13 22:51 | confirmation set, once only (b1f5366) | b1f5366 | 23 questions | 18/23 | 5.0 | 1.878 | 0 | 12/12 | 3 |
| 2026-11-16 04:12 | 20 unanswerable questions: two reviews and starting-point name (6b25bdb) | 6b25bdb | 20 questions | 17/20 | 9.4 | 3.216 | 0 | 0/0 | 0 |
| 2026-11-16 09:35 | holdout questions: two reviews and starting-point name (6b25bdb) | 6b25bdb | 36 questions | 19/27 | 8.9 | 4.602 | 0 | 17/18 | 9 |
| 2026-11-16 11:31 | confirmation set rerun (no longer independent) (6b25bdb) | 6b25bdb | 23 questions | 19/23 | 5.0 | 2.458 | 0 | 10/10 | 3 |
| 2026-11-16 22:43 | holdout questions: second search for uncertain answers (47ed0dc) | 47ed0dc | 36 questions | 26/27 | 9.1 | 4.954 | 0 | 17/17 | 0 |
| 2026-11-17 03:17 | 20 unanswerable questions: second search for uncertain answers (47ed0dc) | 47ed0dc | 20 questions | 19/20 | 13.7 | 4.326 | 0 | 0/0 | 0 |
| 2026-11-24 00:40 | smoke-en-h | 8638dce6 | 1 questions | 0/0 | 0.0 | 0 | 0 | 0/0 | 0 |
| 2026-11-24 08:47 | calibration-en | 8638dce6 | 40 questions | 28/40 | 3.4 | 7.36 | 0 | 11/12 | 10 |
| 2026-11-24 12:43 | control-en1 | 8638dce6 | 36 questions | 23/27 | 6.2 | 3.675 | 0 | 22/22 | 2 |
| 2026-11-24 16:32 | unanswerable-en1 | 8638dce6 | 20 questions | 15/20 | 11.5 | 3.878 | 0 | 0/1 | 0 |
| 2026-11-24 19:34 | confirmation-en1 | 8638dce6 | 23 questions | 21/23 | 7.8 | 3.436 | 0 | 11/11 | 2 |
