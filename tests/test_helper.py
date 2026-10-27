"""The helper is a separate layer. These tests pin that: the app never mentions it, and the proxy only adds one script tag."""
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "helper"))
import serve  # noqa: E402


class Upstream(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-App", "yes")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Upstream.seen.append(("GET", self.path, self.headers.get("Accept-Encoding")))
        if self.path.startswith("/api/"):
            return self._reply(200, b'{"ok":true}', "application/json")
        if self.path == "/nobody":
            return self._reply(200, b"<html><body>no closing", "text/html")
        if self.path == "/pdf":
            return self._reply(200, b"%PDF-1.4" + b"x" * 200000, "application/pdf")
        return self._reply(200, b"<!doctype html><html><body><h1>app</h1></body></html>", "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        Upstream.seen.append(("POST", self.path, data))
        self._reply(201, data, "application/json")


@pytest.fixture
def proxy():
    Upstream.seen = []
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    px = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler("127.0.0.1", up.server_address[1]))
    threading.Thread(target=px.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{px.server_address[1]}", up.server_address[1]
    px.shutdown()
    up.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers, r.read()


def test_the_app_never_mentions_the_helper():
    for name in ("ra/web.py", "ra/answer.py", "ra/ground.py", "ra/retrieve.py", "ra/llm.py"):
        text = open(os.path.join(ROOT, name)).read()
        assert "__helper" not in text and "helper.js" not in text, f"{name} must not know about the helper"


def test_html_gets_one_script_tag_and_nothing_else_changes(proxy):
    base, _ = proxy
    status, headers, body = get(base + "/")
    assert status == 200 and body.count(b"/__helper/helper.js") == 1
    assert body == b'<!doctype html><html><body><h1>app</h1><script src="/__helper/helper.js" defer></script></body></html>'
    assert int(headers["Content-Length"]) == len(body) and headers["X-App"] == "yes"
    assert Upstream.seen[-1][2] == "identity"  # the app is asked for plain text so the page can be read
    assert serve.inject(body) == body  # injecting twice changes nothing
    assert get(base + "/nobody")[2].endswith(b'<script src="/__helper/helper.js" defer></script>')


def test_json_and_files_pass_through_untouched_and_posts_are_forwarded(proxy):
    base, _ = proxy
    assert get(base + "/api/x")[2] == b'{"ok":true}'
    pdf = get(base + "/pdf")[2]
    assert pdf.startswith(b"%PDF") and len(pdf) == 200008 and b"helper" not in pdf
    req = urllib.request.Request(base + "/api/ask", data=json.dumps({"q": "hi"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        assert r.status == 201 and json.loads(r.read()) == {"q": "hi"}
    assert Upstream.seen[-1][0] == "POST" and json.loads(Upstream.seen[-1][2]) == {"q": "hi"}


def test_helper_assets_are_served_by_the_proxy_not_the_app(proxy):
    base, _ = proxy
    _, h, js = get(base + "/__helper/helper.js")
    assert "javascript" in h["Content-Type"] and b"__raHelper" in js and h["Cache-Control"] == "no-store"
    assert b".rah-launch" in get(base + "/__helper/helper.css")[2]
    assert not [s for s in Upstream.seen if "__helper" in s[1]]  # never forwarded


def test_a_dead_app_is_a_clear_502():
    px = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler("127.0.0.1", 1))
    threading.Thread(target=px.serve_forever, daemon=True).start()
    try:
        get(f"http://127.0.0.1:{px.server_address[1]}/")
    except urllib.error.HTTPError as e:
        assert e.code == 502 and "not reachable" in json.loads(e.read())["detail"]
    else:
        raise AssertionError("expected a 502")
    finally:
        px.shutdown()


def test_the_overlay_builds_no_html_from_strings_and_degrades_when_ids_are_missing():
    js = open(os.path.join(ROOT, "helper", "helper.js")).read()
    import re

    assert "innerHTML" not in js and "document.write" not in js and not re.search(r"\beval\(", js)
    assert js.count("act: $(") >= 6  # each action exists only when its element does
