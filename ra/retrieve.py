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


def search_terms(question, named):
    """The question with its edition words removed once they have become a document filter.

    "October 2025" and "monitor" rank every dated page in the edition above the forecast the question is
    about, so they are stripped from the text query when they are already applied as a filter.
    """
    if not named:
        return question
    q = EDITION_RE.sub(" ", question)
    return re.sub(r"\bmonitors?\b", " ", q, flags=re.I)


DOC_FLOOR = 2  # slots every document with a match keeps when no edition is named


def _granted_docs(con, user):
    return [r["id"] for r in con.execute(
        "SELECT d.id FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY d.id", (user,))]


def _diversify(per_doc, k):
    """Each document's top DOC_FLOOR passages first, then the rest by score, up to k.

    Without this, a comparison that names no edition sees only the reports with the most matching words,
    and the other side of the comparison never reaches the model.
    """
    picked, rest = [], []
    for rows in per_doc:
        picked += rows[:DOC_FLOOR]
        rest += rows[DOC_FLOOR:]
    picked.sort(key=lambda p: p.score)
    rest.sort(key=lambda p: p.score)
    return (picked + rest)[:k]


def retrieve(con, user, question, k=12):
    """Top passages for a question, limited to libraries the user holds a grant for.

    The access rule is part of the SQL, so a passage the user may not see is never returned,
    never reaches the model, and can never be cited. When the question names editions, each named
    edition gets its own k slots and the edition words leave the text query. When it names none,
    every granted document keeps a floor of slots so a comparison sees both of its sides.
    """
    docs = named_documents(con, user, question)
    q = fts_query(search_terms(question, docs))
    if q is None:
        return []
    if docs:
        docs = docs[:MAX_NAMED]
        merged = [p for d in docs for p in _rows(con, user, q, k, d)]
        return sorted(merged, key=lambda p: p.score)
    per_doc = [_rows(con, user, q, k, d) for d in _granted_docs(con, user)]
    return _diversify([r for r in per_doc if r], k)
