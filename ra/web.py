"""A small web demo over the same pipeline.

Pick a user, ask a question, and watch the answer built on a canvas: the question, the sources found, the quote
check on every drafted claim, the answer, and a human-gated next step. The login is a demo picker, not
authentication. Access is still enforced in retrieval and on the page route.
"""
import json
import os
import re
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .answer import ask
from .retrieve import find
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
    """Library rows, coverage and the per-library table. Everything that comes from documents and pages."""
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
        rows = con.execute("SELECT p.doc_id, p.page FROM pages p WHERE p.low_text = 1 ORDER BY p.doc_id, p.page").fetchall()
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
# The user catalogue: four widgets, each something MARC Online does not show. Same document shape, same rules.
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
            "SELECT d.id, d.title, d.pages, d.source, l.name AS library FROM documents d JOIN libraries l ON l.id = d.library_id "
            "JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY l.name, d.title", (user,)).fetchall()
        def edition(title):
            m = EDITION_RE.search(title)
            return [int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1, m.group(1).title() + " " + m.group(2)] if m else [0, 0, ""]
        items = [{"id": d["id"], "title": d["title"], "library": d["library"], "pages": d["pages"], "low_text_pages": low.get(d["id"], 0),
                  "cited": cited.get(d["id"], 0), "edition": edition(d["title"]), "pdf": f"/files/{d['id']}?user={user}" if d["source"] else None}
                 for d in docs]
        items.sort(key=lambda x: (x["library"], -x["edition"][0], -x["edition"][1], x["title"]))
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
        cell = {"status": c["status"], "audit_id": c["audit_id"], "claim": None, "page": None, "claims": 0}
        if c["answer"]:
            ans = json.loads(c["answer"])
            if ans.get("claims"):
                cell.update(claim=ans["claims"][0]["text"], page=ans["claims"][0]["page"], claims=len(ans["claims"]))
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
        out = answer_json(con, res, model_name)
        # The checked answer is kept with its audit row, so history replays it without a model call.
        con.execute("UPDATE audit SET answer=? WHERE id=?", (json.dumps(out, separators=(",", ":")), res.audit_id))
        con.commit()
        return out

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
        has_file = bool(con.execute("SELECT source FROM documents WHERE id=?", (doc_id,)).fetchone()["source"])
        return {"title": row["title"], "page": p, "text": row["text"], "span": quote_span(row["text"], quote) if quote else None,
                "pdf": f"/files/{doc_id}?user={user}#page={p}" if has_file else None}

    @app.get("/api/find")
    def find_route(user: str, q: str, limit: int = 20):
        """Find pages by their words: phrases in quotes, AND by default, OR, NOT. Plain full-text search, no model call."""
        con = db()
        if not allowed_libraries(con, user):
            raise HTTPException(403, "This user has no library access.")
        t = time.perf_counter()
        query, hits = find(con, user, q.strip()[:300], max(1, min(limit, 50)))
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
    def dashboard_page():
        return HTMLResponse(DASHBOARD_HTML, headers=NO_CACHE)

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
        return {"libraries": [dict(r) for r in db().execute("SELECT id, name FROM libraries ORDER BY name")]}

    @app.post("/api/admin/users")
    def add_user(body: UserBody, x_token: str = Header(default="")):
        """A user is a name plus library grants. Adding grants to an existing user is the same call; nothing is ever removed here."""
        admin(x_token)
        con = db()
        user = body.user.strip()
        if not user or len(user) > 60 or not re.fullmatch(r"[A-Za-z0-9._@-]+", user):
            raise HTTPException(400, "A user name is 1 to 60 letters, digits, dots, dashes, underscores or @.")
        known_libs = {r["id"] for r in con.execute("SELECT id FROM libraries")}
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


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research assistant</title>
<style>
:root{color-scheme:light dark;--bg:#f6f4ef;--fg:#1d1b17;--muted:#6b665c;--card:#fffdf8;--line:#e3ded2;--accent:#b4531f;--accent2:#f6e7dc;--ok:#2f7d4f;--okbg:#e8f1ea;--bad:#9b2c2c;--badbg:#f7e6e4;--warn:#a8741a;--warnbg:#f7efdc;--mark:#ffe9a8;--grid:#e8e3d8;--answer:#fbf4ea;--answerline:#b4531f;
 --mono:ui-monospace,Menlo,Consolas,monospace;--serif:Charter,"Iowan Old Style",Georgia,serif;--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px}
@media (prefers-color-scheme:dark){:root{--bg:#14130f;--fg:#ece8dd;--muted:#9a9486;--card:#1d1b16;--line:#2e2b23;--accent:#e08a52;--accent2:#3a2416;--ok:#6cc38b;--okbg:#1b2d22;--bad:#ff9d9d;--badbg:#3a1b1b;--warn:#e0b45a;--warnbg:#3a2a12;--mark:#6b5b00;--grid:#1f1d18;--answer:#241f18;--answerline:#e08a52}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,sans-serif;display:flex;flex-direction:column}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.top{padding:.7rem 16px;display:flex;flex-wrap:wrap;gap:.6rem 1.2rem;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}
h1{font-size:1rem;margin:0;font-weight:650}.sub{color:var(--muted);margin:0;font-size:.8rem}
.nav{display:flex;gap:.2rem}.nav a{color:var(--muted);text-decoration:none;padding:4px 12px;border:1px solid transparent;border-radius:6px;font-size:.85rem;transition:background .15s ease-out}.nav a:hover{background:var(--card)}.nav a[aria-current=page]{color:var(--fg);border-color:var(--line);background:var(--card)}
.banner{background:var(--warnbg);color:var(--warn);padding:.45rem 16px;font-size:.85rem}
.askbar{display:flex;gap:.5rem;padding:.6rem 16px;border-bottom:1px solid var(--line);align-items:flex-start}
textarea,select,button{font:inherit;color:inherit;border:1px solid var(--line);border-radius:6px;background:var(--card);padding:.45rem .65rem;transition:background .15s ease-out,border-color .15s ease-out}
textarea{flex:1;min-width:0;height:2.7rem;resize:vertical}label{font-size:.75rem;color:var(--muted);display:block}
button{cursor:pointer}button:hover{border-color:var(--accent)}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button:disabled{opacity:.6;cursor:progress}
#status{font-size:.8rem;color:var(--muted);align-self:center}#status.busy::before{content:"";display:inline-block;width:.55rem;height:.55rem;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;margin-right:.4rem;vertical-align:-1px;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
#stage{position:relative;flex:1;min-height:420px;overflow:hidden;touch-action:none;cursor:grab;background-color:var(--bg);background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:22px 22px}
#stage.drag{cursor:grabbing}
#canvas{position:absolute;left:0;top:0;width:min(700px,calc(100% - 24px));transform-origin:0 0;display:flex;flex-direction:column;gap:84px;padding:12px 0 60px}
#canvas.moving{transition:transform .75s cubic-bezier(.22,1,.36,1)}
.node{position:relative;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:var(--s3) var(--s4) var(--s4);opacity:0;transform:translateY(14px);transition:opacity .5s ease,transform .5s ease}
.node.in{opacity:1;transform:none}
.node:not(:last-child)::after{content:"";position:absolute;left:50%;top:100%;width:2px;height:84px;background:var(--line)}
.tag{display:inline-block;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;font-weight:600;color:var(--accent);margin-bottom:.3rem}
.node h2{font-size:1rem;margin:.1rem 0 .5rem;font-weight:650}.node.answer{background:var(--answer);border-color:var(--answerline)}.node.answer ol li{font:1.05rem/1.45 var(--serif)}.node.answer ol li .chip,.node.answer ol li details{font-family:system-ui,-apple-system,sans-serif}
.muted{color:var(--muted);font-size:.85rem}
.src{border:1px solid var(--line);border-radius:6px;padding:.5rem .65rem;margin:.4rem 0;background:var(--card);cursor:pointer;transition:border-color .15s ease-out,box-shadow .15s ease-out}
.src:hover,.src:focus-visible,.src.open{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent2);outline:none}
.src .more{max-height:0;overflow:hidden;transition:max-height .4s ease}.src:hover .more,.src:focus-visible .more,.src.open .more{max-height:420px}
.src .more p{margin:.5rem 0;font-size:.85rem;white-space:pre-wrap}
.snip{color:var(--muted);font-size:.85rem}
.row{display:flex;gap:.6rem;align-items:flex-start;margin:.5rem 0}.badge{flex:none;font-size:.72rem;font-weight:600;padding:.1rem .45rem;border-radius:4px;font-family:var(--mono)}
.badge.ok{background:var(--okbg);color:var(--ok)}.badge.bad{background:var(--badbg);color:var(--bad)}
.chip{border:1px solid var(--line);color:var(--fg);background:var(--card);border-radius:4px;padding:.05rem .5rem;font-size:.8rem;font-family:var(--mono);font-variant-numeric:tabular-nums}.chip:hover{border-color:var(--accent);color:var(--accent)}
details summary{cursor:pointer;color:var(--accent);font-size:.85rem}blockquote{margin:.4rem 0;padding:.3rem .7rem;border-left:3px solid var(--accent);color:var(--muted);font-size:.88rem}
ol{padding-left:1.2rem;margin:.4rem 0}li{margin:.6rem 0}
.fb{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin-top:.7rem}.fb input{flex:1;min-width:8rem;font:inherit;color:inherit;border:1px solid var(--line);border-radius:6px;background:var(--card);padding:.35rem .6rem}
#spy{position:absolute;left:12px;top:12px;display:flex;flex-direction:column;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px;z-index:3;touch-action:auto}
#spy button{border:0;background:transparent;text-align:left;padding:.3rem .6rem;font-size:.82rem;color:var(--muted);border-radius:7px}
#spy button[aria-current=true]{background:var(--accent2);color:var(--accent);font-weight:600}
#zoom{position:absolute;right:12px;top:12px;display:flex;flex-direction:column;gap:4px;z-index:3;touch-action:auto}#zoom button{width:2.2rem;padding:.3rem 0;text-align:center}
#pane{position:fixed;top:0;right:0;height:100%;width:min(520px,100%);background:var(--card);border-left:1px solid var(--line);z-index:10;transform:translateX(102%);transition:transform .5s cubic-bezier(.22,1,.36,1);display:flex;flex-direction:column;padding:.8rem 1rem}
#pane.open{transform:none}#pane header{display:flex;justify-content:space-between;gap:.5rem;align-items:center;margin-bottom:.5rem}
a.pdf{font-size:.82rem;color:var(--accent);margin-right:.5rem}#pnote{margin:.5rem 0 0}
pre{position:relative;white-space:pre-wrap;word-break:break-word;font:.85rem/1.5 ui-monospace,monospace;margin:0;overflow:auto;flex:1}mark{background:var(--mark);color:inherit;border-radius:3px}
#tabs{display:flex;gap:.25rem;padding:.35rem 16px 0;border-bottom:1px solid var(--line)}
#find{flex:1;overflow:auto}.findwrap{max-width:900px;margin:0 auto;padding:1rem 16px 3rem}#findList{padding-left:1.2rem;margin:.6rem 0}.hit{margin:.7rem 0}.hittop{display:flex;gap:.6rem;align-items:center}.hit .snip{margin:.3rem 0 0;color:var(--fg);font-size:.9rem;line-height:1.5}#findBtn{border-color:var(--line)}
#tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:.35rem .8rem;color:var(--muted)}
#tabs button[aria-selected=true]{color:var(--fg);border-color:var(--accent);font-weight:600}
#steps{flex:1;overflow:auto;position:relative}
#steps .grid{max-width:900px;margin:0 auto;padding:1rem 16px 3rem;display:grid;grid-template-columns:120px minmax(0,1fr);gap:0 1rem}
#rail{position:sticky;top:1rem;align-self:start;display:flex;flex-direction:column;gap:.1rem;border-left:2px solid var(--line);padding-left:.7rem}
#rail button{border:0;background:transparent;text-align:left;color:var(--muted);font-size:.8rem;padding:.15rem 0;position:relative}
#rail button::before{content:"";position:absolute;left:calc(-.7rem - 5px);top:.45rem;width:8px;height:8px;border-radius:50%;background:var(--line)}
#rail button[aria-current=true]{color:var(--accent);font-weight:600}#rail button[aria-current=true]::before{background:var(--accent)}
.step{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.8rem 1rem;margin:.7rem 0}
.stephead{display:flex;justify-content:space-between;align-items:center;font-weight:600}
.stepline{width:100%;display:flex;justify-content:space-between;gap:.6rem;text-align:left;margin-top:.5rem;background:var(--bg)}
.stepbody{margin-top:.5rem}.stepbody ul{margin:.3rem 0;padding-left:1.1rem}.stepbody li{margin:.2rem 0}
.foot{display:flex;justify-content:space-between;gap:.5rem;color:var(--muted);font-size:.78rem;margin-top:.6rem}
.kind{font-weight:600;color:var(--accent)}.toggle{display:block;margin:.4rem auto;background:transparent;color:var(--muted);border-style:dashed}
.flash{animation:flash 1.8s ease}@keyframes flash{0%,60%{background:var(--accent2)}100%{background:var(--card)}}
@media (max-width:700px){#rail{display:none}#steps .grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){#spy{flex-direction:row;flex-wrap:wrap;right:56px}}
@media (prefers-reduced-motion:reduce){#canvas.moving,.node,#pane,.src .more{transition:none}.flash{animation:none}}
</style></head><body>
<div class="top"><div><h1>Research assistant</h1><p class="sub">Every claim is checked against the page it cites.</p></div>
<div style="display:flex;gap:1rem;align-items:center"><nav class="nav" aria-label="Pages"><a href="/" aria-current="page">Chat</a><a id="melink" href="/dashboard?user=alice">Your board</a><a id="dashlink" href="/dashboard">Admin</a></nav><div><label for="user">Demo user (not a real login)</label><select id="user"></select></div></div></div>
<div id="banner" class="banner" hidden></div>
<div class="askbar"><textarea id="q" maxlength="500" aria-label="Your question" placeholder="Ask a question, for example: What GDP growth is projected for Malaysia in 2026?"></textarea><button id="go" class="primary">Ask</button><button id="findBtn" type="button" title="Find pages by their words. No model call: quotes for a phrase, OR, NOT, -word">Find</button><span id="status" class="muted" role="status"></span></div>
<div id="tabs" role="tablist"><button id="tab-canvas" role="tab" aria-selected="true">Canvas</button><button id="tab-steps" role="tab" aria-selected="false">Investigation steps</button><button id="tab-find" role="tab" aria-selected="false">Find</button></div>
<div id="stage" aria-label="Answer canvas">
<nav id="spy" aria-label="Sections"></nav>
<div id="zoom"><button id="zin" aria-label="Zoom in">+</button><button id="zout" aria-label="Zoom out">&minus;</button><button id="zfit" aria-label="Show everything" title="Show everything">&#9633;</button></div>
<div id="canvas"></div></div>
<div id="find" hidden aria-label="Find results"><div class="findwrap"><p class="muted" id="findHead">Type words from a report and press Find. Pages that contain them, with the words marked, in under a second and without a model call. Put a phrase in quotes; OR widens; -word excludes.</p><ol id="findList"></ol></div></div>
<div id="steps" hidden aria-label="Investigation steps"><div class="grid"><nav id="rail" aria-label="Steps"></nav><div id="steplist"><p class="muted">Ask a question to see how the answer was found.</p></div></div></div>
<aside id="pane" aria-label="Source page"><header><strong id="phead">Source page</strong><span><a id="ppdf" class="pdf" target="_blank" rel="noopener" hidden>Open the PDF at this page</a> <button id="pclose" aria-label="Close">Close</button></span></header><pre id="ptext"></pre><p class="muted" id="pnote">Extracted text of the page, with the quote marked. The PDF link opens the original file at the same page.</p></aside>
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
  $("phead").textContent=j.title+", page "+j.page;const pre=$("ptext");pre.replaceChildren();$("pane").classList.add("open");const pl=$("ppdf");pl.hidden=!j.pdf;if(j.pdf)pl.href=j.pdf;
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
const STAGES=[[0,"Searching your libraries"],[800,"Drafting with the model, about ten seconds"],[12000,"Checking again: the first draft failed a check and is being redone"],[26000,"Still waiting on the model"]];
let stageTimers=[];function waitStage(on){stageTimers.forEach(clearTimeout);stageTimers=[];const st=$("status");st.classList.toggle("busy",on);if(!on)return;STAGES.forEach(([ms,t])=>stageTimers.push(setTimeout(()=>{st.textContent=t+"…";},ms)));}
async function ask(){const q=$("q").value.trim();if(!q)return;$("go").disabled=true;$("status").textContent="";waitStage(true);
  try{const j=await post("/api/ask",{user:$("user").value,question:q});waitStage(false);lastAudit=j.audit_id;switchTab("canvas");renderSteps(j,q);nodes=build(j,q);canvas.replaceChildren(...nodes);buildNav();
    const last=nodes[nodes.length-1],answer=nodes[nodes.length-2];
    for(const n of nodes.slice(0,-1)){n.classList.add("in");focusNode(n);await sleep(reduced?0:900)}
    last.classList.add("in");focusNode(answer);  /* the next step appears quietly; the camera stays on the answer */
    $("status").textContent="Model: "+j.model}
  catch(e){waitStage(false);$("status").textContent=e.message}finally{waitStage(false);$("go").disabled=false}}
/* tabs and the steps view */
function switchTab(t){$("stage").hidden=t!=="canvas";$("steps").hidden=t!=="steps";$("find").hidden=t!=="find";["canvas","steps","find"].forEach(x=>$("tab-"+x).setAttribute("aria-selected",String(t===x)));
  if(t==="canvas"&&nodes.length)requestAnimationFrame(()=>focusNode(current||nodes[0]))}
$("tab-canvas").onclick=()=>switchTab("canvas");$("tab-steps").onclick=()=>switchTab("steps");
const fmtMs=ms=>ms<1000?ms+" ms":(ms/1000).toFixed(1)+" s";
function stepLine(s){
  if(s.kind==="search")return "Searched "+(s.libraries.join(", ")||"no libraries")+" for: "+(s.query||"no usable words");
  if(s.kind==="draft")return s.error?"Attempt "+s.attempt+": "+s.model+" did not answer":"Attempt "+s.attempt+": "+s.model+" drafted "+s.claims+" claim"+(s.claims===1?"":"s");
  if(s.kind==="check")return "Quote check, attempt "+s.attempt+": "+s.kept+" kept, "+s.removed+" removed";
  return "Retried once, with the reasons the first attempt failed"}
function stepBody(s,j){
  if(s.kind==="search"){const items=[];
    if(s.editions.length)items.push(el("li",{textContent:"Limited to "+s.editions.join(" and ")+", because the question names "+(s.editions.length>1?"them":"it")+"."}));
    items.push(el("li",{textContent:s.found?"Found "+s.found+" passage"+(s.found===1?"":"s")+" in "+Object.entries(s.documents).map(([t,n])=>t+" ("+n+")").join(", ")+".":"No passage matched. Nothing was drafted."}));
    return [el("ul",{},...items),...j.retrieved.slice(0,6).map(srcCard)]}
  if(s.kind==="draft")return [s.error?el("p",{class:"muted",textContent:"The model endpoint failed ("+s.error+"). Nothing was drafted, so the answer is a refusal; ask again in a moment."}):el("p",{class:"muted",textContent:"The model sees only the retrieved passages. It must return an exact quote for every claim. It never sees libraries the user cannot search."})];
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
$("go").onclick=ask;$("findBtn").onclick=findPages;$("tab-find").onclick=()=>switchTab("find");
async function findPages(){const q=$("q").value.trim();if(!q)return;const list=$("findList"),head=$("findHead");switchTab("find");list.replaceChildren();head.textContent="Finding…";
  try{const j=await api("/api/find?"+new URLSearchParams({user:$("user").value,q}));
    head.textContent=(j.hits.length?j.hits.length+" page"+(j.hits.length===1?"":"s")+" for ":"No page for ")+(j.query||"nothing searchable")+(j.editions.length?" in "+j.editions.join(", "):"")+" · "+j.ms+" ms · no model call";
    if(!j.hits.length){list.appendChild(el("li",{class:"muted",textContent:"No page contains all of those words. Try fewer words, OR between alternatives, or a phrase in quotes."}));return;}
    j.hits.forEach(h=>{const li=el("li",{class:"hit"});const top=el("div",{class:"hittop"});const t=el("button",{type:"button",class:"chip",textContent:h.title+", p. "+h.page,onclick:()=>showPage({doc_id:h.doc_id,page:h.page,quote:""})});top.appendChild(t);
      const pdf=el("a",{class:"pdf",href:"/files/"+h.doc_id+"?user="+encodeURIComponent($("user").value)+"#page="+h.page,target:"_blank",rel:"noopener",textContent:"PDF"});top.appendChild(pdf);li.appendChild(top);
      const sn=el("p",{class:"snip"});h.snippet.forEach(([t,m])=>sn.appendChild(m?el("mark",{textContent:t}):document.createTextNode(t)));li.appendChild(sn);list.appendChild(li);});}
  catch(e){head.textContent=e.message}};$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.metaKey||e.ctrlKey))ask()});
function meLink(){$("melink").href="/dashboard?user="+encodeURIComponent($("user").value);}
$("user").addEventListener("change",meLink);
async function replay(id){$("go").disabled=true;try{const j=await api("/api/answer/"+id+"?user="+encodeURIComponent($("user").value));lastAudit=j.audit_id;$("q").value=j.question;switchTab("canvas");renderSteps(j,j.question);nodes=build(j,j.question);canvas.replaceChildren(...nodes);buildNav();
    nodes.forEach(n=>n.classList.add("in"));focusNode(nodes[nodes.length-2]);$("status").textContent="Stored answer from "+j.at.replace("T"," ").slice(0,16)+" UTC · model "+j.model+" · no model call";if(new URLSearchParams(location.search).get("tab")==="steps")switchTab("steps")}
  catch(e){$("status").textContent=e.message}finally{$("go").disabled=false}}
loadUsers().then(()=>{const u=new URLSearchParams(location.search);if([...$("user").options].some(o=>o.value===u.get("user")))$("user").value=u.get("user");meLink();if(u.get("q")){$("q").value=u.get("q");}
  if(u.get("q")){$("q").value=u.get("q").slice(0,500);$("q").focus();$("q").setSelectionRange($("q").value.length,$("q").value.length);}placeholder();if(u.get("a"))replay(u.get("a"));else if(u.get("find")==="1")findPages();else if(u.get("run")==="1")ask().then(()=>{if(u.get("tab")==="steps")switchTab("steps");if(u.get("expand")==="1"){const t=document.querySelector("#steplist .toggle");if(t&&t.textContent.startsWith("Expand"))t.click()}})});
</script></body></html>
"""




DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Research assistant · admin</title>
<style>
/* One screen, dense, like the home board: counts and names in fixed panels, text bars, tabular figures, no prose where a number will do. */
:root{--bg:#f6f4ef;--fg:#1d1b17;--dim:#6b665c;--card:#fffdf8;--line:#e3ded2;--accent:#b4531f;--ok:#2f7d4f;--warn:#a8741a;
 --mono:ui-monospace,Menlo,Consolas,monospace;--serif:Charter,'Iowan Old Style',Georgia,serif;--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px}
@media (prefers-color-scheme:dark){:root{--bg:#14130f;--fg:#ece8dd;--dim:#9a9486;--card:#1d1b16;--line:#2e2b23;--accent:#e08a52;--ok:#6cc38b;--warn:#e0b45a}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.4 system-ui,-apple-system,sans-serif}
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
.ana{display:grid;grid-template-columns:minmax(16rem,1fr) 2fr;gap:var(--s4)}@media(max-width:900px){.ana{grid-template-columns:1fr}}
.anaform{display:grid;gap:var(--s2);margin-top:var(--s2)}.anaform input,.anaform textarea{font:inherit;padding:var(--s1) var(--s2);border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg);width:100%}.anaform .docs{display:grid;gap:2px;font-size:.8rem;max-height:9rem;overflow:auto}.anaform .row2{display:flex;justify-content:space-between;align-items:center;gap:var(--s2)}
details summary{cursor:pointer;color:var(--accent);font-size:.85rem;margin-top:var(--s2)}
.anahead{display:flex;gap:var(--s2);align-items:baseline;margin-bottom:var(--s2);flex-wrap:wrap}
table.matrix td,table.matrix th{white-space:normal;vertical-align:top;border-bottom:1px solid var(--line);padding:.3rem .4rem}table.matrix td.qq{font:1rem/1.35 var(--serif);min-width:12rem}table.matrix td.cell{font-size:.8rem;min-width:7rem}table.matrix td.cell.answered a{color:var(--fg)}table.matrix td.cell.not_found{color:var(--dim)}table.matrix td.cell.pending{color:var(--dim)}
.todo tr{cursor:pointer}.todo tr[aria-current=true] td{background:color-mix(in srgb,var(--accent) 12%,transparent)}.todo tr:focus-visible{outline:2px solid var(--accent)}
.panel.focus{box-shadow:none}.panel.focus .tododetail{columns:1}
.tododetail{margin-top:var(--s2);border-top:1px solid var(--line);padding-top:var(--s2);font-size:.8rem}.tododetail table{margin-bottom:var(--s1)}.tododetail td{white-space:normal}.tododetail p.meta{margin:var(--s1) 0 0}
.filter{width:100%;max-width:28rem;margin:0 0 var(--s2);font:inherit;padding:var(--s1) var(--s2);border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
.layoutnote{font-size:.75rem;color:var(--dim);margin:var(--s2) 0 0;display:flex;gap:var(--s3);align-items:baseline}
h2{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0 0 var(--s2);display:flex;justify-content:space-between;gap:var(--s2)}
h2 b{color:var(--fg);letter-spacing:0;text-transform:none;font-weight:600}
table{border-collapse:collapse;width:100%}td,th{padding:.18rem .3rem;vertical-align:baseline;text-align:left;white-space:nowrap}
th{font-size:.7rem;color:var(--dim);font-weight:500;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}td.q{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;width:100%}
td.q.w{width:60%}
td.t{font-family:var(--mono);color:var(--dim);font-size:.78rem}tr:hover td{background:color-mix(in srgb,var(--line) 40%,transparent)}
.bar{font-family:var(--mono);color:var(--dim);letter-spacing:-.08em;font-size:.72rem}
.big{font-size:1.45rem;font-weight:600;color:var(--fg);font-family:var(--mono);line-height:1}
.todo td.n{color:var(--accent);font-weight:600;font-size:1.05rem}
.ok{color:var(--ok)}.bad{color:var(--warn)}.dim{color:var(--dim)}
.ring{width:3rem;height:3rem;transform:rotate(-90deg);flex:none}.ring circle{fill:none;stroke-width:3.6}.ring .track{stroke:var(--line)}.ring .fill{stroke:var(--fg);stroke-linecap:round}
.cover{display:flex;gap:.7rem;align-items:center;margin-bottom:.4rem}
.tl{width:100%;display:block}.tl text{font:11px system-ui,-apple-system,sans-serif;fill:var(--fg)}.tl text.dim{fill:var(--dim);font-size:10px}.tl .lane{stroke:var(--line);stroke-width:1}.tl .chain{stroke:var(--dim);stroke-dasharray:2 3}.tl .dot{fill:var(--card);stroke:var(--fg);stroke-width:1.3}.tl .seg{stroke:none}.tl .lab{fill:var(--dim);font-size:10px;letter-spacing:.06em}
.legend{display:flex;gap:.9rem;font-size:.75rem;color:var(--dim);margin-top:.3rem}.legend i{display:inline-block;width:.6rem;height:.6rem;border-radius:2px;vertical-align:-1px;margin-right:.3rem}
.foot{margin-top:.8rem;display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;font-size:.78rem;color:var(--dim)}.foot b{color:var(--fg);font-weight:600}@media(max-width:900px){.foot{grid-template-columns:1fr}}
.empty{color:var(--dim);padding:.3rem 0}
form.inline{display:grid;grid-template-columns:auto 1fr auto;gap:.4rem;align-items:center}form.inline.keys{grid-template-columns:1fr auto auto}
input,select,button.go{font:inherit;padding:.4rem .6rem;border-radius:.4rem;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button.go{background:var(--accent);border-color:var(--accent);color:#fff;cursor:pointer}button.go:disabled{opacity:.6;cursor:progress}button.link{background:none;border:0;color:var(--dim);text-decoration:underline;padding:0;font:inherit;cursor:pointer}
.out{margin-top:.5rem;font-size:.85rem}.keyval{font-family:var(--mono);user-select:all}
#login{margin:4rem auto;max-width:22rem;display:grid;gap:.5rem}#login label{font-size:.85rem;color:var(--dim)}
#help{margin:.4rem 0 0;padding:.5rem .7rem;border-left:2px solid var(--accent);font-size:.85rem;color:var(--dim)}
[hidden]{display:none!important}
</style></head><body><main>
<div class="top"><h1 id="h1">Research assistant · admin</h1><span class="meta" id="model"></span>
<nav class="nav" aria-label="Pages"><a href="/" id="navChat">Chat</a><a id="navMe" href="/dashboard" hidden>Your board</a><a href="/dashboard" id="navAdmin" aria-current="page">Admin</a></nav>
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
 libs.forEach((L,i)=>{const y=14+i*laneH+laneH/2;svg.appendChild(mk("line",{x1:padL-6,y1:y,x2:W-padR,y2:y,class:"lane"}));const t=mk("text",{x:0,y:y+4,class:"lab"});const full=L.label.toUpperCase();t.textContent=full.length>11?full.slice(0,10)+"…":full;const tt=document.createElementNS(ns,"title");tt.textContent=L.label;t.appendChild(tt);svg.appendChild(t);
  const mine=docs.filter(d=>d.library===L.label).sort((a,b)=>x(a)-x(b));
  mine.forEach((d,j)=>{const cx=x(d),r=3+8*Math.sqrt(d.citations/maxC);if(j>0){const p=mine[j-1];svg.appendChild(mk("line",{x1:x(p),y1:y,x2:cx,y2:y,class:"chain"}));}
   const c=mk("circle",{cx,cy:y,r,class:"dot"});const tt=mk("title",{});tt.textContent=d.label+" · "+d.citations+" citation"+(d.citations===1?"":"s");c.appendChild(tt);svg.appendChild(c);
   const lbl=mk("text",{x:cx,y:y-r-5,"text-anchor":"middle"});lbl.textContent=d.edition[0]?MON[d.edition[1]]+" "+d.edition[0]:d.label.replace(/ \(synthetic\)$/,"").slice(0,16);svg.appendChild(lbl);
   const split=by["D"+d.id.slice(1)]||{};const tot=Object.values(split).reduce((a,b)=>a+b,0);if(tot){let sx=cx-14;const bw=28;users.forEach((u,ui)=>{const w=bw*(split[u]||0)/tot;if(w){const rct=mk("rect",{x:sx,y:y+r+5,width:w,height:4,class:"seg",fill:USERC[ui%USERC.length]});const t2=mk("title",{});t2.textContent=u+" "+split[u];rct.appendChild(t2);svg.appendChild(rct);sx+=w;}});
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
 people:{title:"People",sub:'<span id="peopleN"></span>',body:'<table id="people"></table>'},
 library:{title:"Your library",sub:'<span id="libN"></span>',body:'<input id="libFilter" class="filter" placeholder="Filter by title or year" aria-label="Filter reports"><table id="library"></table>'},
 analyses:{title:"Your analyses",sub:'<span id="anaN"></span>',body:'<div class="ana"><div><table id="analyses"></table><details id="anaNew"><summary>New analysis</summary><form id="anaForm" class="anaform"><input id="anaName" maxlength="80" placeholder="Name, for example: Growth outlook by edition" required><textarea id="anaQs" rows="4" placeholder="One question per line, written to fit any edition: What GDP growth is projected for the coming year?"></textarea><div id="anaDocs" class="docs"></div><div class="row2"><span class="meta" id="anaCost"></span><button class="go">Create and run</button></div></form></details></div><div id="anaView" class="meta">Pick an analysis to see its table, or make one. Each cell is one question asked inside one report: a checked claim with its page, or a refusal.</div></div>'},
 answers:{title:"Your recent answers",sub:'<span id="ansN"></span>',body:'<table id="answers"></table>'},
 usage:{title:"Your usage",sub:'<span id="useN"></span>',body:'<table id="myuse"></table><p class="meta" id="mylibs"></p>'},
 ratings:{title:"Your ratings",sub:'<span id="ratN"></span>',body:'<table id="ratings"></table>'},
 reviews:{title:"Your review requests",sub:'<span id="revN"></span>',body:'<table id="reviews"></table>'}};
const ME=new URLSearchParams(location.search).get("user")||"";
let LAYOUT=null,CAT={},OWNER=ME?"user:"+ME:"admin";
// In user mode every call goes to /api/me with the user in the query and no admin token.
function api(path){if(!ME)return path;return path.replace("/api/admin/","/api/me/")+(path.includes("?")?"&":"?")+"user="+encodeURIComponent(ME);}
function hdr(extra){return Object.assign(ME?{}:{"X-Token":tok()},extra||{});}
function buildGrid(){const g=$("grid");g.textContent="";
 LAYOUT.layout.widgets.forEach((w,i)=>{const t=T[w.type];if(!t)return;const home=CAT[w.type]?CAT[w.type].span:4;const open=w.span===12&&home!==12;const p=el("div","panel"+(w.span===12?" w12":""));p.style.setProperty("--span",w.span);
  if(w.x!==null&&w.x!==undefined){p.dataset.x=w.x;p.style.setProperty("--x",w.x);}
  const h=el("h2");h.innerHTML=t.title+" "+t.sub;p.appendChild(h);
  if(home!==12){const b=el("button","exp",open?"shrink":"expand");b.type="button";b.setAttribute("aria-pressed",open?"true":"false");b.setAttribute("aria-label",(open?"Shrink ":"Expand ")+t.title);b.onclick=()=>toggle(i);h.appendChild(b);}
  const body=el("div","pbody");body.innerHTML=t.body;p.appendChild(body);if(open)p.classList.add("open");p.dataset.type=w.type;if(!DATA[w.type])p.classList.add("skel");g.appendChild(p);if(DATA[w.type])fillWidget(w.type);else watch(p);});
 let n=$("layoutnote");if(!n){n=el("p","layoutnote");n.id="layoutnote";g.after(n);}n.textContent="";
 n.appendChild(document.createTextNode("Layout: "+LAYOUT.layout.widgets.length+" widgets, "+(LAYOUT.saved?"saved "+hm(LAYOUT.updated_at):"catalogue default")+" · a JSON document per owner ("+OWNER+") · expand writes a width back · Esc shrinks all"));
 if(LAYOUT.saved){const r=el("button","link","reset to default");r.type="button";r.onclick=resetLayout;n.appendChild(r);}}
async function saveLayout(){const r=await fetch(api("/api/admin/layout"),{method:"PUT",headers:hdr({"content-type":"application/json"}),body:JSON.stringify(ME?{user:ME,layout:LAYOUT.layout}:{owner:OWNER,layout:LAYOUT.layout})});
 if(r.ok){LAYOUT=await r.json();}else{$("msg").textContent="Layout not saved ("+r.status+").";}}
async function toggle(i){const w=LAYOUT.layout.widgets[i];w.span=w.span===12?(CAT[w.type]?CAT[w.type].span:4):12;buildGrid();await saveLayout();buildGrid();}
async function resetLayout(){const r=await fetch(api("/api/admin/layout?owner="+encodeURIComponent(OWNER)),{method:"DELETE",headers:hdr()});if(r.ok){LAYOUT=await r.json();buildGrid();}}
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
let ANA_DOCS=null,ANA_TIMER=null;
async function anaFormInit(){const f=$("anaForm");if(!f||f.dataset.wired)return;f.dataset.wired="1";
 const r=await fetch(api("/api/me/analyses"),{headers:hdr()});if(!r.ok)return;const j=await r.json();ANA_DOCS=j.docs;const box=$("anaDocs");box.textContent="";
 j.docs.forEach(d=>{const lab=el("label");const c=el("input");c.type="checkbox";c.value=d.id;c.checked=true;lab.appendChild(c);lab.appendChild(document.createTextNode(" "+d.title));box.appendChild(lab);});
 const cost=()=>{const q=$("anaQs").value.split("\n").filter(x=>x.trim()).length,n=box.querySelectorAll("input:checked").length;$("anaCost").textContent=q&&n?q+" question"+(q===1?"":"s")+" × "+n+" report"+(n===1?"":"s")+" = "+(q*n)+" model calls, about "+Math.round(q*n*10/60)+" min on "+j.model+(q*n>j.limits.cells?" · over the limit of "+j.limits.cells:""):"";};
 $("anaQs").addEventListener("input",cost);box.addEventListener("change",cost);cost();
 f.onsubmit=async e=>{e.preventDefault();const btn=f.querySelector("button");btn.disabled=true;btn.textContent="Creating…";
  const body={user:ME,name:$("anaName").value,questions:$("anaQs").value.split("\n").filter(x=>x.trim()),doc_ids:[...box.querySelectorAll("input:checked")].map(c=>+c.value)};
  try{const r=await fetch("/api/me/analyses",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});const j=await r.json();
   if(!r.ok){$("anaView").textContent=j.detail||("Could not create ("+r.status+")");return;}
   const run=await fetch("/api/me/analyses/"+j.id+"/run?user="+encodeURIComponent(ME),{method:"POST"});if(!run.ok){const e2=await run.json();$("anaView").textContent=e2.detail||"Could not start";}
   $("anaNew").open=false;delete DATA.analyses;loadWidget("analyses");openAnalysis(j.id);}
  finally{btn.disabled=false;btn.textContent="Create and run";}};}
async function openAnalysis(id){clearTimeout(ANA_TIMER);const v=$("anaView");if(!v)return;const r=await fetch("/api/me/analyses/"+id+"?user="+encodeURIComponent(ME));if(!r.ok){v.textContent="Could not load that analysis ("+r.status+").";return;}
 const a=await r.json();v.textContent="";v.className="ana-view";
 const head=el("div","anahead");head.appendChild(el("b","",a.name));head.appendChild(el("span","meta"," · "+a.status+" · "+a.done+" of "+a.total+" cells, "+a.answered+" answered"+(a.finished_at?" · finished "+hm(a.finished_at):"")));
 if(a.status==="done"||a.status==="failed"){const del=el("button","link","delete");del.type="button";del.onclick=async()=>{if(!confirm("Delete this analysis? Its answers stay in your history."))return;await fetch("/api/me/analyses/"+id+"?user="+encodeURIComponent(ME),{method:"DELETE"});delete DATA.analyses;loadWidget("analyses");v.className="meta";v.textContent="Deleted.";};head.appendChild(del);}
 v.appendChild(head);
 const t=el("table","matrix");const hr=el("tr");hr.appendChild(el("th","","question"));a.docs.forEach(d=>{const th=el("th","n",d.short);th.title=d.title;hr.appendChild(th);});t.appendChild(hr);
 a.questions.forEach((q,qi)=>{const row=el("tr");row.appendChild(td("qq",q,q));a.docs.forEach(d=>{const c=a.cells[qi+":"+d.id]||{status:"pending"};const cell=el("td","cell "+c.status);
  if(c.status==="answered"){const l=el("a","",c.claim);l.href="/?user="+encodeURIComponent(ME)+"&a="+c.audit_id;l.title="p. "+c.page+(c.claims>1?" · "+c.claims+" claims":"")+" · opens the stored answer";cell.appendChild(l);cell.appendChild(el("span","meta"," p."+c.page));}
  else if(c.status==="not_found"){const l=el("a","dim","not found");l.href="/?user="+encodeURIComponent(ME)+"&a="+c.audit_id;cell.appendChild(l);}
  else cell.textContent=c.status==="pending"?(a.status==="running"?"…":"not run"):c.status;
  row.appendChild(cell);});t.appendChild(row);});
 v.appendChild(t);
 if(a.status==="running"||a.status==="new")ANA_TIMER=setTimeout(()=>openAnalysis(id),3000);}
function isOpen(type){return !!(LAYOUT&&LAYOUT.layout.widgets.some(w=>w.type===type&&w.span===12));}
// The head loads first and once: brief, key figures, sources. Each widget then fetches only its own slice when it
// scrolls into view, and keeps it, so expanding or shrinking re-fills from memory without another request.
const DATA={};
function renderHead(d){
 if(ME){document.title="Research assistant · "+ME;$("h1").textContent="Research assistant · "+ME;$("model").textContent=ME+" · "+d.usage.asked+" asked, "+d.usage.answered+" answered, "+d.usage.not_found+" not found";
  const b=$("brief");b.textContent=d.brief||"";
  const kr=$("keyrow");kr.textContent="";const share=d.usage.asked?Math.round(100*d.usage.answered/d.usage.asked):0;
  [[String(d.usage.asked),"questions asked",""],[share+"%","answered with checked citations",""],[d.ratings.up+" / "+d.ratings.down,"rated up / down",d.ratings.down?"bad":""],[String(d.reviews.open),"review requests open",d.reviews.open?"bad":""],[String(d.usage.libraries.length),"libraries you can search",""]]
   .forEach(([v,k,c])=>{const x=el("div");x.appendChild(el("span","v "+c,v));x.appendChild(el("span","k",k));kr.appendChild(x);});
  $("built").textContent="Board built "+d.now.replace("T"," ")+" UTC · your rows from the audit log, your ratings and review requests · nobody else's.";return;}
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
 library(d){ setT("libN",d.library.length+" report"+(d.library.length===1?"":"s")+" you may search");
  const draw=()=>{const f=($("libFilter")?$("libFilter").value:"").trim().toLowerCase();const rows=d.library.filter(r=>!f||(r.title+" "+r.library+" "+r.edition[2]).toLowerCase().includes(f));
   fillT("library",rows.map(r=>{const row=tr([td("t mono",r.edition[2]||"undated"),td("q",r.title,r.title),td("dim",r.library),td("n",r.pages),td("n dim",r.low_text_pages?r.low_text_pages+" ocr":"","pages flagged for OCR"),td("n dim",r.cited?r.cited+" cited":"","times your answers cited it")]);
    const c=el("td");if(r.pdf){const a=el("a","dim","PDF");a.href=r.pdf;a.target="_blank";a.rel="noopener";c.appendChild(a);c.appendChild(document.createTextNode(" "));}
    const ask=el("a","","ask");ask.href="/?user="+encodeURIComponent(ME)+"&q="+encodeURIComponent("What does the "+(r.edition[2]?r.edition[2]+" edition":r.title)+" say about ");ask.title="Open the chat with this report named, so the search stays inside it";c.appendChild(ask);row.appendChild(c);return row;}),
    isOpen("library")?["edition","title","library",">pages","",">cited",""]:null,f?"No report matches that filter.":"No library grants yet; ask the admin.");};
  draw();const inp=$("libFilter");if(inp&&!inp.dataset.wired){inp.dataset.wired="1";inp.addEventListener("input",draw);} },
 analyses(d){ setT("anaN",d.analyses.length+" saved"); fillT("analyses",d.analyses.map(a=>{const row=tr([td("",a.name),td(a.status==="done"?"ok":(a.status==="failed"?"bad":"dim"),a.status),td("t",hm(a.created_at))]);const c=el("td");const o=el("a","","open");o.href="#analysis="+a.id;o.onclick=e=>{e.preventDefault();openAnalysis(a.id);};c.appendChild(o);row.appendChild(c);return row;}),null,"No analyses yet. Make one below: your questions, run across the reports you choose.");
  anaFormInit();const h=location.hash.match(/analysis=(\d+)/);if(h)openAnalysis(+h[1]); },
 answers(d){ setT("ansN","last "+d.answers.length); fillT("answers",d.answers.map(r=>{const row=tr([td("t",hm(r.at)),td(isOpen("answers")?"wrap":"q",r.question,r.question),td(r.status==="answered"?"ok":"bad",r.status==="answered"?"answered":"not found"),td("n dim",r.citations,"citations")]);const a=el("a","dim","open");a.href="/?user="+encodeURIComponent(ME)+"&a="+r.audit_id;a.title="Shows the stored answer; no model call";const c=el("td");c.appendChild(a);row.appendChild(c);return row;}),isOpen("answers")?["at","question","outcome",">cited",""]:null,"You have not asked anything yet."); },
 usage(d){ const u=d.usage;setT("useN",u.asked+" asked"); fillT("myuse",[tr([td("","answered with checked citations"),td("n ok",u.answered)]),tr([td("","not found in your libraries"),td("n bad",u.not_found)]),tr([td("","of which, cells of your analyses"),td("n dim",u.from_analyses)]),tr([td("","first question"),td("t",u.first_at?hm(u.first_at):"—")]),tr([td("","last question"),td("t",u.last_at?hm(u.last_at):"—")])],null,""); setT("mylibs","Libraries you can search: "+(u.libraries.join(", ")||"none")+" · model "+u.model); },
 ratings(d){ setT("ratN",d.ratings.up+" up · "+d.ratings.down+" down"); fillT("ratings",d.ratings.recent.map(r=>tr([td("t",hm(r.at)),td(r.rating==="up"?"ok":"bad",r.rating),td("q",r.question,r.question),td("q w dim",r.reason||"",r.reason||"")])),null,"No ratings yet. Thumbs on an answer land here."); },
 reviews(d){ setT("revN",d.reviews.open+" open"); fillT("reviews",d.reviews.recent.map(r=>tr([td("t",hm(r.at)),td(r.status==="open"?"bad":"ok",r.status),td("q",r.question,r.question),td("q w dim",r.note||"",r.note||"")])),null,"No review requests. Ask for one when an answer needs a person."); },
 timeline(d){ if($("tl"))timeline(d.graph); }};
function fillWidget(type){const d=DATA[type];if(!d||!FILL[type])return;FILL[type](d);document.querySelectorAll('.panel[data-type="'+type+'"]').forEach(p=>p.classList.remove("skel"));}
async function loadWidget(type){if(DATA[type]){fillWidget(type);return;}
 const r=await fetch(api("/api/admin/widget/"+encodeURIComponent(type)),{headers:hdr()});
 if(!r.ok){document.querySelectorAll('.panel[data-type="'+type+'"] .pbody').forEach(b=>{b.textContent="Could not load ("+r.status+").";b.className="pbody meta";});return;}
 DATA[type]=await r.json();fillWidget(type);}
const SEEN=("IntersectionObserver" in window)?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){SEEN.unobserve(e.target);loadWidget(e.target.dataset.type);}}),{rootMargin:"200px"}):null;
function watch(p){if(SEEN)SEEN.observe(p);else loadWidget(p.dataset.type);}
function render(d){renderHead(d);}
function tok(){try{return sessionStorage.getItem("ra.token")||"";}catch(e){return "";}}
async function load(t){let since="";try{since=localStorage.getItem("ra.lastlook")||"";}catch(e){}
 const [r,lr]=await Promise.all([fetch(api("/api/admin/board?parts=head&since="+encodeURIComponent(since)),{headers:hdr()}),fetch(api("/api/admin/layout?owner="+encodeURIComponent(OWNER)),{headers:hdr()})]);
 if(lr.ok){LAYOUT=await lr.json();CAT=LAYOUT.catalogue||{};buildGrid();}
 if(r.status===401){try{sessionStorage.removeItem("ra.token");}catch(e){}show("login","That token was not accepted.");return;}
 if(!r.ok){show("login",ME?"No board for "+ME+": unknown user, or no library grant ("+r.status+").":"The board could not be loaded ("+r.status+").");return;}
 const d=await r.json();render(d);if(!ME){loadKeys();loadLibs();}show("view");try{localStorage.setItem("ra.lastlook",d.now);}catch(e){}}
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
(()=>{if(ME){["helpBtn","forget","help"].forEach(id=>$(id).hidden=true);document.querySelector(".tabs").hidden=true;$("login").hidden=true;$("navChat").href="/?user="+encodeURIComponent(ME);$("navMe").hidden=false;$("navMe").href="/dashboard?user="+encodeURIComponent(ME);$("navMe").setAttribute("aria-current","page");$("navAdmin").remove();document.querySelector(".nav").style.cssText="border-right:0;padding-right:0";load("");return;}
 let t="";const hp=new URLSearchParams(location.hash.slice(1));if(hp.get("token")){t=hp.get("token");try{sessionStorage.setItem("ra.token",t);}catch(e){}hp.delete("token");try{history.replaceState(null,"",location.pathname+(hp.toString()?"#"+hp.toString():""));}catch(e){}}
 if(!t)t=tok();if(t)load(t);else show("login");})();
</script></body></html>"""
