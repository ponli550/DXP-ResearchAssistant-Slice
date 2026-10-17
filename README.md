# Research assistant demo slice

Ask a question across a PDF library and get claims that are each checked against the page they cite.

Status: phase 1 of the demo slice. A working core with tests. No web UI, no real-model run, no real reports yet.

## How an answer is made

1. **Ingest.** Each PDF is read page by page with `pdftotext`, keeping page numbers. Pages with almost no text are flagged as needing OCR.
2. **Retrieve.** SQLite full-text search (BM25). The access rule is in the SQL query, so a user only ever sees passages from libraries they hold a grant for.
3. **Draft.** A model reads only those passages and returns claims, each with the exact quote it relies on.
4. **Gate.** Plain string checks, no model: the quote must be on the cited page, every number in the claim must be in the quote, and the claim may only cite a page the model was given.
5. **Retry once** with the failure reasons. If nothing survives, the answer is "I could not find this in the selected library", with the closest passages.
6. **Audit.** Every question, outcome and citation is logged.

## Run the tests

```
uv run --python 3.14 --with pytest pytest -q
```

Needs `pdftotext` and `pdfinfo` (poppler). 17 tests. Five rules were broken one at a time to confirm the tests catch them: access join, quote check, number check, citable pages, retry.

## Limits, stated plainly

- Keyword search only. No embeddings yet, so a question phrased with different words than the report may miss.
- No OCR. Scanned pages are flagged, not read.
- The gate checks that a quote is on the page and that numbers match. It does not check that the claim means what the quote means. A reviewer can still disagree with a claim that passes.
- Numbers written as words are not checked.
- The Claude adapter (`ra/llm.py`) is tested with a fake client only. It has not been run against the real API.
- Tests use synthetic one-page PDFs. Nothing has been run on real research reports yet.
