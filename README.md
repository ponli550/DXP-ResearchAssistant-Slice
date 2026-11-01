# Research assistant demo slice

Ask a question across a PDF library and get claims that are each checked against the page they cite.

Status: a working demo slice. It has the core pipeline, an evaluation harness, five real public reports, and a web demo: a research desk with the reports, the answer and the page that proves it on one screen. Three real models have been run on the question set nineteen times. The smaller one has never stated a wrong answer and refuses about half of what it could answer. The larger ones answer nearly everything and, after two plain-code rules added from their runs, answer beside the point once or twice a run, always with a true figure for a different reading of the question or half of a comparison.

| Part | State |
|---|---|
| Ingest, retrieval with access control, grounding gate, retry, audit | Built and tested |
| Evaluation harness and 22-question set | Built. Baselines scored. Three real models, nineteen runs |
| Web demo: research desk, live progress, tables, feedback, review request | Built. Checked in a browser against a stand-in; live progress not yet seen against a real model |
| Real-model run | Qwen 3.8 27B, Gemini 4 Argon and Claude Sonnet 5.5, all through one OpenAI-compatible endpoint. The native Anthropic adapter is built, not exercised |
| Cross-report comparisons | Retrieval finds both sides. The small model refuses them; the large one answers two of three |
| Embeddings, OCR, production auth | Not started |

## Repository map

| Path | What it is |
|---|---|
| `ra/` | The package: ingest, retrieve, ground (the gate), answer (the loop and trace), llm, baselines, evalset, web |
| `tests/` | 73 tests, with a small PDF writer so no fixtures are needed |
| `evals/` | The question set and the baseline results |
| `tools/` | A stand-in server, slow on purpose and wrong on its first draft, and a jsdom check for the page. Test tooling, not product code |
| `docs/` | `TECH_STACK.md`: the stack, versions, what differs from the proposal, and code examples checked by a test. `DERIVING_THE_UI.md`: how the first canvas and steps views were derived from reference recordings, since replaced. `DESIGN.md`: the design tokens, the rules the desk and the admin board follow, and where each came from |
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

The second model answers nearly everything and the gate removes a third of what it drafts. Its first runs exposed a gap: it quoted a chart's axis text ("5.1 4.4 3.6 3.3 4.1 2025f") and the number check passed because 4.4 was in the blob. The gate now requires a number to appear on a line with at least three words, so figure labels do not count; that claim is removed and the test that proves it is in `tests/test_core.py`. Its remaining wrong-and-confident answers were each checked against their quotes: none states a false figure. They are true, verified sentences that answer a different question (first-half actual growth when the full-year projection was asked; quarter-on-quarter when year-on-year was anchored) or that reply to an unanswerable question with related facts. On 26 October the figure-label rule was found removing a true claim, "Real GDP grew by 6.3 percent year-on-year (y/y) in Q4 2025": the quote wrapped onto a short second line and the rule counted "2025" and "Q4" as labels. Two corrections, both tested: years and quarter marks are dates, not figures, and a short line directly after a sentence line that still carries a word is the sentence's wrapped tail. A bare number after a caption line is still a label. The saved results were measured with the stricter rule, so they overstate removals slightly; the next three-run measurement replaces them.

The gate cannot judge relevance in general, but one case is plain: a question that asks for a figure (growth, share, how many, a rate) answered by a set of claims none of which states one. The gate now removes that whole set, with the reason, and the retry asks for the figure; years and quarter marks do not count as figures. On three runs that took the larger model's wrong-and-confident from 2 to 4 down to 1 to 2 and every unanswerable question to a correct refusal, at the cost of one more timid answer. What is left is one kind: a true figure for a different reading of the question, every run on the cross-report question about 2025 (it gives the first-half actual instead of the full-year projection) and once on the fourth-quarter question (quarter-on-quarter instead of year-on-year). Plain code cannot see that. The brief counts a confident irrelevant answer as a failure, so the scorer keeps counting it, and it is a reviewer's job or a sharper question.

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

Open http://127.0.0.1:8765. It starts with one question box and nothing else to read. The box holds the choices: **+** for browsing reports and past work, which reports to search, and whether you want an answer, a table across reports, or exact words. A few suggested questions sit under it until you type. The rest of the desk arrives when it has a job: send a question and the box moves to the foot of the page; the answer brings the page panel; a slim strip on the left opens the side panel when asked, and remembers if you keep it open. "New question" returns to the start.

The desk then has three panels, which can be dragged wider or narrower:

1. **Reports, on the left, when opened.** Every edition you may search, as a timeline. Tick the ones to search; select a name to read it. Under them, your past questions and your tables.
2. **The work, in the middle.** Ask a question and watch the real work as it happens: the search and what it found, the draft with a live timer, the check on every quote, and a retry with the reason if the first draft fails. These lines come from the server (`POST /api/ask/stream`, server-sent events), not from timers. The words of a draft are never sent: a claim appears only after it has passed the check. Stop ends the wait; a model call already started finishes on the server, and its answer stays in your history. The steps then fold into one line, "Checked in 7.2 s", which reopens.
3. **The page, on the right.** Selecting a citation opens the page with the quote highlighted, and a line links the claim to the marked words for a moment. Hovering the citation or the marked words brings the line back; scrolling dismisses it. Hovering a citation also previews the quote. A strip shows where in the report the question's passages sit, and finds words in that report. Select words on the page to ask about them. The original PDF opens from here when the file is on the server.

**Table across reports** in the question box, or **Ask across all reports** under an answer, runs the question inside each report and builds a table, one row per question, one column per report, each cell a checked claim with its page or a plain "Not in this report". Add a row by typing a question; only the new cells are asked. Also thumbs up or down, with a reason for down, and **Ask someone to review**, which writes a log entry for a person. Nothing happens unless the button is pressed. **Exact words** in the question box searches the same pages with no model.

Keys: `/` goes to the question box, `[` and `]` turn the page, `Esc` clears the proof line. Reduced-motion settings are respected.

**Uploads.** "Upload a report" in the **+** menu, or dropping a PDF on the page, adds it to a private library of that user's own (`POST /api/me/uploads`, up to 50 MB and 500 pages, 20 uploads each). It goes through the same search, quote check and citations as any report, and through the same grant, so no other user can search it, read its pages or fetch its file. The admin board leaves these libraries out, and they cannot be granted to anyone. Deleting one asks first in a dialog, then removes its pages, its search index, its file, and the stored answers and table cells that used its text; the questions stay in the person's history. A scan with no readable text is refused with that reason. This privacy is only as strong as the login, and the demo's login is a picker: anyone can choose any demo user, so real confidential documents wait for real sign-in. An admin can still see the wording of questions people ask, and a review request deliberately shows that answer to a person.

A shareable link can preselect everything: `/?user=bob&q=...&run=1`.

A second page, **/dashboard**, is the admin board, behind a token typed once per device (`ADMIN_TOKEN`, `demo` when unset). It opens with a brief, one sentence of counts since this device last looked, silent when nothing happened and with a line saying why; a three-card explainer on the first visit; then what to do first (review requests waiting, pages flagged for OCR, thumbs-down reasons to read, a stale or failing question-set run), each with its count and the reason it is there, and only when the count is not zero. Below: libraries with pages and flagged pages, questions by user, people (ratings and open review requests), the last twelve questions across users with their outcome, and every saved evaluation run. Also on the board: a coverage ring (share of pages with extracted text), the sources (database, model endpoint, saved runs, each with where it is and when last used), a test console that asks as a user and shows what the check kept and removed, a reports-by-edition timeline per library (dot size is how often users' answers cited the report, a small bar splits that by user, dashed links join editions), and API keys. One screen, in the home-board idiom: counts and names in panels, text bars, tabular figures, the explainer folded behind one link. The arrangement is a JSON document per owner, not markup: `GET`/`PUT`/`DELETE /api/admin/layout` read, replace and reset it, the page builds its grid from it, and plain code validates it (known widget types only, spans 1 to 12, optional x, y, h and a settings object kept for a later builder). The pilot writes one thing back: expand on a panel sets its span to 12 and saves; shrink or Escape restores the catalogue width. The widget catalogue is code, so a document arranges and configures what exists and cannot invent a type. The board loads in parts: the brief, key figures and sources first (`/api/admin/board?parts=head`), then each panel fetches only its own slice (`/api/admin/widget/{type}`) when it scrolls into view, and keeps it, so expanding and shrinking re-fill from memory. The timeline graph, the heaviest part, is never computed for a board that does not show it.

Every answer is stored with its audit row exactly as it was shown (claims, rejected claims, sources, trace), and **/api/answer/{id}?user=** replays it for the user who asked, in milliseconds and without a model call. "Open" on a board's answer rows goes to the desk with that answer already built, marked as saved, with its date. Questions asked before answers were stored cannot reopen; they offer to ask again. This is the history line from the brief.

Each user has a workspace of their own at **/dashboard?user=NAME**, linked from the desk as "Workspace". It says what it is based on (every report, in full, with the phrase most particular to each and how much of it has been used), then what to do next: an answer a newer edition may change, one that is unreliable, why a question got no answer, and subjects the reports cover that nobody has asked about, each with its reason and the button that does it. Beside that sit **what you've found**, every checked claim from a stored answer with its page, plus questions (grouped, with outcome) and tables. Explore holds the charts: a table of topics against editions, each report as a strip of its pages, and the days asked. One filter row and every selection apply to the whole page, are kept in the address, and Escape clears them. **Add a report** opens a drop dialog on the page, and the new report joins the sources as soon as it is read.

**How we got this** plays any recorded answer as a flow of the steps that really happened: the reports searched, the attempts, what the check kept and removed and why, ending on the quote or on the reason for a refusal. The page opens beside the flow, as the original PDF when the file is in `data/pdfs` and otherwise as its text with the quote marked. Each report can be read the other way, from pages used to the questions that used them. Answers from before steps were stored say so, and offer to run again.

**Dashboards and reports** are saved inside the app (`/api/me/boards`). A dashboard's blocks stay live. A report holds only checked claims, stored as a pointer to the answer so the words cannot be edited or forged, plus notes marked as the person's own, and prints to PDF.

**The assistant** in the workspace explains the page, filters it, and builds dashboards and reports. It never says what a report contains: those questions go to the desk, where each claim is checked. **It is plug and play.** With no key it follows fixed steps. Copy `env.example` to `.env`, set one key, and restart `python -m ra serve`: answers use the model and the assistant takes free conversation, with no code change. A model's reply is untrusted: unknown actions are dropped, a sentence with a number the page does not hold is replaced, and any failure falls back to the fixed steps.

**Find, without a model.** "Exact words" in the question box (and `GET /api/find?user=&q=`) searches the same FTS5 index the pipeline retrieves from and returns pages with the words marked, in milliseconds, with no model call. Every word must appear unless you say otherwise: a phrase in quotes is exact, OR widens, NOT or a leading minus excludes, parentheses group, and a named edition narrows to it as in a question. Every word and phrase is quoted for FTS5, so punctuation cannot break the query. Use it when you know the words; use Ask when you want a claim with a checked quote.

**Helper.** `helper/` is a separate layer that tells a new user where to go: a Help button and `?` open a panel with examples to try (from the person's own history) and what to do next, and a six-stop tour highlights the page. It runs in front of the app as a reverse proxy (`helper/serve.py`), so the app's code is untouched and a test fails if it ever mentions the helper. See `helper/README.md`.

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

Needs `pdftotext` (poppler). 58 tests, including one that fails if a code example in `docs/TECH_STACK.md` stops matching the file it names. Rules were broken one at a time to confirm the tests catch them: access join, quote check, number check, citable pages, retry, the two-column fix, four scoring rules, and the feedback owner check, rating check, the no-auto-review rule, and the trace (libraries by grant, retry, empty search).

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
