"""A small web demo over the same pipeline.

Pick a user, tick the reports to search, ask a question and watch the real work happen: the search, the draft, the
quote check. The answer appears beside the page that proves it, with a human-gated next step. The login is a demo
picker, not authentication. Access is still enforced in retrieval and on the page route.
"""
import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .answer import ask
from .retrieve import find, find_query
from .baselines import Extractive
from .db import connect
from .uploads import MAX_UPLOAD_BYTES, PRIVATE_PREFIX, UploadRefused, add_upload, delete_upload, is_private
from .assistant import reply as assistant_reply
from .workspace import add_to_latest, board_view, clean_board, create_board, list_boards, save_board, topic_pages, user_activity

STATIC = Path(__file__).parent / "static"


class RevalidatedFiles(StaticFiles):
    """The stylesheet and script are the app too: the browser may keep them, but must ask whether they changed."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
    doc_ids: list[int] | None = None  # the reports a person ticked; None searches everything they may see


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


class UserBody(BaseModel):
    user: str
    libraries: list[str]


class LayoutBody(BaseModel):
    owner: str = "admin"
    layout: dict


class MeLayoutBody(BaseModel):
    user: str
    layout: dict


class AnalysisBody(BaseModel):
    user: str
    name: str
    questions: list[str]
    doc_ids: list[int]


class BoardBody(BaseModel):
    user: str
    kind: str = "dashboard"
    title: str = ""
    blocks: list = []


class AssistantBody(BaseModel):
    user: str
    message: str
    history: list = []


class QuestionBody(BaseModel):
    user: str
    question: str


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


def _libraries(con):
    """Library rows, coverage and the per-library table. Everything that comes from documents and pages. Private uploads are left out."""
    libs = con.execute(
        "SELECT l.id, l.name, "
        "(SELECT COUNT(*) FROM documents d WHERE d.library_id = l.id) AS documents, "
        "(SELECT COALESCE(SUM(d.pages),0) FROM documents d WHERE d.library_id = l.id) AS pages, "
        "(SELECT COALESCE(SUM(d.needs_ocr),0) FROM documents d WHERE d.library_id = l.id) AS needs_ocr, "
        "(SELECT COUNT(*) FROM grants g WHERE g.library_id = l.id) AS users "
        "FROM libraries l WHERE l.id NOT LIKE ? ORDER BY l.name", (PRIVATE_PREFIX + "%",)).fetchall()
    low = {r["library_id"]: r["n"] for r in con.execute(
        "SELECT d.library_id, COUNT(*) AS n FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.low_text = 1 AND d.library_id NOT LIKE ? GROUP BY d.library_id", (PRIVATE_PREFIX + "%",))}
    top_pages = max([r["pages"] for r in libs] or [0])
    total_pages = sum(r["pages"] for r in libs)
    flagged_pages = sum(low.values())
    coverage = {"pages": total_pages, "searchable": total_pages - flagged_pages, "flagged": flagged_pages,
                "pct": round(100 * (total_pages - flagged_pages) / total_pages) if total_pages else 0}
    libraries = [{"library": r["name"], "documents": r["documents"], "pages": r["pages"], "low_text_pages": low.get(r["id"], 0),
                  "users": r["users"], "bar": _bar(r["pages"], top_pages)} for r in libs]

    return {"libs": libs, "coverage": coverage, "libraries": libraries}


def _usage(con, model_name):
    """The audit log, read once: totals, per-user counts and the last twelve questions."""
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
    recent = [{"at": r["at"], "user": r["user"], "question": r["question"], "status": r["status"], "audit_id": r["id"],
               "citations": len([c for c in r["cited"].split(",") if c])} for r in rows[:12]]

    return {"rows": rows, "usage": usage, "recent": recent, "users": users}


def _people(con):
    """Ratings and review requests."""
    fb = con.execute("SELECT user, rating, reason, at FROM feedback ORDER BY id DESC").fetchall()
    rv = con.execute("SELECT r.id, r.user, r.note, r.status, r.at, a.question FROM reviews r JOIN audit a ON a.id = r.audit_id ORDER BY r.id DESC").fetchall()
    people = {"up": sum(1 for r in fb if r["rating"] == "up"), "down": sum(1 for r in fb if r["rating"] == "down"),
              "down_reasons": [{"user": r["user"], "reason": r["reason"], "at": r["at"]} for r in fb if r["rating"] == "down" and r["reason"]][:6],
              "reviews_open": [{"user": r["user"], "question": r["question"], "note": r["note"], "at": r["at"]} for r in rv if r["status"] == "open"][:6]}

    return {"fb": fb, "rv": rv, "people": people}


def _runs(results_dir):
    """Saved evaluation runs, newest first, read from the results folder."""
    import glob
    import json
    import os
    from datetime import date

    runs = []
    for f in sorted(glob.glob(os.path.join(results_dir, "*.json")), key=os.path.getmtime, reverse=True):
        try:
            j = json.load(open(f))
        except (OSError, ValueError):
            continue
        runs.append({"name": os.path.splitext(os.path.basename(f))[0], "file": f, "date": date.fromtimestamp(os.path.getmtime(f)).isoformat(),
                     "questions": j.get("questions"), "answerable": j.get("answerable"), "retrieval": j.get("retrieval_hit_rate"),
                     "correct": j.get("correct"), "timid": j.get("timid"), "correct_refusal": j.get("correct_refusal"),
                     "wrong_and_confident": j.get("wrong_and_confident")})

    return runs


def _graph(con, libs, rows, users):
    # Relationships: libraries, documents in edition order and users, joined by grants and by the citations users' answers made.
    # Issuer, sector and instrument nodes arrive with MARC's tables; today the real edges are membership, grant and citation.
    from .retrieve import EDITION_RE, MONTHS
    docs = con.execute("SELECT d.id, d.title, d.library_id, l.name AS library FROM documents d JOIN libraries l ON l.id = d.library_id WHERE l.id NOT LIKE ?", (PRIVATE_PREFIX + "%",)).fetchall()
    shared = {d["id"] for d in docs}
    def edition_key(title):
        m = EDITION_RE.search(title)
        return (int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1) if m else (0, 0)
    cites = {}
    for r in rows:
        for c in r["cited"].split(","):
            if c:
                did = int(c.split(":")[0])
                if did in shared:
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
    edges += [{"a": f"U{r['user']}", "b": f"L{r['library_id']}", "kind": "grant", "w": 1} for r in con.execute("SELECT user, library_id FROM grants WHERE library_id NOT LIKE ?", (PRIVATE_PREFIX + "%",))]
    edges += [{"a": f"D{did}", "b": f"U{u}", "kind": "cited", "w": w} for (did, u), w in cites.items()]
    for ids in by_lib.values():
        ordered = sorted(ids, key=lambda i: edition_key(next(d["title"] for d in docs if d["id"] == i)))
        edges += [{"a": f"D{x}", "b": f"D{y}", "kind": "next_edition", "w": 1} for x, y in zip(ordered, ordered[1:])]
    graph = {"nodes": nodes, "edges": edges,
             "why": "membership from the documents table, grants from the grants table, citations from the audit log's cited pages, edition order from the month and year in each title. Issuer and sector nodes come with MARC's database."}

    return graph


def _sources(db_path, libs, rows, runs, model, model_name, results_dir):
    import os
    from datetime import datetime

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

    return sources


def _todo(now, people, libraries, runs):
    from datetime import date

    # What to do: each item is a count with a reason, and appears only when the count is not zero.
    todo = []
    if people["reviews_open"]:
        todo.append({"kind": "reviews", "n": len(people["reviews_open"]), "what": "review request" + ("s" if len(people["reviews_open"]) != 1 else "") + " waiting for a person",
                     "why": "someone pressed Create review request and nothing happens until a person reads it"})
    flagged = sum(x["low_text_pages"] for x in libraries)
    if flagged:
        todo.append({"kind": "ocr", "n": flagged, "what": "page" + ("s" if flagged != 1 else "") + " flagged for OCR", "why": "pdftotext read almost nothing on them; they are not searchable until OCR runs"})
    if people["down_reasons"]:
        todo.append({"kind": "down", "n": len(people["down_reasons"]), "what": "thumbs-down reason" + ("s" if len(people["down_reasons"]) != 1 else "") + " to read",
                     "why": "a reason becomes a new test question once a person has checked it"})
    if runs:
        bad = [r for r in runs if r["wrong_and_confident"]]
        age = (now.date() - date.fromisoformat(runs[0]["date"])).days
        if bad and bad[0] is runs[0]:
            todo.append({"kind": "wrong", "n": runs[0]["wrong_and_confident"], "what": "wrong-and-confident in the latest run", "why": "the target is zero; read those answers before anything else"})
        if age > 7:
            todo.append({"kind": "stale", "n": age, "what": "days since the question set was last run", "why": "the harness runs on every change; a stale run means a change went unmeasured"})
    else:
        todo.append({"kind": "stale", "n": 0, "what": "evaluation runs saved", "why": "nothing says how well answers hold up until the question set is run"})

    return todo


def _brief(now, since, rows, fb, rv, runs):
    from datetime import datetime

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
    return brief, why


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


# Which parts of the board each widget needs. The head (brief and key figures) needs the counts but never the graph,
# which is the heaviest part and only wanted once the timeline scrolls into view.
WIDGET_PARTS = {
    "todo": ("todo",), "timeline": ("graph",), "coverage": ("coverage", "libraries"), "recent": ("recent",),
    "runs": ("runs",), "users": ("usage",), "people": ("people",),
}
HEAD_PARTS = ("now", "brief", "brief_why", "todo", "coverage", "usage", "runs", "sources")


def board_data(con, model_name, results_dir="evals/results", since=None, db_path=None, model=None, parts=None):
    """The admin board: a brief since the last visit, what to do, and counts by library, user and run.

    Counts, names, reasons and dates only, never a passage or a page's text, because a board stays on screen. Every list
    says in `why` how it was made. `since` is an ISO time from the device that last looked; the brief is silent when
    nothing happened after it. `parts` limits the keys computed and returned, so a widget fetches only its own slice.
    """
    want = set(parts) if parts else None
    need = lambda *ks: want is None or any(k in want for k in ks)
    now = _now()
    out = {"now": now.isoformat(timespec="seconds")}
    lib = _libraries(con) if need("coverage", "libraries", "todo", "graph", "sources") else None
    use = _usage(con, model_name) if need("usage", "recent", "graph", "sources", "brief", "brief_why") else None
    ppl = _people(con) if need("people", "todo", "brief", "brief_why") else None
    runs = _runs(results_dir) if need("runs", "todo", "sources", "brief", "brief_why") else None
    if need("coverage"):
        out["coverage"] = lib["coverage"]
    if need("libraries"):
        out["libraries"] = lib["libraries"]
    if need("usage"):
        out["usage"] = use["usage"]
    if need("recent"):
        out["recent"] = use["recent"]
    if need("people"):
        out["people"] = ppl["people"]
    if need("runs"):
        out["runs"] = runs
    if need("graph"):
        out["graph"] = _graph(con, lib["libs"], use["rows"], use["users"])
    if need("sources"):
        out["sources"] = _sources(db_path, lib["libs"], use["rows"], runs, model, model_name, results_dir)
    if need("todo"):
        out["todo"] = _todo(now, ppl["people"], lib["libraries"], runs)
    if need("brief", "brief_why"):
        out["brief"], out["brief_why"] = _brief(now, since, use["rows"], ppl["fb"], ppl["rv"], runs)
    return out


TODO_KINDS = ("reviews", "ocr", "down", "wrong", "stale")


def _short_title(title):
    """'Malaysia Economic Monitor October 2025' -> 'Oct 2025'; a title without an edition keeps its first words."""
    from .retrieve import EDITION_RE

    m = EDITION_RE.search(title)
    if m:
        return m.group(1).title()[:3] + " " + m.group(2)
    if len(title) <= 22:
        return title
    out = ""
    for w in title.split():
        if len(out) + len(w) + 1 > 20:
            break
        out = (out + " " + w).strip()
    return (out or title[:20]) + "…"


def todo_detail(con, kind, results_dir):
    """The rows behind one to-do count. Names, questions, reasons and pages; never answer text, which opens in the chat."""
    import json as _json

    titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM documents")}
    if kind == "reviews":
        rows = con.execute("SELECT r.id, r.at, r.user, r.note, r.audit_id, a.question FROM reviews r JOIN audit a ON a.id = r.audit_id WHERE r.status='open' ORDER BY r.id DESC").fetchall()
        return {"kind": kind, "rows": [dict(r) for r in rows], "why": "someone pressed Create review request; mark it done once a person has read the answer"}
    if kind == "ocr":
        rows = con.execute("SELECT p.doc_id, p.page FROM pages p JOIN documents d ON d.id = p.doc_id WHERE p.low_text = 1 AND d.library_id NOT LIKE ? ORDER BY p.doc_id, p.page", (PRIVATE_PREFIX + "%",)).fetchall()
        by_doc = {}
        for r in rows:
            by_doc.setdefault(r["doc_id"], []).append(r["page"])
        return {"kind": kind, "rows": [{"doc_id": d, "title": titles.get(d, "?"), "pages": pg} for d, pg in by_doc.items()],
                "why": "pdftotext read almost nothing on these pages; run OCR on them and re-ingest the report"}
    if kind == "down":
        rows = con.execute("SELECT f.id, f.at, f.user, f.reason, f.audit_id, a.question FROM feedback f JOIN audit a ON a.id = f.audit_id WHERE f.rating='down' AND f.reason != '' ORDER BY f.id DESC").fetchall()
        return {"kind": kind, "rows": [dict(r) for r in rows], "why": "a reason becomes a new test question once a person has checked it"}
    if kind in ("wrong", "stale"):
        runs = _runs(results_dir)
        if not runs:
            return {"kind": kind, "rows": [], "run": None, "why": "no saved run; run the question set"}
        latest = runs[0]
        if kind == "stale":
            return {"kind": kind, "rows": [], "run": {"name": latest["name"], "date": latest["date"]},
                    "why": "run the question set after every change: uv run --python 3.14 python -m ra --db data/ra.db eval --llm hermes --out evals/results/<model>.json"}
        try:
            j = _json.load(open(latest["file"]))
        except (OSError, ValueError):
            j = {}
        rows = []
        for r in j.get("rows", []):
            if r.get("outcome") == "wrong_and_confident":
                rows.append({"id": r.get("id"), "question": r.get("q"), "answerable": r.get("answerable"),
                             "claims": [{"text": c[0], "title": titles.get(c[1], f"doc {c[1]}"), "short": _short_title(titles.get(c[1], f"doc {c[1]}")), "page": c[2]} for c in r.get("answer", [])]})
        return {"kind": kind, "rows": rows, "run": {"name": latest["name"], "date": latest["date"]},
                "why": "each is an answer the scorer counted wrong: a true quote beside the point, or an answer where it should have refused"}
    raise HTTPException(404, "Unknown to-do kind; known: " + ", ".join(TODO_KINDS))

def _titles_for(con, user, question):
    from .retrieve import named_documents
    ids = named_documents(con, user, question)
    if not ids:
        return []
    return [r["title"] for r in con.execute(f"SELECT title FROM documents WHERE id IN ({','.join('?' * len(ids))}) ORDER BY title", ids)]

# The board is a document, not markup. Each owner has one; the page builds its grid from it, and the only thing
# the pilot writes back is a width when a panel is expanded. The fields a builder would need (x, y, h) exist from
# day one so that adding one later is a front end, not a schema change. The widget catalogue is code: a document
# may arrange and configure what exists, never invent a type.
WIDGETS = {
    "todo": {"title": "To do", "span": 4},
    "timeline": {"title": "Reports by edition", "span": 8},
    "coverage": {"title": "Coverage", "span": 4},
    "recent": {"title": "Recent", "span": 5},
    "runs": {"title": "Runs", "span": 3},
    "users": {"title": "Users", "span": 4},
    "people": {"title": "People", "span": 8},
}
# The user catalogue, for a portal that builds its own board from /api/me/layout. The activity page draws from user_activity instead.
USER_WIDGETS = {
    "library": {"title": "Your library", "span": 12},
    "analyses": {"title": "Your analyses", "span": 12},
    "answers": {"title": "Your recent answers", "span": 8},
    "usage": {"title": "Your usage", "span": 4},
    "ratings": {"title": "Your ratings", "span": 6},
    "reviews": {"title": "Your review requests", "span": 6},
}
LAYOUT_VERSION = 1
MAX_WIDGETS = 24


def default_layout(catalogue=WIDGETS):
    return {"version": LAYOUT_VERSION, "widgets": [
        {"type": t, "span": w["span"], "x": None, "y": None, "h": 1, "settings": {}} for t, w in catalogue.items()
    ]}


def validate_layout(doc, catalogue=WIDGETS):
    """Problems with a layout document, as plain sentences. Empty means it can be stored."""
    bad = []
    if not isinstance(doc, dict) or not isinstance(doc.get("widgets"), list):
        return ["a layout is an object with a widgets list"]
    if doc.get("version") != LAYOUT_VERSION:
        bad.append(f"version must be {LAYOUT_VERSION}")
    ws = doc["widgets"]
    if not 1 <= len(ws) <= MAX_WIDGETS:
        bad.append(f"between 1 and {MAX_WIDGETS} widgets")
    for i, w in enumerate(ws):
        if not isinstance(w, dict):
            bad.append(f"widget {i} is not an object")
            continue
        if w.get("type") not in catalogue:
            bad.append(f"widget {i}: unknown type {w.get('type')!r}; known: {', '.join(catalogue)}")
        if not (isinstance(w.get("span"), int) and 1 <= w["span"] <= 12):
            bad.append(f"widget {i}: span must be a whole number from 1 to 12")
        if w.get("x") is not None and not (isinstance(w["x"], int) and 0 <= w["x"] <= 11):
            bad.append(f"widget {i}: x must be null or a column from 0 to 11")
        if w.get("y") is not None and not (isinstance(w["y"], int) and w["y"] >= 0):
            bad.append(f"widget {i}: y must be null or a row from 0")
        if not (isinstance(w.get("h", 1), int) and 1 <= w.get("h", 1) <= 6):
            bad.append(f"widget {i}: h must be a whole number from 1 to 6")
        if not isinstance(w.get("settings", {}), dict):
            bad.append(f"widget {i}: settings must be an object")
    return bad


def get_layout(con, owner, catalogue=WIDGETS):
    row = con.execute("SELECT doc, updated_at FROM layouts WHERE owner=?", (owner,)).fetchone()
    if not row:
        return {"owner": owner, "layout": default_layout(catalogue), "saved": False, "updated_at": None, "catalogue": catalogue}
    return {"owner": owner, "layout": json.loads(row["doc"]), "saved": True, "updated_at": row["updated_at"], "catalogue": catalogue}


USER_WIDGET_PARTS = {"library": ("library",), "analyses": ("analyses",), "answers": ("answers",), "usage": ("usage",), "ratings": ("ratings",), "reviews": ("reviews",)}
USER_HEAD_PARTS = ("now", "brief", "usage", "ratings", "reviews")


def user_board(con, user, model_name, parts=None):
    """One user's board: their questions, ratings and review requests, and the libraries they may see. Nothing of anyone else's."""
    from datetime import datetime

    want = set(parts) if parts else None
    need = lambda *ks: want is None or any(k in want for k in ks)
    now = _now()
    out = {"now": now.isoformat(timespec="seconds")}
    rows = con.execute("SELECT id, at, question, status, cited FROM audit WHERE user=? ORDER BY id DESC", (user,)).fetchall()
    answered = sum(1 for r in rows if r["status"] == "answered")
    # Analysis cells are answers too, and count, but they are listed under their analysis, not as recent questions.
    in_analysis = {r["audit_id"] for r in con.execute("SELECT audit_id FROM analysis_cells WHERE audit_id IS NOT NULL")}
    if need("usage", "brief"):
        libs = [r["name"] for r in con.execute("SELECT l.name FROM libraries l JOIN grants g ON g.library_id = l.id AND g.user=? ORDER BY l.name", (user,))]
        out["usage"] = {"user": user, "asked": len(rows), "answered": answered, "not_found": len(rows) - answered, "model": model_name,
                        "libraries": libs, "first_at": rows[-1]["at"] if rows else None, "last_at": rows[0]["at"] if rows else None,
                        "from_analyses": sum(1 for r in rows if r["id"] in in_analysis)}
    if need("library"):
        # Every report the user may search, newest edition first, with how often their own answers cited it.
        from .retrieve import EDITION_RE, MONTHS
        cited = {}
        for r in rows:
            for c in r["cited"].split(","):
                if c:
                    did = int(c.split(":")[0])
                    cited[did] = cited.get(did, 0) + 1
        low = {r["doc_id"]: r["n"] for r in con.execute("SELECT doc_id, COUNT(*) AS n FROM pages WHERE low_text = 1 GROUP BY doc_id")}
        docs = con.execute(
            "SELECT d.id, d.title, d.pages, d.source, d.library_id, l.name AS library FROM documents d JOIN libraries l ON l.id = d.library_id "
            "JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY l.name, d.title", (user,)).fetchall()
        def edition(title):
            m = EDITION_RE.search(title)
            return [int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1, m.group(1).title() + " " + m.group(2)] if m else [0, 0, ""]
        items = [{"id": d["id"], "title": d["title"], "library": d["library"], "pages": d["pages"], "low_text_pages": low.get(d["id"], 0),
                  "cited": cited.get(d["id"], 0), "edition": edition(d["title"]), "private": is_private(d["library_id"]),
                  "pdf": f"/files/{d['id']}?user={user}" if d["source"] and os.path.exists(d["source"]) else None}
                 for d in docs]
        items.sort(key=lambda x: (x["private"], x["library"], -x["edition"][0], -x["edition"][1], x["title"]))
        out["library"] = items
    if need("analyses"):
        out["analyses"] = [{"id": r["id"], "name": r["name"], "status": r["status"], "created_at": r["created_at"]}
                           for r in con.execute("SELECT id, name, status, created_at FROM analyses WHERE user=? ORDER BY id DESC", (user,))]
    if need("answers"):
        out["answers"] = [{"at": r["at"], "question": r["question"], "status": r["status"], "audit_id": r["id"],
                           "citations": len([c for c in r["cited"].split(",") if c])} for r in rows if r["id"] not in in_analysis][:12]
    if need("ratings", "brief"):
        fb = con.execute("SELECT f.at, f.rating, f.reason, a.question FROM feedback f JOIN audit a ON a.id = f.audit_id WHERE f.user=? ORDER BY f.id DESC", (user,)).fetchall()
        out["ratings"] = {"up": sum(1 for r in fb if r["rating"] == "up"), "down": sum(1 for r in fb if r["rating"] == "down"),
                          "recent": [dict(r) for r in fb[:12]]}
    if need("reviews", "brief"):
        rv = con.execute("SELECT r.at, r.status, r.note, a.question FROM reviews r JOIN audit a ON a.id = r.audit_id WHERE r.user=? ORDER BY r.id DESC", (user,)).fetchall()
        out["reviews"] = {"open": sum(1 for r in rv if r["status"] == "open"), "recent": [dict(r) for r in rv[:12]]}
    if need("brief"):
        hello = "Good morning" if 5 <= datetime.now().hour < 12 else "Good afternoon" if datetime.now().hour < 18 else "Good evening"
        parts_ = [f"{len(rows)} question{'s' if len(rows) != 1 else ''}, {answered} answered with checked citations" if rows else "no questions yet"]
        if out["ratings"]["down"]:
            parts_.append(f"{out['ratings']['down']} answer{'s' if out['ratings']['down'] != 1 else ''} you rated down")
        if out["reviews"]["open"]:
            parts_.append(f"{out['reviews']['open']} review request{'s' if out['reviews']['open'] != 1 else ''} waiting for a person")
        out["brief"] = f"{hello}, {user}. " + "; ".join(parts_) + f". You can search {len(out['usage']['libraries'])} librar{'ies' if len(out['usage']['libraries']) != 1 else 'y'}."
    for k in ("usage", "ratings", "reviews"):
        if want is not None and k not in want:
            out.pop(k, None)
    return out


def question_history(con, user):
    """Each question a person asked, once, newest first. A repeat shows its newest stored answer, if one was kept.

    Analysis cells stay under their analysis. Answers were not always stored, so `saved` says whether this one can reopen.
    """
    rows = con.execute(
        "SELECT a.id, a.at, a.question, a.status, COALESCE(a.answer, '') != '' AS saved, "
        "(SELECT rating FROM feedback f WHERE f.audit_id = a.id ORDER BY f.id DESC LIMIT 1) AS rating, "
        "(SELECT status FROM reviews r WHERE r.audit_id = a.id ORDER BY r.id DESC LIMIT 1) AS review "
        "FROM audit a WHERE a.user = ? AND a.id NOT IN (SELECT audit_id FROM analysis_cells WHERE audit_id IS NOT NULL) "
        "ORDER BY a.id DESC", (user,)).fetchall()
    out = {}
    for r in rows:
        entry = out.get(r["question"])
        if entry is None:
            out[r["question"]] = dict(r) | {"saved": bool(r["saved"]), "asked": 1}
            continue
        entry["asked"] += 1
        if r["saved"] and not entry["saved"]:
            entry.update(id=r["id"], at=r["at"], status=r["status"], saved=True)
    return list(out.values())


def put_layout(con, owner, doc):
    con.execute(
        "INSERT INTO layouts(owner, doc) VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET doc=excluded.doc, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
        (owner, json.dumps(doc, separators=(",", ":"))),
    )
    con.commit()


def answer_json(con, res, model_name):
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


# ---------------------------------------------------------------------------------------------------------------
# Analyses: a user's own question list run across the reports they choose. Every cell is one pass through the same
# pipeline (retrieve inside that one report, draft, check, retry, refuse), stored on the audit log like any answer.
# Nothing here summarises or invents: a cell is a checked claim with its page, or a refusal.
MAX_ANALYSIS_QUESTIONS = 10
MAX_ANALYSIS_CELLS = 60
_analysis_lock = threading.Lock()


def _analysis_row(con, user, analysis_id):
    row = con.execute("SELECT * FROM analyses WHERE id=? AND user=?", (analysis_id, user)).fetchone()
    if not row:
        raise HTTPException(404, "No analysis with that id for this user.")
    return row


def analysis_view(con, user, analysis_id):
    """One analysis as the page shows it: questions down, reports across, each cell a stored answer or a refusal."""
    row = _analysis_row(con, user, analysis_id)
    questions, doc_ids = json.loads(row["questions"]), json.loads(row["doc_ids"])
    from .retrieve import EDITION_RE
    marks = ",".join("?" * len(doc_ids)) or "NULL"
    docs = {r["id"]: r for r in con.execute(f"SELECT id, title FROM documents WHERE id IN ({marks})", doc_ids)}
    def short(title):
        m = EDITION_RE.search(title)
        return (m.group(1).title()[:3] + " " + m.group(2)) if m else title[:18]
    cells = {}
    for c in con.execute("SELECT c.qi, c.doc_id, c.audit_id, c.status, a.answer FROM analysis_cells c LEFT JOIN audit a ON a.id = c.audit_id WHERE c.analysis_id=?", (analysis_id,)):
        cell = {"status": c["status"], "audit_id": c["audit_id"], "claim": None, "page": None, "quote": None, "claims": 0}
        if c["answer"]:
            ans = json.loads(c["answer"])
            if ans.get("claims"):
                cell.update(claim=ans["claims"][0]["text"], page=ans["claims"][0]["page"], quote=ans["claims"][0]["quote"], claims=len(ans["claims"]))
        cells[f"{c['qi']}:{c['doc_id']}"] = cell
    total = len(questions) * len(doc_ids)
    done = sum(1 for c in cells.values() if c["status"] in ("answered", "not_found", "error"))
    return {"id": row["id"], "name": row["name"], "status": row["status"], "questions": questions,
            "docs": [{"id": d, "title": docs[d]["title"], "short": short(docs[d]["title"])} for d in doc_ids if d in docs],
            "cells": cells, "total": total, "done": done, "answered": sum(1 for c in cells.values() if c["status"] == "answered"),
            "created_at": row["created_at"], "started_at": row["started_at"], "finished_at": row["finished_at"]}


def run_analysis(db_path, llm, model_name, user, analysis_id):
    """Ask every pending cell, one report at a time, and store each answer on the audit log. Runs on its own connection."""
    con = connect(db_path)
    try:
        row = _analysis_row(con, user, analysis_id)
        questions, doc_ids = json.loads(row["questions"]), json.loads(row["doc_ids"])
        con.execute("UPDATE analyses SET status='running', started_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (analysis_id,))
        con.commit()
        for qi, q in enumerate(questions):
            for d in doc_ids:
                if con.execute("SELECT 1 FROM analysis_cells WHERE analysis_id=? AND qi=? AND doc_id=? AND status != 'pending'", (analysis_id, qi, d)).fetchone():
                    continue
                res = ask(con, llm, user, q, only={d})
                out = answer_json(con, res, model_name)
                con.execute("UPDATE audit SET answer=? WHERE id=?", (json.dumps(out, separators=(",", ":")), res.audit_id))
                con.execute("UPDATE analysis_cells SET audit_id=?, status=? WHERE analysis_id=? AND qi=? AND doc_id=?",
                            (res.audit_id, res.status, analysis_id, qi, d))
                con.commit()
        con.execute("UPDATE analyses SET status='done', finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (analysis_id,))
        con.commit()
    except Exception:
        con.execute("UPDATE analyses SET status='failed', finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (analysis_id,))
        con.commit()
        raise
    finally:
        con.close()


def create_app(db_path, llm_factory=default_llm, results_dir="evals/results", admin_token=None, background=True):
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

    # The pages are the app; a browser that keeps a stale copy shows yesterday's board over today's code.
    NO_CACHE = {"Cache-Control": "no-store"}
    app.mount("/static", RevalidatedFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(INDEX_HTML, headers=NO_CACHE)

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

    def checked_question(con, body, x_api_key):
        """The question to ask, after deciding who is asking and refusing what cannot be asked."""
        question = body.question.strip()
        if not question or len(question) > 500:
            raise HTTPException(400, "Ask a question of up to 500 characters.")
        if body.doc_ids == []:
            raise HTTPException(400, "Tick at least one report to search.")
        if x_api_key:
            # An integration asks with a key, not a user name. The key decides the user; the body's user is ignored.
            user = user_for_key(con, x_api_key)
            if not user:
                raise HTTPException(401, "That API key is unknown or revoked.")
            body.user = user
        if not allowed_libraries(con, body.user):
            raise HTTPException(403, "This user has no library access.")
        return question

    def answer_and_keep(con, body, question, on_step=None):
        only = set(body.doc_ids) if body.doc_ids is not None else None
        res = ask(con, llm, body.user, question, only=only, on_step=on_step)
        out = answer_json(con, res, model_name)
        # The checked answer is kept with its audit row, so history replays it without a model call.
        con.execute("UPDATE audit SET answer=? WHERE id=?", (json.dumps(out, separators=(",", ":")), res.audit_id))
        con.commit()
        return out

    @app.post("/api/ask")
    def ask_route(body: AskBody, x_api_key: str = Header(default="")):
        con = db()
        return answer_and_keep(con, body, checked_question(con, body, x_api_key))

    @app.post("/api/ask/stream")
    def ask_stream(body: AskBody, x_api_key: str = Header(default="")):
        """The same pipeline, sending each step as server-sent events while it runs, then the checked answer last.

        The work runs on its own thread, so a person who stops reading does not stop it: a model call already started
        finishes and its answer is stored as usual. Drafts never appear here, only what the steps did.
        """
        question = checked_question(db(), body, x_api_key)
        events = queue.SimpleQueue()

        def work():
            con = connect(db_path)
            try:
                events.put({"kind": "answer", "answer": answer_and_keep(con, body, question, events.put)})
            except Exception as e:
                events.put({"kind": "error", "detail": f"{type(e).__name__}: {e}"[:200]})
            finally:
                con.close()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def lines():
            while (event := events.get()) is not None:
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

        return StreamingResponse(lines(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/answer/{audit_id}")
    def replay(audit_id: int, user: str):
        """A stored answer, exactly as it was shown, for the user who asked it. No model is called."""
        con = db()
        row = con.execute("SELECT at, question, answer FROM audit WHERE id=? AND user=?", (audit_id, user)).fetchone()
        if not row or not row["answer"]:
            raise HTTPException(404, "No stored answer with that id for this user.")
        return {**json.loads(row["answer"]), "replayed": True, "at": row["at"], "question": row["question"]}


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
        source = con.execute("SELECT source FROM documents WHERE id=?", (doc_id,)).fetchone()["source"]
        has_file = bool(source) and os.path.exists(source)
        return {"title": row["title"], "page": p, "text": row["text"], "span": quote_span(row["text"], quote) if quote else None,
                "pdf": f"/files/{doc_id}?user={user}#page={p}" if has_file else None}

    @app.get("/api/find")
    def find_route(user: str, q: str, limit: int = 20, doc_id: int | None = None):
        """Find pages by their words: phrases in quotes, AND by default, OR, NOT. Plain full-text search, no model call."""
        con = db()
        if not allowed_libraries(con, user):
            raise HTTPException(403, "This user has no library access.")
        t = time.perf_counter()
        query, hits = find(con, user, q.strip()[:300], max(1, min(limit, 200)), doc_id)
        return {"query": query, "hits": hits, "ms": round((time.perf_counter() - t) * 1000, 1), "model_called": False,
                "editions": _titles_for(con, user, q)}

    @app.get("/files/{doc_id}")
    def file(doc_id: int, user: str):
        """The original PDF, for a user who holds a grant on its library. Open at a page with #page=N."""
        con = db()
        row = con.execute(
            "SELECT d.title, d.source FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ? WHERE d.id = ?",
            (user, doc_id),
        ).fetchone()
        if not row:
            raise HTTPException(403, "No access to this document, or it does not exist.")
        if not row["source"] or not os.path.exists(row["source"]):
            raise HTTPException(404, "The original file is not on this server.")
        return FileResponse(row["source"], media_type="application/pdf", filename=os.path.basename(row["source"]),
                            content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})

    @app.get("/api/audit")
    def audit(user: str):
        con = db()
        rows = con.execute("SELECT at, question, status, cited FROM audit WHERE user=? ORDER BY id DESC LIMIT 20", (user,)).fetchall()
        return {"entries": [dict(r) for r in rows]}


    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(user: str = ""):
        return HTMLResponse(ACTIVITY_HTML if user else DASHBOARD_HTML, headers=NO_CACHE)

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

    # A user's own board. Trust is the demo's: the user name in the query, like the chat. In production the session says who you are.
    def known(con, user):
        user = user.strip()
        if not user or not allowed_libraries(con, user):
            raise HTTPException(404, "No such user, or that user holds no library grant.")
        return user

    @app.get("/api/me/board")
    def me_board(user: str, parts: str = ""):
        con = db()
        user = known(con, user)
        wanted = [p for p in parts.split(",") if p] or None
        if parts == "head":
            wanted = list(USER_HEAD_PARTS)
        return user_board(con, user, model_name, wanted)

    @app.get("/api/me/widget/{kind}")
    def me_widget(kind: str, user: str):
        con = db()
        user = known(con, user)
        if kind not in USER_WIDGET_PARTS:
            raise HTTPException(404, f"No widget called {kind!r}; known: {', '.join(USER_WIDGET_PARTS)}")
        return user_board(con, user, model_name, USER_WIDGET_PARTS[kind])

    @app.get("/api/me/activity")
    def me_activity(user: str):
        con = db()
        return user_activity(con, known(con, user), _now(), model_name)

    @app.get("/api/me/topic")
    def me_topic(user: str, q: str):
        """A topic the person typed: the pages of each report that match it."""
        con = db()
        user = known(con, user)
        query = q.strip()[:80]
        if find_query(query) is None:
            raise HTTPException(400, "Type a word or a phrase to look for.")
        return {"term": query.strip('"'), "query": query, "group": "mine", "doc": None, "counts": topic_pages(con, user, query)}

    @app.get("/api/me/boards")
    def boards(user: str):
        con = db()
        return {"boards": list_boards(con, known(con, user))}

    def board_or_404(con, user, board_id):
        view = board_view(con, user, board_id)
        if not view:
            raise HTTPException(404, "No such dashboard or report for this user.")
        return view

    def cleaned(kind, title, blocks):
        title, blocks, problems = clean_board(kind, title, blocks)
        if problems:
            raise HTTPException(400, "; ".join(problems))
        return title, blocks

    @app.post("/api/me/boards")
    def new_board(body: BoardBody):
        con = db()
        user = known(con, body.user)
        title, blocks = cleaned(body.kind, body.title, body.blocks)
        return board_or_404(con, user, create_board(con, user, body.kind, title, blocks))

    @app.post("/api/me/boards/add")
    def add_to_board(body: BoardBody):
        con = db()
        user = known(con, body.user)
        _, blocks, problems = clean_board(body.kind, "add", body.blocks)
        if problems or not blocks:
            raise HTTPException(400, "; ".join(problems) or "Nothing to add.")
        return board_or_404(con, user, add_to_latest(con, user, body.kind, blocks))

    @app.get("/api/me/boards/{board_id}")
    def read_board(board_id: int, user: str):
        con = db()
        return board_or_404(con, known(con, user), board_id)

    @app.put("/api/me/boards/{board_id}")
    def write_board(board_id: int, body: BoardBody):
        con = db()
        user = known(con, body.user)
        kind = board_or_404(con, user, board_id)["kind"]
        title, blocks = cleaned(kind, body.title, body.blocks)
        save_board(con, user, board_id, title, blocks)
        return board_or_404(con, user, board_id)

    @app.delete("/api/me/boards/{board_id}")
    def delete_board(board_id: int, user: str):
        con = db()
        user = known(con, user)
        board_or_404(con, user, board_id)
        con.execute("DELETE FROM boards WHERE id=? AND user=?", (board_id, user))
        con.commit()
        return {"deleted": board_id}

    @app.post("/api/me/assistant")
    def assistant(body: AssistantBody):
        con = db()
        user = known(con, body.user)
        message = body.message.strip()
        if not message or len(message) > 500:
            raise HTTPException(400, "Say something, up to 500 characters.")
        history = [{"role": str(t.get("role"))[:12], "text": str(t.get("text"))[:500]} for t in body.history[-6:] if isinstance(t, dict)]
        reports = [dict(r) for r in con.execute(
            "SELECT d.id, d.title, d.pages FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY d.id", (user,))]
        return assistant_reply(llm, message, history, user_activity(con, user, _now(), model_name), reports)

    def read_upload(user, filename, path):
        con = db()
        try:
            return add_upload(con, user, filename, path)
        finally:
            con.close()

    @app.post("/api/me/uploads")
    async def upload_report(request: Request, user: str, name: str = "report.pdf"):
        """Add a PDF to the person's private library. The body is the file. Its stored name is random, never the one they chose."""
        con = db()
        user = known(con, user)
        con.close()
        too_big = HTTPException(413, f"This file is over the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD_BYTES:
            raise too_big
        folder = Path(db_path).resolve().parent / "uploads"
        folder.mkdir(exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}.pdf"
        try:
            size, start = 0, b""
            with open(path, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise too_big
                    start = start or chunk[:8]
                    out.write(chunk)
            if not start.startswith(b"%PDF-"):
                raise HTTPException(400, "This is not a PDF. Only PDF files can be added.")
            report = await run_in_threadpool(read_upload, user, name, path)
        except UploadRefused as e:
            path.unlink(missing_ok=True)
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            path.unlink(missing_ok=True)
            raise HTTPException(503, "This server cannot read PDFs yet: pdftotext is not installed.") from e
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"doc_id": report["doc_id"], "title": report["title"], "pages": report["pages"], "low_text_pages": len(report["low_text_pages"])}

    @app.delete("/api/me/uploads/{doc_id}")
    def delete_report(doc_id: int, user: str):
        con = db()
        user = known(con, user)
        if con.execute("SELECT 1 FROM analyses WHERE user=? AND status='running'", (user,)).fetchone():
            raise HTTPException(409, "A table is still running. Delete this once it has finished.")
        if not delete_upload(con, user, doc_id):
            raise HTTPException(404, "No upload of yours with that id.")
        return {"deleted": doc_id}

    @app.get("/api/me/history")
    def me_history(user: str):
        con = db()
        return {"questions": question_history(con, known(con, user))}

    @app.get("/api/me/analyses")
    def list_analyses(user: str):
        con = db()
        user = known(con, user)
        rows = con.execute("SELECT id FROM analyses WHERE user=? ORDER BY id DESC", (user,)).fetchall()
        views = [analysis_view(con, user, r["id"]) for r in rows]
        docs = [{"id": r["id"], "title": r["title"]} for r in con.execute(
            "SELECT d.id, d.title FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY d.title", (user,))]
        return {"analyses": [{k: v[k] for k in ("id", "name", "status", "total", "done", "answered", "created_at", "finished_at")} | {"questions": len(v["questions"]), "reports": len(v["docs"])} for v in views],
                "docs": docs, "limits": {"questions": MAX_ANALYSIS_QUESTIONS, "cells": MAX_ANALYSIS_CELLS}, "model": model_name}

    @app.post("/api/me/analyses")
    def create_analysis(body: AnalysisBody):
        con = db()
        user = known(con, body.user)
        name = body.name.strip()[:80]
        questions = [q.strip() for q in body.questions if q.strip()]
        if not name:
            raise HTTPException(400, "An analysis needs a name.")
        if not 1 <= len(questions) <= MAX_ANALYSIS_QUESTIONS or any(len(q) > 300 for q in questions):
            raise HTTPException(400, f"Between 1 and {MAX_ANALYSIS_QUESTIONS} questions, each up to 300 characters.")
        granted = {r["id"] for r in con.execute("SELECT d.id FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ?", (user,))}
        doc_ids = [d for d in dict.fromkeys(body.doc_ids) if d in granted]
        if not doc_ids:
            raise HTTPException(400, "Pick at least one report you may search.")
        if len(questions) * len(doc_ids) > MAX_ANALYSIS_CELLS:
            raise HTTPException(400, f"That is {len(questions) * len(doc_ids)} model calls; the limit is {MAX_ANALYSIS_CELLS}. Fewer questions or reports.")
        cur = con.execute("INSERT INTO analyses(user, name, questions, doc_ids) VALUES (?,?,?,?)", (user, name, json.dumps(questions), json.dumps(doc_ids)))
        aid = cur.lastrowid
        con.executemany("INSERT INTO analysis_cells(analysis_id, qi, doc_id) VALUES (?,?,?)", [(aid, qi, d) for qi in range(len(questions)) for d in doc_ids])
        con.commit()
        return analysis_view(con, user, aid)

    @app.get("/api/me/analyses/{analysis_id}")
    def read_analysis(analysis_id: int, user: str):
        con = db()
        return analysis_view(con, known(con, user), analysis_id)

    @app.post("/api/me/analyses/{analysis_id}/questions")
    def add_analysis_question(analysis_id: int, body: QuestionBody):
        """Add a row to a table. Only its new cells are pending, so the next run asks those and nothing else."""
        con = db()
        user = known(con, body.user)
        row = _analysis_row(con, user, analysis_id)
        if row["status"] == "running":
            raise HTTPException(409, "This analysis is already running.")
        question, questions, doc_ids = body.question.strip(), json.loads(row["questions"]), json.loads(row["doc_ids"])
        if not question or len(question) > 300:
            raise HTTPException(400, "A question of up to 300 characters.")
        if len(questions) >= MAX_ANALYSIS_QUESTIONS or (len(questions) + 1) * len(doc_ids) > MAX_ANALYSIS_CELLS:
            raise HTTPException(400, f"A table holds up to {MAX_ANALYSIS_QUESTIONS} questions and {MAX_ANALYSIS_CELLS} answers.")
        con.execute("UPDATE analyses SET questions=?, status='new' WHERE id=?", (json.dumps(questions + [question]), analysis_id))
        con.executemany("INSERT INTO analysis_cells(analysis_id, qi, doc_id) VALUES (?,?,?)", [(analysis_id, len(questions), d) for d in doc_ids])
        con.commit()
        return analysis_view(con, user, analysis_id)

    @app.post("/api/me/analyses/{analysis_id}/run")
    def start_analysis(analysis_id: int, user: str):
        con = db()
        user = known(con, user)
        row = _analysis_row(con, user, analysis_id)
        if row["status"] == "running":
            raise HTTPException(409, "This analysis is already running.")
        if not _analysis_lock.acquire(blocking=False):
            raise HTTPException(409, "Another analysis is running; one at a time on this server.")
        def job():
            try:
                run_analysis(db_path, llm, model_name, user, analysis_id)
            finally:
                _analysis_lock.release()
        if background:
            threading.Thread(target=job, daemon=True).start()
        else:
            job()
        return analysis_view(db(), user, analysis_id)

    @app.delete("/api/me/analyses/{analysis_id}")
    def delete_analysis(analysis_id: int, user: str):
        con = db()
        user = known(con, user)
        _analysis_row(con, user, analysis_id)
        con.execute("DELETE FROM analysis_cells WHERE analysis_id=?", (analysis_id,))
        con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        con.commit()
        return {"deleted": analysis_id}

    @app.get("/api/me/layout")
    def me_layout(user: str):
        con = db()
        return get_layout(con, "user:" + known(con, user), USER_WIDGETS)

    @app.put("/api/me/layout")
    def me_write_layout(body: MeLayoutBody):
        con = db()
        user = known(con, body.user)
        problems = validate_layout(body.layout, USER_WIDGETS)
        if problems:
            raise HTTPException(400, "; ".join(problems))
        put_layout(con, "user:" + user, body.layout)
        return get_layout(con, "user:" + user, USER_WIDGETS)

    @app.delete("/api/me/layout")
    def me_reset_layout(user: str):
        con = db()
        user = known(con, user)
        con.execute("DELETE FROM layouts WHERE owner=?", ("user:" + user,))
        con.commit()
        return get_layout(con, "user:" + user, USER_WIDGETS)

    @app.get("/api/admin/todo/{kind}")
    def admin_todo(kind: str, x_token: str = Header(default="")):
        admin(x_token)
        return todo_detail(db(), kind, results_dir)

    @app.post("/api/admin/reviews/{review_id}/close")
    def close_review(review_id: int, x_token: str = Header(default="")):
        admin(x_token)
        con = db()
        cur = con.execute("UPDATE reviews SET status='done' WHERE id=? AND status='open'", (review_id,))
        con.commit()
        if not cur.rowcount:
            raise HTTPException(404, "No open review request with that id.")
        return {"closed": review_id}

    @app.get("/api/admin/libraries")
    def admin_libraries(x_token: str = Header(default="")):
        admin(x_token)
        return {"libraries": [dict(r) for r in db().execute("SELECT id, name FROM libraries WHERE id NOT LIKE ? ORDER BY name", (PRIVATE_PREFIX + "%",))]}

    @app.post("/api/admin/users")
    def add_user(body: UserBody, x_token: str = Header(default="")):
        """A user is a name plus library grants. Adding grants to an existing user is the same call; nothing is ever removed here."""
        admin(x_token)
        con = db()
        user = body.user.strip()
        if not user or len(user) > 60 or not re.fullmatch(r"[A-Za-z0-9._@-]+", user):
            raise HTTPException(400, "A user name is 1 to 60 letters, digits, dots, dashes, underscores or @.")
        known_libs = {r["id"] for r in con.execute("SELECT id FROM libraries WHERE id NOT LIKE ?", (PRIVATE_PREFIX + "%",))}  # a private library can never be granted
        libs = [l for l in body.libraries if l in known_libs]
        if not libs:
            raise HTTPException(400, "Pick at least one library that exists: " + ", ".join(sorted(known_libs)))
        before = {r["library_id"] for r in con.execute("SELECT library_id FROM grants WHERE user=?", (user,))}
        con.executemany("INSERT OR IGNORE INTO grants(user, library_id) VALUES (?,?)", [(user, l) for l in libs])
        con.commit()
        return {"user": user, "libraries": sorted(set(libs) | before), "added": sorted(set(libs) - before), "existed": bool(before)}

    @app.get("/api/admin/layout")
    def read_layout(owner: str = "admin", x_token: str = Header(default="")):
        admin(x_token)
        return get_layout(db(), owner.strip() or "admin")

    @app.put("/api/admin/layout")
    def write_layout(body: LayoutBody, x_token: str = Header(default="")):
        admin(x_token)
        problems = validate_layout(body.layout)
        if problems:
            raise HTTPException(400, "; ".join(problems))
        owner = body.owner.strip() or "admin"
        put_layout(db(), owner, body.layout)
        return get_layout(db(), owner)

    @app.delete("/api/admin/layout")
    def reset_layout(owner: str = "admin", x_token: str = Header(default="")):
        admin(x_token)
        con = db()
        con.execute("DELETE FROM layouts WHERE owner=?", (owner.strip() or "admin",))
        con.commit()
        return get_layout(con, owner.strip() or "admin")

    @app.get("/api/admin/board")
    def admin_board(since: str = "", parts: str = "", x_token: str = Header(default="")):
        admin(x_token)
        wanted = [p for p in parts.split(",") if p] or None
        if parts == "head":
            wanted = list(HEAD_PARTS)
        return board_data(db(), model_name, results_dir, since or None, db_path=db_path, model=model_source(llm, model_name), parts=wanted)

    @app.get("/api/admin/widget/{kind}")
    def admin_widget(kind: str, x_token: str = Header(default="")):
        admin(x_token)
        if kind not in WIDGET_PARTS:
            raise HTTPException(404, f"No widget called {kind!r}; known: {', '.join(WIDGET_PARTS)}")
        return board_data(db(), model_name, results_dir, None, db_path=db_path, model=model_source(llm, model_name), parts=WIDGET_PARTS[kind])

    return app


INDEX_HTML = (STATIC / "desk.html").read_text(encoding="utf-8")
ACTIVITY_HTML = (STATIC / "activity.html").read_text(encoding="utf-8")


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Research assistant · admin</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<script>document.documentElement.dataset.theme=localStorage.getItem("ra-theme")||"light"</script>
<link rel="stylesheet" href="/static/tokens.css">
<style>
/* One screen, dense, like the home board: counts and names in fixed panels, text bars, tabular figures, no prose where a number will do. */
:root{--bg:var(--desk);--fg:var(--text);--dim:var(--text-muted);--card:var(--panel);--accent:var(--action);
 --mono:var(--font-data);--serif:var(--font-read);--s1:var(--space-1);--s2:var(--space-2);--s3:var(--space-3);--s4:var(--space-4);--s5:var(--space-6)}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 var(--font-ui)}
main{max-width:1200px;margin:0 auto;padding:var(--s3) var(--s4) var(--s5)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media (pointer:coarse){button,select,input{min-height:44px}}
.keyrow{display:flex;gap:var(--s5);flex-wrap:wrap;margin:var(--s3) 0 0;padding:var(--s2) 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.keyrow div{display:flex;align-items:baseline;gap:var(--s2)}.keyrow .v{font:600 1.25rem/1 var(--mono);font-variant-numeric:tabular-nums}.keyrow .k{font-size:.75rem;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.panel.skel{min-height:7rem}.panel.skel .pbody{visibility:hidden}.panel.skel::after{content:"";display:block;height:.6rem;width:60%;border-radius:3px;background:var(--line);margin-top:var(--s2);box-shadow:0 1rem 0 0 var(--line),0 2rem 0 0 var(--line)}
.top{display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:.5rem}
h1{font-size:1rem;margin:0;font-weight:650}.top .meta{flex:1}
.meta{color:var(--dim);font-size:.8rem}.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.nav{display:flex;gap:.2rem;padding-right:var(--s3);border-right:1px solid var(--line)}.nav a{color:var(--dim);text-decoration:none;padding:var(--s1) var(--s3);border:1px solid transparent;border-radius:6px;font-size:.85rem;transition:background .15s ease-out}.nav a:hover{background:var(--card)}.nav a[aria-current=page]{color:var(--fg);border-color:var(--line);background:var(--card)}
.tabs{display:flex;gap:.2rem}.tabs button{background:none;border:1px solid transparent;border-radius:6px;padding:var(--s1) var(--s3);color:var(--dim);font:inherit;cursor:pointer;transition:background .15s ease-out}.tabs button:hover{background:var(--card)}
.tabs button[aria-selected=true]{color:var(--fg);border-color:var(--line);background:var(--card)}
.brief{margin:var(--s3) 0 var(--s3);font:1.08rem/1.4 var(--serif);text-wrap:balance;max-inline-size:90ch}.brief .meta{display:block;margin-top:var(--s1)}
.brief .why{cursor:help;text-decoration:underline dotted;color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:var(--s3);grid-auto-flow:dense}
.panel{grid-column:span var(--span,4);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:var(--s2) var(--s3) var(--s3);min-width:0;transition:box-shadow .15s ease-out}
.panel.w12{grid-column:span 12}.panel[data-x]{grid-column-start:calc(var(--x) + 1)}.panel.open{box-shadow:0 0 0 2px var(--accent) inset}
@media(max-width:900px){.panel,.panel[data-x]{grid-column:span 12}}
h2 .exp{background:none;border:1px solid transparent;border-radius:4px;color:var(--dim);font:inherit;letter-spacing:0;text-transform:none;padding:0 var(--s1);cursor:pointer;margin-left:auto}h2 .exp:hover{border-color:var(--line)}h2 .exp[aria-pressed=true]{color:var(--accent)}
.panel.open table{width:auto;min-width:60%}.panel.open table:has(td.wrap){width:100%}td.wrap{white-space:normal}
.panel td a{color:var(--accent);text-decoration:none}.panel td a:hover{text-decoration:underline}
.todo tr{cursor:pointer}.todo tr[aria-current=true] td{background:color-mix(in srgb,var(--accent) 12%,transparent)}.todo tr:focus-visible{outline:2px solid var(--accent)}
.panel.focus{box-shadow:none}.panel.focus .tododetail{columns:1}
.tododetail{margin-top:var(--s2);border-top:1px solid var(--line);padding-top:var(--s2);font-size:.8rem}.tododetail table{margin-bottom:var(--s1)}.tododetail td{white-space:normal}.tododetail p.meta{margin:var(--s1) 0 0}
.layoutnote{font-size:.75rem;color:var(--dim);margin:var(--s2) 0 0;display:flex;gap:var(--s3);align-items:baseline}
h2{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0 0 var(--s2);display:flex;justify-content:space-between;gap:var(--s2)}
h2 b{color:var(--fg);letter-spacing:0;text-transform:none;font-weight:600}
table{border-collapse:collapse;width:100%}td,th{padding:.18rem .3rem;vertical-align:baseline;text-align:left;white-space:nowrap}
th{font-size:.7rem;color:var(--dim);font-weight:500;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}td.q{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;width:100%}
td.t{font-family:var(--mono);color:var(--dim);font-size:.78rem}tr:hover td{background:color-mix(in srgb,var(--line) 40%,transparent)}
.bar{font-family:var(--mono);color:var(--dim);letter-spacing:-.08em;font-size:.72rem}
.big{font-size:1.45rem;font-weight:600;color:var(--fg);font-family:var(--mono);line-height:1}
.todo td.n{color:var(--accent);font-weight:600;font-size:1.05rem}
.ok{color:var(--ok)}.bad{color:var(--warn)}.dim{color:var(--dim)}
.ring{width:3rem;height:3rem;transform:rotate(-90deg);flex:none}.ring circle{fill:none;stroke-width:3.6}.ring .track{stroke:var(--line)}.ring .fill{stroke:var(--fg);stroke-linecap:round}
.cover{display:flex;gap:.7rem;align-items:center;margin-bottom:.4rem}
.tl{width:100%;display:block}.tl text{font:11px var(--font-ui);fill:var(--fg)}.tl text.dim{fill:var(--dim);font-size:10px}.tl .lane{stroke:var(--line);stroke-width:1}.tl .chain{stroke:var(--dim);stroke-dasharray:2 3}.tl .dot{fill:var(--card);stroke:var(--fg);stroke-width:1.3}.tl .seg{stroke:none}.tl .lab{fill:var(--dim);font-size:10px;letter-spacing:.06em}
.legend{display:flex;gap:.9rem;font-size:.75rem;color:var(--dim);margin-top:.3rem}.legend i{display:inline-block;width:.6rem;height:.6rem;border-radius:2px;vertical-align:-1px;margin-right:.3rem}
.foot{margin-top:.8rem;display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;font-size:.78rem;color:var(--dim)}.foot b{color:var(--fg);font-weight:600}@media(max-width:900px){.foot{grid-template-columns:1fr}}
.empty{color:var(--dim);padding:.3rem 0}
form.inline{display:grid;grid-template-columns:auto 1fr auto;gap:.4rem;align-items:center}form.inline.keys{grid-template-columns:1fr auto auto}
input,select,button.go{font:inherit;padding:.4rem .6rem;border-radius:.4rem;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button.go{background:var(--accent);border-color:var(--accent);color:var(--on-action);cursor:pointer}button.go:disabled{opacity:.6;cursor:progress}button.link{background:none;border:0;color:var(--dim);text-decoration:underline;padding:0;font:inherit;cursor:pointer}
.out{margin-top:.5rem;font-size:.85rem}.keyval{font-family:var(--mono);user-select:all}
#login{margin:4rem auto;max-width:22rem;display:grid;gap:.5rem}#login label{font-size:.85rem;color:var(--dim)}
#help{margin:.4rem 0 0;padding:.5rem .7rem;border-left:2px solid var(--accent);font-size:.85rem;color:var(--dim)}
[hidden]{display:none!important}
</style></head><body><main>
<div class="top"><h1 id="h1">Research assistant · admin</h1><span class="meta" id="model"></span>
<nav class="nav" aria-label="Pages"><a href="/" id="navChat">Research desk</a><a href="/dashboard" id="navAdmin" aria-current="page">Admin</a></nav>
<div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-v="board">Board</button><button role="tab" aria-selected="false" data-v="tools">Try a question · keys</button></div>
<button class="link" id="helpBtn" type="button">what is this?</button><button class="link" id="forget" type="button">forget token</button></div>
<div id="msg" class="meta bad"></div>
<form id="login" hidden><label for="t">Admin token</label><input id="t" type="password" autocomplete="current-password" placeholder="the admin token"><button class="go">Show the board</button><span class="meta">Typed once per device. In the demo it is <code>demo</code>; set <code>ADMIN_TOKEN</code> for anything shared.</span></form>
<div id="view" hidden>
<p id="help" hidden>A reading room on top of the report library: a user asks, retrieval returns only passages they may see, a model drafts claims with exact quotes, and plain code checks each quote against its page before anything is shown. This board is counts and names from the audit log, the library and the saved runs; it never shows answer text. To read an answer, open the chat as that user. <button class="link" id="gotit" type="button">got it</button></p>
<div class="brief" id="brief"></div>
<div class="keyrow" id="keyrow"></div>
<section id="board">
<div class="grid" id="grid" aria-live="polite"></div>
<div class="foot" id="sources"></div>
</section>
<section id="tools" hidden>
<div class="grid">
 <div class="panel w12"><h2>Try a question <b>as a user, to check an answer after an ingest or a model change</b></h2>
  <form id="try" class="inline"><select id="tryUser" aria-label="As user"></select><input id="tryQ" maxlength="500" placeholder="A question, as that user would ask it"><button class="go">Ask</button></form>
  <div class="out meta" id="tryOut">Shows outcome, attempts, model time, what the check kept and removed, and the citations. Open shows the full answer in the chat as that user.</div></div>
 <div class="panel w12"><h2>Users <b>a user is a name and the libraries they may search; nothing else is stored about them</b></h2>
  <form id="newuser" class="inline keys"><input id="userName" maxlength="60" placeholder="User name, for example: subscriber@marc.com.my"><span id="userLibs" class="meta" style="display:flex;gap:var(--s3);align-items:center"></span><button class="go">Add user</button></form>
  <div class="out meta" id="userOut">Adding a name that exists adds the ticked libraries to it. Grants are only ever removed from the command line.</div></div>
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
function fillT(id,rows,head,emptyText){const t=$(id);if(!t)return;t.textContent="";if(head){const h=el("tr");head.forEach(x=>{const c=el("th",x.startsWith(">")?"n":"",x.replace(/^>/,""));h.appendChild(c);});t.appendChild(h);}
 if(!rows.length){const r=el("tr");const c=el("td","empty",emptyText);c.colSpan=(head||[1]).length;r.appendChild(c);t.appendChild(r);return;}rows.forEach(r=>t.appendChild(r));}
function show(name,msg){["login","view"].forEach(n=>$(n).hidden=n!==name);$("msg").textContent=msg||"";}
const USERC=["var(--series-1)","var(--series-2)","var(--series-3)","var(--series-4)","var(--series-5)"];
function timeline(g){const svg=$("tl");svg.textContent="";const ns="http://www.w3.org/2000/svg",mk=(t,a)=>{const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
 const libs=g.nodes.filter(n=>n.kind==="library"),docs=g.nodes.filter(n=>n.kind==="document"),users=g.nodes.filter(n=>n.kind==="user").map(u=>u.label);
 const W=800,laneH=58,padL=90,padR=70,H=laneH*libs.length+22;svg.setAttribute("viewBox","0 0 "+W+" "+H);
 // Spaced by edition order, not elapsed time: the lane says which report came after which, and one 2015 edition must not squash 2023 to 2026 together.
 const order=docs.slice().sort((a,b)=>(a.edition[0]*12+a.edition[1])-(b.edition[0]*12+b.edition[1]));const dated=order.filter(d=>d.edition[0]);
 const rank={};dated.forEach((d,i)=>rank[d.id]=i);const nD=Math.max(1,dated.length-1);
 const x=d=>{if(!d.edition[0])return W-padR+30;return padL+(rank[d.id]/nD)*(W-padL-padR);};
 const MON=["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
 const maxC=Math.max(1,...docs.map(d=>d.citations));const by={};g.edges.filter(e=>e.kind==="cited").forEach(e=>{(by[e.a]=by[e.a]||{})[e.b.slice(1)]=e.w;});
 libs.forEach((L,i)=>{const y=14+i*laneH+laneH/2;svg.appendChild(mk("line",{x1:padL-6,y1:y,x2:W-padR,y2:y,class:"lane"}));const t=mk("text",{x:0,y:y+4,class:"lab"});const full=L.label.toUpperCase();t.textContent=full.length>11?full.slice(0,10)+"…":full;const tt=document.createElementNS(ns,"title");tt.textContent=L.label;t.appendChild(tt);svg.appendChild(t);
  const mine=docs.filter(d=>d.library===L.label).sort((a,b)=>x(a)-x(b));
  mine.forEach((d,j)=>{const cx=x(d),r=3+8*Math.sqrt(d.citations/maxC);if(j>0){const p=mine[j-1];svg.appendChild(mk("line",{x1:x(p),y1:y,x2:cx,y2:y,class:"chain"}));}
   const c=mk("circle",{cx,cy:y,r,class:"dot"});const tt=mk("title",{});tt.textContent=d.label+" · "+d.citations+" citation"+(d.citations===1?"":"s");c.appendChild(tt);svg.appendChild(c);
   const lbl=mk("text",{x:cx,y:y-r-5,"text-anchor":"middle"});lbl.textContent=d.edition[0]?MON[d.edition[1]]+" "+d.edition[0]:d.label.replace(/ \(synthetic\)$/,"").slice(0,16);svg.appendChild(lbl);
   const split=by["D"+d.id.slice(1)]||{};const tot=Object.values(split).reduce((a,b)=>a+b,0);if(tot){let sx=cx-14;const bw=28;users.forEach((u,ui)=>{const w=bw*(split[u]||0)/tot;if(w){const rct=mk("rect",{x:sx,y:y+r+5,width:w,height:4,class:"seg",style:"fill:"+USERC[ui%USERC.length]});const t2=mk("title",{});t2.textContent=u+" "+split[u];rct.appendChild(t2);svg.appendChild(rct);sx+=w;}});
    const n=mk("text",{x:cx,y:y+r+19,"text-anchor":"middle",class:"dim"});n.textContent=tot;svg.appendChild(n);}});});
 if(docs.some(d=>!d.edition[0])){const t=mk("text",{x:W-padR+30,y:H-2,"text-anchor":"middle",class:"dim"});t.textContent="undated";svg.appendChild(t);}
 $("legend").textContent="";users.forEach((u,i)=>{const s=el("span");const sw=el("i");sw.style.background=USERC[i%USERC.length];s.appendChild(sw);s.appendChild(document.createTextNode(u));$("legend").appendChild(s);});
 const s=el("span","","dashed: next edition · hover a dot for the title");$("legend").appendChild(s);}
// Widget catalogue, mirrored from WIDGETS in Python: type -> the panel's inner markup. Static strings only, so innerHTML is safe here.
const T={
 todo:{title:"To do",sub:'<span id="todoN"></span>',body:'<table class="todo" id="todo"></table><div id="todoDetail" class="tododetail" hidden></div>'},
 timeline:{title:"Reports by edition",sub:'<span>dot size: citations · bar: by user</span>',body:'<svg class="tl" id="tl" role="img" aria-label="Reports on a timeline per library, sized by citations"></svg><div class="legend" id="legend"></div>'},
 coverage:{title:"Coverage",sub:'<span id="covN"></span>',body:'<div class="cover"><svg viewBox="0 0 36 36" class="ring" aria-hidden="true"><circle cx="18" cy="18" r="15.9" class="track"/><circle cx="18" cy="18" r="15.9" class="fill" id="ringFill"/></svg><div><div class="big" id="covPct"></div><div class="meta" id="covText"></div></div></div><table id="libs"></table>'},
 recent:{title:"Recent",sub:'<span id="recentN"></span>',body:'<table id="recent"></table>'},
 runs:{title:"Runs",sub:'<span>wrong and confident</span>',body:'<table id="runs"></table>'},
 users:{title:"Users",sub:'<span id="usersN"></span>',body:'<table id="users"></table>'},
 people:{title:"People",sub:'<span id="peopleN"></span>',body:'<table id="people"></table>'}};
let LAYOUT=null,CAT={},OWNER="admin";
function hdr(extra){return Object.assign({"X-Token":tok()},extra||{});}
function buildGrid(){const g=$("grid");g.textContent="";
 LAYOUT.layout.widgets.forEach((w,i)=>{const t=T[w.type];if(!t)return;const home=CAT[w.type]?CAT[w.type].span:4;const open=w.span===12&&home!==12;const p=el("div","panel"+(w.span===12?" w12":""));p.style.setProperty("--span",w.span);
  if(w.x!==null&&w.x!==undefined){p.dataset.x=w.x;p.style.setProperty("--x",w.x);}
  const h=el("h2");h.innerHTML=t.title+" "+t.sub;p.appendChild(h);
  if(home!==12){const b=el("button","exp",open?"shrink":"expand");b.type="button";b.setAttribute("aria-pressed",open?"true":"false");b.setAttribute("aria-label",(open?"Shrink ":"Expand ")+t.title);b.onclick=()=>toggle(i);h.appendChild(b);}
  const body=el("div","pbody");body.innerHTML=t.body;p.appendChild(body);if(open)p.classList.add("open");p.dataset.type=w.type;if(!DATA[w.type])p.classList.add("skel");g.appendChild(p);if(DATA[w.type])fillWidget(w.type);else watch(p);});
 let n=$("layoutnote");if(!n){n=el("p","layoutnote");n.id="layoutnote";g.after(n);}n.textContent="";
 n.appendChild(document.createTextNode("Layout: "+LAYOUT.layout.widgets.length+" widgets, "+(LAYOUT.saved?"saved "+hm(LAYOUT.updated_at):"catalogue default")+" · a JSON document per owner ("+OWNER+") · expand writes a width back · Esc shrinks all"));
 if(LAYOUT.saved){const r=el("button","link","reset to default");r.type="button";r.onclick=resetLayout;n.appendChild(r);}}
async function saveLayout(){const r=await fetch("/api/admin/layout",{method:"PUT",headers:hdr({"content-type":"application/json"}),body:JSON.stringify({owner:OWNER,layout:LAYOUT.layout})});
 if(r.ok){LAYOUT=await r.json();}else{$("msg").textContent="Layout not saved ("+r.status+").";}}
async function toggle(i){const w=LAYOUT.layout.widgets[i];w.span=w.span===12?(CAT[w.type]?CAT[w.type].span:4):12;buildGrid();await saveLayout();buildGrid();}
async function resetLayout(){const r=await fetch("/api/admin/layout?owner="+encodeURIComponent(OWNER),{method:"DELETE",headers:hdr()});if(r.ok){LAYOUT=await r.json();buildGrid();}}
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&LAYOUT&&LAYOUT.layout.widgets.some(w=>w.span===12)){LAYOUT.layout.widgets.forEach(w=>{if(w.span===12)w.span=CAT[w.type]?CAT[w.type].span:4;});buildGrid();saveLayout();}});
function setT(id,t){const e=$(id);if(e)e.textContent=t;}
let TODO_OPEN=null;
function todoFocus(on){const p=$("todo")&&$("todo").closest(".panel");if(!p)return;const w=LAYOUT&&LAYOUT.layout.widgets.find(x=>x.type==="todo");const home=w?w.span:4;if(on){p.style.setProperty("--span",12);p.classList.add("open","focus");}else{p.style.setProperty("--span",home);p.classList.remove("open","focus");}}
async function loadTodo(kind,row){const box=$("todoDetail");if(!box)return;if(TODO_OPEN===kind){box.hidden=true;TODO_OPEN=null;document.querySelectorAll("#todo tr").forEach(r=>r.removeAttribute("aria-current"));todoFocus(false);return;}
 TODO_OPEN=kind;todoFocus(true);document.querySelectorAll("#todo tr").forEach(r=>r.removeAttribute("aria-current"));if(row)row.setAttribute("aria-current","true");box.hidden=false;box.textContent="Loading…";
 const r=await fetch("/api/admin/todo/"+kind,{headers:{"X-Token":tok()}});if(!r.ok){box.textContent="Could not load ("+r.status+").";return;}const j=await r.json();box.textContent="";
 const openLink=(user,aid)=>{const a=el("a","","open");a.href="/?user="+encodeURIComponent(user)+"&a="+aid;a.title="Opens the stored answer in the chat as "+user;return a;};
 if(kind==="reviews"){const t=el("table");j.rows.forEach(x=>{const c=el("td");c.appendChild(openLink(x.user,x.audit_id));c.appendChild(document.createTextNode(" "));const b=el("button","link","done");b.type="button";b.onclick=async()=>{b.disabled=true;await fetch("/api/admin/reviews/"+x.id+"/close",{method:"POST",headers:{"X-Token":tok()}});delete DATA.todo;delete DATA.people;TODO_OPEN=null;loadWidget("todo");loadWidget("people");};c.appendChild(b);const row=tr([td("t",hm(x.at)),td("",x.user),td("q",x.question,x.question),td("dim",x.note||"")]);row.appendChild(c);t.appendChild(row);});box.appendChild(t);if(!j.rows.length)box.appendChild(el("p","empty","No open review requests."));}
 else if(kind==="ocr"){const t=el("table");j.rows.forEach(x=>t.appendChild(tr([td("",x.title),td("n",x.pages.length),td("dim mono","p. "+x.pages.join(", "))])));box.appendChild(t);if(!j.rows.length)box.appendChild(el("p","empty","No page is flagged."));}
 else if(kind==="down"){const t=el("table");j.rows.forEach(x=>{const row=tr([td("t",hm(x.at)),td("",x.user),td("q",x.question,x.question),td("",x.reason,x.reason)]);const c=el("td");c.appendChild(openLink(x.user,x.audit_id));row.appendChild(c);t.appendChild(row);});box.appendChild(t);if(!j.rows.length)box.appendChild(el("p","empty","No reasons to read."));}
 else if(kind==="wrong"){if(j.run)box.appendChild(el("p","meta","Run "+j.run.name+" · "+j.run.date));const t=el("table");j.rows.forEach(x=>{const row=tr([td("mono dim",x.id),td("q",x.question,x.question),td(x.answerable?"dim":"bad",x.answerable?"answerable":"should refuse")]);t.appendChild(row);x.claims.forEach(c=>{const sub=tr([td("",""),td("q dim",c.text,c.text),td("t",c.short+", p. "+c.page,c.title)]);t.appendChild(sub);});});box.appendChild(t);if(!j.rows.length)box.appendChild(el("p","empty","Nothing wrong in the latest run."));}
 else {box.appendChild(el("p","meta",j.run?("Latest run "+j.run.name+" on "+j.run.date):"No saved run."));}
 box.appendChild(el("p","meta",j.why));}
function isOpen(type){return !!(LAYOUT&&LAYOUT.layout.widgets.some(w=>w.type===type&&w.span===12));}
// The head loads first and once: brief, key figures, sources. Each widget then fetches only its own slice when it
// scrolls into view, and keeps it, so expanding or shrinking re-fills from memory without another request.
const DATA={};
function renderHead(d){
 $("model").textContent="model "+d.usage.model+" · "+d.usage.asked+" asked, "+d.usage.answered+" answered, "+d.usage.not_found+" not found";
 const b=$("brief");b.textContent="";if(d.brief){b.appendChild(document.createTextNode(d.brief+" "));const w=el("span","why","why?");w.title=d.brief_why+". Silent when nothing happened.";b.appendChild(w);}else{b.appendChild(el("span","meta","Nothing new since this device last looked ("+d.brief_why+")."));}
 let seen=false;try{seen=localStorage.getItem("ra.explained")==="1";}catch(e){}$("help").hidden=seen;
 const kr=$("keyrow");kr.textContent="";const latest=d.runs[0];const share=d.usage.asked?Math.round(100*d.usage.answered/d.usage.asked):0;
 [[latest?String(latest.wrong_and_confident):"—","wrong and confident, latest run",latest?(latest.wrong_and_confident?"bad":"ok"):""],[share+"%","answered with checked citations",""],[d.coverage.pct+"%","pages searchable",""],[String(d.todo.length),"to do",d.todo.length?"bad":""]]
  .forEach(([v,k,c])=>{const b=el("div");b.appendChild(el("span","v "+c,v));b.appendChild(el("span","k",k));kr.appendChild(b);});
 const f=$("sources");f.textContent="";d.sources.forEach(x=>{const p=el("div");p.appendChild(el("b","",x.kind+" "));p.appendChild(document.createTextNode(x.name+" · "+x.state+(x.last_used?" · last used "+hm(x.last_used):"")));p.title=x.where;f.appendChild(p);});
 $("built").textContent="Board built "+d.now.replace("T"," ")+" UTC · rows are the audit log · libraries are documents behind grants · runs are the files in evals/results.";
}
const FILL={
 todo(d){ setT("todoN",d.todo.length?d.todo.length:"nothing waiting"); fillT("todo",d.todo.map(t=>{const row=tr([td("n",t.n),td("",t.what,"why: "+t.why)]);row.tabIndex=0;row.setAttribute("role","button");row.title="why: "+t.why+" · click for the details";row.onclick=()=>loadTodo(t.kind,row);row.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();loadTodo(t.kind,row);}};return row;}),null,"Every count that would appear here is zero."); const hk=location.hash.match(/todo=(\w+)/);if(hk&&TODO_OPEN!==hk[1]){const r=[...document.querySelectorAll("#todo tr")][d.todo.findIndex(t=>t.kind===hk[1])];loadTodo(hk[1],r);} },
 coverage(d){ if($("ringFill"))$("ringFill").style.strokeDasharray=d.coverage.pct+" 100";setT("covPct",d.coverage.pct+"%");setT("covN",d.coverage.pages+" pages"); setT("covText",d.coverage.searchable+" searchable · "+d.coverage.flagged+" wait for OCR"); fillT("libs",d.libraries.map(l=>tr([td("",l.library),el("td","bar",l.bar),td("n",l.pages),td("n dim",l.low_text_pages?l.low_text_pages+" ocr":"", "pages flagged for OCR"),td("n dim",l.users+" u","users granted")])),null,"No libraries."); },
 recent(d){ setT("recentN","last "+d.recent.length); fillT("recent",d.recent.map(r=>tr([td("t",hm(r.at)),td("",r.user),td(isOpen("recent")?"wrap":"q",r.question,r.question),td(r.status==="answered"?"ok":"bad",r.status==="answered"?"answered":"not found"),td("n dim",r.citations,"citations")])),isOpen("recent")?["at","user","question","outcome",">cited"]:null,"No questions yet."); },
 runs(d){ if(isOpen("runs"))fillT("runs",d.runs.map(r=>tr([td("",r.name),td("t",r.date),td(r.wrong_and_confident?"n bad":"n ok",r.wrong_and_confident),td("n",r.correct+"/"+r.answerable),td("n dim",r.timid),td("n dim",r.correct_refusal+"/"+(r.questions-r.answerable)),td("n dim",r.retrieval==null?"":r.retrieval.toFixed(2))])),["run","saved",">wrong",">correct",">timid",">refused right",">retrieval"],"No saved run."); else fillT("runs",d.runs.map(r=>tr([td("",r.name,r.date),td(r.wrong_and_confident?"n bad":"n ok",r.wrong_and_confident),td("n dim",r.correct+"/"+r.answerable,"correct of answerable · timid "+r.timid+" · refused right "+r.correct_refusal)])),["run",">wrong",">correct"],"No saved run."); },
 users(d){ setT("usersN",d.usage.users.length); fillT("users",d.usage.users.map(u=>tr([td("",u.user),el("td","bar",u.bar),td("n",u.asked),td("n ok",u.answered),td("n bad",u.not_found)])),["user","",">asked",">ok",">not found"],"No questions yet."); },
 people(d){ const ppl=d.people;setT("peopleN",ppl.up+" up · "+ppl.down+" down · "+ppl.reviews_open.length+" review"+(ppl.reviews_open.length===1?"":"s")+" open"); const prow=[];ppl.reviews_open.forEach(r=>prow.push(tr([td("t",hm(r.at)),td("",r.user),td("","review"),td("q",r.question+(r.note?" · "+r.note:""),r.question)])));ppl.down_reasons.forEach(r=>prow.push(tr([td("t",hm(r.at)),td("",r.user),td("bad","down"),td("q",r.reason,r.reason)]))); fillT("people",prow,null,"No open review requests and no thumbs-down reasons."); },
 timeline(d){ if($("tl"))timeline(d.graph); }};
function fillWidget(type){const d=DATA[type];if(!d||!FILL[type])return;FILL[type](d);document.querySelectorAll('.panel[data-type="'+type+'"]').forEach(p=>p.classList.remove("skel"));}
async function loadWidget(type){if(DATA[type]){fillWidget(type);return;}
 const r=await fetch("/api/admin/widget/"+encodeURIComponent(type),{headers:hdr()});
 if(!r.ok){document.querySelectorAll('.panel[data-type="'+type+'"] .pbody').forEach(b=>{b.textContent="Could not load ("+r.status+").";b.className="pbody meta";});return;}
 DATA[type]=await r.json();fillWidget(type);}
const SEEN=("IntersectionObserver" in window)?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){SEEN.unobserve(e.target);loadWidget(e.target.dataset.type);}}),{rootMargin:"200px"}):null;
function watch(p){if(SEEN)SEEN.observe(p);else loadWidget(p.dataset.type);}
function render(d){renderHead(d);}
function tok(){try{return sessionStorage.getItem("ra.token")||"";}catch(e){return "";}}
async function load(t){let since="";try{since=localStorage.getItem("ra.lastlook")||"";}catch(e){}
 const [r,lr]=await Promise.all([fetch("/api/admin/board?parts=head&since="+encodeURIComponent(since),{headers:hdr()}),fetch("/api/admin/layout?owner="+encodeURIComponent(OWNER),{headers:hdr()})]);
 if(lr.ok){LAYOUT=await lr.json();CAT=LAYOUT.catalogue||{};buildGrid();}
 if(r.status===401){try{sessionStorage.removeItem("ra.token");}catch(e){}show("login","That token was not accepted.");return;}
 if(!r.ok){show("login","The board could not be loaded ("+r.status+").");return;}
 const d=await r.json();render(d);loadKeys();loadLibs();show("view");try{localStorage.setItem("ra.lastlook",d.now);}catch(e){}}
async function loadKeys(){const r=await fetch("/api/admin/keys",{headers:{"X-Token":tok()}});if(!r.ok)return;const j=await r.json();
 fillT("keys",j.keys.map(k=>{const row=tr([td("",k.name),td("",k.user),td("mono dim",k.prefix+"…"),td("t",hm(k.created_at)),td("dim",k.last_used?hm(k.last_used):"never used"),td(k.revoked_at?"bad":"ok",k.revoked_at?"revoked":"active")]);
  const c=el("td");if(!k.revoked_at){const b=el("button","link","revoke");b.onclick=async()=>{if(!confirm("Revoke “"+k.name+"”? Anything using it stops now."))return;await fetch("/api/admin/keys/"+k.id,{method:"DELETE",headers:{"X-Token":tok()}});loadKeys();};c.appendChild(b);}row.appendChild(c);return row;}),["name","user","key","created","last used","",""],"No API keys. The chat works without one; a key is for another system asking on a user’s behalf.");}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x===b));$("board").hidden=b.dataset.v!=="board";$("tools").hidden=b.dataset.v!=="tools";});
$("login").onsubmit=e=>{e.preventDefault();const t=$("t").value.trim();if(!t)return;try{sessionStorage.setItem("ra.token",t);}catch(e){}load(t);};
$("forget").onclick=()=>{try{sessionStorage.removeItem("ra.token");localStorage.removeItem("ra.lastlook");}catch(e){}show("login","Token forgotten on this device.");};
$("helpBtn").onclick=()=>{$("help").hidden=!$("help").hidden;};$("gotit").onclick=()=>{try{localStorage.setItem("ra.explained","1");}catch(e){}$("help").hidden=true;};
(async()=>{try{const j=await (await fetch("/api/users")).json();["tryUser","keyUser"].forEach(id=>{$(id).textContent="";j.users.forEach(u=>$(id).appendChild(el("option","",u)));});}catch(e){}})();
$("try").onsubmit=async e=>{e.preventDefault();const u=$("tryUser").value,q=$("tryQ").value.trim();if(!q)return;const out=$("tryOut");const btn=e.target.querySelector("button");btn.disabled=true;btn.textContent="Asking…";out.textContent="Asking as "+u+"…";try{
 const r=await fetch("/api/ask",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({user:u,question:q})});
 if(!r.ok){out.textContent="Refused ("+r.status+"): "+((await r.json()).detail||"");return;}
 const j=await r.json();const checks=j.trace.filter(t=>t.kind==="check");const kept=checks.reduce((a,c)=>a+c.kept,0),removed=checks.reduce((a,c)=>a+c.removed,0);const reasons=[...new Set(checks.flatMap(c=>c.reasons||[]))];const ms=j.trace.filter(t=>t.kind==="draft").reduce((a,t)=>a+(t.ms||0),0);
 out.textContent="";out.appendChild(el("span",j.status==="answered"?"ok":"bad",j.status==="answered"?"answered":"not found"));out.appendChild(document.createTextNode(" · "+j.attempts+" attempt"+(j.attempts===1?"":"s")+" · model "+Math.round(ms/1000)+" s · kept "+kept+", removed "+removed+(reasons.length?" ("+reasons.join("; ")+")":"")+" · citations "+((j.claims||[]).map(c=>c.doc_id+":"+c.page).join(", ")||"none")+" · "));
 const a=el("a","","open in the chat as "+u);a.href="/?user="+encodeURIComponent(u)+"&q="+encodeURIComponent(q)+"&run=1";out.appendChild(a);}finally{btn.disabled=false;btn.textContent="Ask";}};
async function loadUserSelects(){try{const j=await (await fetch("/api/users")).json();["tryUser","keyUser"].forEach(id=>{const keep=$(id).value;$(id).textContent="";j.users.forEach(u=>$(id).appendChild(el("option","",u)));if(keep)$(id).value=keep;});}catch(e){}}
async function loadLibs(){const r=await fetch("/api/admin/libraries",{headers:{"X-Token":tok()}});if(!r.ok)return;const j=await r.json();const box=$("userLibs");box.textContent="";
 j.libraries.forEach(l=>{const lab=el("label");const c=el("input");c.type="checkbox";c.value=l.id;c.checked=true;lab.appendChild(c);lab.appendChild(document.createTextNode(" "+l.name));box.appendChild(lab);});}
$("newuser").onsubmit=async e=>{e.preventDefault();const user=$("userName").value.trim();const libraries=[...$("userLibs").querySelectorAll("input:checked")].map(c=>c.value);if(!user)return;const btn=e.target.querySelector("button");btn.disabled=true;btn.textContent="Adding…";const out=$("userOut");
 try{const r=await fetch("/api/admin/users",{method:"POST",headers:{"content-type":"application/json","X-Token":tok()},body:JSON.stringify({user,libraries})});const j=await r.json();
  if(!r.ok){out.textContent=j.detail||("Could not add the user ("+r.status+")");return;}
  out.textContent=(j.existed?"Updated ":"Added ")+j.user+": may search "+j.libraries.join(", ")+(j.added.length?" (new: "+j.added.join(", ")+")":" (no new library)")+". They can open /?user="+j.user+" now.";$("userName").value="";loadUserSelects();}
 finally{btn.disabled=false;btn.textContent="Add user";}};
$("newkey").onsubmit=async e=>{e.preventDefault();const name=$("keyName").value.trim(),user=$("keyUser").value;if(!name)return;const btn=e.target.querySelector("button");btn.disabled=true;btn.textContent="Creating…";setTimeout(()=>{btn.disabled=false;btn.textContent="Create key";},800);
 const r=await fetch("/api/admin/keys",{method:"POST",headers:{"content-type":"application/json","X-Token":tok()},body:JSON.stringify({name,user})});const j=await r.json();const out=$("keyOut");out.textContent="";
 if(!r.ok){out.textContent=j.detail||("Could not create the key ("+r.status+")");return;}
 out.appendChild(document.createTextNode("Key for “"+j.name+"” as "+j.user+", shown once: "));out.appendChild(el("span","keyval",j.key));out.appendChild(document.createTextNode(" · copy it now."));$("keyName").value="";loadKeys();};
(()=>{let t="";const hp=new URLSearchParams(location.hash.slice(1));if(hp.get("token")){t=hp.get("token");try{sessionStorage.setItem("ra.token",t);}catch(e){}hp.delete("token");try{history.replaceState(null,"",location.pathname+(hp.toString()?"#"+hp.toString():""));}catch(e){}}
 if(!t)t=tok();if(t)load(t);else show("login");})();
</script></body></html>"""
