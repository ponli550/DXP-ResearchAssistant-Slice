"""What a person's workspace is made of: their questions, the checked facts they hold, what to do next, and what they have saved.

Everything here is counted from the audit log and the search index, inside the person's grant. No model is called.
"""
import json
import math
import re
from collections import Counter, defaultdict

from .retrieve import EDITION_RE, STOPWORDS, find_query, named_documents

# Words too general to name a topic, on top of the search stopwords and the words in the report titles.
TOPIC_NOISE = set("quarter half first second third fourth year years full world bank grow goes give gives project forecast compare change".split())
TOPIC_WORD = re.compile(r"[a-z][a-z0-9-]+")


def _word_pairs(text, skip):
    words = TOPIC_WORD.findall(text.lower())
    return [f"{a} {b}" for a, b in zip(words, words[1:]) if a not in skip and b not in skip and len(a) > 2 and len(b) > 2]


def topic_pages(con, user, query):
    """How many pages of each report the user may search match a search box query. Same index and grant as Find; no model."""
    match = find_query(query)
    if match is None:
        return {}
    return {r["doc_id"]: r["n"] for r in con.execute(
        "SELECT p.doc_id, COUNT(DISTINCT p.page) AS n FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
        "JOIN documents d ON d.id = p.doc_id JOIN grants g ON g.library_id = d.library_id AND g.user = ? "
        "WHERE passages_fts MATCH ? GROUP BY p.doc_id", (user, match))}


def edition_topics(con, user, skip):
    """For each report, the two-word phrase most particular to it, so a reader sees what sets one edition apart.

    Scored by how much of the phrase's use sits in one report, weighted by how often it is used. Page furniture is left
    out: a phrase found at the very top or foot of most of its pages is a running head, not a subject.
    """
    # ponytail: scans every page on each load, about 200 ms on five reports; cache per set of documents if libraries grow.
    pages, edge = defaultdict(lambda: defaultdict(set)), Counter()
    for r in con.execute("SELECT pg.doc_id, pg.page, pg.text FROM pages pg JOIN documents d ON d.id = pg.doc_id "
                         "JOIN grants g ON g.library_id = d.library_id AND g.user = ?", (user,)):
        text = r["text"].lower()
        rim = set(TOPIC_WORD.findall(text[:160] + " " + text[-160:]))
        for phrase in sorted(set(_word_pairs(text, skip))):
            pages[phrase][r["doc_id"]].add(r["page"])
            if rim.issuperset(phrase.split()):
                edge[phrase] += 1
    best = {}
    for phrase, by_doc in pages.items():
        total = sum(len(v) for v in by_doc.values())
        if total < 10 or edge[phrase] > total * 0.4:
            continue
        doc, most = max(((d, len(v)) for d, v in by_doc.items()), key=lambda x: x[1])
        score = most / total * math.log(total)
        if score > best.get(doc, (0, ""))[0]:
            best[doc] = (score, phrase)
    return {doc: phrase for doc, (_, phrase) in best.items()}


def question_topics(con, user, questions, skip, limit=4):
    """The subjects a person keeps asking about: the phrases most repeated across their different questions, if the reports use them."""
    counts = Counter(phrase for q in questions for phrase in dict.fromkeys(_word_pairs(q, skip)))
    out, used = [], set()
    for phrase, _ in counts.most_common():
        words = set(phrase.split())
        if words & used or not topic_pages(con, user, f'"{phrase}"'):
            continue
        out.append(phrase)
        used |= words
        if len(out) == limit:
            break
    return out


def _editions(con, user):
    """The reports a person may search that carry a date, oldest first, as (year, month, id, "April 2026")."""
    out = []
    for r in con.execute("SELECT d.id, d.title FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ?", (user,)):
        m = EDITION_RE.search(r["title"])
        if m:
            month = "january february march april may june july august september october november december".split().index(m.group(1).lower()) + 1
            out.append((int(m.group(2)), month, r["id"], f"{m.group(1).title()} {m.group(2)}"))
    return sorted(out)


def _why_refused(answer):
    """The reason a stored refusal gives, in the words of the check that made it."""
    for c in answer.get("rejected_claims") or []:
        if c.get("reason"):
            return c["reason"]
    return "no passage in your reports matched" if not answer.get("retrieved") else "no passage answered the question"


def _next_steps(con, user, rows, stored, questions, apart, limit=6):
    """What is worth doing next, most pressing first: each says why, and carries the action that does it."""
    steps = []
    own = [r for r in rows if not r["cell"]]
    ask = lambda text: {"label": "Ask", "desk": {"q": text}}  # noqa: E731
    how = lambda audit_id: {"label": "How we got this", "flow": audit_id}  # noqa: E731

    for r in own:
        if r["review"] == "open":
            steps.append({"kind": "review", "title": "A review request is waiting for a person", "why": f"“{r['question']}”",
                          "actions": [how(r["id"])] if r["id"] in stored else [{"label": "Open", "desk": {"a": r["id"]}}]})
            break

    editions = _editions(con, user)
    latest = editions[-1] if editions else None
    if latest:
        for r in own:
            docs = {int(c.split(":")[0]) for c in r["cited"].split(";") if c}
            if r["status"] != "answered" or not docs or latest[2] in docs or named_documents(con, user, r["question"]):
                continue
            pair = next((p for p in _word_pairs(r["question"], STOPWORDS | TOPIC_NOISE) if topic_pages(con, user, f'"{p}"').get(latest[2])), None)
            if pair:
                older = next(e[3] for e in reversed(editions) if e[2] in docs) if any(e[2] in docs for e in editions) else "an older report"
                steps.append({"kind": "stale", "title": "A newer edition may change this answer",
                              "why": f"“{r['question']}” cites {older}, and {latest[3]} also covers {pair}.",
                              "actions": [ask(f"According to the {latest[3]} monitor, {r['question'][0].lower()}{r['question'][1:]}")]})
                break

    groups = defaultdict(list)
    for r in own:
        groups[r["question"]].append(r)
    mixed = [(sum(x["status"] == "answered" for x in g) / len(g), q, g) for q, g in groups.items()
             if len(g) >= 3 and 0 < sum(x["status"] == "answered" for x in g) < len(g)]
    if mixed:
        _, q, g = min(mixed, key=lambda x: x[0])
        yes = sum(x["status"] == "answered" for x in g)
        saved = next((x["id"] for x in g if x["id"] in stored), None)
        steps.append({"kind": "unreliable", "title": "Don't rely on this answer yet", "why": f"“{q}” was answered {yes} of {len(g)} times.",
                      "actions": [how(saved)] if saved else [ask(q)]})

    for r in own:
        if r["status"] != "answered" and r["id"] in stored:
            steps.append({"kind": "refused", "title": "Why this got no answer", "why": f"“{r['question']}”: {stored[r['id']]}.",
                          "actions": [how(r["id"]), ask(r["question"])]})
            break

    if not own:
        steps.append({"kind": "start", "title": "Ask your first question", "why": "Each answer comes with the page that proves it, and collects here.",
                      "actions": [{"label": "Ask", "desk": {}}]})
    asked = " ".join(questions).lower()
    untouched = [(t, c) for t, c, d in apart if t not in asked]
    steps = steps[:limit - 1] if untouched else steps
    by_year = {e[2]: e[3] for e in editions}
    for term, counts in untouched:
        if len(steps) >= limit:
            break
        doc, n = max(counts.items(), key=lambda x: x[1])
        where = by_year.get(doc)
        steps.append({"kind": "blind", "title": f"You haven't asked about {term}", "why": f"{n} pages{' of ' + where if where else ''} cover it.",
                      "actions": [ask(f"What does the {where} edition say about {term}?" if where else f"What do the reports say about {term}?"),
                                  {"label": "Read the pages", "topic": f'"{term}"'}]})
    return steps[:limit]


def user_activity(con, user, now, model_name):
    """What the workspace draws: every question the person asked, the checked facts in their stored answers, and the topics and next steps."""
    rows = con.execute(
        "SELECT a.id, a.at, a.question, a.status, a.cited, a.answer, "
        "(SELECT rating FROM feedback f WHERE f.audit_id = a.id ORDER BY f.id DESC LIMIT 1) AS rating, "
        "(SELECT status FROM reviews r WHERE r.audit_id = a.id ORDER BY r.id DESC LIMIT 1) AS review, "
        "a.id IN (SELECT audit_id FROM analysis_cells WHERE audit_id IS NOT NULL) AS cell "
        "FROM audit a WHERE a.user = ? ORDER BY a.id DESC", (user,)).fetchall()
    questions = list(dict.fromkeys(r["question"] for r in rows))
    index = {q: i for i, q in enumerate(questions)}
    titles = [r["title"] for r in con.execute("SELECT d.title FROM documents d JOIN grants g ON g.library_id = d.library_id AND g.user = ?", (user,))]
    skip = STOPWORDS | TOPIC_NOISE | {w for t in titles for w in TOPIC_WORD.findall(t.lower())}
    asked = question_topics(con, user, questions, skip)
    apart = {d: t for d, t in edition_topics(con, user, skip).items() if t not in asked}
    joined = "\n".join(questions)

    def topic(term, group, doc=None):
        # Spelled as the person wrote it, so GDP stays GDP; a phrase from the reports stays lower case.
        written = re.search(re.escape(term), joined, re.I) if group == "asked" else None
        return {"term": written.group() if written else term, "query": f'"{term}"', "group": group, "doc": doc, "counts": topic_pages(con, user, f'"{term}"')}

    topics = [topic(t, "asked") for t in asked] + [topic(t, "apart", d) for d, t in apart.items()]

    stored, findings, seen = {}, [], set()
    for r in rows:
        if not r["answer"]:
            continue
        answer = json.loads(r["answer"])
        stored[r["id"]] = _why_refused(answer) if r["status"] != "answered" else ""
        for i, c in enumerate(answer.get("claims") or [] if r["status"] == "answered" else []):
            key = (c["doc_id"], c["page"], c["quote"])
            if key not in seen:
                seen.add(key)
                findings.append({"audit_id": r["id"], "claim": i, "text": c["text"], "quote": c["quote"], "doc_id": c["doc_id"], "page": c["page"],
                                 "question": r["question"], "at": r["at"]})
    return {
        "now": now.isoformat(timespec="seconds"),
        "model": model_name,
        "connected": not model_name.startswith("extractive"),
        "questions": questions,
        "rows": [{"id": r["id"], "at": r["at"], "q": index[r["question"]], "status": r["status"], "saved": r["id"] in stored, "why": stored.get(r["id"]) or None,
                  "rating": r["rating"], "review": r["review"], "cell": bool(r["cell"]),
                  "cited": [[int(d), int(p)] for d, p in (c.split(":") for c in r["cited"].split(";") if c)]}
                 for r in rows],
        "findings": findings,
        "topics": topics,
        "next": _next_steps(con, user, rows, stored, questions, [(t["term"], t["counts"], t["doc"]) for t in topics if t["group"] == "apart" and t["counts"]]),
    }


# ---- saved dashboards and reports ----
# The catalogue is code, as on the admin board: a saved document may choose and order blocks, but cannot define one.
LIVE_BLOCKS = {"topics", "pages", "days", "findings", "questions"}
BLOCKS = {"dashboard": LIVE_BLOCKS | {"finding"}, "report": {"heading", "note", "finding", "topics", "pages", "days"}}
MAX_BLOCKS = 60
FIELDS = {"terms": list, "topic": str, "report": int, "outcome": str, "audit_id": int, "claim": int, "text": str}


def clean_board(kind, title, blocks):
    """(title, blocks, problems). Only the fields a block may have survive: a report cannot carry a claim's words, only a pointer to a checked one."""
    if kind not in BLOCKS:
        return "", [], ["kind must be dashboard or report"]
    title = str(title or "").strip()[:80]
    problems = [] if title else ["it needs a title"]
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        return title, [], problems + [f"blocks must be a list of at most {MAX_BLOCKS}"]
    out = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict) or b.get("type") not in BLOCKS[kind]:
            problems.append(f"block {i}: not something a {kind} can hold")
            continue
        block = {"type": b["type"]}
        for field, want in FIELDS.items():
            value = b.get(field)
            if isinstance(value, want) and not isinstance(value, bool):
                block[field] = value[:2000] if want is str else value
        if "terms" in block:
            block["terms"] = [str(t)[:80] for t in block["terms"]][:12]
        if b["type"] == "finding" and not ("audit_id" in block and "claim" in block):
            problems.append(f"block {i}: a finding points at one claim of one answer")
            continue
        if b["type"] != "finding":
            block.pop("audit_id", None)
            block.pop("claim", None)
        if b["type"] in ("heading", "note") and not block.get("text", "").strip():
            problems.append(f"block {i}: needs some text")
            continue
        out.append(block)
    return title, out, problems


def _resolve(con, user, block):
    """A finding's words always come from the stored answer, for the person who owns it. If that answer is gone, the block says so."""
    if block["type"] != "finding":
        return block
    row = con.execute("SELECT question, answer FROM audit WHERE id=? AND user=?", (block["audit_id"], user)).fetchone()
    claims = (json.loads(row["answer"]).get("claims") or []) if row and row["answer"] else []
    if not 0 <= block["claim"] < len(claims):
        return {**block, "missing": True}
    c = claims[block["claim"]]
    return {**block, "text": c["text"], "quote": c["quote"], "doc_id": c["doc_id"], "page": c["page"], "title": c["title"], "question": row["question"]}


def board_view(con, user, board_id):
    r = con.execute("SELECT id, kind, title, doc, updated_at FROM boards WHERE id=? AND user=?", (board_id, user)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "title": r["title"], "updated_at": r["updated_at"], "blocks": [_resolve(con, user, b) for b in json.loads(r["doc"])]}


def list_boards(con, user):
    return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "updated_at": r["updated_at"], "blocks": len(json.loads(r["doc"]))}
            for r in con.execute("SELECT id, kind, title, doc, updated_at FROM boards WHERE user=? ORDER BY updated_at DESC, id DESC", (user,))]


def create_board(con, user, kind, title, blocks):
    cur = con.execute("INSERT INTO boards(user, kind, title, doc) VALUES (?,?,?,?)", (user, kind, title, json.dumps(blocks)))
    con.commit()
    return cur.lastrowid


def save_board(con, user, board_id, title, blocks):
    con.execute("UPDATE boards SET title=?, doc=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=? AND user=?", (title, json.dumps(blocks), board_id, user))
    con.commit()


def add_to_latest(con, user, kind, blocks):
    """Pin or add to the board last worked on, starting one if there is none. Something already there is not added twice."""
    r = con.execute("SELECT id, title, doc FROM boards WHERE user=? AND kind=? ORDER BY updated_at DESC, id DESC LIMIT 1", (user, kind)).fetchone()
    if not r:
        return create_board(con, user, kind, "My report" if kind == "report" else "My dashboard", blocks)
    held = json.loads(r["doc"])
    save_board(con, user, r["id"], r["title"], held + [b for b in blocks if b not in held])
    return r["id"]
