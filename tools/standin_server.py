"""TEST TOOLING, NOT A MODEL. Serves the web demo with a stand-in that first fabricates a quote and then quotes for real.

It exists so the removed claim, the retry and the live progress can be seen and checked without an API key. The stand-in
takes as long as a real model would (DELAY seconds, default 3) so the progress lines have something to show.

    uv run --python 3.14 python tools/standin_server.py DB_PATH PORT [DELAY]
"""
import sys
import time

import uvicorn

from ra.ground import Claim
from ra.web import create_app


class StandIn:
    name = "stand-in"

    def __init__(self, delay=3.0):
        self.delay = delay

    def draft(self, question, passages, feedback=None):
        time.sleep(self.delay)
        p = passages[0]
        if feedback is None:
            return [Claim("Output doubled in 2027.", p.doc_id, p.page, "Output doubled in 2027 across every sector")]
        quote = " ".join(p.text.split())[:110].rsplit(" ", 1)[0]
        return [Claim(quote, p.doc_id, p.page, quote)]


if __name__ == "__main__":
    db, port = sys.argv[1], int(sys.argv[2])
    delay = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    uvicorn.run(create_app(db, lambda: (StandIn(delay), "demo stand-in")), host="127.0.0.1", port=port)
