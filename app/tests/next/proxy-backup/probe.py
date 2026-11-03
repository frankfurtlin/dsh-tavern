"""Actual proxy header/quota and early-stream checks, not a full archive upload."""
import http.client
import json
import socket
import time


for _ in range(60):
    try:
        with socket.create_connection(("web", 80), timeout=1):
            break
    except OSError:
        time.sleep(.25)
else:
    raise RuntimeError("Owned proxy did not start")


def request(path, declared, prefix, expected):
    conn = http.client.HTTPConnection("web", 80, timeout=5)
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Length", str(declared))
        conn.putheader("Content-Type", "application/octet-stream")
        conn.endheaders()
        if prefix:
            conn.send(b"x" * prefix)
        response = conn.getresponse()
        body = response.read(4096)
        assert response.status == expected, (path, declared, response.status)
        data = json.loads(body) if expected == 202 else None
        if data is not None:
            assert data == {"path": path, "declaredBytes": declared,
                            "receivedPrefixBytes": min(declared, 1024)}, data
        return {"path": path, "declaredBytes": declared, "actuallySentBytes": prefix,
                "status": response.status, "upstream": data,
                "incompleteBodyEarlyResponse": expected == 202 and prefix < declared}
    finally:
        conn.close()


cases = [
    request("/api/books/import", 1024, 1024, 202),
    request("/api/books/import", 51 * 1024**2 + 1, 0, 413),
    request("/api/reader/v1/backups/prepare", 60 * 1024**2, 65536, 202),
    request("/api/reader/v1/backups/prepare", 2 * 1024**3 + 64 * 1024**2 + 128 + 1024, 65536, 202),
    request("/api/reader/v1/backups/prepare", 2113 * 1024**2 + 1, 0, 413),
]
print(json.dumps({"cases": cases, "databaseUsed": False, "realProviderCalls": 0,
                  "fullLargeArchiveUploaded": False, "authenticatedApiExecuted": False}))
