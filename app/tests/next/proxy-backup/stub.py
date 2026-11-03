"""Synthetic HTTP sink: no account, database, credential or model requests."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        prefix = self.rfile.read(min(size, 1024))
        body = json.dumps({"path": self.path, "declaredBytes": size,
                           "receivedPrefixBytes": len(prefix)}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *_):
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Sink).serve_forever()
