"""TEST TOOLING, NOT A MODEL. Serves the web demo with a stand-in that first fabricates a quote and then quotes for real.

It exists so the Removed row, the retry and the steps view can be seen and checked without an API key.

    uv run --python 3.14 python tools/standin_server.py DB_PATH PORT
"""
import sys

import uvicorn

from ra.ground import Claim
from ra.web import create_app


class StandIn:
    name = "stand-in"

    def draft(self, question, passages, feedback=None):
        p = passages[0]
        if feedback is None:
            return [Claim("Output doubled in 2027.", p.doc_id, p.page, "Output doubled in 2027 across every sector")]
        quote = " ".join(p.text.split())[:110].rsplit(" ", 1)[0]
        return [Claim(quote, p.doc_id, p.page, quote)]


if __name__ == "__main__":
    db, port = sys.argv[1], int(sys.argv[2])
    uvicorn.run(create_app(db, lambda: (StandIn(), "demo stand-in")), host="127.0.0.1", port=port)
