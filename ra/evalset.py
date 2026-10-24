"""Score an assistant on a graded question set. Four outcomes, in the order that matters:

  wrong_and_confident   answered when it should not, or answered with the wrong fact. Target: zero.
  timid                 refused a question the library can answer.
  correct               answered with the expected fact, cited from the right report and page.
  correct_refusal       refused a question the library cannot answer.
"""
import json

from .answer import ask
from .ground import normalise
from .retrieve import retrieve


def load(path):
    with open(path) as f:
        return json.load(f)


def anchors(fact):
    """A fact's anchor is one sentence, or a list of sentences when the report states the fact on several pages."""
    a = fact["anchor"]
    return [a] if isinstance(a, str) else list(a)


def resolve(con, fact):
    """Doc id and every page of that doc where any anchor sentence appears. Empty if an anchor cannot be found.

    Every anchor must be found somewhere, so a mistyped second anchor is caught by check_questions rather than silently
    narrowing the accepted pages.
    """
    row = con.execute("SELECT id FROM documents WHERE title=?", (fact["doc"],)).fetchone()
    if not row:
        return None, set()
    texts = [(r["page"], normalise(r["text"])) for r in con.execute("SELECT page, text FROM pages WHERE doc_id=?", (row["id"],))]
    pages = set()
    for a in anchors(fact):
        found = {pg for pg, t in texts if normalise(a) in t}
        if not found:
            return row["id"], set()
        pages |= found
    return row["id"], pages


def check_questions(con, questions):
    """Return a list of problems: a fact whose document or anchor sentence is not in the library."""
    bad = []
    for q in questions:
        for f in q["facts"]:
            doc_id, pages = resolve(con, f)
            if doc_id is None or not pages:
                bad.append(f"{q['id']}: anchor not found in {f['doc']}: {f['anchor']!r}")
    return bad


def score(con, llm, questions, user, k=12):
    rows, drafted, rejected = [], 0, 0
    for q in questions:
        facts = [(resolve(con, f), f) for f in q["facts"]]
        res = ask(con, llm, user, q["q"], k)
        drafted += len(res.claims) + len(res.rejected)
        rejected += len(res.rejected)
        got = {(p.doc_id, p.page) for p in retrieve(con, user, q["q"], k)}
        hit = bool(facts) and all(any((d, pg) in got for pg in pages) for (d, pages), _ in facts)
        if q["facts"]:
            quotes = normalise(" ".join(c.quote for c in res.claims))
            ok = res.status == "answered" and all(
                any((c.doc_id, c.page) in {(d, pg) for pg in pages} for c in res.claims) and normalise(f["must"]) in quotes
                for (d, pages), f in facts
            )
            outcome = "correct" if ok else ("timid" if res.status == "not_found" else "wrong_and_confident")
        else:
            outcome = "wrong_and_confident" if res.status == "answered" else "correct_refusal"
        rows.append({"id": q["id"], "q": q["q"], "answerable": bool(q["facts"]), "retrieval_hit": hit if q["facts"] else None,
                     "outcome": outcome, "attempts": res.attempts,
                     "answer": [[c.text, c.doc_id, c.page, c.quote] for c in res.claims]})
    ans = [r for r in rows if r["answerable"]]
    count = lambda o: sum(1 for r in rows if r["outcome"] == o)
    return {
        "questions": len(rows),
        "answerable": len(ans),
        "unanswerable": len(rows) - len(ans),
        "retrieval_hit_rate": round(sum(1 for r in ans if r["retrieval_hit"]) / len(ans), 3) if ans else None,
        "correct": count("correct"),
        "timid": count("timid"),
        "correct_refusal": count("correct_refusal"),
        "wrong_and_confident": count("wrong_and_confident"),
        "claims_drafted": drafted,
        "claims_rejected_by_gate": rejected,
        "rows": rows,
    }


def report(s, name):
    lines = [
        f"== {name}: {s['questions']} questions ({s['answerable']} answerable, {s['unanswerable']} not answerable)",
        f"retrieval found the right page   {s['retrieval_hit_rate']}",
        f"correct                          {s['correct']} / {s['answerable']}",
        f"timid (refused, could answer)    {s['timid']}",
        f"correct refusal                  {s['correct_refusal']} / {s['unanswerable']}",
        f"WRONG AND CONFIDENT              {s['wrong_and_confident']}   (target 0)",
        f"gate rejected                    {s['claims_rejected_by_gate']} of {s['claims_drafted']} drafted claims",
    ]
    for r in s["rows"]:
        if r["outcome"] in ("wrong_and_confident", "timid"):
            lines.append(f"   {r['outcome']:20s} {r['id']} {r['q'][:70]}")
    return "\n".join(lines)
