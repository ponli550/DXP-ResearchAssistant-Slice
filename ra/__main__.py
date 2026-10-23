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
    w = sub.add_parser("serve")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--host", default="127.0.0.1")
    e = sub.add_parser("eval")
    e.add_argument("--questions", default="evals/questions.json")
    e.add_argument("--llm", choices=["extractive", "threshold", "claude", "hermes"], default="extractive")
    e.add_argument("--user", default="alice")
    e.add_argument("--out")
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
    elif args.cmd == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    elif args.cmd == "eval":
        import json

        from .baselines import Extractive, Threshold
        from .evalset import check_questions, load, report, score

        qs = load(args.questions)
        problems = check_questions(con, qs)
        if problems:
            print("\n".join(problems))
            return 1
        if args.llm == "claude":
            from .llm import ClaudeLLM

            llm = ClaudeLLM()
        elif args.llm == "hermes":
            from .llm import OpenAICompatLLM

            llm = OpenAICompatLLM()
        else:
            llm = Extractive() if args.llm == "extractive" else Threshold()
        res = score(con, llm, qs, args.user)
        print(report(res, args.llm))
        if args.out:
            with open(args.out, "w") as f:
                json.dump(res, f, indent=2)
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
