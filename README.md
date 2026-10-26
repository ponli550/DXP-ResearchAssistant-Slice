# Research assistant demo slice

Ask a question across a PDF library and get claims that are each checked against the page they cite.

Status: a working demo slice. It has the core pipeline, an evaluation harness, five real public reports, and a web demo with a canvas view and an investigation-steps view. Three real models have been run on the question set nineteen times. The smaller one has never stated a wrong answer and refuses about half of what it could answer. The larger ones answer nearly everything and, after two plain-code rules added from their runs, answer beside the point once or twice a run, always with a true figure for a different reading of the question or half of a comparison.

| Part | State |
|---|---|
| Ingest, retrieval with access control, grounding gate, retry, audit | Built and tested |
| Evaluation harness and 22-question set | Built. Baselines scored. Three real models, nineteen runs |
| Web demo: canvas, steps, feedback, review request | Built. Camera maths, hover and drag not exercised |
| Real-model run | Qwen 3.8 27B, Gemini 4 Argon and Claude Sonnet 5.5, all through one OpenAI-compatible endpoint. The native Anthropic adapter is built, not exercised |
| Cross-report comparisons | Retrieval finds both sides. The small model refuses them; the large one answers two of three |
| Embeddings, OCR, production auth | Not started |

## Repository map

| Path | What it is |
|---|---|
| `ra/` | The package: ingest, retrieve, ground (the gate), answer (the loop and trace), llm, baselines, evalset, web |
| `tests/` | 73 tests, with a small PDF writer so no fixtures are needed |
| `evals/` | The question set and the baseline results |
| `tools/` | A stand-in server and a jsdom check for the page. Test tooling, not product code |
| `docs/` | `TECH_STACK.md`: the stack, versions, what differs from the proposal, and code examples checked by a test. `DERIVING_THE_UI.md`: how the canvas and steps views were derived from reference recordings. `DESIGN.md`: the rules the admin board follows and where each came from |
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

| Model, runs | Retrieval | Correct | Timid | Correct refusal | Wrong and confident |
|---|---|---|---|---|---|
| Qwen 3.8 27B, runs 1 to 4, before the retrieval fix | 0.867 | 5 to 9 of 15 | 6 to 10 | 7 of 7 | 0 |
| Qwen 3.8 27B, runs 5 to 9, after it | 1.0 | 5 to 8 of 15 | 7 to 10 | 7 of 7 | 0 |
| Gemini 4 Argon, 3 runs, old gate | 1.0 | 12 to 13 of 15 | 0 to 1 | 6 to 7 of 7 | 2 to 3 |
| Gemini 4 Argon, 3 runs, figure-label rule in the gate | 1.0 | 11 to 12 of 15 | 0 to 2 | 6 to 7 of 7 | 2 to 4 |
| Gemini 4 Argon, 3 runs, plus the figure-question rule | 1.0 | 10 to 12 of 15 | 2 to 3 | 7 of 7 | 1 to 2 |
| Qwen 3.8 27B, run 10, same gate | 1.0 | 6 of 15 | 9 | 7 of 7 | 0 |
| Claude Sonnet 5.5, 3 runs, same gate | 1.0 | 10 to 12 of 15 | 2 to 3 | 7 of 7 | 1 to 2 |

The second model answers nearly everything and the gate removes a third of what it drafts. Its first runs exposed a gap: it quoted a chart's axis text ("5.1 4.4 3.6 3.3 4.1 2025f") and the number check passed because 4.4 was in the blob. The gate now requires a number to appear on a line with at least three words, so figure labels do not count; that claim is removed and the test that proves it is in `tests/test_core.py`. Its remaining wrong-and-confident answers were each checked against their quotes: none states a false figure. They are true, verified sentences that answer a different question (first-half actual growth when the full-year projection was asked; quarter-on-quarter when year-on-year was anchored) or that reply to an unanswerable question with related facts. The gate cannot judge relevance in general, but one case is plain: a question that asks for a figure (growth, share, how many, a rate) answered by a set of claims none of which states one. The gate now removes that whole set, with the reason, and the retry asks for the figure; years and quarter marks do not count as figures. On three runs that took the larger model's wrong-and-confident from 2 to 4 down to 1 to 2 and every unanswerable question to a correct refusal, at the cost of one more timid answer. What is left is one kind: a true figure for a different reading of the question, every run on the cross-report question about 2025 (it gives the first-half actual instead of the full-year projection) and once on the fourth-quarter question (quarter-on-quarter instead of year-on-year). Plain code cannot see that. The brief counts a confident irrelevant answer as a failure, so the scorer keeps counting it, and it is a reviewer's job or a sharper question.

Three things to read into that. First, no model stated a false figure as fact in any run, 285 answerable attempts across three, and the gate removed between 2 of 10 and 33 of 52 drafted claims, so the design holds. Claude Sonnet 5.5 sits between the other two: every single-report question it answers is right, it drafts the most claims and the gate removes the most, and its one or two wrong-and-confident answers per run are all cross-report comparisons where it states one true side or reads 2025 as the first-half actual. Through the same endpoint as the others, so the comparison is like for like; the native Anthropic adapter in `ra/llm.py` is still unexercised. Second, the two models fail in opposite directions. The smaller one refuses about half the answerable questions and every comparison; the larger one answers almost everything and once or twice a run answers confidently beside the point. Coverage and zero-false pull against each other, and the brief ranks zero-false first. Third, results vary from run to run at temperature 0, so one run is a signal and three is the minimum worth quoting.

The first runs reported one to three wrong-and-confident. Every one was checked against the page: right report, right figure, right context, and the gate had verified the quote. They were scored wrong because the question set anchored a single page and these reports repeat forecasts in the executive summary. A fact may now carry several anchor sentences, every anchor must resolve, and 19 were added after reading each page. That is label correction, declared here. The rule that a right number on an unrelated page is wrong still stands and still has a test.

```
HERMES_API_KEY=... HERMES_MODEL=qwen/qwen3.8-27b    uv run --python 3.14 python -m ra --db data/ra.db eval --llm hermes --out evals/results/qwen3.8-27b.json
HERMES_API_KEY=... HERMES_MODEL=google/gemini-4-argon uv run --python 3.14 python -m ra --db data/ra.db eval --llm hermes --out evals/results/gemini-4-argon.json
HERMES_API_KEY=... HERMES_MODEL=anthropic/claude-sonnet-5.5 uv run --python 3.14 python -m ra --db data/ra.db eval --llm hermes --out evals/results/claude-sonnet-5.5.json
```

## The web demo

The proposal deck is `deck.html`, published as a static Cloudflare Worker at https://dxp-research-assistant-deck.nazrijz336.workers.dev (public to anyone with the link; `scripts/deploy_deck.sh` republishes it with the screenshots in `deck-assets/`). For the client meeting: `docs/DEMO.md` is the rehearsal script, and `scripts/seed_demo.py` builds a clean demo database (`data/demo.db`, ignored by git) through the real pipeline, so history can be shown in seconds and one or two questions asked live.

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

A third page, **/dashboard**, is the admin board, behind a token typed once per device (`ADMIN_TOKEN`, `demo` when unset). It opens with a brief, one sentence of counts since this device last looked, silent when nothing happened and with a line saying why; a three-card explainer on the first visit; then what to do first (review requests waiting, pages flagged for OCR, thumbs-down reasons to read, a stale or failing question-set run), each with its count and the reason it is there, and only when the count is not zero. Below: libraries with pages and flagged pages, questions by user, people (ratings and open review requests), the last twelve questions across users with their outcome, and every saved evaluation run. Also on the board: a coverage ring (share of pages with extracted text), the sources (database, model endpoint, saved runs, each with where it is and when last used), a test console that asks as a user and shows what the check kept and removed, a reports-by-edition timeline per library (dot size is how often users' answers cited the report, a small bar splits that by user, dashed links join editions), and API keys. One screen, in the home-board idiom: counts and names in panels, text bars, tabular figures, the explainer folded behind one link. The arrangement is a JSON document per owner, not markup: `GET`/`PUT`/`DELETE /api/admin/layout` read, replace and reset it, the page builds its grid from it, and plain code validates it (known widget types only, spans 1 to 12, optional x, y, h and a settings object kept for a later builder). The pilot writes one thing back: expand on a panel sets its span to 12 and saves; shrink or Escape restores the catalogue width. The widget catalogue is code, so a document arranges and configures what exists and cannot invent a type. The board loads in parts: the brief, key figures and sources first (`/api/admin/board?parts=head`), then each panel fetches only its own slice (`/api/admin/widget/{type}`) when it scrolls into view, and keeps it, so expanding and shrinking re-fill from memory. The timeline graph, the heaviest part, is never computed for a board that does not show it.

Every answer is stored with its audit row exactly as it was shown (claims, rejected claims, sources, trace), and **/api/answer/{id}?user=** replays it for the user who asked, in milliseconds and without a model call. "Open" on a board's answer rows goes to the chat with that answer already built; the status line says it is stored and when. This is the history line from the brief.

Each user has a board of their own at **/dashboard?user=NAME**, linked from the chat page as "Your board". Same page, same layout machinery, a second widget catalogue: your library (every report you may search, newest edition first, with a filter, OCR flags, how often your answers cited it, a PDF link and an "ask" link that opens the chat with that edition named), your analyses, your recent answers (each opening in the chat), your usage, your ratings, your review requests.

**The original PDF.** `/files/{doc}?user=` serves the file behind the user's grant, and the page viewer links to it at the cited page (`#page=N`), so a reader goes from a claim to the quote on the extracted page to the page in the real document.

**Analyses.** An analysis is a user's own question list, run across the reports they choose (`POST /api/me/analyses`, then `/run`). Each cell is one pass through the same pipeline with retrieval limited to that one report, so a cell is a checked claim with its page or a refusal, stored on the audit log like any answer and replayable. The result is a table, questions down and editions across, on the user's board, with progress while it runs. Limits: ten questions, sixty cells, one run at a time per server. Cost is shown before running: cells times about ten seconds on the current model. There is no summary step and no second pipeline; "analysis" means the same checked answers, many at once. It reads only that user's rows from the audit log, feedback and reviews. Its layout document is `user:NAME` in the same table, through `/api/me/layout`, and the two catalogues are kept apart: a user layout cannot name an admin widget and vice versa. Trust is the demo's, the user name in the query as in the chat; a production session would say who you are. Counts, names, reasons and dates only; no passage or page text, because a board stays on screen.

**API keys.** An integration asks with `X-Api-Key` on `POST /api/ask` instead of a user name. A key stands for one user and inherits that user's grants, nothing more; the body's user is ignored. Keys are created, listed and revoked from the board (or `/api/admin/keys` with the admin token), shown once and stored as a hash, with created and last-used times. This is the first brick of the identity layer, not a replacement for login.

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

1. Close the gap between the two models. The smaller one is timid and never wrong; the larger one covers the set and answers beside the point once or twice a run. Loosening the smaller model's prompt was tried and reverted (it raised wrong-and-confident without lowering timid). The figure-question rule was tried and kept (it removed two of the larger model's three failure kinds). The one left, a true figure for a different reading of the question, is not a plain-code problem: it needs a reviewer, or questions that name the reading they want.
2. Run the Claude adapter on the same set so the two models can be compared.
3. Add embeddings beside the keyword search, and measure whether they help.
4. Only then think about authentication, hosting and the client's own reports.

## Limits, stated plainly

- Keyword search only. No embeddings yet, so a question phrased with different words than the report may miss. Comparisons across reports are retrieved; the small model refuses them and the large one answers two of three.
- No OCR. Scanned pages are flagged, not read.
- The gate checks that a quote is on the page and that numbers match. It does not check that the claim means what the quote means. A reviewer can still disagree with a claim that passes.
- Numbers written as words are not checked.
- The Claude adapter (`ra/llm.py`) is tested with a fake client only. It has not been run against the real API. The OpenAI-compatible adapter has.
- Unit tests use small synthetic PDFs. The 22-question set is small, so a score on it is a signal, not a measurement of production quality.
- Everything so far is World Bank economic reports. The client's own research library may differ in layout, language and tables.
