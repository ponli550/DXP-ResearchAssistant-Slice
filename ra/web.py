"""A small web demo over the same pipeline.

Pick a user, ask a question, and watch the answer built on a canvas: the question, the sources found, the quote
check on every drafted claim, the answer, and a human-gated next step. The login is a demo picker, not
authentication. Access is still enforced in retrieval and on the page route.
"""
import os
import re

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .answer import ask
from .baselines import Extractive
from .db import connect


def default_llm():
    """Claude or an OpenAI-compatible model when a key is set, otherwise the extractive baseline, and say which one."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from .llm import ClaudeLLM

        return ClaudeLLM(), "claude"
    if os.environ.get("HERMES_API_KEY"):
        from .llm import OpenAICompatLLM

        llm = OpenAICompatLLM()
        return llm, llm.name
    return Extractive(), "extractive baseline (no model key set)"


def model_source(llm, name):
    """Where the model runs, for the sources list: a provider and endpoint, or the note that there is none."""
    base = getattr(llm, "base_url", None)
    if base:
        return {"kind": "model", "name": name, "where": base, "state": "key set"}
    if name == "claude":
        return {"kind": "model", "name": name, "where": "Anthropic API", "state": "key set"}
    if name.startswith("extractive"):
        return {"kind": "model", "name": name, "where": "no provider", "state": "no key: answers come from the baseline, which is often irrelevant"}
    return {"kind": "model", "name": name, "where": "in process", "state": "scripted"}


class AskBody(BaseModel):
    user: str
    question: str


class FeedbackBody(BaseModel):
    user: str
    audit_id: int
    rating: str
    reason: str = ""


class ReviewBody(BaseModel):
    user: str
    audit_id: int
    note: str = ""


class KeyBody(BaseModel):
    name: str
    user: str


def _hash_key(key):
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()


def user_for_key(con, key):
    """The user an API key stands for, or None. A key is a credential for one user, so it inherits that user's grants and nothing more."""
    row = con.execute("SELECT id, user FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (_hash_key(key),)).fetchone()
    if not row:
        return None
    con.execute("UPDATE api_keys SET last_used=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (row["id"],))
    con.commit()
    return row["user"]


def quote_span(text, quote):
    """Offsets of the quote inside the page text, ignoring differences in whitespace and case. None if not found."""
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I)
    return [m.start(), m.end()] if m else None


def _bar(n, top, width=8):
    """A text bar, as the home board draws them: proportional, never empty for a non-zero count."""
    if not top or n <= 0:
        return ""
    return "█" * max(1, round(width * n / top))


def board_data(con, model_name, results_dir="evals/results", since=None, db_path=None, model=None):
    """The admin board: a brief since the last visit, what to do, and counts by library, user and run.

    Counts, names, reasons and dates only, never a passage or a page's text, because a board stays on screen. Every list
    says in `why` how it was made. `since` is an ISO time from the device that last looked; the brief is silent when
    nothing happened after it.
    """
    import glob
    import json
    import os
    from datetime import date, datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    libs = con.execute(
        "SELECT l.id, l.name, "
        "(SELECT COUNT(*) FROM documents d WHERE d.library_id = l.id) AS documents, "
        "(SELECT COALESCE(SUM(d.pages),0) FROM documents d WHERE d.library_id = l.id) AS pages, "
        "(SELECT COALESCE(SUM(d.needs_ocr),0) FROM documents d WHERE d.library_id = l.id) AS needs_ocr, "
        "(SELECT COUNT(*) FROM grants g WHERE g.library_id = l.id) AS users "
        "FROM libraries l ORDER BY l.name").fetchall()
    low = {r["library_id"]: r["n"] for r in con.execute(
        "SELECT d.library_id, COUNT(*) AS n FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.low_text = 1 GROUP BY d.library_id")}
    top_pages = max([r["pages"] for r in libs] or [0])
    total_pages = sum(r["pages"] for r in libs)
    flagged_pages = sum(low.values())
    coverage = {"pages": total_pages, "searchable": total_pages - flagged_pages, "flagged": flagged_pages,
                "pct": round(100 * (total_pages - flagged_pages) / total_pages) if total_pages else 0}
    libraries = [{"library": r["name"], "documents": r["documents"], "pages": r["pages"], "low_text_pages": low.get(r["id"], 0),
                  "users": r["users"], "bar": _bar(r["pages"], top_pages)} for r in libs]

    rows = con.execute("SELECT id, at, user, question, status, cited FROM audit ORDER BY id DESC").fetchall()
    per_user = {}
    for r in rows:
        u = per_user.setdefault(r["user"], {"user": r["user"], "asked": 0, "answered": 0, "not_found": 0})
        u["asked"] += 1
        u["answered" if r["status"] == "answered" else "not_found"] += 1
    top_asked = max([u["asked"] for u in per_user.values()] or [0])
    users = sorted(per_user.values(), key=lambda u: (-u["asked"], u["user"]))
    for u in users:
        u["bar"] = _bar(u["asked"], top_asked)
    usage = {"asked": len(rows), "answered": sum(1 for r in rows if r["status"] == "answered"),
             "not_found": sum(1 for r in rows if r["status"] != "answered"), "model": model_name, "users": users}
    recent = [{"at": r["at"], "user": r["user"], "question": r["question"], "status": r["status"],
               "citations": len([c for c in r["cited"].split(",") if c])} for r in rows[:12]]

    fb = con.execute("SELECT user, rating, reason, at FROM feedback ORDER BY id DESC").fetchall()
    rv = con.execute("SELECT r.id, r.user, r.note, r.status, r.at, a.question FROM reviews r JOIN audit a ON a.id = r.audit_id ORDER BY r.id DESC").fetchall()
    people = {"up": sum(1 for r in fb if r["rating"] == "up"), "down": sum(1 for r in fb if r["rating"] == "down"),
              "down_reasons": [{"user": r["user"], "reason": r["reason"], "at": r["at"]} for r in fb if r["rating"] == "down" and r["reason"]][:6],
              "reviews_open": [{"user": r["user"], "question": r["question"], "note": r["note"], "at": r["at"]} for r in rv if r["status"] == "open"][:6]}

    runs = []
    for f in sorted(glob.glob(os.path.join(results_dir, "*.json")), key=os.path.getmtime, reverse=True):
        try:
            j = json.load(open(f))
        except (OSError, ValueError):
            continue
        runs.append({"name": os.path.splitext(os.path.basename(f))[0], "date": date.fromtimestamp(os.path.getmtime(f)).isoformat(),
                     "questions": j.get("questions"), "answerable": j.get("answerable"), "retrieval": j.get("retrieval_hit_rate"),
                     "correct": j.get("correct"), "timid": j.get("timid"), "correct_refusal": j.get("correct_refusal"),
                     "wrong_and_confident": j.get("wrong_and_confident")})

    # Relationships: libraries, documents in edition order and users, joined by grants and by the citations users' answers made.
    # Issuer, sector and instrument nodes arrive with MARC's tables; today the real edges are membership, grant and citation.
    from .retrieve import EDITION_RE, MONTHS
    docs = con.execute("SELECT d.id, d.title, d.library_id, l.name AS library FROM documents d JOIN libraries l ON l.id = d.library_id").fetchall()
    def edition_key(title):
        m = EDITION_RE.search(title)
        return (int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1) if m else (0, 0)
    cites = {}
    for r in rows:
        for c in r["cited"].split(","):
            if c:
                did = int(c.split(":")[0])
                cites[(did, r["user"])] = cites.get((did, r["user"]), 0) + 1
    nodes = [{"id": f"L{r['id']}", "kind": "library", "label": r["name"]} for r in libs]
    by_lib = {}
    for d in sorted(docs, key=lambda d: (d["library"], edition_key(d["title"]))):
        n = sum(v for (did, _), v in cites.items() if did == d["id"])
        nodes.append({"id": f"D{d['id']}", "kind": "document", "label": d["title"], "library": d["library"], "citations": n,
                      "edition": edition_key(d["title"])})
        by_lib.setdefault(d["library_id"], []).append(d["id"])
    for u in users:
        nodes.append({"id": f"U{u['user']}", "kind": "user", "label": u["user"], "asked": u["asked"]})
    edges = [{"a": f"L{d['library_id']}", "b": f"D{d['id']}", "kind": "member", "w": 1} for d in docs]
    edges += [{"a": f"U{r['user']}", "b": f"L{r['library_id']}", "kind": "grant", "w": 1} for r in con.execute("SELECT user, library_id FROM grants")]
    edges += [{"a": f"D{did}", "b": f"U{u}", "kind": "cited", "w": w} for (did, u), w in cites.items()]
    for ids in by_lib.values():
        ordered = sorted(ids, key=lambda i: edition_key(next(d["title"] for d in docs if d["id"] == i)))
        edges += [{"a": f"D{x}", "b": f"D{y}", "kind": "next_edition", "w": 1} for x, y in zip(ordered, ordered[1:])]
    graph = {"nodes": nodes, "edges": edges,
             "why": "membership from the documents table, grants from the grants table, citations from the audit log's cited pages, edition order from the month and year in each title. Issuer and sector nodes come with MARC's database."}

    # Sources: where the data and the model live, and when each was last used. States are facts, not health scores.
    sources = []
    last_q = rows[0]["at"] if rows else None
    if db_path and db_path != ":memory:" and os.path.exists(db_path):
        st = os.stat(db_path)
        sources.append({"kind": "library database", "name": os.path.basename(db_path), "where": os.path.abspath(db_path),
                        "state": f"{len(libs)} librar{'ies' if len(libs) != 1 else 'y'}, {sum(r['documents'] for r in libs)} documents, {st.st_size // 1024} KB, last written {datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')}"})
    else:
        sources.append({"kind": "library database", "name": "in memory", "where": "this process", "state": f"{len(libs)} libraries"})
    sources.append(model or {"kind": "model", "name": model_name, "where": "unknown", "state": ""})
    sources[-1] = dict(sources[-1], last_used=last_q)
    sources.append({"kind": "evaluation results", "name": results_dir, "where": os.path.abspath(results_dir),
                    "state": f"{len(runs)} saved run{'s' if len(runs) != 1 else ''}" + (f", latest {runs[0]['date']}" if runs else "")})

    # What to do: each item is a count with a reason, and appears only when the count is not zero.
    todo = []
    if people["reviews_open"]:
        todo.append({"n": len(people["reviews_open"]), "what": "review request" + ("s" if len(people["reviews_open"]) != 1 else "") + " waiting for a person",
                     "why": "someone pressed Create review request and nothing happens until a person reads it"})
    flagged = sum(x["low_text_pages"] for x in libraries)
    if flagged:
        todo.append({"n": flagged, "what": "page" + ("s" if flagged != 1 else "") + " flagged for OCR", "why": "pdftotext read almost nothing on them; they are not searchable until OCR runs"})
    if people["down_reasons"]:
        todo.append({"n": len(people["down_reasons"]), "what": "thumbs-down reason" + ("s" if len(people["down_reasons"]) != 1 else "") + " to read",
                     "why": "a reason becomes a new test question once a person has checked it"})
    if runs:
        bad = [r for r in runs if r["wrong_and_confident"]]
        age = (now.date() - date.fromisoformat(runs[0]["date"])).days
        if bad and bad[0] is runs[0]:
            todo.append({"n": runs[0]["wrong_and_confident"], "what": "wrong-and-confident in the latest run", "why": "the target is zero; read those answers before anything else"})
        if age > 7:
            todo.append({"n": age, "what": "days since the question set was last run", "why": "the harness runs on every change; a stale run means a change went unmeasured"})
    else:
        todo.append({"n": 0, "what": "evaluation runs saved", "why": "nothing says how well answers hold up until the question set is run"})

    # The brief: what happened after `since`, in one sentence. Silent when nothing did.
    brief, why = "", ""
    try:
        t0 = datetime.fromisoformat(since.replace("Z", "")) if since else None
    except ValueError:
        t0 = None
    if t0 is None:
        why = "first visit from this device: no since time, so the brief covers everything"
        new_rows, new_fb, new_rv = rows, fb, [r for r in rv if r["status"] == "open"]
    else:
        new_rows = [r for r in rows if r["at"] > t0.isoformat(timespec="seconds")]
        new_fb = [r for r in fb if r["at"] > t0.isoformat(timespec="seconds")]
        new_rv = [r for r in rv if r["at"] > t0.isoformat(timespec="seconds") and r["status"] == "open"]
        away = now - t0
        why = f"away {away.days} d {away.seconds // 3600} h; counting rows after {t0.isoformat(timespec='minutes')}"
    parts = []
    if new_rows:
        who = sorted({r["user"] for r in new_rows})
        ans = sum(1 for r in new_rows if r["status"] == "answered")
        parts.append(f"{len(new_rows)} question{'s' if len(new_rows) != 1 else ''} from {len(who)} user{'s' if len(who) != 1 else ''}, {ans} answered with checked citations, {len(new_rows) - ans} not found")
    if new_fb:
        parts.append(f"{len(new_fb)} rating{'s' if len(new_fb) != 1 else ''}" + (f", {sum(1 for r in new_fb if r['rating'] == 'down')} down" if any(r["rating"] == "down" for r in new_fb) else ""))
    if new_rv:
        parts.append(f"{len(new_rv)} review request{'s' if len(new_rv) != 1 else ''} waiting")
    if runs and (t0 is None or runs[0]["date"] >= t0.date().isoformat()):
        parts.append(f"the question set ran on {runs[0]['date']}: wrong-and-confident {runs[0]['wrong_and_confident']}")
    if parts:
        hello = "Good morning" if 5 <= datetime.now().hour < 12 else "Good afternoon" if datetime.now().hour < 18 else "Good evening"
        lead = "Since your last look: " if t0 is not None else "So far: "
        brief = f"{hello}. {lead}" + "; ".join(parts) + "."
    return {"now": now.isoformat(timespec="seconds"), "brief": brief, "brief_why": why, "todo": todo, "coverage": coverage,
            "sources": sources, "libraries": libraries, "usage": usage, "recent": recent, "people": people, "runs": runs, "graph": graph}


def create_app(db_path, llm_factory=default_llm, results_dir="evals/results", admin_token=None):
    app = FastAPI(title="Research assistant demo")
    llm, model_name = llm_factory()
    # The admin board is behind a token, typed once per device. The demo default is "demo"; set ADMIN_TOKEN for anything shared.
    admin_token = admin_token or os.environ.get("ADMIN_TOKEN") or "demo"

    def db():
        return connect(db_path)

    def allowed_libraries(con, user):
        return [r["library_id"] for r in con.execute("SELECT library_id FROM grants WHERE user=?", (user,))]

    def own_audit(con, user, audit_id):
        if not con.execute("SELECT 1 FROM audit WHERE id=? AND user=?", (audit_id, user)).fetchone():
            raise HTTPException(403, "That answer does not belong to this user.")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    @app.get("/api/users")
    def users():
        con = db()
        return {"users": [r["user"] for r in con.execute("SELECT DISTINCT user FROM grants ORDER BY user")], "model": model_name}

    @app.get("/api/library")
    def library(user: str):
        con = db()
        rows = con.execute(
            "SELECT d.id, d.title, d.pages, d.needs_ocr, l.name AS library FROM documents d "
            "JOIN libraries l ON l.id = d.library_id JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY d.title",
            (user,),
        ).fetchall()
        return {"documents": [dict(r) for r in rows]}

    @app.post("/api/ask")
    def ask_route(body: AskBody, x_api_key: str = Header(default="")):
        question = body.question.strip()
        if not question or len(question) > 500:
            raise HTTPException(400, "Ask a question of up to 500 characters.")
        con = db()
        if x_api_key:
            # An integration asks with a key, not a user name. The key decides the user; the body's user is ignored.
            user = user_for_key(con, x_api_key)
            if not user:
                raise HTTPException(401, "That API key is unknown or revoked.")
            body.user = user
        if not allowed_libraries(con, body.user):
            raise HTTPException(403, "This user has no library access.")
        res = ask(con, llm, body.user, question)
        titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM documents")}
        return {
            "audit_id": res.audit_id,
            "status": res.status,
            "model": model_name,
            "attempts": res.attempts,
            "retrieved": [{"doc_id": p.doc_id, "title": p.title, "page": p.page, "text": p.text[:700]} for p in res.closest],
            "claims": [{"text": c.text, "doc_id": c.doc_id, "title": titles[c.doc_id], "page": c.page, "quote": c.quote} for c in res.claims],
            "rejected": len(res.rejected),
            "rejected_claims": [
                {"text": c.text, "doc_id": c.doc_id, "title": titles.get(c.doc_id, "unknown document"), "page": c.page, "quote": c.quote, "reason": why}
                for c, why in res.rejected
            ],
            "closest": [{"title": p.title, "doc_id": p.doc_id, "page": p.page} for p in res.closest[:3]],
            "trace": [{**st, "model": model_name} if st["kind"] == "draft" else st for st in res.trace],
        }

    @app.post("/api/feedback")
    def feedback(body: FeedbackBody):
        if body.rating not in ("up", "down") or len(body.reason) > 300:
            raise HTTPException(400, "Rating must be up or down, reason up to 300 characters.")
        con = db()
        own_audit(con, body.user, body.audit_id)
        cur = con.execute("INSERT INTO feedback(user, audit_id, rating, reason) VALUES (?,?,?,?)", (body.user, body.audit_id, body.rating, body.reason.strip()))
        con.commit()
        return {"id": cur.lastrowid}

    @app.post("/api/review")
    def review(body: ReviewBody):
        if len(body.note) > 500:
            raise HTTPException(400, "Note up to 500 characters.")
        con = db()
        own_audit(con, body.user, body.audit_id)
        cur = con.execute("INSERT INTO reviews(user, audit_id, note) VALUES (?,?,?)", (body.user, body.audit_id, body.note.strip()))
        con.commit()
        return {"id": cur.lastrowid, "status": "open"}

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
        return {"title": row["title"], "page": p, "text": row["text"], "span": quote_span(row["text"], quote) if quote else None}

    @app.get("/api/audit")
    def audit(user: str):
        con = db()
        rows = con.execute("SELECT at, question, status, cited FROM audit WHERE user=? ORDER BY id DESC LIMIT 20", (user,)).fetchall()
        return {"entries": [dict(r) for r in rows]}


    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page():
        return DASHBOARD_HTML

    def admin(x_token):
        if not x_token or x_token != admin_token:
            raise HTTPException(401, "That token is not the admin token.")

    @app.get("/api/admin/keys")
    def list_keys(x_token: str = Header(default="")):
        admin(x_token)
        rows = db().execute("SELECT id, name, user, prefix, created_at, last_used, revoked_at FROM api_keys ORDER BY id DESC").fetchall()
        return {"keys": [dict(r) for r in rows]}

    @app.post("/api/admin/keys")
    def make_key(body: KeyBody, x_token: str = Header(default="")):
        import secrets

        admin(x_token)
        con = db()
        name, user = body.name.strip(), body.user.strip()
        if not name or not user:
            raise HTTPException(400, "A key needs a name and a user.")
        if not allowed_libraries(con, user):
            raise HTTPException(400, "That user holds no library grant, so a key for them could search nothing.")
        key = "ra_" + secrets.token_urlsafe(24)
        cur = con.execute("INSERT INTO api_keys(name, user, key_hash, prefix) VALUES (?,?,?,?)", (name, user, _hash_key(key), key[:7]))
        con.commit()
        # The key is returned once and never stored; only its hash is.
        return {"id": cur.lastrowid, "name": name, "user": user, "key": key, "prefix": key[:7]}

    @app.delete("/api/admin/keys/{key_id}")
    def revoke_key(key_id: int, x_token: str = Header(default="")):
        admin(x_token)
        con = db()
        cur = con.execute("UPDATE api_keys SET revoked_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=? AND revoked_at IS NULL", (key_id,))
        con.commit()
        if not cur.rowcount:
            raise HTTPException(404, "No active key with that id.")
        return {"revoked": key_id}

    @app.get("/api/admin/board")
    def admin_board(since: str = "", x_token: str = Header(default="")):
        if not x_token or x_token != admin_token:
            raise HTTPException(401, "That token is not the admin token.")
        return board_data(db(), model_name, results_dir, since or None, db_path=db_path, model=model_source(llm, model_name))

    return app


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research assistant</title>
<style>
:root{color-scheme:light dark;--bg:#f4f3ee;--fg:#1d2327;--muted:#5f6b73;--card:#fff;--line:#d3d0c5;--accent:#0b5c6b;--accent2:#e3f1f4;--ok:#1f6b3a;--okbg:#e6f4ea;--bad:#9b2c2c;--badbg:#fdecec;--warn:#8a4b00;--warnbg:#fff3e0;--mark:#ffe58a;--grid:#e4e1d6;--answer:#e8ecff;--answerline:#9aa8e8}
@media (prefers-color-scheme:dark){:root{--bg:#14181b;--fg:#e8ecee;--muted:#98a4ab;--card:#1d2327;--line:#323a3f;--accent:#5fc4d6;--accent2:#173238;--ok:#7fd49a;--okbg:#17301f;--bad:#ff9d9d;--badbg:#3a1b1b;--warn:#ffb866;--warnbg:#3a2a12;--mark:#6b5b00;--grid:#1f2529;--answer:#1d2340;--answerline:#4a5aa8}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;display:flex;flex-direction:column}
.top{padding:.7rem 16px;display:flex;flex-wrap:wrap;gap:.6rem 1.2rem;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}
h1{font-size:1.15rem;margin:0}.sub{color:var(--muted);margin:0;font-size:.82rem}
.banner{background:var(--warnbg);color:var(--warn);padding:.45rem 16px;font-size:.85rem}
.askbar{display:flex;gap:.5rem;padding:.6rem 16px;border-bottom:1px solid var(--line);align-items:flex-start}
textarea,select,button{font:inherit;color:inherit;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:.45rem .65rem}
textarea{flex:1;min-width:0;height:2.7rem;resize:vertical}label{font-size:.75rem;color:var(--muted);display:block}
button{cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button:disabled{opacity:.6;cursor:wait}
#stage{position:relative;flex:1;min-height:420px;overflow:hidden;touch-action:none;cursor:grab;background-color:var(--bg);background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:22px 22px}
#stage.drag{cursor:grabbing}
#canvas{position:absolute;left:0;top:0;width:min(700px,calc(100% - 24px));transform-origin:0 0;display:flex;flex-direction:column;gap:84px;padding:12px 0 60px}
#canvas.moving{transition:transform .75s cubic-bezier(.22,1,.36,1)}
.node{position:relative;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.1rem;opacity:0;transform:translateY(14px);transition:opacity .5s ease,transform .5s ease}
.node.in{opacity:1;transform:none}
.node:not(:last-child)::after{content:"";position:absolute;left:50%;top:100%;width:2px;height:84px;background:var(--line)}
.tag{display:inline-block;font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;font-weight:700;padding:.1rem .45rem;border-radius:5px;background:var(--accent2);color:var(--accent);margin-bottom:.4rem}
.node h2{font-size:1.02rem;margin:.1rem 0 .5rem}.node.answer{background:var(--answer);border-color:var(--answerline)}
.muted{color:var(--muted);font-size:.85rem}
.src{border:1px solid var(--line);border-radius:9px;padding:.5rem .65rem;margin:.4rem 0;background:var(--card);cursor:pointer;transition:border-color .2s,box-shadow .2s}
.src:hover,.src:focus-visible,.src.open{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent2);outline:none}
.src .more{max-height:0;overflow:hidden;transition:max-height .4s ease}.src:hover .more,.src:focus-visible .more,.src.open .more{max-height:420px}
.src .more p{margin:.5rem 0;font-size:.85rem;white-space:pre-wrap}
.snip{color:var(--muted);font-size:.85rem}
.row{display:flex;gap:.6rem;align-items:flex-start;margin:.5rem 0}.badge{flex:none;font-size:.72rem;font-weight:700;padding:.1rem .45rem;border-radius:5px}
.badge.ok{background:var(--okbg);color:var(--ok)}.badge.bad{background:var(--badbg);color:var(--bad)}
.chip{border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:999px;padding:.05rem .55rem;font-size:.82rem}
details summary{cursor:pointer;color:var(--accent);font-size:.85rem}blockquote{margin:.4rem 0;padding:.3rem .7rem;border-left:3px solid var(--accent);color:var(--muted);font-size:.88rem}
ol{padding-left:1.2rem;margin:.4rem 0}li{margin:.6rem 0}
.fb{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin-top:.7rem}.fb input{flex:1;min-width:8rem;font:inherit;color:inherit;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:.35rem .6rem}
#spy{position:absolute;left:12px;top:12px;display:flex;flex-direction:column;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px;z-index:3;touch-action:auto}
#spy button{border:0;background:transparent;text-align:left;padding:.3rem .6rem;font-size:.82rem;color:var(--muted);border-radius:7px}
#spy button[aria-current=true]{background:var(--accent2);color:var(--accent);font-weight:700}
#zoom{position:absolute;right:12px;top:12px;display:flex;flex-direction:column;gap:4px;z-index:3;touch-action:auto}#zoom button{width:2.2rem;padding:.3rem 0;text-align:center}
#pane{position:fixed;top:0;right:0;height:100%;width:min(520px,100%);background:var(--card);border-left:1px solid var(--line);z-index:10;transform:translateX(102%);transition:transform .5s cubic-bezier(.22,1,.36,1);display:flex;flex-direction:column;padding:.8rem 1rem}
#pane.open{transform:none}#pane header{display:flex;justify-content:space-between;gap:.5rem;align-items:center;margin-bottom:.5rem}
pre{position:relative;white-space:pre-wrap;word-break:break-word;font:.85rem/1.5 ui-monospace,monospace;margin:0;overflow:auto;flex:1}mark{background:var(--mark);color:inherit;border-radius:3px}
#tabs{display:flex;gap:.25rem;padding:.35rem 16px 0;border-bottom:1px solid var(--line)}
#tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:.35rem .8rem;color:var(--muted)}
#tabs button[aria-selected=true]{color:var(--accent);border-color:var(--accent);font-weight:700}
#steps{flex:1;overflow:auto;position:relative}
#steps .grid{max-width:900px;margin:0 auto;padding:1rem 16px 3rem;display:grid;grid-template-columns:120px minmax(0,1fr);gap:0 1rem}
#rail{position:sticky;top:1rem;align-self:start;display:flex;flex-direction:column;gap:.1rem;border-left:2px solid var(--line);padding-left:.7rem}
#rail button{border:0;background:transparent;text-align:left;color:var(--muted);font-size:.8rem;padding:.15rem 0;position:relative}
#rail button::before{content:"";position:absolute;left:calc(-.7rem - 5px);top:.45rem;width:8px;height:8px;border-radius:50%;background:var(--line)}
#rail button[aria-current=true]{color:var(--accent);font-weight:700}#rail button[aria-current=true]::before{background:var(--accent)}
.step{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:.8rem 1rem;margin:.7rem 0}
.stephead{display:flex;justify-content:space-between;align-items:center;font-weight:700}
.stepline{width:100%;display:flex;justify-content:space-between;gap:.6rem;text-align:left;margin-top:.5rem;background:var(--bg)}
.stepbody{margin-top:.5rem}.stepbody ul{margin:.3rem 0;padding-left:1.1rem}.stepbody li{margin:.2rem 0}
.foot{display:flex;justify-content:space-between;gap:.5rem;color:var(--muted);font-size:.78rem;margin-top:.6rem}
.kind{font-weight:700;color:var(--accent)}.toggle{display:block;margin:.4rem auto;background:transparent;color:var(--muted);border-style:dashed}
.flash{animation:flash 1.8s ease}@keyframes flash{0%,60%{background:var(--accent2)}100%{background:var(--card)}}
@media (max-width:700px){#rail{display:none}#steps .grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){#spy{flex-direction:row;flex-wrap:wrap;right:56px}}
@media (prefers-reduced-motion:reduce){#canvas.moving,.node,#pane,.src .more{transition:none}.flash{animation:none}}
</style></head><body>
<div class="top"><div><h1>Research assistant</h1><p class="sub">Every claim is checked against the page it cites.</p></div>
<div style="display:flex;gap:1rem;align-items:end"><a id="dashlink" href="/dashboard" style="font-size:.85rem;color:var(--accent)">Dashboard</a><div><label for="user">Demo user (not a real login)</label><select id="user"></select></div></div></div>
<div id="banner" class="banner" hidden></div>
<div class="askbar"><textarea id="q" maxlength="500" aria-label="Your question" placeholder="Ask a question, for example: What GDP growth is projected for Malaysia in 2026?"></textarea><button id="go" class="primary">Ask</button><span id="status" class="muted" role="status"></span></div>
<div id="tabs" role="tablist"><button id="tab-canvas" role="tab" aria-selected="true">Canvas</button><button id="tab-steps" role="tab" aria-selected="false">Investigation steps</button></div>
<div id="stage" aria-label="Answer canvas">
<nav id="spy" aria-label="Sections"></nav>
<div id="zoom"><button id="zin" aria-label="Zoom in">+</button><button id="zout" aria-label="Zoom out">&minus;</button><button id="zfit" aria-label="Show everything" title="Show everything">&#9633;</button></div>
<div id="canvas"></div></div>
<div id="steps" hidden aria-label="Investigation steps"><div class="grid"><nav id="rail" aria-label="Steps"></nav><div id="steplist"><p class="muted">Ask a question to see how the answer was found.</p></div></div></div>
<aside id="pane" aria-label="Source page"><header><strong id="phead">Source page</strong><button id="pclose" aria-label="Close">Close</button></header><pre id="ptext"></pre></aside>
<script>
const $=id=>document.getElementById(id);
const el=(t,p={},...k)=>{const e=document.createElement(t);for(const[a,v]of Object.entries(p)){if(a==="class")e.className=v;else if(a in e)e[a]=v;else e.setAttribute(a,v)}k.flat().forEach(x=>e.append(x));return e};
const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches||new URLSearchParams(location.search).get("reveal")==="0";  /* ?reveal=0 skips the staged reveal */
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const stage=$("stage"),canvas=$("canvas");
let view={x:0,y:0,s:1},nodes=[],lastAudit=null,current=null;
async function api(path,opts){const r=await fetch(path,opts);const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||r.statusText);return j}
const post=(p,b)=>api(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
/* camera */
function apply(){canvas.style.transform=`translate(${view.x}px,${view.y}px) scale(${view.s})`;spy()}
function move(animate){canvas.classList.toggle("moving",animate&&!reduced);apply()}
const fitScale=()=>Math.min(1,(stage.clientWidth-24)/canvas.offsetWidth);
function focusNode(n){current=n;const s=fitScale(),top=n.offsetTop,h=n.offsetHeight,sh=stage.clientHeight,pad=stage.clientWidth<700?$("spy").offsetHeight+24:24;view.s=s;view.x=(stage.clientWidth-canvas.offsetWidth*s)/2;view.y=h*s<sh-pad-24?(sh+pad)/2-(top+h/2)*s:pad-top*s;move(true)}
function overview(){const s=Math.min(fitScale(),(stage.clientHeight-24)/canvas.offsetHeight);view.s=s;view.x=(stage.clientWidth-canvas.offsetWidth*s)/2;view.y=12;move(true)}
function zoomAt(f,cx,cy){const ns=Math.min(2.5,Math.max(.2,view.s*f));view.x=cx-(cx-view.x)*(ns/view.s);view.y=cy-(cy-view.y)*(ns/view.s);view.s=ns;move(false)}
function spy(){const cy=(stage.clientHeight/2-view.y)/view.s;let best=null,bd=1e9;nodes.forEach(n=>{const d=Math.abs(n.offsetTop+n.offsetHeight/2-cy);if(d<bd){bd=d;best=n}});
  document.querySelectorAll("#spy button").forEach(b=>b.setAttribute("aria-current",String(!!best&&b.dataset.id===best.id)))}
let drag=null;
stage.addEventListener("pointerdown",e=>{if(e.target.closest("button,input,textarea,summary,.src,a"))return;drag={x:e.clientX,y:e.clientY};stage.classList.add("drag");stage.setPointerCapture(e.pointerId)});
stage.addEventListener("pointermove",e=>{if(!drag)return;view.x+=e.clientX-drag.x;view.y+=e.clientY-drag.y;drag={x:e.clientX,y:e.clientY};move(false)});
const endDrag=()=>{drag=null;stage.classList.remove("drag")};stage.addEventListener("pointerup",endDrag);stage.addEventListener("pointercancel",endDrag);
stage.addEventListener("wheel",e=>{e.preventDefault();const r=stage.getBoundingClientRect();if(e.ctrlKey||e.metaKey)zoomAt(e.deltaY<0?1.1:.9,e.clientX-r.left,e.clientY-r.top);else{view.x-=e.deltaX;view.y-=e.deltaY;move(false)}},{passive:false});
$("zin").onclick=()=>zoomAt(1.2,stage.clientWidth/2,stage.clientHeight/2);$("zout").onclick=()=>zoomAt(1/1.2,stage.clientWidth/2,stage.clientHeight/2);$("zfit").onclick=overview;
addEventListener("resize",()=>nodes.length&&spy());
/* page pane */
async function showPage(c){try{const u=new URLSearchParams({user:$("user").value,doc_id:c.doc_id,p:c.page,quote:c.quote||""});const j=await api("/api/page?"+u);
  $("phead").textContent=j.title+", page "+j.page;const pre=$("ptext");pre.replaceChildren();$("pane").classList.add("open");
  if(j.span){pre.append(j.text.slice(0,j.span[0]),el("mark",{textContent:j.text.slice(j.span[0],j.span[1])}),j.text.slice(j.span[1]));const m=pre.querySelector("mark");pre.scrollTop=Math.max(0,m.offsetTop-pre.clientHeight/3)}else pre.textContent=j.text}
  catch(e){$("phead").textContent=e.message;$("ptext").textContent="";$("pane").classList.add("open")}}
$("pclose").onclick=()=>$("pane").classList.remove("open");
/* nodes */
const chip=(c,label)=>el("button",{class:"chip",type:"button",textContent:label||(c.title+", p. "+c.page),onclick:e=>{e.stopPropagation();showPage(c)}});
function node(id,tag,title,cls,...kids){return el("section",{id,class:"node "+(cls||"")},el("span",{class:"tag",textContent:tag}),el("h2",{textContent:title}),...kids)}
function srcCard(p){const more=el("div",{class:"more"},el("p",{textContent:p.text}),chip({doc_id:p.doc_id,page:p.page,quote:""},"Open page "+p.page));
  const c=el("div",{class:"src",tabIndex:0,role:"button","aria-expanded":"false"},el("div",{},el("strong",{textContent:p.title}),el("span",{class:"muted",textContent:"  p. "+p.page})),el("div",{class:"snip",textContent:p.text.slice(0,140)+(p.text.length>140?"…":"")}),more);
  c.dataset.key=p.doc_id+":"+p.page;c.onclick=()=>c.setAttribute("aria-expanded",String(c.classList.toggle("open")));c.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();c.click()}};return c}
function quoteDetails(c,found){return el("details",{},el("summary",{textContent:"Show the quote"}),el("blockquote",{textContent:c.quote}),found?el("button",{type:"button",class:"chip",textContent:"Show how it was found",onclick:()=>showHow(c)}):"")}
function checkRow(c,reason){const ok=!reason;return el("div",{class:"row"},el("span",{class:"badge "+(ok?"ok":"bad"),textContent:ok?"Quote on page "+c.page:"Removed"}),
  el("div",{},el("div",{textContent:c.text}),el("div",{class:"muted",textContent:ok?c.title:reason+" ("+c.title+", p. "+c.page+")"}),quoteDetails(c)))}
function feedbackBlock(j){const box=el("div",{class:"fb"});const msg=el("span",{class:"muted"});
  const send=async(rating,reason)=>{try{await post("/api/feedback",{user:$("user").value,audit_id:j.audit_id,rating,reason});box.replaceChildren(el("span",{class:"muted",textContent:"Thanks, your feedback was recorded."}))}catch(e){msg.textContent=e.message}};
  const up=el("button",{type:"button","aria-label":"Helpful",textContent:"👍 Helpful",onclick:()=>send("up","")});
  const down=el("button",{type:"button","aria-label":"Not helpful",textContent:"👎 Not helpful",onclick:()=>{const inp=el("input",{placeholder:"What was wrong? (optional)",maxLength:300});const s=el("button",{type:"button",textContent:"Send",onclick:()=>send("down",inp.value)});box.replaceChildren(inp,s,msg);inp.focus()}});
  box.append(up,down,msg);return box}
function reviewBlock(j){const note=el("input",{placeholder:"Why should someone look at this? (optional)",maxLength:500});const out=el("div",{class:"muted"});
  const btn=el("button",{type:"button",class:"primary",textContent:"Create review request",onclick:async()=>{btn.disabled=true;try{const r=await post("/api/review",{user:$("user").value,audit_id:j.audit_id,note:note.value});out.textContent="Review request #"+r.id+" recorded. Nobody has been notified automatically; this is a log entry for a person to pick up."}catch(e){out.textContent=e.message;btn.disabled=false}}});
  return [el("p",{class:"muted",textContent:"The assistant never acts on its own. This only happens if you press the button."}),el("div",{class:"fb"},note,btn),out]}
function build(j,q){const out=[];
  out.push(node("n-q","Question","Your question","",el("p",{textContent:q}),el("p",{class:"muted",textContent:"Searched as "+$("user").value+", only in libraries this user may see."})));
  out.push(node("n-src","Sources","Sources found ("+j.retrieved.length+")","",
    el("p",{class:"muted",textContent:j.retrieved.length?"Hover or tap a source to read the passage. These are candidates, not yet answers.":"Nothing in the libraries this user can search matched the question."}),...j.retrieved.slice(0,6).map(srcCard),
    j.retrieved.length>6?el("details",{},el("summary",{textContent:"Show "+(j.retrieved.length-6)+" more sources"}),...j.retrieved.slice(6).map(srcCard)):""));
  const kept=j.claims.length,removed=j.rejected_claims.length;
  out.push(node("n-chk","Checks","Quote check: "+kept+" kept, "+removed+" removed","",
    el("p",{class:"muted",textContent:"A plain string check, no model. It confirms the quote is on the cited page and that every number in the claim is in the quote."+(j.attempts>1?" A second attempt was made after the first was rejected.":"")}),
    ...j.rejected_claims.map(c=>checkRow(c,c.reason)),...j.claims.map(c=>checkRow(c,"")),
    kept+removed===0?el("p",{textContent:j.retrieved.length?"The model returned no claims.":"No sources, so nothing was drafted."}):""));
  const ans=j.status==="answered"
    ?[el("ol",{},...j.claims.map(c=>el("li",{},c.text+" ",chip(c,c.title+", p. "+c.page),quoteDetails(c,true)))),
      el("p",{class:"muted",textContent:"Each quote was found on the page it cites. That confirms the source, not that the claim answers your question."})]
    :[el("p",{style:"font-weight:600",textContent:"I could not find this in the selected library."}),...(j.closest.length?[el("p",{class:"muted",textContent:"Closest sources:"}),el("div",{},...j.closest.map(c=>chip(c)))]:[])];
  out.push(node("n-ans","Answer",j.status==="answered"?"Answer":"No answer","answer",...ans,feedbackBlock(j)));
  out.push(node("n-next","Next step","Flag this answer for review","",...reviewBlock(j)));
  return out}
function placeholder(){canvas.replaceChildren(node("n-start","Start","Ask a question","in",el("p",{textContent:"The answer is built here step by step: the sources found, the quote check on every drafted claim, then the answer."}),el("p",{class:"muted",textContent:"Drag to move. Pinch or hold Ctrl and scroll to zoom. Use the list on the left to jump."})));
  nodes=[canvas.firstChild];$("spy").replaceChildren();setTimeout(()=>focusNode(nodes[0]),0)}
function buildNav(){$("spy").replaceChildren(...nodes.map(n=>el("button",{type:"button","data-id":n.id,textContent:n.querySelector(".tag").textContent,onclick:()=>focusNode(n)})))}
async function ask(){const q=$("q").value.trim();if(!q)return;$("go").disabled=true;$("status").textContent="";
  try{const j=await post("/api/ask",{user:$("user").value,question:q});lastAudit=j.audit_id;switchTab("canvas");renderSteps(j,q);nodes=build(j,q);canvas.replaceChildren(...nodes);buildNav();
    const last=nodes[nodes.length-1],answer=nodes[nodes.length-2];
    for(const n of nodes.slice(0,-1)){n.classList.add("in");focusNode(n);await sleep(reduced?0:900)}
    last.classList.add("in");focusNode(answer);  /* the next step appears quietly; the camera stays on the answer */
    $("status").textContent="Model: "+j.model}
  catch(e){$("status").textContent=e.message}finally{$("go").disabled=false}}
/* tabs and the steps view */
function switchTab(t){$("stage").hidden=t!=="canvas";$("steps").hidden=t!=="steps";$("tab-canvas").setAttribute("aria-selected",String(t==="canvas"));$("tab-steps").setAttribute("aria-selected",String(t==="steps"));
  if(t==="canvas"&&nodes.length)requestAnimationFrame(()=>focusNode(current||nodes[0]))}
$("tab-canvas").onclick=()=>switchTab("canvas");$("tab-steps").onclick=()=>switchTab("steps");
const fmtMs=ms=>ms<1000?ms+" ms":(ms/1000).toFixed(1)+" s";
function stepLine(s){
  if(s.kind==="search")return "Searched "+(s.libraries.join(", ")||"no libraries")+" for: "+(s.query||"no usable words");
  if(s.kind==="draft")return "Attempt "+s.attempt+": "+s.model+" drafted "+s.claims+" claim"+(s.claims===1?"":"s");
  if(s.kind==="check")return "Quote check, attempt "+s.attempt+": "+s.kept+" kept, "+s.removed+" removed";
  return "Retried once, with the reasons the first attempt failed"}
function stepBody(s,j){
  if(s.kind==="search"){const items=[];
    if(s.editions.length)items.push(el("li",{textContent:"Limited to "+s.editions.join(" and ")+", because the question names "+(s.editions.length>1?"them":"it")+"."}));
    items.push(el("li",{textContent:s.found?"Found "+s.found+" passage"+(s.found===1?"":"s")+" in "+Object.entries(s.documents).map(([t,n])=>t+" ("+n+")").join(", ")+".":"No passage matched. Nothing was drafted."}));
    return [el("ul",{},...items),...j.retrieved.slice(0,6).map(srcCard)]}
  if(s.kind==="draft")return [el("p",{class:"muted",textContent:"The model sees only the retrieved passages. It must return an exact quote for every claim. It never sees libraries the user cannot search."})];
  if(s.kind==="check")return [s.reasons.length?el("ul",{},...s.reasons.map(r=>el("li",{textContent:"Removed: "+r})) ):el("p",{class:"muted",textContent:"Every quote was found on its cited page, and every number in a claim was in its quote."}),el("p",{class:"muted",textContent:"Plain string check. No model is involved."})];
  return [el("ul",{},...(s.feedback||[]).map(f=>el("li",{textContent:f})))]}
function renderSteps(j,q){
  const steps=j.trace.filter(s=>s.kind!=="outcome"),out=j.trace.find(s=>s.kind==="outcome");
  const list=$("steplist"),cards=[],bodies=[];
  const trigger=el("div",{class:"step",id:"st-trigger"},el("div",{class:"stephead"},el("span",{textContent:"Question"}),el("span",{class:"tag",textContent:"Trigger"})),el("p",{textContent:q}),el("p",{class:"muted",textContent:"Asked as "+$("user").value+"."}));
  const toggle=el("button",{type:"button",class:"toggle",textContent:"Expand "+steps.length+" steps",onclick:()=>{const open=bodies.every(b=>!b.hidden);bodies.forEach(b=>{b.hidden=open});toggle.textContent=(open?"Expand ":"Collapse ")+steps.length+" steps"}});
  steps.forEach((s,i)=>{const body=el("div",{class:"stepbody",hidden:true},...stepBody(s,j));bodies.push(body);
    const line=el("button",{type:"button",class:"stepline","aria-expanded":"false",onclick:()=>{body.hidden=!body.hidden;line.setAttribute("aria-expanded",String(!body.hidden))}},el("span",{textContent:stepLine(s)}),el("span",{textContent:"▾"}));
    const card=el("div",{class:"step",id:"st-"+i,"data-kind":s.kind},el("div",{class:"stephead"},el("span",{textContent:"Investigation step"}),el("span",{class:"tag",textContent:"Step "+(i+1)})),line,body,
      el("div",{class:"foot"},el("span",{class:"kind",textContent:s.tag}),el("span",{textContent:s.ms!=null?"Duration "+fmtMs(s.ms):""})));cards.push(card)});
  const status=j.status==="answered"?"Answered with "+out.claims+" claim"+(out.claims===1?"":"s")+" that passed the quote check":(out.found?"No answer: nothing survived the quote check":"No answer: no passage matched");
  const concl=el("div",{class:"step",id:"st-end"},el("span",{class:"tag",textContent:"Conclusion"}),el("h2",{style:"font-size:1.02rem;margin:.2rem 0",textContent:status}),
    el("p",{class:"muted",textContent:"The quote check proves each quote is on its cited page. It does not prove the claim answers the question."}));
  list.replaceChildren(trigger,toggle,...cards,concl);
  const rail=$("rail");rail.replaceChildren(...[["Question",trigger],...cards.map((c,i)=>["Step "+(i+1),c]),["Conclusion",concl]].map(([t,target])=>el("button",{type:"button","data-t":target.id,textContent:t,onclick:()=>target.scrollIntoView({behavior:reduced?"auto":"smooth",block:"start"})})));
  if(stepsIO)stepsIO.disconnect();
  stepsIO=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting)rail.querySelectorAll("button").forEach(b=>b.setAttribute("aria-current",String(b.dataset.t===e.target.id)))})},{root:$("steps"),rootMargin:"-30% 0px -55% 0px"});
  [trigger,...cards,concl].forEach(c=>stepsIO.observe(c))}
function showHow(c){switchTab("steps");const first=$("st-0");if(!first)return;const body=first.querySelector(".stepbody"),line=first.querySelector(".stepline");body.hidden=false;line.setAttribute("aria-expanded","true");
  const card=[...first.querySelectorAll(".src")].find(x=>x.dataset.key===c.doc_id+":"+c.page);
  const target=card||first;if(card)card.classList.add("open");target.classList.remove("flash");void target.offsetWidth;target.classList.add("flash");target.scrollIntoView({behavior:reduced?"auto":"smooth",block:"center"})}
let stepsIO=null;
/* page setup */
async function loadUsers(){const j=await api("/api/users");j.users.forEach(u=>$("user").append(el("option",{value:u,textContent:u})));
  if(j.model.startsWith("extractive")){$("banner").hidden=false;$("banner").textContent="No model key is set, so answers come from an extractive baseline that quotes the best-matching sentence. It is often irrelevant. This is not the real assistant."}}
$("go").onclick=ask;$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.metaKey||e.ctrlKey))ask()});
loadUsers().then(()=>{const u=new URLSearchParams(location.search);if([...$("user").options].some(o=>o.value===u.get("user")))$("user").value=u.get("user");
  if(u.get("q"))$("q").value=u.get("q").slice(0,500);placeholder();if(u.get("run")==="1")ask().then(()=>{if(u.get("tab")==="steps")switchTab("steps");if(u.get("expand")==="1"){const t=document.querySelector("#steplist .toggle");if(t&&t.textContent.startsWith("Expand"))t.click()}})});
</script></body></html>
"""




DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Research assistant · admin</title>
<style>
/* One screen, dense, like the home board: counts and names in fixed panels, text bars, tabular figures, no prose where a number will do. */
:root{--bg:#f6f4ef;--fg:#1d1b17;--dim:#6b665c;--card:#fffdf8;--line:#e3ded2;--accent:#b4531f;--ok:#2f7d4f;--warn:#a8741a;--mono:ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#14130f;--fg:#ece8dd;--dim:#9a9486;--card:#1d1b16;--line:#2e2b23;--accent:#e08a52;--ok:#6cc38b;--warn:#e0b45a}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 system-ui,-apple-system,sans-serif}
main{max-width:1200px;margin:0 auto;padding:.8rem 1rem 2rem}
.top{display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:.5rem}
h1{font-size:1rem;margin:0;font-weight:650}.top .meta{flex:1}
.meta{color:var(--dim);font-size:.8rem}.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.tabs{display:flex;gap:.2rem}.tabs button{background:none;border:1px solid transparent;border-radius:.4rem;padding:.2rem .6rem;color:var(--dim);font:inherit;cursor:pointer}
.tabs button[aria-selected=true]{color:var(--fg);border-color:var(--line);background:var(--card)}
.brief{margin:.7rem 0 .8rem;font-size:1.05rem;line-height:1.35}.brief .meta{display:block;margin-top:.15rem}
.brief .why{cursor:help;text-decoration:underline dotted;color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:.7rem}
.panel{grid-column:span 4;background:var(--card);border:1px solid var(--line);border-radius:.5rem;padding:.55rem .7rem .6rem;min-width:0}
.panel.w8{grid-column:span 8}.panel.w5{grid-column:span 5}.panel.w3{grid-column:span 3}.panel.w12{grid-column:span 12}
@media(max-width:900px){.panel,.panel.w8,.panel.w5,.panel.w3{grid-column:span 12}}
h2{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0 0 .4rem;display:flex;justify-content:space-between;gap:.5rem}
h2 b{color:var(--fg);letter-spacing:0;text-transform:none;font-weight:600}
table{border-collapse:collapse;width:100%}td,th{padding:.18rem .3rem;vertical-align:baseline;text-align:left;white-space:nowrap}
th{font-size:.7rem;color:var(--dim);font-weight:500;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}td.q{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;width:100%}
td.t{font-family:var(--mono);color:var(--accent);font-size:.78rem}tr:hover td{background:color-mix(in srgb,var(--line) 40%,transparent)}
.bar{font-family:var(--mono);color:var(--accent);letter-spacing:-.08em;font-size:.72rem}
.big{font-size:1.45rem;font-weight:600;color:var(--accent);font-family:var(--mono);line-height:1}
.todo td.n{color:var(--accent);font-weight:600;font-size:1.05rem}
.ok{color:var(--ok)}.bad{color:var(--warn)}.dim{color:var(--dim)}
.ring{width:3rem;height:3rem;transform:rotate(-90deg);flex:none}.ring circle{fill:none;stroke-width:3.6}.ring .track{stroke:var(--line)}.ring .fill{stroke:var(--accent);stroke-linecap:round}
.cover{display:flex;gap:.7rem;align-items:center;margin-bottom:.4rem}
.tl{width:100%;display:block}.tl text{font:11px system-ui,-apple-system,sans-serif;fill:var(--fg)}.tl text.dim{fill:var(--dim);font-size:10px}.tl .lane{stroke:var(--line);stroke-width:1}.tl .chain{stroke:var(--dim);stroke-dasharray:2 3}.tl .dot{fill:var(--card);stroke:var(--accent);stroke-width:1.5}.tl .seg{stroke:none}.tl .lab{fill:var(--dim);font-size:10px;letter-spacing:.06em}
.legend{display:flex;gap:.9rem;font-size:.75rem;color:var(--dim);margin-top:.3rem}.legend i{display:inline-block;width:.6rem;height:.6rem;border-radius:2px;vertical-align:-1px;margin-right:.3rem}
.foot{margin-top:.8rem;display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;font-size:.78rem;color:var(--dim)}.foot b{color:var(--fg);font-weight:600}@media(max-width:900px){.foot{grid-template-columns:1fr}}
.empty{color:var(--dim);padding:.3rem 0}
form.inline{display:grid;grid-template-columns:auto 1fr auto;gap:.4rem;align-items:center}form.inline.keys{grid-template-columns:1fr auto auto}
input,select,button.go{font:inherit;padding:.4rem .6rem;border-radius:.4rem;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button.go{background:var(--accent);border-color:var(--accent);color:#fff;cursor:pointer}button.link{background:none;border:0;color:var(--dim);text-decoration:underline;padding:0;font:inherit;cursor:pointer}
.out{margin-top:.5rem;font-size:.85rem}.keyval{font-family:var(--mono);user-select:all}
#login{margin:4rem auto;max-width:22rem;display:grid;gap:.5rem}#login label{font-size:.85rem;color:var(--dim)}
#help{margin:.4rem 0 0;padding:.5rem .7rem;border-left:2px solid var(--accent);font-size:.85rem;color:var(--dim)}
[hidden]{display:none!important}
</style></head><body><main>
<div class="top"><h1>Research assistant · admin</h1><span class="meta" id="model"></span>
<div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-v="board">Board</button><button role="tab" aria-selected="false" data-v="tools">Try a question · keys</button></div>
<button class="link" id="helpBtn" type="button">what is this?</button><button class="link" id="forget" type="button">forget token</button></div>
<div id="msg" class="meta bad"></div>
<form id="login" hidden><label for="t">Admin token</label><input id="t" type="password" autocomplete="current-password" placeholder="the admin token"><button class="go">Show the board</button><span class="meta">Typed once per device. In the demo it is <code>demo</code>; set <code>ADMIN_TOKEN</code> for anything shared.</span></form>
<div id="view" hidden>
<p id="help" hidden>A reading room on top of the report library: a user asks, retrieval returns only passages they may see, a model drafts claims with exact quotes, and plain code checks each quote against its page before anything is shown. This board is counts and names from the audit log, the library and the saved runs; it never shows answer text. To read an answer, open the chat as that user. <button class="link" id="gotit" type="button">got it</button></p>
<div class="brief" id="brief"></div>
<section id="board">
<div class="grid">
 <div class="panel"><h2>To do <span id="todoN"></span></h2><table class="todo" id="todo"></table></div>
 <div class="panel w8"><h2>Reports by edition <span>dot size: citations · bar: by user</span></h2><svg class="tl" id="tl" role="img" aria-label="Reports on a timeline per library, sized by citations"></svg><div class="legend" id="legend"></div></div>
 <div class="panel"><h2>Coverage <span id="covN"></span></h2><div class="cover"><svg viewBox="0 0 36 36" class="ring" aria-hidden="true"><circle cx="18" cy="18" r="15.9" class="track"/><circle cx="18" cy="18" r="15.9" class="fill" id="ringFill"/></svg><div><div class="big" id="covPct"></div><div class="meta" id="covText"></div></div></div><table id="libs"></table></div>
 <div class="panel w5"><h2>Recent <span id="recentN"></span></h2><table id="recent"></table></div>
 <div class="panel w3"><h2>Runs <span>wrong and confident</span></h2><table id="runs"></table></div>
 <div class="panel"><h2>Users <span id="usersN"></span></h2><table id="users"></table></div>
 <div class="panel w8"><h2>People <span id="peopleN"></span></h2><table id="people"></table></div>
</div>
<div class="foot" id="sources"></div>
</section>
<section id="tools" hidden>
<div class="grid">
 <div class="panel w12"><h2>Try a question <b>as a user, to check an answer after an ingest or a model change</b></h2>
  <form id="try" class="inline"><select id="tryUser" aria-label="As user"></select><input id="tryQ" maxlength="500" placeholder="A question, as that user would ask it"><button class="go">Ask</button></form>
  <div class="out meta" id="tryOut">Shows outcome, attempts, model time, what the check kept and removed, and the citations. Open shows the full answer in the chat as that user.</div></div>
 <div class="panel w12"><h2>API keys <b>a key stands for one user and inherits that user’s grants, nothing more</b></h2>
  <form id="newkey" class="inline keys"><input id="keyName" maxlength="60" placeholder="What will use this key, for example: MARC Online portal"><select id="keyUser" aria-label="As user"></select><button class="go">Create key</button></form>
  <div class="out meta" id="keyOut">Shown once, stored as a hash. Send it as <code>X-Api-Key</code> on <code>POST /api/ask</code>; the body’s user is then ignored.</div>
  <table id="keys" style="margin-top:.5rem"></table></div>
</div>
</section>
<p class="meta" id="built"></p>
</div></main>
<script>
const $=id=>document.getElementById(id);
function el(tag,cls,t){const e=document.createElement(tag);if(cls)e.className=cls;if(t!==undefined)e.textContent=t;return e;}
function tr(cells){const r=el("tr");cells.forEach(c=>{if(typeof c==="string"||typeof c==="number"){r.appendChild(el("td","",String(c)));}else{r.appendChild(c);}});return r;}
function td(cls,t,title){const c=el("td",cls,t);if(title)c.title=title;return c;}
function hm(iso){return iso.replace("T"," ").slice(5,16);}
function fillT(id,rows,head,emptyText){const t=$(id);t.textContent="";if(head){const h=el("tr");head.forEach(x=>{const c=el("th",x.startsWith(">")?"n":"",x.replace(/^>/,""));h.appendChild(c);});t.appendChild(h);}
 if(!rows.length){const r=el("tr");const c=el("td","empty",emptyText);c.colSpan=(head||[1]).length;r.appendChild(c);t.appendChild(r);return;}rows.forEach(r=>t.appendChild(r));}
function show(name,msg){["login","view"].forEach(n=>$(n).hidden=n!==name);$("msg").textContent=msg||"";}
const USERC=["#b4531f","#6b665c","#2f7d4f","#a8741a","#3f6fa8"];
function timeline(g){const svg=$("tl");svg.textContent="";const ns="http://www.w3.org/2000/svg",mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
 const libs=g.nodes.filter(n=>n.kind==="library"),docs=g.nodes.filter(n=>n.kind==="document"),users=g.nodes.filter(n=>n.kind==="user").map(u=>u.label);
 const W=800,laneH=58,padL=90,padR=70,H=laneH*libs.length+22;svg.setAttribute("viewBox","0 0 "+W+" "+H);
 // Spaced by edition order, not elapsed time: the lane says which report came after which, and one 2015 edition must not squash 2023 to 2026 together.
 const order=docs.slice().sort((a,b)=>(a.edition[0]*12+a.edition[1])-(b.edition[0]*12+b.edition[1]));const dated=order.filter(d=>d.edition[0]);
 const rank={};dated.forEach((d,i)=>rank[d.id]=i);const nD=Math.max(1,dated.length-1);
 const x=d=>{if(!d.edition[0])return W-padR+30;return padL+(rank[d.id]/nD)*(W-padL-padR);};
 const MON=["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
 const maxC=Math.max(1,...docs.map(d=>d.citations));const by={};g.edges.filter(e=>e.kind==="cited").forEach(e=>{(by[e.a]=by[e.a]||{})[e.b.slice(1)]=e.w;});
 libs.forEach((L,i)=>{const y=14+i*laneH+laneH/2;svg.appendChild(mk("line",{x1:padL-6,y1:y,x2:W-padR,y2:y,class:"lane"}));const t=mk("text",{x:0,y:y+4,class:"lab"});t.textContent=L.label.toUpperCase();svg.appendChild(t);
  const mine=docs.filter(d=>d.library===L.label).sort((a,b)=>x(a)-x(b));
  mine.forEach((d,j)=>{const cx=x(d),r=3+8*Math.sqrt(d.citations/maxC);if(j>0){const p=mine[j-1];svg.appendChild(mk("line",{x1:x(p),y1:y,x2:cx,y2:y,class:"chain"}));}
   const c=mk("circle",{cx,cy:y,r,class:"dot"});const tt=mk("title",{});tt.textContent=d.label+" · "+d.citations+" citation"+(d.citations===1?"":"s");c.appendChild(tt);svg.appendChild(c);
   const lbl=mk("text",{x:cx,y:y-r-5,"text-anchor":"middle"});lbl.textContent=d.edition[0]?MON[d.edition[1]]+" "+d.edition[0]:d.label.replace(/ \(synthetic\)$/,"").slice(0,16);svg.appendChild(lbl);
   const split=by["D"+d.id.slice(1)]||{};const tot=Object.values(split).reduce((a,b)=>a+b,0);if(tot){let sx=cx-14;const bw=28;users.forEach((u,ui)=>{const w=bw*(split[u]||0)/tot;if(w){const rct=mk("rect",{x:sx,y:y+r+5,width:w,height:4,class:"seg",fill:USERC[ui%USERC.length]});const t2=mk("title",{});t2.textContent=u+" "+split[u];rct.appendChild(t2);svg.appendChild(rct);sx+=w;}});
    const n=mk("text",{x:cx,y:y+r+19,"text-anchor":"middle",class:"dim"});n.textContent=tot;svg.appendChild(n);}});});
 if(docs.some(d=>!d.edition[0])){const t=mk("text",{x:W-padR+30,y:H-2,"text-anchor":"middle",class:"dim"});t.textContent="undated";svg.appendChild(t);}
 $("legend").textContent="";users.forEach((u,i)=>{const s=el("span");const sw=el("i");sw.style.background=USERC[i%USERC.length];s.appendChild(sw);s.appendChild(document.createTextNode(u));$("legend").appendChild(s);});
 const s=el("span","","dashed: next edition · hover a dot for the title");$("legend").appendChild(s);}
function render(d){
 $("model").textContent="model "+d.usage.model+" · "+d.usage.asked+" asked, "+d.usage.answered+" answered, "+d.usage.not_found+" not found";
 const b=$("brief");b.textContent="";if(d.brief){b.appendChild(document.createTextNode(d.brief+" "));const w=el("span","why","why?");w.title=d.brief_why+". Silent when nothing happened.";b.appendChild(w);}else{b.appendChild(el("span","meta","Nothing new since this device last looked ("+d.brief_why+")."));}
 let seen=false;try{seen=localStorage.getItem("ra.explained")==="1";}catch(e){}$("help").hidden=seen;
 $("todoN").textContent=d.todo.length?d.todo.length:"nothing waiting";
 fillT("todo",d.todo.map(t=>tr([td("n",t.n),td("",t.what,"why: "+t.why)])),null,"Every count that would appear here is zero.");
 $("ringFill").style.strokeDasharray=d.coverage.pct+" 100";$("covPct").textContent=d.coverage.pct+"%";$("covN").textContent=d.coverage.pages+" pages";
 $("covText").textContent=d.coverage.searchable+" searchable · "+d.coverage.flagged+" wait for OCR";
 fillT("libs",d.libraries.map(l=>tr([td("",l.library),el("td","bar",l.bar),td("n",l.pages),td("n dim",l.low_text_pages?l.low_text_pages+" ocr":"", "pages flagged for OCR"),td("n dim",l.users+" u","users granted")])),null,"No libraries.");
 $("recentN").textContent="last "+d.recent.length;
 fillT("recent",d.recent.map(r=>tr([td("t",hm(r.at)),td("",r.user),td("q",r.question,r.question),td(r.status==="answered"?"ok":"bad",r.status==="answered"?"answered":"not found"),td("n dim",r.citations)])),null,"No questions yet.");
 fillT("runs",d.runs.map(r=>tr([td("",r.name,r.date),td(r.wrong_and_confident?"n bad":"n ok",r.wrong_and_confident),td("n dim",r.correct+"/"+r.answerable,"correct of answerable · timid "+r.timid+" · refused right "+r.correct_refusal)])),["run",">wrong",">correct"],"No saved run.");
 $("usersN").textContent=d.usage.users.length;
 fillT("users",d.usage.users.map(u=>tr([td("",u.user),el("td","bar",u.bar),td("n",u.asked),td("n ok",u.answered),td("n bad",u.not_found)])),["user","",">asked",">ok",">not found"],"No questions yet.");
 const ppl=d.people;$("peopleN").textContent=ppl.up+" up · "+ppl.down+" down · "+ppl.reviews_open.length+" review"+(ppl.reviews_open.length===1?"":"s")+" open";
 const prow=[];ppl.reviews_open.forEach(r=>prow.push(tr([td("t",hm(r.at)),td("",r.user),td("","review"),td("q",r.question+(r.note?" · "+r.note:""),r.question)])));ppl.down_reasons.forEach(r=>prow.push(tr([td("t",hm(r.at)),td("",r.user),td("bad","down"),td("q",r.reason,r.reason)])));
 fillT("people",prow,null,"No open review requests and no thumbs-down reasons.");
 timeline(d.graph);
 const f=$("sources");f.textContent="";d.sources.forEach(x=>{const p=el("div");p.appendChild(el("b","",x.kind+" "));p.appendChild(document.createTextNode(x.name+" · "+x.state+(x.last_used?" · last used "+hm(x.last_used):"")));p.title=x.where;f.appendChild(p);});
 $("built").textContent="Board built "+d.now.replace("T"," ")+" UTC · rows are the audit log · libraries are documents behind grants · runs are the files in evals/results.";
}
function tok(){try{return sessionStorage.getItem("ra.token")||"";}catch(e){return "";}}
async function load(t){let since="";try{since=localStorage.getItem("ra.lastlook")||"";}catch(e){}
 const r=await fetch("/api/admin/board?since="+encodeURIComponent(since),{headers:{"X-Token":t}});
 if(r.status===401){try{sessionStorage.removeItem("ra.token");}catch(e){}show("login","That token was not accepted.");return;}
 if(!r.ok){show("login","The board could not be loaded ("+r.status+").");return;}
 const d=await r.json();render(d);loadKeys();show("view");try{localStorage.setItem("ra.lastlook",d.now);}catch(e){}}
async function loadKeys(){const r=await fetch("/api/admin/keys",{headers:{"X-Token":tok()}});if(!r.ok)return;const j=await r.json();
 fillT("keys",j.keys.map(k=>{const row=tr([td("",k.name),td("",k.user),td("mono dim",k.prefix+"…"),td("t",hm(k.created_at)),td("dim",k.last_used?hm(k.last_used):"never used"),td(k.revoked_at?"bad":"ok",k.revoked_at?"revoked":"active")]);
  const c=el("td");if(!k.revoked_at){const b=el("button","link","revoke");b.onclick=async()=>{if(!confirm("Revoke “"+k.name+"”? Anything using it stops now."))return;await fetch("/api/admin/keys/"+k.id,{method:"DELETE",headers:{"X-Token":tok()}});loadKeys();};c.appendChild(b);}row.appendChild(c);return row;}),["name","user","key","created","last used","",""],"No API keys. The chat works without one; a key is for another system asking on a user’s behalf.");}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x===b));$("board").hidden=b.dataset.v!=="board";$("tools").hidden=b.dataset.v!=="tools";});
$("login").onsubmit=e=>{e.preventDefault();const t=$("t").value.trim();if(!t)return;try{sessionStorage.setItem("ra.token",t);}catch(e){}load(t);};
$("forget").onclick=()=>{try{sessionStorage.removeItem("ra.token");localStorage.removeItem("ra.lastlook");}catch(e){}show("login","Token forgotten on this device.");};
$("helpBtn").onclick=()=>{$("help").hidden=!$("help").hidden;};$("gotit").onclick=()=>{try{localStorage.setItem("ra.explained","1");}catch(e){}$("help").hidden=true;};
(async()=>{try{const j=await (await fetch("/api/users")).json();["tryUser","keyUser"].forEach(id=>{$(id).textContent="";j.users.forEach(u=>$(id).appendChild(el("option","",u)));});}catch(e){}})();
$("try").onsubmit=async e=>{e.preventDefault();const u=$("tryUser").value,q=$("tryQ").value.trim();if(!q)return;const out=$("tryOut");out.textContent="Asking as "+u+"…";
 const r=await fetch("/api/ask",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({user:u,question:q})});
 if(!r.ok){out.textContent="Refused ("+r.status+"): "+((await r.json()).detail||"");return;}
 const j=await r.json();const checks=j.trace.filter(t=>t.kind==="check");const kept=checks.reduce((a,c)=>a+c.kept,0),removed=checks.reduce((a,c)=>a+c.removed,0);const reasons=[...new Set(checks.flatMap(c=>c.reasons||[]))];const ms=j.trace.filter(t=>t.kind==="draft").reduce((a,t)=>a+(t.ms||0),0);
 out.textContent="";out.appendChild(el("span",j.status==="answered"?"ok":"bad",j.status==="answered"?"answered":"not found"));out.appendChild(document.createTextNode(" · "+j.attempts+" attempt"+(j.attempts===1?"":"s")+" · model "+Math.round(ms/1000)+" s · kept "+kept+", removed "+removed+(reasons.length?" ("+reasons.join("; ")+")":"")+" · citations "+((j.claims||[]).map(c=>c.doc_id+":"+c.page).join(", ")||"none")+" · "));
 const a=el("a","","open in the chat as "+u);a.href="/?user="+encodeURIComponent(u)+"&q="+encodeURIComponent(q)+"&run=1";out.appendChild(a);};
$("newkey").onsubmit=async e=>{e.preventDefault();const name=$("keyName").value.trim(),user=$("keyUser").value;if(!name)return;
 const r=await fetch("/api/admin/keys",{method:"POST",headers:{"content-type":"application/json","X-Token":tok()},body:JSON.stringify({name,user})});const j=await r.json();const out=$("keyOut");out.textContent="";
 if(!r.ok){out.textContent=j.detail||("Could not create the key ("+r.status+")");return;}
 out.appendChild(document.createTextNode("Key for “"+j.name+"” as "+j.user+", shown once: "));out.appendChild(el("span","keyval",j.key));out.appendChild(document.createTextNode(" · copy it now."));$("keyName").value="";loadKeys();};
(()=>{let t="";if(location.hash.startsWith("#token=")){t=decodeURIComponent(location.hash.slice(7));try{sessionStorage.setItem("ra.token",t);}catch(e){}try{history.replaceState(null,"",location.pathname);}catch(e){}}
 if(!t)t=tok();if(t)load(t);else show("login");})();
</script></body></html>"""
