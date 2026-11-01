"""An upload is private to the person who added it, in every route, and deleting it leaves nothing of its text behind."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from ra.db import connect
from ra.ground import Claim
from ra.ingest import ingest_pdf
from ra.llm import ScriptedLLM
from ra.uploads import MAX_UPLOADS, clean_title
from ra.web import create_app

from .pdfmaker import make_pdf

SECRET = "Zebra quota rose 7 percent in the third quarter"


@pytest.fixture
def web(tmp_path):
    path = str(tmp_path / "web.db")
    con = connect(path)
    ingest_pdf(con, "public", "Sector Outlook 2025", make_pdf(tmp_path / "o.pdf", [["Palm oil output rose 4.2 percent in 2024, driven by higher yields."]]))
    for u in ("alice", "bob"):
        con.execute("INSERT INTO grants VALUES (?, 'public')", (u,))
    con.commit()
    llm = ScriptedLLM()
    client = TestClient(create_app(path, lambda: (llm, "scripted"), background=False))
    return client, llm, path, tmp_path


def pdf_bytes(tmp_path, pages=None, name="up.pdf"):
    return open(make_pdf(tmp_path / name, pages if pages is not None else [[SECRET + ", according to the plan."]]), "rb").read()


def upload(client, tmp_path, user="alice", name="Board notes.pdf", body=None):
    return client.post("/api/me/uploads", params={"user": user, "name": name}, content=body if body is not None else pdf_bytes(tmp_path), headers={"Content-Type": "application/pdf"})


def test_an_upload_is_searchable_by_its_owner_and_nobody_else(web):
    client, llm, path, tmp = web
    r = upload(client, tmp)
    assert r.status_code == 200
    made = r.json()
    doc = made["doc_id"]
    assert made["title"] == "Board notes" and made["pages"] == 1 and made["low_text_pages"] == 0

    mine = client.get("/api/me/widget/library", params={"user": "alice"}).json()["library"]
    entry = next(d for d in mine if d["id"] == doc)
    assert entry["private"] is True and entry["library"] == "Your uploads" and entry["pdf"].startswith(f"/files/{doc}")
    assert [d["private"] for d in mine] == [False, True]  # shared reports come first
    assert client.get("/api/find", params={"user": "alice", "q": "zebra"}).json()["hits"][0]["doc_id"] == doc
    assert client.get("/api/page", params={"user": "alice", "doc_id": doc, "p": 1}).status_code == 200
    assert client.get(f"/files/{doc}", params={"user": "alice"}).content[:4] == b"%PDF"
    llm.responses = [[Claim("Zebra quota rose 7 percent.", doc, 1, SECRET)]]
    assert client.post("/api/ask", json={"user": "alice", "question": "How did the zebra quota change?"}).json()["status"] == "answered"

    # bob, on every route that could reach it
    assert all(d["id"] != doc for d in client.get("/api/me/widget/library", params={"user": "bob"}).json()["library"])
    assert all(d["id"] != doc for d in client.get("/api/library", params={"user": "bob"}).json()["documents"])
    assert client.get("/api/find", params={"user": "bob", "q": "zebra"}).json()["hits"] == []
    assert client.get("/api/find", params={"user": "bob", "q": "zebra", "doc_id": doc}).json()["hits"] == []
    assert client.get("/api/page", params={"user": "bob", "doc_id": doc, "p": 1}).status_code == 403
    assert client.get(f"/files/{doc}", params={"user": "bob"}).status_code == 403
    calls = len(llm.calls)
    asked = client.post("/api/ask", json={"user": "bob", "question": "How did the zebra quota change?", "doc_ids": [doc]}).json()
    assert asked["retrieved"] == [] and asked["status"] == "not_found" and len(llm.calls) == calls  # never reached the model
    assert client.post("/api/me/analyses", json={"user": "bob", "name": "x", "questions": ["q"], "doc_ids": [doc]}).status_code == 400
    assert client.delete(f"/api/me/uploads/{doc}", params={"user": "bob"}).status_code == 404
    assert client.get("/api/page", params={"user": "alice", "doc_id": doc, "p": 1}).status_code == 200  # bob's delete did nothing


def test_the_admin_views_leave_uploads_out_and_cannot_grant_one(web):
    client, llm, path, tmp = web
    doc = upload(client, tmp).json()["doc_id"]
    llm.responses = [[Claim("Zebra quota rose 7 percent.", doc, 1, SECRET)]]
    client.post("/api/ask", json={"user": "alice", "question": "How did the zebra quota change?"})
    h = {"X-Token": "demo"}
    board = client.get("/api/admin/board", headers=h).json()
    text = json.dumps({k: board[k] for k in ("libraries", "coverage", "graph", "sources")})
    assert "Board notes" not in text and "private:" not in text and "Your uploads" not in text
    assert board["coverage"]["pages"] == 1  # only the shared report
    assert {n["id"] for n in board["graph"]["nodes"] if n["kind"] == "document"} == {"D1"}
    ends = {e["a"] for e in board["graph"]["edges"]} | {e["b"] for e in board["graph"]["edges"]}
    assert f"D{doc}" not in ends and "Lprivate:alice" not in ends
    assert client.get("/api/admin/todo/ocr", headers=h).json()["rows"] == []
    assert all(not lib["id"].startswith("private:") for lib in client.get("/api/admin/libraries", headers=h).json()["libraries"])
    assert client.post("/api/admin/users", json={"user": "bob", "libraries": ["private:alice"]}, headers=h).status_code == 400
    assert client.get("/api/page", params={"user": "bob", "doc_id": doc, "p": 1}).status_code == 403


def test_deleting_an_upload_leaves_nothing_of_its_text_behind(web):
    client, llm, path, tmp = web
    doc = upload(client, tmp).json()["doc_id"]
    stored_file = connect(path).execute("SELECT source FROM documents WHERE id=?", (doc,)).fetchone()["source"]
    assert os.path.exists(stored_file) and os.path.dirname(stored_file).endswith("uploads")
    llm.responses = [[Claim("Zebra quota rose 7 percent.", doc, 1, SECRET)]]
    answer = client.post("/api/ask", json={"user": "alice", "question": "How did the zebra quota change?"}).json()
    other = client.post("/api/ask", json={"user": "alice", "question": "unrelated words only"}).json()
    llm.responses = [[Claim("Zebra quota rose 7 percent.", doc, 1, SECRET)]]
    table = client.post("/api/me/analyses", json={"user": "alice", "name": "t", "questions": ["How did the zebra quota change?"], "doc_ids": [doc]}).json()
    client.post(f"/api/me/analyses/{table['id']}/run", params={"user": "alice"})

    assert client.delete(f"/api/me/uploads/{doc}", params={"user": "alice"}).json() == {"deleted": doc}
    assert not os.path.exists(stored_file)
    con = connect(path)
    for sql in ("SELECT COUNT(*) FROM documents WHERE id=?", "SELECT COUNT(*) FROM pages WHERE doc_id=?", "SELECT COUNT(*) FROM passages WHERE doc_id=?", "SELECT COUNT(*) FROM analysis_cells WHERE doc_id=?"):
        assert con.execute(sql, (doc,)).fetchone()[0] == 0, sql
    assert client.get("/api/find", params={"user": "alice", "q": "zebra"}).json()["hits"] == []
    assert con.execute("SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH 'zebra'").fetchone()[0] == 0
    assert SECRET not in json.dumps([r["answer"] for r in con.execute("SELECT answer FROM audit") if r["answer"]])
    assert client.get(f"/api/answer/{answer['audit_id']}", params={"user": "alice"}).status_code == 404
    assert client.get(f"/api/answer/{other['audit_id']}", params={"user": "alice"}).status_code == 200  # untouched
    rows = {h["question"]: h for h in client.get("/api/me/history", params={"user": "alice"}).json()["questions"]}
    assert rows["How did the zebra quota change?"]["saved"] is False  # the question stays; it can be asked again
    assert client.get(f"/api/me/analyses/{table['id']}", params={"user": "alice"}).status_code == 404  # a table of only this report goes too
    assert client.delete(f"/api/me/uploads/{doc}", params={"user": "alice"}).status_code == 404
    assert client.delete("/api/me/uploads/1", params={"user": "alice"}).status_code == 404  # a shared report is not theirs to delete
    assert connect(path).execute("SELECT COUNT(*) FROM documents WHERE id=1").fetchone()[0] == 1


def test_deleting_keeps_the_rest_of_a_table(web):
    client, llm, path, tmp = web
    doc = upload(client, tmp).json()["doc_id"]
    table = client.post("/api/me/analyses", json={"user": "alice", "name": "t", "questions": ["Anything about palm oil?"], "doc_ids": [1, doc]}).json()
    client.delete(f"/api/me/uploads/{doc}", params={"user": "alice"})
    view = client.get(f"/api/me/analyses/{table['id']}", params={"user": "alice"}).json()
    assert [d["id"] for d in view["docs"]] == [1] and list(view["cells"]) == ["0:1"]


def test_files_that_cannot_be_added_are_refused_with_a_reason_and_leave_nothing(web, monkeypatch):
    client, llm, path, tmp = web
    uploads = tmp / "uploads"
    bad = upload(client, tmp, body=b"this is not a pdf at all")
    assert bad.status_code == 400 and "not a PDF" in bad.json()["detail"]
    fake = upload(client, tmp, body=b"%PDF-1.4 but nothing a reader can use")
    assert fake.status_code == 400 and "could not be read" in fake.json()["detail"]
    blank = upload(client, tmp, body=pdf_bytes(tmp, [[]], "blank.pdf"))
    assert blank.status_code == 400 and "scan" in blank.json()["detail"]
    assert client.post("/api/me/uploads", params={"user": "nobody", "name": "x.pdf"}, content=pdf_bytes(tmp)).status_code == 404
    monkeypatch.setattr("ra.web.MAX_UPLOAD_BYTES", 10)
    big = upload(client, tmp)
    assert big.status_code == 413 and "MB" in big.json()["detail"]
    monkeypatch.undo()
    monkeypatch.setattr("ra.uploads.MAX_UPLOAD_PAGES", 1)
    many = upload(client, tmp, body=pdf_bytes(tmp, [["A page of readable text, long enough to count."], ["Another page of readable text, long enough."]], "two.pdf"))
    assert many.status_code == 400 and "pages" in many.json()["detail"]
    assert not list(uploads.iterdir())
    assert connect(path).execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_a_stored_file_never_takes_its_name_from_the_person(web):
    client, llm, path, tmp = web
    for name in ("../../evil.pdf", "..\\..\\evil.pdf", "/etc/passwd.pdf"):
        doc = upload(client, tmp, name=name).json()["doc_id"]
        source = connect(path).execute("SELECT source FROM documents WHERE id=?", (doc,)).fetchone()["source"]
        assert os.path.dirname(source) == str(tmp / "uploads") and "evil" not in source and "passwd" not in source


def test_titles_are_made_safe_and_uploads_are_limited(web, monkeypatch):
    client, llm, path, tmp = web
    assert clean_title("../../my_board  notes.PDF") == "my board notes" and clean_title("a\x00b.pdf") == "a b" and clean_title("") == "Untitled report"
    assert len(clean_title("x" * 500 + ".pdf")) == 120
    monkeypatch.setattr("ra.uploads.MAX_UPLOADS", 1)
    assert upload(client, tmp).status_code == 200
    second = upload(client, tmp)
    assert second.status_code == 400 and "Delete one" in second.json()["detail"]
    assert MAX_UPLOADS >= 1


def test_a_page_with_no_text_is_reported_not_hidden(web):
    client, llm, path, tmp = web
    made = upload(client, tmp, body=pdf_bytes(tmp, [["A page of readable text, long enough to count as text."], []], "mixed.pdf")).json()
    assert made["pages"] == 2 and made["low_text_pages"] == 1
