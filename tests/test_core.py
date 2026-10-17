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
