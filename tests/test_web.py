import json

import pytest
from fastapi.testclient import TestClient

from ra.db import connect
from ra.ground import Claim
from ra.ingest import ingest_pdf
from ra.llm import ScriptedLLM
from ra.web import INDEX_HTML, STATIC, create_app, quote_span

from .pdfmaker import make_pdf

QUOTE = "Palm oil output rose 4.2 percent in 2024"
DESK_JS = (STATIC / "desk.js").read_text(encoding="utf-8")


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
    client = TestClient(create_app(path, lambda: (llm, "scripted"), background=False))
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
    for text in (DESK_JS, INDEX_HTML):
        assert "innerHTML" not in text and "outerHTML" not in text and "insertAdjacentHTML" not in text and "document.write" not in text
    assert "textContent" in DESK_JS


# canvas data, feedback and the human-gated next step
def ask_as(client, llm, doc, user="alice", responses=None):
    llm.responses = responses if responses is not None else [[good(doc)]]
    return client.post("/api/ask", json={"user": user, "question": "How did palm oil output change?"}).json()


def test_ask_returns_everything_the_canvas_shows(web):
    client, llm, doc, *_ = web
    fake = Claim("Output doubled.", doc, 1, "Palm oil output doubled in 2024")
    j = ask_as(client, llm, doc, responses=[[fake], [good(doc)]])
    assert j["audit_id"] and j["attempts"] == 2
    assert j["retrieved"] and j["retrieved"][0]["title"] == "Sector Outlook 2025"
    assert j["rejected_claims"][0]["reason"] == "quote is not on the cited page"
    assert len(j["claims"]) == 1


def test_retrieved_sources_never_include_a_library_the_user_cannot_see(web):
    client, llm, doc, *_ = web
    j = client.post("/api/ask", json={"user": "alice", "question": "What merger did the board approve with Orion Holdings?"}).json()
    assert all(p["title"] != "Strategy Memo" for p in j["retrieved"])
    j2 = client.post("/api/ask", json={"user": "bob", "question": "What merger did the board approve with Orion Holdings?"}).json()
    assert any(p["title"] == "Strategy Memo" for p in j2["retrieved"])


def test_feedback_is_recorded_with_its_reason(web):
    client, llm, doc, _, path = web
    j = ask_as(client, llm, doc)
    r = client.post("/api/feedback", json={"user": "alice", "audit_id": j["audit_id"], "rating": "down", "reason": "wrong year"})
    assert r.status_code == 200
    row = connect(path).execute("SELECT user, rating, reason FROM feedback").fetchone()
    assert (row["user"], row["rating"], row["reason"]) == ("alice", "down", "wrong year")


def test_feedback_rejects_bad_rating_and_overlong_reason(web):
    client, llm, doc, *_ = web
    j = ask_as(client, llm, doc)
    base = {"user": "alice", "audit_id": j["audit_id"]}
    assert client.post("/api/feedback", json={**base, "rating": "meh"}).status_code == 400
    assert client.post("/api/feedback", json={**base, "rating": "up", "reason": "x" * 301}).status_code == 400


def test_a_user_cannot_rate_or_flag_someone_elses_answer(web):
    client, llm, doc, *_ = web
    j = ask_as(client, llm, doc, user="alice")
    assert client.post("/api/feedback", json={"user": "bob", "audit_id": j["audit_id"], "rating": "up"}).status_code == 403
    assert client.post("/api/review", json={"user": "bob", "audit_id": j["audit_id"]}).status_code == 403


def test_review_request_is_only_a_logged_open_entry(web):
    client, llm, doc, _, path = web
    j = ask_as(client, llm, doc)
    assert connect(path).execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0  # nothing happens on its own
    r = client.post("/api/review", json={"user": "alice", "audit_id": j["audit_id"], "note": "check the year"}).json()
    row = connect(path).execute("SELECT status, note FROM reviews WHERE id=?", (r["id"],)).fetchone()
    assert r["status"] == "open" and row["status"] == "open" and row["note"] == "check the year"


def test_page_labels_say_what_was_checked_not_verified():
    for text in (DESK_JS, INDEX_HTML):
        assert "VERIFIED" not in text.upper().replace("UNVERIFIED", "")
    assert "Quote found on this page" in DESK_JS and "That confirms the source, not that the claim answers your question." in DESK_JS


def test_ask_returns_the_trace_with_the_model_name(web):
    client, llm, doc, *_ = web
    j = ask_as(client, llm, doc)
    assert [s["kind"] for s in j["trace"]] == ["search", "draft", "check", "outcome"]
    assert j["trace"][1]["model"] == "scripted" or j["trace"][1]["model"] == "ScriptedLLM"
    assert j["trace"][0]["libraries"] == ["public"]


def test_the_page_can_show_every_step_it_may_be_sent():
    for kind in ("search", "drafting", "draft", "checking", "check", "retry"):
        assert f's.kind === "{kind}"' in DESK_JS



def test_admin_board_needs_the_token_and_counts_every_user(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)], []]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    client.post("/api/ask", json={"user": "bob", "question": "What did the board decide?"})
    client.post("/api/review", json={"user": "alice", "audit_id": 1, "note": "please check"})
    assert client.get("/api/admin/board").status_code == 401
    assert client.get("/api/admin/board", headers={"X-Token": "wrong"}).status_code == 401
    d = client.get("/api/admin/board", headers={"X-Token": "demo"}).json()
    assert d["usage"]["asked"] == 2 and [u["user"] for u in d["usage"]["users"]] == ["alice", "bob"]
    assert sorted(l["library"] for l in d["libraries"]) == ["public", "restricted"]
    pub = next(l for l in d["libraries"] if l["library"] == "public")
    assert pub["pages"] == 1 and pub["documents"] == 1 and pub["users"] == 2  # two grants must not double the page count
    assert d["recent"][0]["user"] == "bob" and d["recent"][0]["status"] == "not_found" and d["recent"][1]["citations"] == 1
    assert d["todo"][0]["n"] == 1 and "review request" in d["todo"][0]["what"]
    assert len(d["people"]["reviews_open"]) == 1 and "palm oil" in d["people"]["reviews_open"][0]["question"]
    assert d["brief"].startswith("Good ") and "2 questions from 2 users, 1 answered" in d["brief"] and "1 review request waiting" in d["brief"]
    assert "first visit" in d["brief_why"]
    quiet = client.get("/api/admin/board", params={"since": "2999-01-01T00:00:00"}, headers={"X-Token": "demo"}).json()
    assert quiet["brief"] == "" and "away" in quiet["brief_why"]


def test_admin_board_token_is_configurable_and_reads_saved_runs(tmp_path, web):
    import json

    from ra.web import create_app

    client, llm, doc, memo, path = web
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "qwen.json").write_text(json.dumps({"questions": 22, "answerable": 15, "retrieval_hit_rate": 1.0, "correct": 8, "timid": 7, "correct_refusal": 7, "wrong_and_confident": 0}))
    c2 = TestClient(create_app(path, lambda: (llm, "scripted"), results_dir=str(tmp_path / "r"), admin_token="s3cret"))
    assert c2.get("/api/admin/board", headers={"X-Token": "demo"}).status_code == 401
    d = c2.get("/api/admin/board", headers={"X-Token": "s3cret"}).json()
    assert d["runs"][0]["name"] == "qwen" and d["runs"][0]["wrong_and_confident"] == 0
    assert all(t["what"] != "evaluation runs saved" for t in d["todo"])


def test_dashboard_page_is_served_and_links_back(web):
    client, *_ = web
    html = client.get("/dashboard").text
    assert "Show the board" in html and "/api/admin/board" in html and "forget token" in html
    assert 'href="/dashboard"' in client.get("/").text


def test_admin_board_has_coverage_and_sources(web):
    client, llm, doc, memo, path = web
    d = client.get("/api/admin/board", headers={"X-Token": "demo"}).json()
    assert d["coverage"] == {"pages": 2, "searchable": 2, "flagged": 0, "pct": 100}
    kinds = [x["kind"] for x in d["sources"]]
    assert kinds == ["library database", "model", "evaluation results"]
    assert d["sources"][0]["name"] == "web.db" and "2 libraries" in d["sources"][0]["state"]
    assert d["sources"][1]["name"] == "scripted"
    assert "Try a question" in client.get("/dashboard").text


def test_admin_board_graph_links_libraries_documents_and_users_by_citation(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)], [good(doc)]]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    client.post("/api/ask", json={"user": "alice", "question": "Did palm oil output rise?"})
    g = client.get("/api/admin/board", headers={"X-Token": "demo"}).json()["graph"]
    kinds = {n["kind"] for n in g["nodes"]}
    assert kinds == {"library", "document", "user"}
    cited = [e for e in g["edges"] if e["kind"] == "cited"]
    assert cited == [{"a": f"D{doc}", "b": "Ualice", "kind": "cited", "w": 2}]
    assert {e["kind"] for e in g["edges"]} >= {"member", "grant", "cited"}
    assert next(n for n in g["nodes"] if n["id"] == f"D{doc}")["citations"] == 2


def test_api_keys_stand_for_one_user_and_can_be_revoked(web):
    client, llm, doc, memo, path = web
    h = {"X-Token": "demo"}
    assert client.post("/api/admin/keys", json={"name": "portal", "user": "alice"}).status_code == 401
    assert client.post("/api/admin/keys", json={"name": "portal", "user": "nobody"}, headers=h).status_code == 400
    made = client.post("/api/admin/keys", json={"name": "portal", "user": "alice"}, headers=h).json()
    key = made["key"]
    assert key.startswith("ra_") and made["prefix"] == key[:7]
    listed = client.get("/api/admin/keys", headers=h).json()["keys"]
    assert listed[0]["name"] == "portal" and listed[0]["user"] == "alice" and listed[0]["last_used"] is None and "key" not in listed[0]
    # the key asks as alice whatever the body says, and alice cannot see the restricted memo
    llm.responses = [[good(doc)], []]
    r = client.post("/api/ask", json={"user": "bob", "question": "How did palm oil output change?"}, headers={"X-Api-Key": key})
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert client.get("/api/audit", params={"user": "alice"}).json()["entries"][0]["question"].startswith("How did palm oil")
    r = client.post("/api/ask", json={"user": "bob", "question": "merger Orion Holdings"}, headers={"X-Api-Key": key})
    assert r.json()["status"] == "not_found"
    assert client.get("/api/admin/keys", headers=h).json()["keys"][0]["last_used"] is not None
    assert client.post("/api/ask", json={"user": "alice", "question": "anything"}, headers={"X-Api-Key": "ra_wrong"}).status_code == 401
    assert client.delete(f"/api/admin/keys/{made['id']}", headers=h).json() == {"revoked": made["id"]}
    assert client.post("/api/ask", json={"user": "alice", "question": "anything"}, headers={"X-Api-Key": key}).status_code == 401
    assert client.delete(f"/api/admin/keys/{made['id']}", headers=h).status_code == 404


def test_layout_is_a_document_per_owner_validated_in_plain_code(web):
    client, llm, doc, memo, path = web
    h = {"X-Token": "demo"}
    assert client.get("/api/admin/layout").status_code == 401
    d = client.get("/api/admin/layout", headers=h).json()
    assert d["owner"] == "admin" and not d["saved"] and d["layout"]["version"] == 1
    types = [w["type"] for w in d["layout"]["widgets"]]
    assert types == list(d["catalogue"]) and len(types) == 7
    assert all(set(w) == {"type", "span", "x", "y", "h", "settings"} for w in d["layout"]["widgets"])

    lay = d["layout"]
    lay["widgets"][0]["span"] = 12
    saved = client.put("/api/admin/layout", json={"owner": "admin", "layout": lay}, headers=h).json()
    assert saved["saved"] and saved["layout"]["widgets"][0]["span"] == 12 and saved["updated_at"]
    assert client.get("/api/admin/layout", headers=h).json()["layout"]["widgets"][0]["span"] == 12
    # another owner still sees the default
    assert not client.get("/api/admin/layout", params={"owner": "bob"}, headers=h).json()["saved"]

    bad_type = {"version": 1, "widgets": [{"type": "weather", "span": 4, "x": None, "y": None, "h": 1, "settings": {}}]}
    r = client.put("/api/admin/layout", json={"layout": bad_type}, headers=h)
    assert r.status_code == 400 and "unknown type 'weather'" in r.json()["detail"]
    bad_span = {"version": 1, "widgets": [{"type": "todo", "span": 13, "x": None, "y": None, "h": 1, "settings": {}}]}
    assert "span" in client.put("/api/admin/layout", json={"layout": bad_span}, headers=h).json()["detail"]
    bad_pos = {"version": 1, "widgets": [{"type": "todo", "span": 4, "x": 12, "y": -1, "h": 9, "settings": []}]}
    detail = client.put("/api/admin/layout", json={"layout": bad_pos}, headers=h).json()["detail"]
    assert all(k in detail for k in ("x must", "y must", "h must", "settings must"))
    assert client.put("/api/admin/layout", json={"layout": {"widgets": []}}, headers=h).status_code == 400
    # a rejected document never touches the stored one
    assert client.get("/api/admin/layout", headers=h).json()["layout"]["widgets"][0]["span"] == 12

    reset = client.delete("/api/admin/layout", headers=h).json()
    assert not reset["saved"] and reset["layout"]["widgets"][0]["span"] == 4


def test_dashboard_page_builds_its_grid_from_the_layout(web):
    client, llm, doc, memo, path = web
    page = client.get("/dashboard").text
    assert '<div class="grid" id="grid"' in page and "<h2>To do" not in page  # no static panels left
    assert "IntersectionObserver" in page and "/api/admin/widget/" in page and "parts=head" in page
    for t in ("todo", "timeline", "coverage", "recent", "runs", "users", "people"):
        assert f" {t}:{{title:" in page  # the page's catalogue mirrors the server's


def test_pages_are_never_cached(web):
    client = web[0]
    for path in ("/", "/dashboard"):
        assert client.get(path).headers["cache-control"] == "no-store"


def test_board_loads_in_parts_head_first_then_one_widget_at_a_time(web):
    client, llm, doc, memo, path = web
    h = {"X-Token": "demo"}
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    head = client.get("/api/admin/board", params={"parts": "head"}, headers=h).json()
    assert {"now", "brief", "brief_why", "todo", "coverage", "usage", "runs", "sources"} <= set(head)
    assert "graph" not in head and "recent" not in head and "people" not in head
    tl = client.get("/api/admin/widget/timeline", headers=h).json()
    assert set(tl) == {"now", "graph"} and any(n["kind"] == "document" for n in tl["graph"]["nodes"])
    cov = client.get("/api/admin/widget/coverage", headers=h).json()
    assert set(cov) == {"now", "coverage", "libraries"} and cov["coverage"]["pages"] >= 1
    assert set(client.get("/api/admin/widget/recent", headers=h).json()) == {"now", "recent"}
    assert client.get("/api/admin/widget/weather", headers=h).status_code == 404
    assert client.get("/api/admin/widget/recent").status_code == 401
    # the whole board is still one call, and the parts agree with it
    whole = client.get("/api/admin/board", headers=h).json()
    assert whole["coverage"] == cov["coverage"] and whole["graph"]["nodes"] == tl["graph"]["nodes"]


def test_user_board_shows_only_that_users_rows_and_has_its_own_layout(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)], []]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    client.post("/api/ask", json={"user": "bob", "question": "What did the board decide?"})
    client.post("/api/feedback", json={"user": "alice", "audit_id": 1, "rating": "down", "reason": "wrong year"})
    client.post("/api/review", json={"user": "alice", "audit_id": 1, "note": "please check"})

    head = client.get("/api/me/board", params={"user": "alice", "parts": "head"}).json()
    assert set(head) == {"now", "brief", "usage", "ratings", "reviews"}
    assert head["usage"]["asked"] == 1 and head["usage"]["answered"] == 1 and len(head["usage"]["libraries"]) >= 1
    assert head["ratings"]["down"] == 1 and head["reviews"]["open"] == 1
    assert "alice" in head["brief"] and "1 review request waiting" in head["brief"]

    ans = client.get("/api/me/widget/answers", params={"user": "alice"}).json()
    assert [a["question"] for a in ans["answers"]] == ["How did palm oil output change?"]  # never bob's
    bob = client.get("/api/me/widget/answers", params={"user": "bob"}).json()
    assert [a["question"] for a in bob["answers"]] == ["What did the board decide?"]
    assert client.get("/api/me/widget/timeline", params={"user": "alice"}).status_code == 404  # admin widget, not in the user catalogue
    assert client.get("/api/me/board", params={"user": "nobody"}).status_code == 404

    lay = client.get("/api/me/layout", params={"user": "alice"}).json()
    assert lay["owner"] == "user:alice" and [w["type"] for w in lay["layout"]["widgets"]] == ["library", "analyses", "answers", "usage", "ratings", "reviews"]
    doc_ = lay["layout"]
    doc_["widgets"][0]["span"] = 12
    assert client.put("/api/me/layout", json={"user": "alice", "layout": doc_}).json()["saved"]
    assert client.get("/api/me/layout", params={"user": "alice"}).json()["layout"]["widgets"][0]["span"] == 12
    assert not client.get("/api/me/layout", params={"user": "bob"}).json()["saved"]
    # a user layout cannot name an admin widget, and vice versa
    doc_["widgets"][0]["type"] = "timeline"
    assert client.put("/api/me/layout", json={"user": "alice", "layout": doc_}).status_code == 400
    admin_doc = client.get("/api/admin/layout", headers={"X-Token": "demo"}).json()["layout"]
    admin_doc["widgets"][0]["type"] = "answers"
    assert client.put("/api/admin/layout", json={"layout": admin_doc}, headers={"X-Token": "demo"}).status_code == 400
    assert not client.delete("/api/me/layout", params={"user": "alice"}).json()["saved"]
    # one page navigation on both pages: the desk, a person's activity, and manage, with the current page marked
    chat, board = client.get("/").text, client.get("/dashboard").text
    assert 'aria-label="Pages"' in chat and 'aria-label="Pages"' in board
    assert 'aria-current="page"' in chat and 'id="melink"' in chat and 'id="dashlink"' in chat
    assert 'id="navChat"' in board and 'id="navAdmin" aria-current="page"' in board


def test_a_stored_answer_replays_for_its_owner_without_the_model(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)]]
    j = client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"}).json()
    calls = len(llm.calls)
    r = client.get(f"/api/answer/{j['audit_id']}", params={"user": "alice"}).json()
    assert r["replayed"] and r["question"] == "How did palm oil output change?" and r["claims"] == j["claims"] and r["trace"] == j["trace"]
    assert len(llm.calls) == calls  # no model call
    assert client.get(f"/api/answer/{j['audit_id']}", params={"user": "bob"}).status_code == 404  # not bob's
    assert client.get("/api/answer/999", params={"user": "alice"}).status_code == 404
    page = client.get("/").text
    assert 'src="/static/desk.js"' in page and "async function replay" in DESK_JS and 'u.get("a")' in DESK_JS
    ans = client.get("/api/me/widget/answers", params={"user": "alice"}).json()["answers"][0]
    assert ans["audit_id"] == j["audit_id"]


def test_admin_adds_a_user_as_a_name_plus_grants(web):
    client, llm, doc, memo, path = web
    h = {"X-Token": "demo"}
    assert client.post("/api/admin/users", json={"user": "carol", "libraries": ["public"]}).status_code == 401
    libs = client.get("/api/admin/libraries", headers=h).json()["libraries"]
    ids = [l["id"] for l in libs]
    assert len(ids) >= 2 and all("name" in l for l in libs)
    r = client.post("/api/admin/users", json={"user": "carol", "libraries": [ids[0]]}, headers=h).json()
    assert r == {"user": "carol", "libraries": [ids[0]], "added": [ids[0]], "existed": False}
    assert "carol" in client.get("/api/users").json()["users"]
    r = client.post("/api/admin/users", json={"user": "carol", "libraries": [ids[1], "nope"]}, headers=h).json()
    assert r["existed"] and r["added"] == [ids[1]] and r["libraries"] == sorted(ids[:2])
    assert client.post("/api/admin/users", json={"user": "carol", "libraries": ["nope"]}, headers=h).status_code == 400
    assert client.post("/api/admin/users", json={"user": "bad name!", "libraries": [ids[0]]}, headers=h).status_code == 400
    # carol can now ask, and only sees her libraries
    llm.responses = [[good(doc)]]
    assert client.post("/api/ask", json={"user": "carol", "question": "How did palm oil output change?"}).json()["status"] in ("answered", "not_found")
    assert 'id="newuser"' in client.get("/dashboard").text


def test_the_original_pdf_is_served_only_behind_the_grant(web):
    client, llm, doc, memo, path = web
    r = client.get(f"/files/{doc}", params={"user": "alice"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF"
    assert "inline" in r.headers["content-disposition"] and r.headers["cache-control"] == "private, no-store"
    assert client.get(f"/files/{memo}", params={"user": "alice"}).status_code == 403  # alice holds no grant on the restricted library
    assert client.get(f"/files/{memo}", params={"user": "bob"}).status_code == 200
    assert client.get("/files/999", params={"user": "alice"}).status_code == 403
    page = client.get("/api/page", params={"user": "alice", "doc_id": doc, "p": 1}).json()
    assert page["pdf"] == f"/files/{doc}?user=alice#page=1"
    assert 'id="ppdf"' in client.get("/").text
    # a link is only offered for a file that is on this server
    import os
    source = connect(path).execute("SELECT source FROM documents WHERE id=?", (doc,)).fetchone()["source"]
    os.remove(source)
    assert client.get("/api/page", params={"user": "alice", "doc_id": doc, "p": 1}).json()["pdf"] is None
    assert client.get("/api/me/widget/library", params={"user": "alice"}).json()["library"][0]["pdf"] is None


def test_the_library_widget_lists_only_granted_reports_with_edition_and_links(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)]]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    lib = client.get("/api/me/widget/library", params={"user": "alice"}).json()["library"]
    assert [r["title"] for r in lib] == ["Sector Outlook 2025"]  # not the restricted memo
    r = lib[0]
    assert r["pages"] == 1 and r["cited"] == 1 and r["pdf"] == f"/files/{doc}?user=alice" and r["edition"] == [0, 0, ""]
    bob = client.get("/api/me/widget/library", params={"user": "bob"}).json()["library"]
    assert {r["title"] for r in bob} == {"Sector Outlook 2025", "Strategy Memo"} and all(r["cited"] == 0 for r in bob)


def test_edition_is_read_from_the_title_and_sorted_newest_first(tmp_path):
    from ra.web import user_board
    from tests.pdfmaker import make_pdf

    con = connect(str(tmp_path / "e.db"))
    for title in ("Monitor April 2024", "Monitor October 2025", "Monitor June 2015"):
        ingest_pdf(con, "wb", title, make_pdf(tmp_path / (title + ".pdf"), [["Growth was 4.1 percent in 2025."]]))
    con.execute("INSERT INTO grants VALUES ('alice','wb')")
    con.commit()
    lib = user_board(con, "alice", "m", ["library"])["library"]
    assert [r["edition"][2] for r in lib] == ["October 2025", "April 2024", "June 2015"]


def test_an_analysis_runs_every_question_inside_each_chosen_report_and_stores_the_answers(web):
    client, llm, doc, memo, path = web
    # bob may see both reports; the memo question is answered from the memo, the palm-oil question refused there
    memo_claim = Claim("The board approved a merger with Orion Holdings for 900 million.", memo, 1, "The board approved a merger with Orion Holdings for 900 million.")
    # cell order is q0:doc, q0:memo, q1:doc, q1:memo; the two cross cells find no passage in their report and never call the model
    llm.responses = [[good(doc)], [memo_claim]]
    body = {"user": "bob", "name": "Outlook check", "questions": ["How did palm oil output change?", "What did the board approve?"], "doc_ids": [doc, memo]}
    a = client.post("/api/me/analyses", json=body).json()
    assert a["status"] == "new" and a["total"] == 4 and a["done"] == 0 and [d["id"] for d in a["docs"]] == [doc, memo]
    v = client.post(f"/api/me/analyses/{a['id']}/run", params={"user": "bob"}).json()
    assert v["status"] == "done" and v["done"] == 4 and v["answered"] == 2
    assert v["cells"][f"0:{doc}"]["status"] == "answered" and v["cells"][f"0:{doc}"]["page"] == 1 and "4.2" in v["cells"][f"0:{doc}"]["claim"]
    assert v["cells"][f"1:{memo}"]["status"] == "answered" and v["cells"][f"0:{memo}"]["status"] == "not_found"
    # every cell searched one report only, and a cell with no passage in its report never reached the model
    assert len(llm.calls) == 2 and all(len({d for d, _ in call[1]}) == 1 for call in llm.calls)
    # cells are ordinary stored answers and replay for their owner
    aid = v["cells"][f"0:{doc}"]["audit_id"]
    assert client.get(f"/api/answer/{aid}", params={"user": "bob"}).json()["claims"][0]["page"] == 1
    lst = client.get("/api/me/analyses", params={"user": "bob"}).json()
    assert lst["analyses"][0]["name"] == "Outlook check" and lst["analyses"][0]["answered"] == 2 and len(lst["docs"]) == 2
    # alice cannot see bob's analysis, and cannot create one over the restricted memo
    assert client.get(f"/api/me/analyses/{a['id']}", params={"user": "alice"}).status_code == 404
    assert client.post("/api/me/analyses", json={"user": "alice", "name": "x", "questions": ["q"], "doc_ids": [memo]}).status_code == 400
    assert client.post("/api/me/analyses", json={"user": "alice", "name": "x", "questions": [], "doc_ids": [doc]}).status_code == 400
    assert client.post("/api/me/analyses", json={"user": "alice", "name": "x", "questions": ["q"] * 11, "doc_ids": [doc]}).status_code == 400
    assert client.get("/api/me/widget/analyses", params={"user": "bob"}).json()["analyses"][0]["status"] == "done"
    # cells count as questions but do not crowd the recent list
    me = client.get("/api/me/board", params={"user": "bob", "parts": "usage,answers"}).json()
    assert me["usage"]["asked"] == 4 and me["usage"]["from_analyses"] == 4 and me["answers"] == []
    assert client.delete(f"/api/me/analyses/{a['id']}", params={"user": "bob"}).json() == {"deleted": a["id"]}
    assert client.get(f"/api/me/analyses/{a['id']}", params={"user": "bob"}).status_code == 404


def test_each_todo_count_opens_its_rows_and_a_review_can_be_closed(web, tmp_path):
    _, llm, doc, memo, path = web
    client = TestClient(create_app(path, lambda: (llm, "scripted"), results_dir=str(tmp_path / "r"), background=False))
    h = {"X-Token": "demo"}
    llm.responses = [[good(doc)]]
    j = client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"}).json()
    client.post("/api/review", json={"user": "alice", "audit_id": j["audit_id"], "note": "please check"})
    client.post("/api/feedback", json={"user": "alice", "audit_id": j["audit_id"], "rating": "down", "reason": "wrong year"})
    import json as _json
    (tmp_path / "r").mkdir(exist_ok=True)
    (tmp_path / "r" / "m.json").write_text(_json.dumps({"wrong_and_confident": 1, "correct": 0, "timid": 0, "correct_refusal": 0, "questions": 1, "answerable": 1, "rows": [
        {"id": "A01", "q": "What was 2024 growth?", "answerable": True, "outcome": "wrong_and_confident", "answer": [["Output rose 4.2 percent.", doc, 1, "Palm oil output rose 4.2 percent in 2024"]]}]}))
    todo = client.get("/api/admin/board", params={"parts": "todo"}, headers=h).json()["todo"]
    kinds = {t["kind"]: t for t in todo}
    assert {"reviews", "down", "wrong"} <= set(kinds) and kinds["reviews"]["n"] == 1 and kinds["wrong"]["n"] == 1
    assert client.get("/api/admin/todo/reviews").status_code == 401
    rv = client.get("/api/admin/todo/reviews", headers=h).json()
    assert rv["rows"][0]["question"] == "How did palm oil output change?" and rv["rows"][0]["note"] == "please check" and rv["rows"][0]["audit_id"] == j["audit_id"]
    down = client.get("/api/admin/todo/down", headers=h).json()
    assert down["rows"][0]["reason"] == "wrong year" and down["rows"][0]["user"] == "alice"
    wrong = client.get("/api/admin/todo/wrong", headers=h).json()
    c = wrong["rows"][0]["claims"][0]
    assert wrong["run"]["name"] == "m" and (c["text"], c["title"], c["short"], c["page"]) == ("Output rose 4.2 percent.", "Sector Outlook 2025", "Sector Outlook 2025", 1)
    assert client.get("/api/admin/todo/ocr", headers=h).json()["rows"] == []
    assert client.get("/api/admin/todo/nope", headers=h).status_code == 404
    rid = rv["rows"][0]["id"]
    assert client.post(f"/api/admin/reviews/{rid}/close", headers=h).json() == {"closed": rid}
    assert client.post(f"/api/admin/reviews/{rid}/close", headers=h).status_code == 404
    todo = client.get("/api/admin/board", params={"parts": "todo"}, headers=h).json()["todo"]
    assert "reviews" not in {t["kind"] for t in todo}
    assert 'id="todoDetail"' in client.get("/dashboard").text


def test_find_route_never_calls_the_model(web):
    client, llm, doc, memo, path = web
    r = client.get("/api/find", params={"user": "alice", "q": "palm oil"}).json()
    assert r["model_called"] is False and r["hits"][0]["doc_id"] == doc and r["hits"][0]["page"] == 1 and any(m for _, m in r["hits"][0]["snippet"])
    assert r["query"] == '"palm" AND "oil"' and r["ms"] < 1000 and llm.calls == []
    assert client.get("/api/find", params={"user": "nobody", "q": "palm"}).status_code == 403
    assert client.get("/api/find", params={"user": "alice", "q": '"'}).json()["hits"] == []
    assert 'id="findBtn"' in client.get("/").text
    assert client.get("/api/find", params={"user": "alice", "q": "palm oil", "doc_id": memo}).json()["hits"] == []  # one report, and alice has no grant on it
    assert client.get("/api/find", params={"user": "alice", "q": "palm oil", "doc_id": doc}).json()["hits"][0]["doc_id"] == doc


def stream(client, **body):
    """The server-sent events of one question, as a list."""
    with client.stream("POST", "/api/ask/stream", json=body) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.headers["cache-control"] == "no-store"
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_streaming_sends_each_real_step_in_order_and_the_checked_answer_last(web):
    client, llm, doc, _, path = web
    fake = Claim("Output doubled.", doc, 1, "Palm oil output doubled in 2024")
    llm.responses = [[fake], [good(doc)]]
    events = stream(client, user="alice", question="How did palm oil output change?")
    assert [e["kind"] for e in events] == ["search", "drafting", "draft", "checking", "check", "retry", "drafting", "draft", "checking", "check", "outcome", "answer"]
    assert events[0]["passages"][0]["title"] == "Sector Outlook 2025" and events[0]["found"] >= 1
    assert events[4]["removed"] == 1 and events[4]["reasons"] == ["quote is not on the cited page"]
    answer = events[-1]["answer"]
    assert answer["status"] == "answered" and answer["attempts"] == 2 and answer["claims"][0]["text"] == good(doc).text
    # the rejected draft's words never reach the live events, and the answer is stored like any other
    assert "doubled" not in json.dumps(events[:-1])
    stored = client.get(f"/api/answer/{answer['audit_id']}", params={"user": "alice"}).json()
    assert stored["claims"] == answer["claims"] and connect(path).execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1


def test_streaming_refuses_what_asking_refuses_and_reports_a_failure_as_an_event(web):
    client, llm, doc, *_ = web
    assert client.post("/api/ask/stream", json={"user": "mallory", "question": "palm oil"}).status_code == 403
    assert client.post("/api/ask/stream", json={"user": "alice", "question": "  "}).status_code == 400
    assert client.post("/api/ask/stream", json={"user": "alice", "question": "palm oil", "doc_ids": []}).status_code == 400

    class Broken:
        def draft(self, *a, **k):
            raise RuntimeError("model is down")

    down = TestClient(create_app(web[4], lambda: (Broken(), "broken"), background=False))
    events = stream(down, user="alice", question="How did palm oil output change?")
    assert events[-1]["kind"] == "answer" and events[-1]["answer"]["status"] == "not_found"
    assert [e for e in events if e["kind"] == "draft"][0]["error"].startswith("RuntimeError")


def test_ticked_reports_limit_the_search_and_can_never_widen_a_grant(web):
    client, llm, doc, memo, _ = web
    q = "palm oil output merger board Orion Holdings"
    both = client.post("/api/ask", json={"user": "bob", "question": q}).json()
    assert {p["doc_id"] for p in both["retrieved"]} == {doc, memo}
    one = client.post("/api/ask", json={"user": "bob", "question": q, "doc_ids": [memo]}).json()
    assert {p["doc_id"] for p in one["retrieved"]} == {memo} and one["trace"][0]["only"] == [memo]
    assert client.post("/api/ask", json={"user": "alice", "question": q, "doc_ids": [memo]}).json()["retrieved"] == []


def test_every_ticked_report_is_searched_even_past_the_named_edition_limit(tmp_path):
    from ra.retrieve import MAX_NAMED, retrieve

    con = connect(str(tmp_path / "many.db"))
    ids = []
    for i in range(MAX_NAMED + 2):
        ingest_pdf(con, "wb", f"Report {i}", make_pdf(tmp_path / f"{i}.pdf", [["Palm oil output rose in this report."]]))
        ids.append(con.execute("SELECT id FROM documents WHERE title=?", (f"Report {i}",)).fetchone()["id"])
    con.execute("INSERT INTO grants VALUES ('alice','wb')")
    con.commit()
    assert {p.doc_id for p in retrieve(con, "alice", "palm oil output", 12, set(ids))} == set(ids)
    assert {p.doc_id for p in retrieve(con, "alice", "palm oil output", 12, set(ids[:MAX_NAMED + 1]))} == set(ids[:MAX_NAMED + 1])


def test_history_lists_each_question_once_and_says_which_can_reopen(web):
    client, llm, doc, _, path = web
    q = "How did palm oil output change?"
    llm.responses = [[good(doc)], [good(doc)]]
    first = client.post("/api/ask", json={"user": "alice", "question": q}).json()
    client.post("/api/feedback", json={"user": "alice", "audit_id": first["audit_id"], "rating": "up"})
    second = client.post("/api/ask", json={"user": "alice", "question": q}).json()
    client.post("/api/review", json={"user": "alice", "audit_id": second["audit_id"]})
    con = connect(path)
    con.execute("INSERT INTO audit(user, question, status, cited) VALUES ('alice','An old question','not_found','')")
    con.execute("INSERT INTO audit(user, question, status, cited) VALUES ('bob','Not alice''s','not_found','')")
    con.commit()
    rows = client.get("/api/me/history", params={"user": "alice"}).json()["questions"]
    assert [r["question"] for r in rows] == ["An old question", q]
    assert rows[0]["saved"] is False and rows[1]["saved"] is True and rows[1]["asked"] == 2
    assert rows[1]["id"] == second["audit_id"] and rows[1]["review"] == "open"
    assert client.get("/api/me/history", params={"user": "nobody"}).status_code == 404


def test_a_table_gains_a_row_and_only_the_new_cells_are_asked(web):
    client, llm, doc, memo, _ = web
    llm.responses = [[good(doc)]]
    a = client.post("/api/me/analyses", json={"user": "alice", "name": "t", "questions": ["How did palm oil output change?"], "doc_ids": [doc]}).json()
    done = client.post(f"/api/me/analyses/{a['id']}/run", params={"user": "alice"}).json()
    assert done["cells"][f"0:{doc}"]["quote"] == QUOTE and len(llm.calls) == 1
    llm.responses = [[good(doc)]]
    grown = client.post(f"/api/me/analyses/{a['id']}/questions", json={"user": "alice", "question": "Did palm oil yields rise?"}).json()
    assert grown["questions"][1] == "Did palm oil yields rise?" and grown["cells"][f"1:{doc}"]["status"] == "pending" and grown["done"] == 1
    v = client.post(f"/api/me/analyses/{a['id']}/run", params={"user": "alice"}).json()
    assert v["cells"][f"1:{doc}"]["status"] == "answered" and len(llm.calls) == 2  # the first cell was not asked again
    assert client.post(f"/api/me/analyses/{a['id']}/questions", json={"user": "bob", "question": "x"}).status_code == 404
    assert client.post(f"/api/me/analyses/{a['id']}/questions", json={"user": "alice", "question": " "}).status_code == 400
    for i in range(8):
        assert client.post(f"/api/me/analyses/{a['id']}/questions", json={"user": "alice", "question": f"q{i}"}).status_code == 200
    assert client.post(f"/api/me/analyses/{a['id']}/questions", json={"user": "alice", "question": "one too many"}).status_code == 400


def test_static_files_are_served_and_the_browser_must_revalidate_them(web):
    client = web[0]
    for path in ("/static/tokens.css", "/static/desk.css", "/static/desk.js", "/static/fonts/Geist-Variable.woff2"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
