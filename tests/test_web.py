import pytest
from fastapi.testclient import TestClient

from ra.db import connect
from ra.ground import Claim
from ra.ingest import ingest_pdf
from ra.llm import ScriptedLLM
from ra.web import INDEX_HTML, create_app, quote_span

from .pdfmaker import make_pdf

QUOTE = "Palm oil output rose 4.2 percent in 2024"


@pytest.fixture
def web(tmp_path):
    path = str(tmp_path / "web.db")
    con = connect(path)
    ingest_pdf(con, "public", "Sector Outlook 2025", make_pdf(tmp_path / "o.pdf", [["Palm oil output rose 4.2 percent in 2024, driven by higher yields."]]))
    ingest_pdf(con, "restricted", "Strategy Memo", make_pdf(tmp_path / "m.pdf", [["The board approved a merger with Orion Holdings for 900 million."]]))
    for u, lib in [("alice", "public"), ("bob", "public"), ("bob", "restricted")]:
        con.execute("INSERT INTO grants VALUES (?,?)", (u, lib))
    con.commit()
    doc = con.execute("SELECT id FROM documents WHERE title='Sector Outlook 2025'").fetchone()["id"]
    memo = con.execute("SELECT id FROM documents WHERE title='Strategy Memo'").fetchone()["id"]
    llm = ScriptedLLM()
    client = TestClient(create_app(path, lambda: (llm, "scripted")))
    return client, llm, doc, memo, path


def good(doc):
    return Claim("Palm oil output rose 4.2 percent in 2024.", doc, 1, QUOTE)


def test_users_and_model(web):
    client, *_ = web
    j = client.get("/api/users").json()
    assert j["users"] == ["alice", "bob"] and j["model"] == "scripted"


def test_ask_returns_cited_claims_and_logs_it(web):
    client, llm, doc, _, path = web
    llm.responses = [[good(doc)]]
    j = client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"}).json()
    assert j["status"] == "answered" and j["claims"][0]["title"] == "Sector Outlook 2025" and j["claims"][0]["page"] == 1
    assert connect(path).execute("SELECT user, status FROM audit").fetchone()["user"] == "alice"


def test_a_removed_claim_is_reported_not_shown(web):
    client, llm, doc, *_ = web
    fake = Claim("Output doubled.", doc, 1, "Palm oil output doubled in 2024")
    llm.responses = [[fake], [good(doc)]]
    j = client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"}).json()
    assert j["status"] == "answered" and j["rejected"] == 1 and len(j["claims"]) == 1


def test_user_without_grants_is_refused(web):
    client, *_ = web
    assert client.post("/api/ask", json={"user": "mallory", "question": "palm oil"}).status_code == 403


def test_restricted_library_is_not_searchable_by_alice(web):
    client, llm, doc, memo, _ = web
    llm.responses = [[Claim("The board approved a merger.", memo, 1, "The board approved a merger with Orion Holdings")]]
    j = client.post("/api/ask", json={"user": "alice", "question": "What merger did the board approve?"}).json()
    assert j["status"] == "not_found" and j["closest"] == [] and len(llm.responses) == 1


def test_page_route_enforces_access_and_returns_highlight(web):
    client, _, doc, memo, _ = web
    ok = client.get("/api/page", params={"user": "alice", "doc_id": doc, "p": 1, "quote": QUOTE}).json()
    assert ok["title"] == "Sector Outlook 2025" and ok["text"][slice(*ok["span"])].lower() == QUOTE.lower()
    assert client.get("/api/page", params={"user": "alice", "doc_id": memo, "p": 1}).status_code == 403
    assert client.get("/api/page", params={"user": "bob", "doc_id": memo, "p": 1}).status_code == 200


def test_library_lists_only_permitted_documents(web):
    client, *_ = web
    a = {d["title"] for d in client.get("/api/library", params={"user": "alice"}).json()["documents"]}
    b = {d["title"] for d in client.get("/api/library", params={"user": "bob"}).json()["documents"]}
    assert a == {"Sector Outlook 2025"} and b == {"Sector Outlook 2025", "Strategy Memo"}


def test_bad_questions_are_rejected(web):
    client, *_ = web
    assert client.post("/api/ask", json={"user": "alice", "question": "   "}).status_code == 400
    assert client.post("/api/ask", json={"user": "alice", "question": "x" * 501}).status_code == 400


def test_audit_shows_only_the_users_own_questions(web):
    client, llm, doc, *_ = web
    llm.responses = [[good(doc)]]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    assert len(client.get("/api/audit", params={"user": "alice"}).json()["entries"]) == 1
    assert client.get("/api/audit", params={"user": "bob"}).json()["entries"] == []


def test_quote_span_ignores_case_and_whitespace_and_misses_cleanly():
    assert quote_span("a  Palm\noil output", "palm OIL output") == [3, 18]
    assert quote_span("nothing here", "palm oil") is None


def test_page_never_builds_html_from_data():
    assert "innerHTML" not in INDEX_HTML and "textContent" in INDEX_HTML
