import json

from ra.answer import ask
from ra.db import connect
from ra.ingest import ingest_pdf, split_passages
from .pdfmaker import make_pdf_positioned
from ra.ground import Claim, check_claim
from ra.llm import ClaudeLLM, ScriptedLLM, parse_claims
from ra.retrieve import retrieve

GOOD = lambda doc: Claim("Palm oil output rose 4.2 percent in 2024.", doc, 1, "Palm oil output rose 4.2 percent in 2024")
ALLOWED = lambda doc: {(doc, 1), (doc, 2)}


def doc_id(db, title):
    return db.execute("SELECT id FROM documents WHERE title=?", (title,)).fetchone()["id"]


# ingestion
def test_ingest_keeps_page_numbers_and_flags_blank_page(db):
    assert db.report["pages"] == 3
    assert db.report["low_text_pages"] == [3]
    assert db.execute("SELECT needs_ocr FROM documents WHERE id=?", (db.report["doc_id"],)).fetchone()[0] == 1
    pages = {r["page"] for r in db.execute("SELECT page FROM passages WHERE doc_id=?", (db.report["doc_id"],))}
    assert pages == {1, 2}


# retrieval and access
def test_retrieve_returns_cited_page(db):
    hits = retrieve(db, "alice", "How did palm oil output change?")
    assert hits and hits[0].page == 1 and hits[0].title == "Sector Outlook 2025"


def test_access_is_enforced_inside_retrieval(db):
    assert retrieve(db, "alice", "merger Orion Holdings") == []
    assert retrieve(db, "bob", "merger Orion Holdings")
    assert retrieve(db, "nobody", "palm oil") == []


def test_model_is_never_called_when_user_has_no_access(db):
    llm = ScriptedLLM(GOOD(1))
    res = ask(db, llm, "alice", "What merger did the board approve?")
    assert res.status == "not_found" and llm.calls == []


# the gate
def test_gate_accepts_exact_quote_ignoring_case_and_spacing(db):
    d = doc_id(db, "Sector Outlook 2025")
    page = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=1", (d,)).fetchone()["text"]
    c = Claim("Palm oil output rose 4.2 percent.", d, 1, "PALM  OIL output rose   4.2 percent")
    assert check_claim(c, page, ALLOWED(d)).ok


def test_gate_rejects_fabricated_quote(db):
    d = doc_id(db, "Sector Outlook 2025")
    page = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=1", (d,)).fetchone()["text"]
    v = check_claim(Claim("Output doubled.", d, 1, "Palm oil output doubled in 2024"), page, ALLOWED(d))
    assert not v.ok and "not on the cited page" in v.reason


def test_gate_rejects_quote_from_the_wrong_page(db):
    d = doc_id(db, "Sector Outlook 2025")
    page2 = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=2", (d,)).fetchone()["text"]
    assert not check_claim(GOOD(d), page2, ALLOWED(d)).ok


def test_gate_rejects_number_not_in_quote(db):
    d = doc_id(db, "Sector Outlook 2025")
    page = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=1", (d,)).fetchone()["text"]
    c = Claim("Palm oil output rose 5.2 percent in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    v = check_claim(c, page, ALLOWED(d))
    assert not v.ok and "5.2" in v.reason


def test_gate_rejects_short_quote_and_uncited_page(db):
    d = doc_id(db, "Sector Outlook 2025")
    page = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=1", (d,)).fetchone()["text"]
    assert not check_claim(Claim("x", d, 1, "palm oil"), page, ALLOWED(d)).ok
    assert not check_claim(GOOD(d), page, set()).ok


# the loop
def test_answered_on_first_attempt_and_audited(db):
    d = doc_id(db, "Sector Outlook 2025")
    res = ask(db, ScriptedLLM([GOOD(d)]), "alice", "How did palm oil output change?")
    assert res.status == "answered" and res.attempts == 1 and len(res.claims) == 1
    row = db.execute("SELECT * FROM audit").fetchone()
    assert row["user"] == "alice" and row["status"] == "answered" and row["cited"] == f"{d}:1"


def test_retry_once_with_feedback_then_answer(db):
    d = doc_id(db, "Sector Outlook 2025")
    bad = Claim("Output doubled.", d, 1, "Palm oil output doubled in 2024")
    llm = ScriptedLLM([bad], [GOOD(d)])
    res = ask(db, llm, "alice", "How did palm oil output change?")
    assert res.status == "answered" and res.attempts == 2
    assert llm.calls[0][2] is None and "rejected" in llm.calls[1][2][0]


def test_refuses_when_nothing_survives_the_gate(db):
    d = doc_id(db, "Sector Outlook 2025")
    bad = Claim("Output doubled.", d, 1, "Palm oil output doubled in 2024")
    res = ask(db, ScriptedLLM([bad], [bad]), "alice", "How did palm oil output change?")
    assert res.status == "not_found" and res.claims == [] and len(res.rejected) == 2 and res.closest


def test_real_quote_with_wrong_number_never_reaches_the_user(db):
    d = doc_id(db, "Sector Outlook 2025")
    wrong = Claim("Output rose 40 percent.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    res = ask(db, ScriptedLLM([wrong], [wrong]), "alice", "palm oil output")
    assert res.status == "not_found"


def test_model_cannot_cite_a_document_it_was_not_given(db):
    memo = doc_id(db, "Strategy Memo")
    steal = Claim("The board approved a merger.", memo, 1, "The board approved a merger with Orion Holdings")
    res = ask(db, ScriptedLLM([steal], [steal]), "alice", "palm oil output")
    assert res.status == "not_found"
    assert any("not in the retrieved" in why for _, why in res.rejected)


def test_no_claims_returned_is_a_refusal(db):
    assert ask(db, ScriptedLLM([], []), "alice", "palm oil output").status == "not_found"


# the real-model adapter, with a fake client
def test_parse_claims_handles_fences_and_garbage():
    body = '```json\n{"claims":[{"text":"t","doc_id":1,"page":2,"quote":"q"}]}\n```'
    assert parse_claims(body) == [Claim("t", 1, 2, "q")]
    assert parse_claims("sorry, no") == []
    assert parse_claims('{"claims":[{"text":"t"}]}') == []


def test_claude_adapter_builds_prompt_and_parses_reply(db):
    class Blk:
        type = "text"
        text = json.dumps({"claims": [{"text": "t", "doc_id": 1, "page": 1, "quote": "a long enough quote"}]})

    class Msgs:
        def create(self, **kw):
            self.kw = kw
            return type("M", (), {"content": [Blk()]})()

    class Client:
        messages = Msgs()

    llm = ClaudeLLM(client=Client())
    claims = llm.draft("q?", retrieve(db, "alice", "palm oil output"))
    assert claims and "ONLY the passages" in Client.messages.kw["system"]
    assert "doc_id=" in Client.messages.kw["messages"][0]["content"]


# real-report lessons
def test_two_column_page_keeps_a_sentence_that_runs_across_lines_contiguous(tmp_path):
    left = [(50, 780 - 14 * i, t) for i, t in enumerate(["The reform will lower costs for households and", "small firms across the country over time."])]
    right = [(320, 780 - 14 * i, t) for i, t in enumerate(["Exports rose 3.1 percent in the", "second half of the year."])]
    pdf = make_pdf_positioned(tmp_path / "cols.pdf", left + right)
    con = connect()
    ingest_pdf(con, "lib", "Two columns", pdf)
    page = con.execute("SELECT text FROM pages").fetchone()["text"]
    quote = "The reform will lower costs for households and small firms across the country over time"
    from ra.ground import normalise

    assert normalise(quote) in normalise(page)


def test_long_block_without_blank_lines_is_split():
    block = "\n".join(f"line {i} " + "word " * 12 for i in range(60))
    parts = split_passages(block)
    assert len(parts) > 3 and all(len(p) < 1800 for p in parts)


def test_named_edition_narrows_search_and_each_edition_gets_slots(db, tmp_path):
    from ra.retrieve import named_documents
    from .pdfmaker import make_pdf

    a = make_pdf(tmp_path / "a.pdf", [["Growth is projected at 4.3 percent in 2024.", "Palm oil output was steady."]])
    b = make_pdf(tmp_path / "b.pdf", [["Growth is projected at 5.1 percent in 2025.", "Palm oil output was steady."]])
    ingest_pdf(db, "public", "Monitor April 2024", a)
    ingest_pdf(db, "public", "Monitor October 2025", b)
    ids = named_documents(db, "alice", "What did the April 2024 monitor say about growth?")
    assert len(ids) == 1
    hits = retrieve(db, "alice", "What did the April 2024 monitor say about growth?")
    assert {h.title for h in hits} == {"Monitor April 2024"}
    both = retrieve(db, "alice", "Compare growth in the April 2024 and October 2025 monitors")
    assert {h.title for h in both} == {"Monitor April 2024", "Monitor October 2025"}


def test_named_edition_never_widens_access(db, tmp_path):
    from ra.retrieve import named_documents
    from .pdfmaker import make_pdf

    c = make_pdf(tmp_path / "c.pdf", [["Secret growth plan for the year."]])
    ingest_pdf(db, "restricted", "Memo April 2024", c)
    assert named_documents(db, "alice", "What does the April 2024 memo say?") == []
    assert named_documents(db, "bob", "What does the April 2024 memo say?") != []


# the trace: what the Steps view shows
def kinds(res):
    return [s["kind"] for s in res.trace]


def test_trace_of_a_first_attempt_answer(db):
    d = doc_id(db, "Sector Outlook 2025")
    res = ask(db, ScriptedLLM([GOOD(d)]), "alice", "How did palm oil output change?")
    assert kinds(res) == ["search", "draft", "check", "outcome"]
    search = res.trace[0]
    assert search["found"] > 0 and search["documents"]["Sector Outlook 2025"] > 0 and "palm" in search["query"]
    assert res.trace[1]["tag"] == "Model call" and res.trace[2]["tag"] == "Plain code"
    assert res.trace[2]["kept"] == 1 and res.trace[2]["removed"] == 0
    assert all(isinstance(s["ms"], float) and s["ms"] >= 0 for s in res.trace if "ms" in s)


def test_trace_records_the_retry_and_why(db):
    d = doc_id(db, "Sector Outlook 2025")
    bad = Claim("Output doubled.", d, 1, "Palm oil output doubled in 2024")
    res = ask(db, ScriptedLLM([bad], [GOOD(d)]), "alice", "How did palm oil output change?")
    assert kinds(res) == ["search", "draft", "check", "retry", "draft", "check", "outcome"]
    assert res.trace[2]["reasons"] == ["quote is not on the cited page"] and res.trace[2]["removed"] == 1
    assert "rejected" in res.trace[3]["feedback"][0]


def test_trace_keeps_the_empty_result(db):
    res = ask(db, ScriptedLLM(), "alice", "What merger did the board approve with Orion Holdings?")
    assert kinds(res) == ["search", "outcome"] and res.trace[0]["found"] == 0 and res.trace[1]["status"] == "not_found"


def test_trace_never_names_a_library_the_user_cannot_see(db):
    alice = ask(db, ScriptedLLM(), "alice", "palm oil").trace[0]
    bob = ask(db, ScriptedLLM(), "bob", "palm oil").trace[0]
    assert alice["libraries"] == ["public"] and bob["libraries"] == ["public", "restricted"]
    assert "Strategy Memo" not in alice["documents"]


def test_trace_names_the_edition_a_question_selects(db, tmp_path):
    from .pdfmaker import make_pdf

    ingest_pdf(db, "public", "Monitor April 2024", make_pdf(tmp_path / "a.pdf", [["Growth is projected at 4.3 percent in 2024."]]))
    res = ask(db, ScriptedLLM(), "alice", "What did the April 2024 monitor project for growth?")
    assert res.trace[0]["editions"] == ["Monitor April 2024"]


def test_openai_compat_adapter_parses_and_strips_thinking():
    from ra.llm import OpenAICompatLLM

    seen = {}

    def fake_post(body):
        seen.update(body)
        return {"choices": [{"message": {"content": '<think>musing</think>{"claims":[{"text":"GDP grew 5.2 percent","doc_id":5,"page":10,"quote":"full-year growth to 5.2 percent"}]}'}}]}

    llm = OpenAICompatLLM(model="Hermes-4-70B", api_key="x", post=fake_post)
    claims = llm.draft("q", [], None)
    assert [c.quote for c in claims] == ["full-year growth to 5.2 percent"]
    assert seen["model"] == "Hermes-4-70B" and seen["messages"][0]["role"] == "system"


def test_edition_words_leave_the_query_once_they_filter(db, tmp_path):
    from ra.ingest import ingest_pdf
    from ra.retrieve import search_terms

    from .pdfmaker import make_pdf

    # A page stuffed with the edition's own name would outrank the forecast if "october" and "2025" stayed in the query.
    pdf = make_pdf(tmp_path / "oct.pdf", [
        ["October 2025 monitor. October 2025 edition. Published October 2025. See the October 2025 monitor."],
        ["Growth is projected to ease to 4.1 percent in 2025."]])
    ingest_pdf(db, "public", "Monitor October 2025", pdf)
    q = "What growth did the October 2025 monitor project for 2025?"
    assert "october" not in search_terms(q, [1]).lower() and "monitor" not in search_terms(q, [1]).lower()
    assert search_terms(q, []) == q
    top = retrieve(db, "alice", q, k=1)[0]
    assert top.page == 2


def test_a_comparison_with_no_edition_named_sees_every_report(db, tmp_path):
    from ra.ingest import ingest_pdf
    from ra.retrieve import DOC_FLOOR

    from .pdfmaker import make_pdf

    # One report says "growth" on every page; the other says it once. Without a floor the second never appears.
    loud = make_pdf(tmp_path / "loud.pdf", [[f"Growth growth growth, page {i} on growth."] for i in range(1, 7)])
    quiet = make_pdf(tmp_path / "quiet.pdf", [["Weather was mild."], ["Growth in the fourth quarter was 6.3 percent."]])
    ingest_pdf(db, "public", "Loud Report", loud)
    ingest_pdf(db, "public", "Quiet Report", quiet)
    got = retrieve(db, "alice", "How did growth compare between the reports?", k=4)
    titles = {p.title for p in got}
    assert "Quiet Report" in titles and "Loud Report" in titles
    assert sum(1 for p in got if p.title == "Loud Report") <= 4 - 1  # the quiet report kept a slot
    assert DOC_FLOOR >= 1


def test_a_number_that_appears_only_in_figure_labels_is_rejected(db):
    from ra.ground import check_claim

    d = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    chart = "FIGURE 3\nOutput growth is projected to moderate\nPercent\n6\n5.1\n4.4\n3.6\n4.1\n2025f\n2026f"
    db.execute("UPDATE pages SET text = text || ? WHERE doc_id=? AND page=1", ("\n" + chart, d))
    db.commit()
    page = db.execute("SELECT text FROM pages WHERE doc_id=? AND page=1", (d,)).fetchone()["text"]
    from_chart = Claim("Growth is projected to moderate to 4.4 percent.", d, 1, chart)
    v = check_claim(from_chart, page, {(d, 1)})
    assert not v.ok and "figure label" in v.reason and "4.4" in v.reason
    in_prose = Claim("Palm oil output rose 4.2 percent in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    assert check_claim(in_prose, page, {(d, 1)}).ok


def test_a_figure_question_is_refused_when_no_claim_states_a_figure(db):
    from ra.answer import NO_FIGURE, ask
    from ra.llm import ScriptedLLM

    d = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    prose = Claim("Palm oil output rose in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    llm = ScriptedLLM([prose], [prose])
    res = ask(db, llm, "alice", "How much did palm oil output grow in 2024?")
    assert res.status == "not_found" and res.attempts == 2
    assert all(why == NO_FIGURE for _, why in res.rejected) and len(res.rejected) == 2
    assert any(NO_FIGURE in f for f in llm.calls[1][2])


def test_a_figure_question_is_answered_when_one_claim_states_the_figure(db):
    from ra.answer import ask
    from ra.llm import ScriptedLLM

    d = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    prose = Claim("Palm oil output rose in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    figure = Claim("Palm oil output rose 4.2 percent in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    res = ask(db, ScriptedLLM([prose, figure]), "alice", "How much did palm oil output grow in 2024?")
    assert res.status == "answered" and len(res.claims) == 2 and res.attempts == 1


def test_a_question_that_does_not_ask_for_a_figure_accepts_prose(db):
    from ra.answer import ask
    from ra.ground import asks_for_figure
    from ra.llm import ScriptedLLM

    d = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    prose = Claim("Palm oil output rose in 2024.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    q = "What does the outlook say about palm oil output?"
    assert not asks_for_figure(q)
    assert ask(db, ScriptedLLM([prose]), "alice", q).status == "answered"


def test_years_and_quarter_marks_are_not_figures():
    from ra.ground import asks_for_figure, figures_in

    assert figures_in("Growth is expected to ease in 2026 after an upside surprise in 2025.") == set()
    assert figures_in("Growth was 5.1 percent in 2024 and 2.9 percent in Q2 2023.") == {"5.1", "2.9"}
    assert figures_in("The 2025f projection is 4.1 percent") == {"4.1"}
    assert asks_for_figure("What GDP growth is projected for Malaysia in 2026?")
    assert asks_for_figure("How many electric vehicles were sold in Malaysia in 2025?")
    assert not asks_for_figure("Who is the chief executive officer of PETRONAS?")


def test_a_model_failure_is_a_refusal_with_the_reason_in_the_trace(db):
    class Down:
        name = "down"

        def draft(self, q, passages, feedback=None):
            raise TimeoutError("The read operation timed out")

    res = ask(db, Down(), "alice", "How much did palm oil output grow in 2024?")
    assert res.status == "not_found" and res.attempts == 1
    draft = [s for s in res.trace if s["kind"] == "draft"][0]
    assert draft["claims"] == 0 and draft["error"].startswith("TimeoutError")
    assert not [s for s in res.trace if s["kind"] == "retry"]


def test_openai_compat_adapter_retries_once_on_a_dropped_connection():
    from ra.llm import OpenAICompatLLM

    calls = []

    def post(body):
        calls.append(body)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return {"choices": [{"message": {"content": '{"claims":[]}'}}]}

    llm = OpenAICompatLLM(model="m", api_key="k", post=post)
    assert llm.draft("q", []) == [] and len(calls) == 2 and llm.last_error.startswith("TimeoutError")


def test_a_document_filter_narrows_retrieval_and_never_widens_access(db):
    from ra.retrieve import retrieve

    pub = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    memo = db.execute("SELECT id FROM documents WHERE title='Strategy Memo'").fetchone()["id"]
    assert {p.doc_id for p in retrieve(db, "bob", "board approved merger output", 12)} == {pub, memo}
    assert {p.doc_id for p in retrieve(db, "bob", "board approved merger output", 12, only={memo})} == {memo}
    assert retrieve(db, "alice", "board approved merger", 12, only={memo}) == []  # alice holds no grant on the memo's library
    res = ask(db, ScriptedLLM([]), "bob", "What did the board approve?", only={memo})
    assert res.trace[0]["only"] == [memo] and {p.doc_id for p in res.closest} == {memo}
