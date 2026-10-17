from dataclasses import dataclass, field
from typing import Protocol

from .ground import Claim, check_claim
from .retrieve import Passage, retrieve


class LLM(Protocol):
    def draft(self, question: str, passages: list[Passage], feedback: list[str] | None = None) -> list[Claim]: ...


@dataclass
class Result:
    status: str  # "answered" or "not_found"
    claims: list[Claim] = field(default_factory=list)
    rejected: list[tuple[Claim, str]] = field(default_factory=list)
    attempts: int = 0
    closest: list[Passage] = field(default_factory=list)


def _page_text(con, doc_id, page):
    r = con.execute("SELECT text FROM pages WHERE doc_id=? AND page=?", (doc_id, page)).fetchone()
    return r["text"] if r else None


def _verify(con, claims, allowed):
    ok, bad = [], []
    for c in claims:
        v = check_claim(c, _page_text(con, c.doc_id, c.page), allowed)
        (ok if v.ok else bad).append(c if v.ok else (c, v.reason))
    return ok, bad


def ask(con, llm, user, question, k=12):
    """Retrieve, draft, check every claim, retry once on failures, otherwise say not found."""
    passages = retrieve(con, user, question, k)
    allowed = {(p.doc_id, p.page) for p in passages}
    result = Result(status="not_found", closest=passages)
    if passages:
        feedback = None
        for attempt in (1, 2):
            result.attempts = attempt
            ok, bad = _verify(con, llm.draft(question, passages, feedback), allowed)
            result.rejected.extend(bad)
            if ok:
                result.status, result.claims = "answered", ok
                break
            feedback = [f"'{c.text}' was rejected: {why}" for c, why in bad] or ["no claims were returned"]
    con.execute(
        "INSERT INTO audit(user, question, status, cited) VALUES (?,?,?,?)",
        (user, question, result.status, ";".join(f"{c.doc_id}:{c.page}" for c in result.claims)),
    )
    con.commit()
    return result
