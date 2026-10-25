import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from .ground import Claim, asks_for_figure, check_claim, states_a_figure
from .retrieve import Passage, fts_query, named_documents, retrieve, search_terms


class LLM(Protocol):
    def draft(self, question: str, passages: list[Passage], feedback: list[str] | None = None) -> list[Claim]: ...


@dataclass
class Result:
    status: str  # "answered" or "not_found"
    claims: list[Claim] = field(default_factory=list)
    rejected: list[tuple[Claim, str]] = field(default_factory=list)
    attempts: int = 0
    closest: list[Passage] = field(default_factory=list)
    audit_id: int | None = None
    trace: list = field(default_factory=list)


def _page_text(con, doc_id, page):
    r = con.execute("SELECT text FROM pages WHERE doc_id=? AND page=?", (doc_id, page)).fetchone()
    return r["text"] if r else None


NO_FIGURE = "the question asks for a figure and no claim states one"


def _verify(con, claims, allowed):
    ok, bad = [], []
    for c in claims:
        v = check_claim(c, _page_text(con, c.doc_id, c.page), allowed)
        (ok if v.ok else bad).append(c if v.ok else (c, v.reason))
    return ok, bad


def _ms(t):
    return round((time.perf_counter() - t) * 1000, 1)


def _libraries(con, user):
    return [r["name"] for r in con.execute("SELECT l.name FROM libraries l JOIN grants g ON g.library_id = l.id AND g.user = ? ORDER BY l.name", (user,))]


def _titles(con, ids):
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [r["title"] for r in con.execute(f"SELECT title FROM documents WHERE id IN ({marks}) ORDER BY title", ids)]


def ask(con, llm, user, question, k=12, only=None):
    """Retrieve, draft, check every claim, retry once on failures, otherwise say not found.

    Every step is recorded in result.trace with what it did and how long it took, including the steps that
    found nothing. The trace only names libraries the user holds a grant for.
    """
    trace = []
    t = time.perf_counter()
    passages = retrieve(con, user, question, k, only)
    trace.append({
        "kind": "search", "tag": "Plain code", "query": (fts_query(search_terms(question, named_documents(con, user, question))) or "").replace('"', ""),
        "libraries": _libraries(con, user), "editions": _titles(con, named_documents(con, user, question)),
        "limit": k, "found": len(passages), "documents": dict(Counter(p.title for p in passages)), "ms": _ms(t),
        **({"only": sorted(only)} if only is not None else {}),
    })
    allowed = {(p.doc_id, p.page) for p in passages}
    result = Result(status="not_found", closest=passages, trace=trace)
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
    trace.append({"kind": "outcome", "status": result.status, "claims": len(result.claims), "found": len(passages)})
    cur = con.execute(
        "INSERT INTO audit(user, question, status, cited) VALUES (?,?,?,?)",
        (user, question, result.status, ";".join(f"{c.doc_id}:{c.page}" for c in result.claims)),
    )
    result.audit_id = cur.lastrowid
    con.commit()
    return result
