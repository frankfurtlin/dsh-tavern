#!/usr/bin/env python3
"""Serve the built client with SPA fallback (subpath prefix is stripped by Caddy)."""
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT = os.environ.get("STORY_DIST", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client", "dist"))


class Handler(SimpleHTTPRequestHandler):
    server_version = "story-between-static"
    sys_version = ""

    def translate_path(self, path):
        path = urlparse(path).path
        path = unquote(path)
        parts = [p for p in path.split("/") if p not in ("", ".", "..")]
        target = os.path.join(ROOT, *parts)
        if os.path.isdir(target):
            index = os.path.join(target, "index.html")
            if os.path.isfile(index):
                return index
        if os.path.isfile(target):
            return target
        # SPA fallback for route paths without a file extension
        if "." not in os.path.basename(path):
            return os.path.join(ROOT, "index.html")
        return target

    def end_headers(self):
        if self.path.startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    addr = os.environ.get("STORY_BIND", "127.0.0.1")
    port = int(os.environ.get("STORY_PORT", "8810"))
    ThreadingHTTPServer((addr, port), partial(Handler, directory=ROOT)).serve_forever()
