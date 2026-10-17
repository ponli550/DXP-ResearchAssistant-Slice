import argparse
import sys

from .answer import ask
from .db import connect
from .ingest import ingest_pdf


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ra")
    ap.add_argument("--db", default="ra.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest")
    a.add_argument("library")
    a.add_argument("title")
    a.add_argument("pdf")
    g = sub.add_parser("grant")
    g.add_argument("user")
    g.add_argument("library")
    q = sub.add_parser("ask")
    q.add_argument("user")
    q.add_argument("question")
    args = ap.parse_args(argv)
    con = connect(args.db)
    if args.cmd == "ingest":
        rep = ingest_pdf(con, args.library, args.title, args.pdf)
        print(rep)
        if rep["low_text_pages"]:
            print("WARNING: pages with almost no text, probably need OCR:", rep["low_text_pages"])
    elif args.cmd == "grant":
        con.execute("INSERT OR IGNORE INTO grants(user, library_id) VALUES (?,?)", (args.user, args.library))
        con.commit()
    else:
        from .llm import ClaudeLLM

        res = ask(con, ClaudeLLM(), args.user, args.question)
        if res.status == "not_found":
            print("I could not find this in the selected library.")
            for p in res.closest[:3]:
                print(f"  closest: {p.title} p.{p.page}")
            return 0
        titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM documents")}
        for i, c in enumerate(res.claims, 1):
            print(f"{i}. {c.text} [{titles[c.doc_id]}, p. {c.page}]")
        return 0


if __name__ == "__main__":
    sys.exit(main())
