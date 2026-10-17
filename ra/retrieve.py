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


def retrieve(con, user, question, k=6):
    """Top passages for a question, limited to libraries the user holds a grant for.

    The access rule is part of the SQL, so a passage the user may not see is never returned,
    never reaches the model, and can never be cited.
    """
    q = fts_query(question)
    if q is None:
        return []
    rows = con.execute(
        """
        SELECT p.id, p.doc_id, d.title, p.page, p.text, bm25(passages_fts) AS score
        FROM passages_fts
        JOIN passages p ON p.id = passages_fts.rowid
        JOIN documents d ON d.id = p.doc_id
        JOIN grants g ON g.library_id = d.library_id AND g.user = ?
        WHERE passages_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (user, q, k),
    ).fetchall()
    return [Passage(r["id"], r["doc_id"], r["title"], r["page"], r["text"], r["score"]) for r in rows]
