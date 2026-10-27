"""Run the helper in front of the app without touching the app.

    uv run --python 3.14 python helper/serve.py --app http://127.0.0.1:8765 --port 8770

It is a small reverse proxy. Every request goes to the app unchanged; an HTML response gets one script tag added before
</body>; /__helper/helper.js and /__helper/helper.css are served from this folder. The app does not know it is there:
point a browser (or a tunnel) at the helper's port instead of the app's. Standard library only.
"""
import argparse
import http.client
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = {
    "/__helper/helper.js": ("helper.js", "text/javascript; charset=utf-8"),
    "/__helper/helper.css": ("helper.css", "text/css; charset=utf-8"),
}
SNIPPET = b'<script src="/__helper/helper.js" defer></script>'
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
BODY_END = re.compile(rb"</body\s*>", re.I)


def inject(html):
    """Add the helper's script tag before </body>, or at the end when the page has none. Idempotent."""
    if SNIPPET in html:
        return html
    m = None
    for m in BODY_END.finditer(html):
        pass
    return html[: m.start()] + SNIPPET + html[m.start():] if m else html + SNIPPET


def make_handler(host, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # one quiet line per request
            sys.stderr.write("%s %s\n" % (self.command, self.path))

        def _send(self, status, body, ctype, extra=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _proxy(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ASSETS:
                name, ctype = ASSETS[path]
                with open(os.path.join(HERE, name), "rb") as f:
                    return self._send(200, f.read(), ctype, [("Cache-Control", "no-store")])
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP | {"host", "accept-encoding"}}
            headers["Host"] = f"{host}:{port}"
            headers["Accept-Encoding"] = "identity"  # so an HTML page can be read and a script tag added
            try:
                conn = http.client.HTTPConnection(host, port, timeout=300)
                conn.request(self.command, self.path, body, headers)
                resp = conn.getresponse()
            except OSError as e:
                msg = ('{"detail":"The app is not reachable at %s:%s (%s)."}' % (host, port, type(e).__name__)).encode()
                return self._send(502, msg, "application/json")
            ctype = resp.getheader("Content-Type", "")
            if ctype.startswith("text/html") and self.command != "HEAD":
                data = inject(resp.read())
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP_BY_HOP | {"content-length", "content-encoding"}:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return conn.close()
            self.send_response(resp.status)
            has_length = False
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP:
                    continue
                has_length = has_length or k.lower() == "content-length"
                self.send_header(k, v)
            if not has_length:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            conn.close()

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _proxy

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reverse proxy that adds the helper overlay to the app's pages.")
    ap.add_argument("--app", default="http://127.0.0.1:8765", help="the app's address")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args(argv)
    u = urllib.parse.urlsplit(args.app)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(u.hostname, u.port or 80))
    print(f"helper on http://{args.host}:{args.port}  ->  {args.app}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
