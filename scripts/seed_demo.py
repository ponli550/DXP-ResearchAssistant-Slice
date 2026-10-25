"""Build a clean demo database: five reports, two users, a day's worth of realistic questions asked through the real pipeline.

    set -a; . ./.env; set +a
    uv run --python 3.14 python scripts/seed_demo.py data/demo.db

Every answer is real: the model named in HERMES_MODEL answers, the gate checks, refusals stay refusals. Nothing is faked.
Run it before the meeting, not during it: sixteen model calls, a few minutes. Safe to run again: it keeps what it has and asks only what is missing.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient  # noqa: E402

from ra.db import connect  # noqa: E402
from ra.ingest import ingest_pdf  # noqa: E402
from ra.web import create_app  # noqa: E402

PDFS = [
    ("wb", "World Bank Malaysia Economic Monitor", "Malaysia Economic Monitor June 2015", "data/pdfs/wb-mem-2015-06.pdf"),
    ("wb", "World Bank Malaysia Economic Monitor", "Malaysia Economic Monitor October 2023", "data/pdfs/wb-mem-2023-10.pdf"),
    ("wb", "World Bank Malaysia Economic Monitor", "Malaysia Economic Monitor April 2024", "data/pdfs/wb-mem-2024-04.pdf"),
    ("wb", "World Bank Malaysia Economic Monitor", "Malaysia Economic Monitor October 2025", "data/pdfs/wb-mem-2025-10.pdf"),
    ("wb", "World Bank Malaysia Economic Monitor", "Malaysia Economic Monitor April 2026", "data/pdfs/wb-mem-2026-04.pdf"),
    ("internal", "Internal research notes", "Internal Memo (synthetic)", "data/pdfs/synthetic-internal-memo.pdf"),
]
GRANTS = [("alice", "wb"), ("alice", "internal"), ("bob", "wb")]

# (user, question). Facts the small model answers reliably, analysis questions, two comparisons, two it must refuse.
QUESTIONS = [
    ("alice", "What GDP growth is projected for Malaysia in 2025 in the October 2025 monitor?"),
    ("alice", "What growth did the October 2025 monitor give for Malaysia in 2024?"),
    ("alice", "How much did Malaysia's economy grow in the first half of 2025?"),
    ("alice", "What was Malaysia's real GDP growth in the fourth quarter of 2025?"),
    ("alice", "What share of social assistance transfers goes to the B40 households?"),
    ("alice", "What forecast did the October 2023 monitor give for Malaysia's growth in 2024?"),
    ("alice", "What does the April 2026 monitor say drove growth in 2025?"),
    ("alice", "What are the main downside risks to Malaysia's outlook in the October 2025 monitor?"),
    ("alice", "What does the April 2024 monitor say about inflation in Malaysia?"),
    ("alice", "How did the view of Malaysia's 2025 growth change between the October 2025 and April 2026 monitors?"),
    ("alice", "What does the internal memo say about the coverage review?"),
    ("alice", "What will Singapore's minimum wage be in 2029?"),
    ("bob", "What did the April 2024 monitor give as Malaysia's GDP growth in 2023?"),
    ("bob", "What does the monitor say about the ringgit in the October 2025 edition?"),
    ("bob", "Who is the chief executive officer of PETRONAS?"),
    ("bob", "What does the internal memo say about the coverage review?"),  # bob holds no grant on the internal library, so the memo is never retrieved for him
]


def main(path):
    """Resumable: an existing file keeps its reports and answers, and only questions not yet asked are asked."""
    con = connect(path)
    have = {r["title"] for r in con.execute("SELECT title FROM documents")}
    for lib, lib_name, title, pdf in PDFS:
        if title in have:
            continue
        rep = ingest_pdf(con, lib, title, pdf, library_name=lib_name)
        print("ingested", title, rep["pages"], "pages", "OCR flagged:" if rep["low_text_pages"] else "", rep["low_text_pages"] or "", flush=True)
    con.executemany("INSERT OR IGNORE INTO grants(user, library_id) VALUES (?,?)", GRANTS)
    con.commit()
    asked = {(r["user"], r["question"]): (r["id"], r["status"]) for r in con.execute("SELECT id, user, question, status FROM audit")}
    con.close()
    app = create_app(path)
    client = TestClient(app)
    ids = []
    for user, q in QUESTIONS:
        if (user, q) in asked:
            ids.append((user,) + asked[(user, q)])
            print(f"{user:6} {asked[(user, q)][1]:10}   kept  {q[:70]}", flush=True)
            continue
        t = time.perf_counter()
        r = client.post("/api/ask", json={"user": user, "question": q})
        j = r.json()
        ids.append((user, j.get("audit_id"), j.get("status")))
        print(f"{user:6} {j.get('status', r.status_code):10} {time.perf_counter() - t:5.1f}s  {q[:70]}", flush=True)
    answered = [(u, i) for u, i, st in ids if st == "answered"]
    not_found = [(u, i) for u, i, st in ids if st == "not_found" and u == "alice"]
    con = connect(path)
    if con.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]:
        print("ratings and review already seeded")
        return 0
    con.close()
    if answered:
        client.post("/api/feedback", json={"user": answered[0][0], "audit_id": answered[0][1], "rating": "up", "reason": ""})
    if len(answered) > 1:
        client.post("/api/feedback", json={"user": answered[1][0], "audit_id": answered[1][1], "rating": "up", "reason": ""})
    if not_found:
        client.post("/api/feedback", json={"user": "alice", "audit_id": not_found[0][1], "rating": "down", "reason": "The April 2026 report covers this; please check page 12."})
        client.post("/api/review", json={"user": "alice", "audit_id": not_found[0][1], "note": "Needed for a client call on Thursday."})
    print("seeded", path, ":", len(QUESTIONS), "questions,", len(answered), "answered")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/demo.db"))
