# Tech stack and code examples

What runs today, why it was chosen, how it differs from what the proposal said, and real code from the repository. Every code block below is checked by `tests/test_docs.py` against the file it names, so an example cannot drift from the code without a test failing.

## The stack that runs today

| Layer | Choice | Version here | Why | The proposal said |
|---|---|---|---|---|
| Language | Python | 3.14 (needs 3.12 or later) | One language for pipeline, API and tests | Python (FastAPI) |
| Runner and environments | uv | 0.11 | Reproducible, one command to run anything | not stated |
| PDF text | poppler `pdftotext`, called as a subprocess | 26.10 | Keeps page numbers, no Python dependency, reading-order mode handles two-column pages | Page-aware ingestion, OCR for scans |
| Store and search | SQLite with FTS5 (BM25), standard library `sqlite3` | 3.53 | One file, no server, full-text ranking built in, access rule can live in the SQL | PostgreSQL with vector and full-text search |
| Core runtime dependencies | none | | The whole pipeline is standard library | |
| Web API | FastAPI with uvicorn | 0.143 and 0.54 (pydantic 2.14) | Typed request bodies, easy to test, matches the proposal | FastAPI |
| Front end | One HTML page, plain JavaScript, CSS variables, no build step, no CDN | | Nothing to install or bundle, and it can be read in one sitting | React |
| Model | The `anthropic` SDK, optional, behind a small `LLM` protocol | default model id `claude-sonnet-5-5` | The pipeline never imports a vendor outside `ra/llm.py` | LLM behind a thin interface |
| Tests | pytest, httpx for the API, a hand-written PDF writer | pytest 9.1, httpx 0.28 | No fixtures to commit, and two-column pages can be built on purpose | A graded question set |
| Browser checks | Chrome headless, and jsdom under Node | Chrome 148, Node 24 | Playwright would not install here | not stated |

Runtime dependencies, exactly as `pyproject.toml` declares them: none for the core, `fastapi` and `uvicorn` for the web demo, `anthropic` only when the real model is used.

## Where the slice differs from the proposal, and what that costs

| Proposal | Slice | What this means |
|---|---|---|
| PostgreSQL with vector and full-text | SQLite FTS5 only | No embeddings, so a question phrased unlike the report can miss. SQLite has one writer, so it is not the multi-user answer. |
| React | One plain page | Fine for a demo. A real product needs a component framework, and accessibility and state handling would need a proper pass. |
| Private object storage | Local files and one database file | No storage for uploaded reports, no retention rules. |
| Role-based access | A user to library grants table, enforced in the SQL | The idea is right and tested. The user picker is a demo and not authentication. |
| Evaluation dashboard | A harness that prints scores and writes JSON | The numbers exist. A dashboard does not. |
| Staged pilot | Not started | Not a code question. |

A migration to the proposal's stack changes the store, the search and the front end. The gate, the loop, the trace, the access rule and the question set carry over unchanged, because they depend on `retrieve()` and the `LLM` protocol and not on SQLite or on the page.

## Code examples

### 1. The grounding gate, plain code with no model

A claim is accepted only if its quote is on the page it cites, every number in the claim is in the quote, and it cites a page the model was actually given. The last rule is what stops a model from citing a document it was never shown.

<!-- source: ra/ground.py -->
```python
def check_claim(claim, page_text, allowed_pages):
    """Accept a claim only if its quote is on the cited page and every number in the claim is in the quote.

    allowed_pages is the set of (doc_id, page) the model was actually given. A citation outside it is rejected.
    """
    if (claim.doc_id, claim.page) not in allowed_pages:
        return Verdict(False, "cited a page that was not in the retrieved passages")
    if page_text is None:
        return Verdict(False, "cited page does not exist")
    q = normalise(claim.quote)
    if len(q) < MIN_QUOTE_CHARS:
        return Verdict(False, "quote too short to verify")
    if q not in normalise(page_text):
        return Verdict(False, "quote is not on the cited page")
    missing = numbers_in(claim.text) - numbers_in(claim.quote)
    if missing:
        return Verdict(False, "number in the claim is not in the quote: " + ", ".join(sorted(missing)))
    labels_only = figures_in(claim.text) - numbers_in_prose(claim.quote)
    if labels_only:
        return Verdict(False, "number appears in the quote only as a figure label, not in a sentence: " + ", ".join(sorted(labels_only)))
    return Verdict(True)
```

### 2. Access control inside the search

The access rule is part of the query. A passage the user may not see is never returned, never reaches the model, and can never be cited. There is no later filter that could be forgotten.

<!-- source: ra/retrieve.py -->
```python
_SQL = """
    SELECT p.id, p.doc_id, d.title, p.page, p.text, bm25(passages_fts) AS score
    FROM passages_fts
    JOIN passages p ON p.id = passages_fts.rowid
    JOIN documents d ON d.id = p.doc_id
    JOIN grants g ON g.library_id = d.library_id AND g.user = ?
    WHERE passages_fts MATCH ? {doc_filter}
    ORDER BY score
    LIMIT ?
"""
```

### 3. The answer loop: draft, check, retry once, otherwise refuse

If nothing survives the gate, the second attempt gets the reasons the first failed. One check looks at the set rather than each claim: when the question asks for a figure (growth, share, how many, a rate) and no surviving claim states one, the whole set is removed with that reason, so a true sentence that avoids the number cannot stand in for it. If that also fails, the answer is "not found". Every step is appended to the trace, which is stored with the answer on the audit row.

<!-- source: ra/answer.py -->
```python
    if passages:
        feedback = None
        for attempt in (1, 2):
            result.attempts = attempt
            if attempt == 2:
                trace.append({"kind": "retry", "tag": "Plain code", "feedback": feedback})
            t = time.perf_counter()
            try:
                drafted = llm.draft(question, passages, feedback)
                error = None
            except Exception as e:  # the model did not answer: that is a refusal with a reason, not a crash
                drafted, error = [], f"{type(e).__name__}: {e}"[:200]
            step = {"kind": "draft", "tag": "Model call", "attempt": attempt, "model": getattr(llm, "name", type(llm).__name__), "claims": len(drafted), "ms": _ms(t)}
            if error:
                step["error"] = error
            trace.append(step)
            t = time.perf_counter()
            ok, bad = _verify(con, drafted, allowed)
            if ok and asks_for_figure(question) and not states_a_figure(ok):
                bad += [(c, NO_FIGURE) for c in ok]
                ok = []
            trace.append({"kind": "check", "tag": "Plain code", "attempt": attempt, "kept": len(ok), "removed": len(bad), "reasons": sorted({why for _, why in bad}), "ms": _ms(t)})
            result.rejected.extend(bad)
            if ok:
                result.status, result.claims = "answered", ok
                break
            if error:
                break  # do not retry a model that is down; the trace says why
            feedback = [f"'{c.text}' was rejected: {why}" for c, why in bad] or ["no claims were returned"]
```

### 4. The model sits behind a small interface

The instruction to the model, and the only code that talks to the vendor. Swapping the model means a new adapter in this file and one line in `default_llm` in `ra/web.py` that chooses it. The loop, the gate and the trace do not change.

<!-- source: ra/llm.py -->
```python
SYSTEM = (
    "You answer questions using ONLY the passages provided. Return JSON only, in the form "
    '{"claims":[{"text":"...","doc_id":0,"page":0,"quote":"..."}]}. '
    "Each claim is one statement. Its quote must be copied exactly, word for word, from the cited passage. "
    "Any number in a claim must appear in its quote. If the passages do not answer the question, return {\"claims\":[]}. "
    "Never use outside knowledge."
)
```


<!-- source: ra/llm.py -->
```python
    def draft(self, question, passages, feedback=None):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(question, passages, feedback)}],
        )
        return parse_claims("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))
```

### 5. The page route repeats the access check

Defence in depth: even if a page id is guessed, the join on grants returns nothing for a user without access.

<!-- source: ra/web.py -->
```python
    @app.get("/api/page")
    def page(user: str, doc_id: int, p: int, quote: str = ""):
        con = db()
        row = con.execute(
            "SELECT d.title, pg.text FROM pages pg JOIN documents d ON d.id = pg.doc_id "
            "JOIN grants g ON g.library_id = d.library_id AND g.user = ? WHERE pg.doc_id = ? AND pg.page = ?",
            (user, doc_id, p),
        ).fetchone()
        if not row:
            raise HTTPException(403, "No access to this page, or it does not exist.")
        has_file = bool(con.execute("SELECT source FROM documents WHERE id=?", (doc_id,)).fetchone()["source"])
        return {"title": row["title"], "page": p, "text": row["text"], "span": quote_span(row["text"], quote) if quote else None,
                "pdf": f"/files/{doc_id}?user={user}#page={p}" if has_file else None}
```

### 6. The scoring that decides whether an answer counts

Four outcomes, and the page and the report both have to match. A right number on the wrong page is not correct.

<!-- source: ra/evalset.py -->
```python
        if q["facts"]:
            quotes = normalise(" ".join(c.quote for c in res.claims))
            ok = res.status == "answered" and all(
                any((c.doc_id, c.page) in {(d, pg) for pg in pages} for c in res.claims) and normalise(f["must"]) in quotes
                for (d, pages), f in facts
            )
            outcome = "correct" if ok else ("timid" if res.status == "not_found" else "wrong_and_confident")
        else:
            outcome = "wrong_and_confident" if res.status == "answered" else "correct_refusal"
```

### 7. A test that proves a rule, not just a result

This test would fail if the grants join were removed from the search. That was confirmed by removing it. See the list of rules that were broken on purpose in the README.

<!-- source: tests/test_core.py -->
```python
def test_access_is_enforced_inside_retrieval(db):
    assert retrieve(db, "alice", "merger Orion Holdings") == []
    assert retrieve(db, "bob", "merger Orion Holdings")
    assert retrieve(db, "nobody", "palm oil") == []
```

### 8. A PDF written by hand for the tests

So the tests need no fixture files, and a two-column page can be built on purpose, which is how the reading-order fix was proved.

<!-- source: tests/pdfmaker.py -->
```python
def make_pdf(path, pages):
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("PAGES_PLACEHOLDER")
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for ln in lines:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops)
        cid = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        pid = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {cid} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>")
        kids.append(f"{pid} 0 R")
```

## Running it

```sh
# tests (needs poppler for pdftotext and pdfinfo)
uv run --python 3.14 pytest -q

# ingest a report into a library, grant a user, serve the demo
uv run --python 3.14 python -m ra --db data/ra.db ingest wb "Malaysia Economic Monitor April 2026" data/pdfs/wb-mem-2026-04.pdf
uv run --python 3.14 python -m ra --db data/ra.db grant alice wb
uv run --python 3.14 python -m ra --db data/ra.db serve --port 8765

# score an assistant on the 22 questions
uv run --python 3.14 python -m ra --db data/ra.db eval --llm extractive    # or threshold, or claude
```

## What is not covered by any of this

The real model has not been run on the question set, so nothing here says how well the assistant answers. The Claude adapter is tested with a fake client only. The code shows how the checks work, not that the answers are good.
