# Research assistant demo slice

Ask a question across a PDF library and get claims that are each checked against the page they cite.

Status: phase 3 of the demo slice. Working core, an evaluation harness, a library of five real public reports, and a small web demo. No real-model run yet.

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
| Two reports, 3 questions | 0 of 3 get both pages |

Two things made the difference: reading PDFs in reading order, because layout mode interleaves two-column pages and breaks quotes, and treating "the April 2024 monitor" in a question as a date filter. The cross-report misses need a model to rewrite a comparison into one query per report. That is phase 3. I did not tune further on these 22 questions, because that would overfit.

Baselines with no model, as a floor:

| | Correct | Timid | Correct refusal | Wrong and confident |
|---|---|---|---|---|
| extractive: quote the best sentence | 1 of 15 | 0 | 0 of 7 | 21 |
| threshold: refuse if few question words match | 0 of 15 | 9 | 4 of 7 | 9 |

No model-free approach comes close. The gate cannot catch an answer that is real but irrelevant, so a model has to judge relevance and refuse. The real-model result is not in yet.

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

Movement: the camera eases from node to node as they appear, a left list highlights where you are and jumps on click, and there are zoom buttons and a show-everything button. Drag to pan, Ctrl or pinch to zoom. `?reveal=0` skips the staged reveal, and reduced-motion settings are respected. The staged reveal is presentation only: the server returns the whole result at once, and every node shows real data from that result.

A shareable link can preselect everything: `/?user=bob&q=...&run=1`.

- The user picker is a demo, not a login. Access is still enforced in retrieval and on the page route, which the tests check. Feedback and review requests can only be filed on your own answers.
- With `ANTHROPIC_API_KEY` set it uses Claude. Without it, it uses the extractive baseline and shows a banner saying so. That baseline is often irrelevant. Do not present it as the real assistant.
- Labels say what happened, not "verified". The quote check proves the quote is on the page, not that the claim answers the question, and the page says so.
- Tested in headless Chrome on the real library as two users, with a stand-in model that first fabricates a quote and then quotes for real: the fabricated claim shows as Removed, the camera ends on the answer, and a 390 px phone width is readable with no sideways scroll. Those browser checks were by hand, not part of the test suite. Hover-expand and drag were not exercised.

## Run the tests

```
uv run --python 3.14 --with pytest pytest -q
```

Needs `pdftotext` and `pdfinfo` (poppler). 49 tests. Rules were broken one at a time to confirm the tests catch them: access join, quote check, number check, citable pages, retry, the two-column fix, four scoring rules, and the feedback owner check, rating check and the no-auto-review rule.

## Limits, stated plainly

- Keyword search only. No embeddings yet, so a question phrased with different words than the report may miss. Comparisons across reports miss one side today.
- No OCR. Scanned pages are flagged, not read.
- The gate checks that a quote is on the page and that numbers match. It does not check that the claim means what the quote means. A reviewer can still disagree with a claim that passes.
- Numbers written as words are not checked.
- The Claude adapter (`ra/llm.py`) is tested with a fake client only. It has not been run against the real API.
- Unit tests use small synthetic PDFs. The 22-question set is small, so a score on it is a signal, not a measurement of production quality.
- Everything so far is World Bank economic reports. The client's own research library may differ in layout, language and tables.
