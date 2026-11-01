"""A person's activity page: the rows and topics it draws from, and whose they are."""
import os
import re

from ra.__main__ import load_env
from ra.assistant import reply as assistant_reply
from ra.db import connect
from ra.web import STATIC
from ra.workspace import _word_pairs, edition_topics, question_topics, topic_pages, user_activity

from . import test_web

web = test_web.web  # the shared app fixture
good = test_web.good
SCRIPTS = "".join((STATIC / n).read_text(encoding="utf-8") for n in ("activity.js", "activity-flow.js", "activity-boards.js", "activity-assistant.js"))


def test_the_activity_page_is_the_user_page_and_admin_stays_admin(web):
    client, *_ = web
    assert "/static/activity.js" in client.get("/dashboard", params={"user": "alice"}).text
    admin = client.get("/dashboard").text
    assert "Show the board" in admin and "/static/activity.js" not in admin


def test_rows_are_only_the_persons_own_with_every_citation(web):
    client, llm, doc, memo, path = web
    llm.responses = [[good(doc)], []]
    client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"})
    client.post("/api/ask", json={"user": "bob", "question": "What did the board decide?"})
    con = connect(path)
    con.execute("UPDATE audit SET cited=? WHERE user='alice'", (f"{doc}:1;{doc}:1;{memo}:1",))
    con.commit()
    client.post("/api/feedback", json={"user": "alice", "audit_id": 1, "rating": "down", "reason": "wrong year"})
    client.post("/api/review", json={"user": "alice", "audit_id": 1, "note": "please check"})

    j = client.get("/api/me/activity", params={"user": "alice"}).json()
    assert j["questions"] == ["How did palm oil output change?"]  # never bob's
    r = j["rows"][0]
    assert r["cited"] == [[doc, 1], [doc, 1], [memo, 1]]  # all of them, not only the first
    assert r["rating"] == "down" and r["review"] == "open" and not r["cell"]
    assert client.get("/api/me/activity", params={"user": "nobody"}).status_code == 404


def test_topic_counts_stay_inside_the_grant(web):
    client, *_ = web
    alice = client.get("/api/me/topic", params={"user": "alice", "q": "merger"}).json()
    bob = client.get("/api/me/topic", params={"user": "bob", "q": "merger"}).json()
    assert alice["counts"] == {} and len(bob["counts"]) == 1  # the memo is bob's alone
    assert client.get("/api/me/topic", params={"user": "alice", "q": "   "}).status_code == 400
    assert client.get("/api/me/topic", params={"user": "nobody", "q": "merger"}).status_code == 404


def test_a_phrase_particular_to_a_report_is_found_and_a_running_head_is_not(db):
    for lib, title in [("lib", "Alpha"), ("lib", "Beta")]:
        db.execute("INSERT OR IGNORE INTO libraries VALUES (?, ?)", (lib, lib))
        cur = db.execute("INSERT INTO documents(library_id, title, pages) VALUES (?, ?, 12)", (lib, title))
        for n in range(1, 13):
            body = "tidal energy subsidies" if title == "Alpha" else "wind turbine leasing"
            text = f"running header {' padding words here' * 30} {body} {' more padding words' * 30} trailing"
            db.execute("INSERT INTO pages(doc_id, page, text) VALUES (?, ?, ?)", (cur.lastrowid, n, text))
    db.execute("INSERT INTO grants VALUES ('ann', 'lib')")
    apart = edition_topics(db, "ann", {"padding", "words", "here", "more"})
    titles = {r["id"]: r["title"] for r in db.execute("SELECT id, title FROM documents")}
    by_title = {titles[d]: phrase for d, phrase in apart.items()}
    assert set(by_title["Alpha"].split()) <= {"tidal", "energy", "subsidies"}
    assert set(by_title["Beta"].split()) <= {"wind", "turbine", "leasing"}
    assert "running header" not in by_title.values()
    assert edition_topics(db, "stranger", set()) == {}  # nothing outside a grant


def test_topics_from_questions_must_be_in_the_reports_and_not_overlap(web):
    client, llm, doc, memo, path = web
    con = connect(path)
    asked = ["How did palm oil output change?", "Is palm oil output up?", "What about unicorn farming?"]
    assert question_topics(con, "alice", asked, {"how", "did", "what", "about"}) == ["palm oil"]  # output overlaps; unicorns are not in the reports
    assert _word_pairs("GDP growth in 2024", {"in"}) == ["gdp growth"]
    assert topic_pages(con, "alice", '"palm oil"') == {doc: 1}


def test_the_script_never_builds_markup_from_text():
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", SCRIPTS)


def ask_alice(client, llm, doc):
    llm.responses = [[good(doc)]]
    return client.post("/api/ask", json={"user": "alice", "question": "How did palm oil output change?"}).json()


def test_a_checked_fact_comes_from_the_stored_answer_and_only_once(web):
    client, llm, doc, *_ = web
    ask_alice(client, llm, doc)
    llm.responses = [[good(doc)]]
    client.post("/api/ask", json={"user": "alice", "question": "Palm oil output?"})
    found = client.get("/api/me/activity", params={"user": "alice"}).json()["findings"]
    assert len(found) == 1 and found[0]["doc_id"] == doc and found[0]["page"] == 1 and found[0]["claim"] == 0


def test_next_steps_say_why_and_carry_an_action(web):
    client, llm, doc, *_ = web
    assert client.get("/api/me/activity", params={"user": "alice"}).json()["next"][0]["kind"] == "start"
    a = ask_alice(client, llm, doc)
    client.post("/api/review", json={"user": "alice", "audit_id": a["audit_id"], "note": "check"})
    steps = client.get("/api/me/activity", params={"user": "alice"}).json()["next"]
    assert steps[0]["kind"] == "review" and steps[0]["actions"][0]["flow"] == a["audit_id"]
    assert all(s["title"] and s["why"] and s["actions"] for s in steps)


def test_a_report_can_only_hold_a_real_checked_claim_of_its_owner(web):
    client, llm, doc, *_ = web
    a = ask_alice(client, llm, doc)
    forged = {"type": "finding", "audit_id": a["audit_id"], "claim": 0, "text": "GDP grew 900 percent", "quote": "made up"}
    r = client.post("/api/me/boards", json={"user": "alice", "kind": "report", "title": "Mine", "blocks": [forged]}).json()
    assert r["blocks"][0]["text"] == a["claims"][0]["text"] and r["blocks"][0]["quote"] == a["claims"][0]["quote"]  # the forged words are gone
    theirs = client.post("/api/me/boards", json={"user": "bob", "kind": "report", "title": "Bob's", "blocks": [{"type": "finding", "audit_id": a["audit_id"], "claim": 0}]}).json()
    assert theirs["blocks"][0]["missing"] and "text" not in theirs["blocks"][0]  # another person's answer stays theirs
    assert client.get(f"/api/me/boards/{r['id']}", params={"user": "bob"}).status_code == 404
    assert client.delete(f"/api/me/boards/{r['id']}", params={"user": "bob"}).status_code == 404


def test_boards_validate_what_they_hold_and_add_goes_to_the_latest(web):
    client, *_ = web
    bad = {"user": "alice", "kind": "dashboard", "title": "X"}
    assert client.post("/api/me/boards", json={**bad, "blocks": [{"type": "weather"}]}).status_code == 400
    assert client.post("/api/me/boards", json={**bad, "kind": "secret", "blocks": []}).status_code == 400
    assert client.post("/api/me/boards", json={**bad, "title": " ", "blocks": []}).status_code == 400
    assert client.post("/api/me/boards", json={**bad, "kind": "report", "blocks": [{"type": "note", "text": " "}]}).status_code == 400
    first = client.post("/api/me/boards/add", json={"user": "alice", "kind": "dashboard", "blocks": [{"type": "days"}]}).json()
    assert first["title"] == "My dashboard" and len(first["blocks"]) == 1
    again = client.post("/api/me/boards/add", json={"user": "alice", "kind": "dashboard", "blocks": [{"type": "days"}, {"type": "pages"}]}).json()
    assert again["id"] == first["id"] and [b["type"] for b in again["blocks"]] == ["days", "pages"]  # days is not added twice
    saved = client.put(f"/api/me/boards/{first['id']}", json={"user": "alice", "title": "Renamed", "blocks": [{"type": "pages"}]}).json()
    assert saved["title"] == "Renamed" and len(saved["blocks"]) == 1
    assert client.get("/api/me/boards", params={"user": "bob"}).json()["boards"] == []
    assert client.delete(f"/api/me/boards/{first['id']}", params={"user": "alice"}).json() == {"deleted": first["id"]}


def talk(client, message):
    return client.post("/api/me/assistant", json={"user": "alice", "message": message}).json()


def test_with_no_model_the_assistant_follows_fixed_steps(web):
    client, llm, doc, *_ = web
    ask_alice(client, llm, doc)
    assert talk(client, "What am I looking at?")["mode"] == "guided"
    assert "workspace" in talk(client, "what am i looking at")["say"]
    dash = talk(client, "Build a dashboard about palm oil")["actions"][0]
    assert dash["do"] == "create_dashboard" and dash["blocks"][0]["terms"] == ["palm oil"]
    rep = talk(client, "Draft a report on palm oil")["actions"][0]
    assert rep["do"] == "create_report" and rep["blocks"][1]["type"] == "heading" and rep["blocks"][2]["type"] == "finding"
    assert talk(client, "Draft a report on unicorns")["actions"][0]["do"] == "handoff"  # nothing checked to report
    assert talk(client, "Show tariff across the editions")["actions"][0] == {"do": "add_topic", "term": "tariff"}
    assert talk(client, "show questions with no answer")["actions"][0] == {"do": "filter", "outcome": "none"}
    out = talk(client, "What was GDP growth in 2024?")
    assert out["actions"][0] == {"do": "handoff", "question": "What was GDP growth in 2024?"}  # never answered here
    assert client.post("/api/me/assistant", json={"user": "alice", "message": " "}).status_code == 400
    assert client.post("/api/me/assistant", json={"user": "nobody", "message": "hi"}).status_code == 404


class TalkingLLM:
    def __init__(self, text):
        self.text, self.prompts = text, []

    def complete(self, system, prompt):
        self.prompts.append((system, prompt))
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def activity_and_reports(web):
    client, llm, doc, memo, path = web
    ask_alice(client, llm, doc)
    con = connect(path)
    from datetime import datetime
    return user_activity(con, "alice", datetime(2026, 11, 1), "a model"), [{"id": doc, "title": "Sector Outlook 2025", "pages": 1}]


def test_a_model_plugs_in_but_can_only_use_known_actions(web):
    activity, reports = activity_and_reports(web)
    llm = TalkingLLM('<think>hm</think>{"say":"Filtered it.","actions":[{"do":"filter","outcome":"none"},{"do":"delete_everything"},{"do":"open_flow","audit_id":999},{"do":"filter","report":999}]}')
    r = assistant_reply(llm, "only refusals please", [], activity, reports)
    assert r["mode"] == "model" and r["say"] == "Filtered it." and r["actions"] == [{"do": "filter", "outcome": "none"}]
    assert "Sector Outlook 2025" in llm.prompts[0][1] and "NEVER state what a report says" in llm.prompts[0][0]
    assert "Palm oil output rose" not in llm.prompts[0][1]  # the model is given counts and titles, not report text to repeat


def test_a_model_inventing_a_number_is_not_believed(web):
    activity, reports = activity_and_reports(web)
    r = assistant_reply(TalkingLLM('{"say":"Growth was 87654 percent.","actions":[]}'), "what am i looking at", [], activity, reports)
    assert "87654" not in r["say"] and "workspace" in r["say"]


def test_a_failing_or_rambling_model_falls_back_to_the_fixed_steps(web):
    activity, reports = activity_and_reports(web)
    for broken in (OSError("down"), "not json at all", "{}"):
        r = assistant_reply(TalkingLLM(broken), "what am i looking at", [], activity, reports)
        assert r["mode"] == "guided" and "workspace" in r["say"]


def test_a_key_in_dotenv_is_all_it_takes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# a comment\nexport RA_TEST_ONE="from file"\nRA_TEST_TWO=from file\nnonsense\n', encoding="utf-8")
    monkeypatch.delenv("RA_TEST_ONE", raising=False)
    monkeypatch.setenv("RA_TEST_TWO", "from shell")
    load_env(str(env))
    assert os.environ["RA_TEST_ONE"] == "from file" and os.environ["RA_TEST_TWO"] == "from shell"
    monkeypatch.delenv("RA_TEST_ONE")
    load_env(str(tmp_path / "missing"))  # no file is fine


def test_topics_come_out_the_same_on_every_run_and_keep_the_persons_spelling(web):
    client, llm, doc, *_ = web
    for q in ("How did palm oil output change?", "Why did PALM OIL prices move?", "Is output higher?"):
        llm.responses = [[good(doc)]]
        client.post("/api/ask", json={"user": "alice", "question": q})
    first = client.get("/api/me/activity", params={"user": "alice"}).json()["topics"]
    assert first == client.get("/api/me/activity", params={"user": "alice"}).json()["topics"]
    assert first[0]["term"] == "PALM OIL" and first[0]["group"] == "asked"  # the newest spelling the person used
