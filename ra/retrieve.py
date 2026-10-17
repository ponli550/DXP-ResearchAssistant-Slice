import re
from dataclasses import dataclass

STOPWORDS = set(
    "a an and are as at be by for from has have how in is it its of on or that the this to was were what when which who why will with "
    "about across did do does between over under into than then their there these those during each any all more most".split()
)


@dataclass
class Passage:
    id: int
    doc_id: int
    title: str
    page: int
    text: str
    score: float


def fts_query(question):
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", question.lower()) if t not in STOPWORDS and len(t) > 1]
    if not toks:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(toks))


MAX_NAMED = 3  # editions a single question may pull in, each with a full share of k slots
MONTHS = "january february march april may june july august september october november december".split()
EDITION_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(20\d\d|19\d\d)\b", re.I)

_SQL = """
    SELECT p.id, p.doc_id, d.title, p.page, p.text, bm25(passages_fts) AS score
    FROM passages_fts
    JOIN passages p ON p.id = passages_fts.rowid
    JOIN documents d ON d.id = p.doc_id
    JOIN grants g ON g.library_id = d.library_id AND g.user = ?
    WHERE passages_fts MATCH ? {doc_filter}
    ORDER BY score
    LIMIT ?
"""


def named_documents(con, user, question):
    """Ids of documents the user may see whose title carries a month and year named in the question.

    "the April 2024 monitor" narrows the search to that edition. It is a date filter taken from the question,
    and it never widens access: only documents behind a grant are considered.
    """
    wanted = {f"{m.group(1).lower()} {m.group(2)}" for m in EDITION_RE.finditer(question)}
    if not wanted:
        return []
    rows = con.execute(
        "SELECT d.id, d.title FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ?", (user,)
    ).fetchall()
    return [r["id"] for r in rows if any(w in r["title"].lower() for w in wanted)]


def _rows(con, user, q, k, doc_id=None):
    sql = _SQL.format(doc_filter="AND p.doc_id = ?" if doc_id is not None else "")
    args = (user, q) + ((doc_id,) if doc_id is not None else ()) + (k,)
    return [Passage(r["id"], r["doc_id"], r["title"], r["page"], r["text"], r["score"]) for r in con.execute(sql, args)]


def retrieve(con, user, question, k=12):
    """Top passages for a question, limited to libraries the user holds a grant for.

    The access rule is part of the SQL, so a passage the user may not see is never returned,
    never reaches the model, and can never be cited. When the question names editions, each named
    edition gets its own k slots, so a comparison across reports sees every report.
    """
    q = fts_query(question)
    if q is None:
        return []
    docs = named_documents(con, user, question)
    if not docs:
        return _rows(con, user, q, k)
    docs = docs[:MAX_NAMED]
    merged = [p for d in docs for p in _rows(con, user, q, k, d)]
    return sorted(merged, key=lambda p: p.score)
