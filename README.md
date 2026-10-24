# Research assistant demo slice

Ask a question across a PDF library and get claims that are each checked against the page they cite.

Status: a working demo slice. It has the core pipeline, an evaluation harness, five real public reports, and a web demo with a canvas view and an investigation-steps view. A real model has been run on the question set five times: wrong-and-confident was zero in every run. The model refuses about half of what it could answer, and every cross-report comparison.

| Part | State |
|---|---|
| Ingest, retrieval with access control, grounding gate, retry, audit | Built and tested |
| Evaluation harness and 22-question set | Built. Baselines scored. Real model run five times |
| Web demo: canvas, steps, feedback, review request | Built. Camera maths, hover and drag not exercised |
| Real-model run | Done with Qwen 3.8 27B through an OpenAI-compatible API. Claude adapter built, not yet run |
| Cross-report comparisons | Retrieval now finds both sides. The model still refuses them |
| Embeddings, OCR, production auth | Not started |

## Repository map

| Path | What it is |
|---|---|
| `ra/` | The package: ingest, retrieve, ground (the gate), answer (the loop and trace), llm, baselines, evalset, web |
| `tests/` | 62 tests, with a small PDF writer so no fixtures are needed |
| `evals/` | The question set and the baseline results |
| `tools/` | A stand-in server and a jsdom check for the page. Test tooling, not product code |
| `docs/` | `TECH_STACK.md`: the stack, versions, what differs from the proposal, and code examples checked by a test. `DERIVING_THE_UI.md`: how the canvas and steps views were derived from reference recordings |
| `data/` | Ignored. The PDFs and the database |

## How an answer is made

1. **Ingest.** Each PDF is read page by page with `pdftotext`, keeping page numbers. Pages with almost no text are flagged as needing OCR.
2. **Retrieve.** SQLite full-text search (BM25). The access rule is in the SQL query, so a user only ever sees passages from libraries they hold a grant for.
3. **Draft.** A model reads only those passages and returns claims, each with the exact quote it relies on.
4. **Gate.** Plain string checks, no model: the quote must be on the cited page, every number in the claim must be in the quote, and the claim may only cite a page the model was given.
5. **Retry once** with the failure reasons. If nothing survives, the answer is "I could not find this in the selected library", with the closest passages.
6. **Audit.** Every question, outcome and citation is logged.

## The library and the question set

Five editions of the World Bank Malaysia Economic Monitor (June 2015, October 2023, April 2024, October 2025, April 2026), about 490 pages, downloaded from the World Bank site. They are public documents. The PDFs are not committed (`data/` is ignored). Check each edition's licence before redistributing anything.

`evals/questions.json` holds 22 questions: 12 single-fact, 3 that need two reports, and 7 the library cannot answer. Every answerable question is tied to an anchor sentence that was read in the report. Every unanswerable one was checked: the asked-for fact is not in the library. `python -m ra eval` first checks that every anchor is still found.

Scoring has four outcomes: correct, timid (refused a question it could answer), correct refusal, and wrong-and-confident (answered when it should not, or with the wrong fact, or from the wrong report or page). The target for wrong-and-confident is zero.

```
uv run --python 3.14 python -m ra --db data/ra.db eval --llm extractive|threshold|claude
```

## Results so far

Retrieval, 12 passages per question:

| Question type | Right page in the results |
|---|---|
| Single report, 12 questions | 12 of 12 |
| Two reports, 3 questions | 3 of 3 get both pages |

Four things made the difference: reading PDFs in reading order, because layout mode interleaves two-column pages and breaks quotes; treating "the April 2024 monitor" in a question as a date filter; dropping the edition's own words from the text query once they have become that filter, because "October 2025" otherwise outranks the forecast inside the report; and, when no edition is named, keeping two slots for every report that matches, so a comparison sees both of its sides. The last two were chosen from the ranks a diagnosis printed, each has a synthetic test, and nothing was tuned on the 22 questions.

Baselines with no model, as a floor:

| | Correct | Timid | Correct refusal | Wrong and confident |
|---|---|---|---|---|
| extractive: quote the best sentence | 1 of 15 | 0 | 0 of 7 | 21 |
| threshold: refuse if few question words match | 0 of 15 | 9 | 4 of 7 | 9 |

No model-free approach comes close. The gate cannot catch an answer that is real but irrelevant, so a model has to judge relevance and refuse.

A real model, Qwen 3.8 27B through Nous Research's OpenAI-compatible API, temperature 0, 12 passages per question:

| Run | Retrieval | Correct | Timid | Correct refusal | Wrong and confident |
|---|---|---|---|---|---|
| 1 to 4, before the retrieval fix | 0.867 | 5 to 9 of 15 | 6 to 10 | 7 of 7 | 0 |
| 5, after it | 1.0 | 8 of 15 | 7 | 7 of 7 | 0 |

Three things to read into that. First, the model never stated a false figure as fact in 75 answerable attempts, and the gate removed 2 of 10 drafted claims in run 5, so the design holds. Second, timid is the failure mode: the model refuses about half the answerable questions, and all three comparisons in every run even now that both reports are in front of it. That is a prompting and model problem, not retrieval. Third, results vary from run to run at temperature 0, so one run is a signal and three is the minimum worth quoting.

The first runs reported one to three wrong-and-confident. Every one was checked against the page: right report, right figure, right context, and the gate had verified the quote. They were scored wrong because the question set anchored a single page and these reports repeat forecasts in the executive summary. A fact may now carry several anchor sentences, every anchor must resolve, and 19 were added after reading each page. That is label correction, declared here. The rule that a right number on an unrelated page is wrong still stands and still has a test.

```
HERMES_API_KEY=... HERMES_MODEL=qwen/qwen3.8-27b uv run --python 3.14 python -m ra --db data/ra.db eval --llm hermes --out evals/results/qwen3.8-27b.json
```

## The web demo

```
uv run --python 3.14 python -m ra --db data/ra.db serve --port 8765
```

Open http://127.0.0.1:8765. Pick a demo user and ask a question. The answer is built on a canvas, one node at a time:

1. **Question.** What was asked, and as whom.
2. **Sources.** The passages that were found. Hover or tap one to read it in place, or open the full page.
3. **Checks.** Every drafted claim, with a green "Quote on page N" or a red "Removed" and the reason. Plain string check, no model.
4. **Answer.** The kept claims, each with a citation chip and a "Show the quote" link. Click a chip to open the page with the quote highlighted. Thumbs up or down, with a reason for down.
5. **Next step.** "Create review request" writes a log entry for a person. Nothing happens unless the button is pressed.

A second tab, **Investigation steps**, shows how the answer was found, as a log in the same order it happened: the search (which libraries, the exact query, any edition the question selected, how many passages in which documents), each model call, each quote check with the reasons for removals, a retry if there was one, and a conclusion. Every step says whether it was a **Model call** or **Plain code** and how long it took, and empty results are shown too ("No passage matched. Nothing was drafted."). Steps are one line each, expand on click, and "Expand 6 steps" opens them all. "Show how it was found" on any claim jumps to the exact source card in step 1. The steps never name a library the user cannot search. `?tab=steps&expand=1` opens it directly.

Movement: the camera eases from node to node as they appear, a left list highlights where you are and jumps on click, and there are zoom buttons and a show-everything button. Drag to pan, Ctrl or pinch to zoom. `?reveal=0` skips the staged reveal, and reduced-motion settings are respected. The staged reveal is presentation only: the server returns the whole result at once, and every node shows real data from that result.

A shareable link can preselect everything: `/?user=bob&q=...&run=1`.

A third page, **/dashboard**, is a short paper rather than a control room: what this user can search (documents, pages, pages flagged for OCR, per library held), what they asked and what came back (all-time totals and a 14-day bar per day, from the audit log), the last ten questions with their outcome and a link that re-runs each in the chat, their ratings and open review requests, and every saved evaluation run with its wrong-and-confident count. Five widgets in a fixed layout declared in `LAYOUT`, each ending with one line on how its numbers were made. Scoped to the user by grant and by audit row; the eval results are not user data.

- The stack and real code examples are in [docs/TECH_STACK.md](docs/TECH_STACK.md).
- The design of the canvas and steps views was derived from observing two reference recordings. Nothing was copied. The method, the limits and the labels deliberately left out are in [docs/DERIVING_THE_UI.md](docs/DERIVING_THE_UI.md).
- The user picker is a demo, not a login. Access is still enforced in retrieval and on the page route, which the tests check. Feedback and review requests can only be filed on your own answers.
- With `ANTHROPIC_API_KEY` set it uses Claude; with `HERMES_API_KEY` set it uses any OpenAI-compatible endpoint (`HERMES_BASE_URL`, `HERMES_MODEL`). With neither it uses the extractive baseline and shows a banner saying so. That baseline is often irrelevant. Do not present it as the real assistant.
- Labels say what happened, not "verified". The quote check proves the quote is on the page, not that the claim answers the question, and the page says so.
- Tested in headless Chrome on the real library as two users, with a stand-in model that first fabricates a quote and then quotes for real: the fabricated claim shows as Removed, the camera ends on the answer, and a 390 px phone width is readable with no sideways scroll. Those screenshots were checked by hand. The interactions (source cards, nav, Show how it was found, expand all, thumbs, review request) were run against the real page script in jsdom with `tools/dom_check.mjs`, 18 checks, on a copy of the database. jsdom has no layout, so camera maths, hover and drag were not exercised.

## Run the tests

```
uv run --python 3.14 --with pytest pytest -q
```

Needs `pdftotext` and `pdfinfo` (poppler). 58 tests, including one that fails if a code example in `docs/TECH_STACK.md` stops matching the file it names. Rules were broken one at a time to confirm the tests catch them: access join, quote check, number check, citable pages, retry, the two-column fix, four scoring rules, and the feedback owner check, rating check, the no-auto-review rule, and the trace (libraries by grant, retry, empty search).

## What comes next

1. Bring the timid rate down without raising wrong-and-confident: the system prompt tells the model to return nothing when unsure, and a 27B model takes that literally on comparisons. Try asking for one claim per report, and re-measure over three runs.
2. Run the Claude adapter on the same set so the two models can be compared.
3. Add embeddings beside the keyword search, and measure whether they help.
4. Only then think about authentication, hosting and the client's own reports.

## Limits, stated plainly

- Keyword search only. No embeddings yet, so a question phrased with different words than the report may miss. Comparisons across reports are retrieved but the model refuses to answer them.
- No OCR. Scanned pages are flagged, not read.
- The gate checks that a quote is on the page and that numbers match. It does not check that the claim means what the quote means. A reviewer can still disagree with a claim that passes.
- Numbers written as words are not checked.
- The Claude adapter (`ra/llm.py`) is tested with a fake client only. It has not been run against the real API. The OpenAI-compatible adapter has.
- Unit tests use small synthetic PDFs. The 22-question set is small, so a score on it is a signal, not a measurement of production quality.
- Everything so far is World Bank economic reports. The client's own research library may differ in layout, language and tables.
