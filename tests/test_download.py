"""Test of the model download — the one piece of this project that touches a network.

It earned a test the hard way: the first version used `urllib.request.urlretrieve`,
which has no timeout. A connection that died quietly left the download hanging at
32 MiB forever, printing nothing, indistinguishable from a slow link. The rewrite
streams with a deadline, retries, and resumes from the partial file.

Everything here runs against a local HTTP server on 127.0.0.1 — no internet.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import http.server
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

from ai_memory.features import embed
ok = True
BODY = bytes(range(256)) * 400  # 102,400 bytes of recognisable content


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


class Handler(http.server.BaseHTTPRequestHandler):
    """Serves BODY, honouring Range unless told to ignore it."""
    ignore_range = False

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng and not self.ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY)-1}/{len(BODY)}")
        else:
            self.send_response(200)
        chunk = BODY[start:]
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *a):
        pass


def serve():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/model"


httpd, url = serve()

# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    quiet = lambda *a, **k: None

    # --- a plain download ---
    target = Path(tmp) / "fresh.bin"
    embed._fetch(url, target, quiet, timeout=5, attempts=2)
    check("downloads the whole file", target.read_bytes() == BODY)
    check("leaves no .part behind", not target.with_suffix(".bin.part").exists())

    # --- resuming from a partial file ---
    target = Path(tmp) / "resumed.bin"
    part = target.with_suffix(".bin.part")
    part.write_bytes(BODY[:40000])
    embed._fetch(url, target, quiet, timeout=5, attempts=2)
    check("resuming produces the identical file", target.read_bytes() == BODY)

    # --- a server that ignores Range must not corrupt the result ---
    # answering 200 to a Range request means "here is the whole thing from zero";
    # appending that to the partial file would silently produce a broken model
    Handler.ignore_range = True
    target = Path(tmp) / "ignored.bin"
    part = target.with_suffix(".bin.part")
    part.write_bytes(BODY[:40000])
    embed._fetch(url, target, quiet, timeout=5, attempts=2)
    check("a server ignoring Range restarts instead of appending",
          target.read_bytes() == BODY)
    Handler.ignore_range = False

    # --- a dead server fails with a useful message, and keeps what it has ---
    httpd.shutdown()
    target = Path(tmp) / "dead.bin"
    try:
        embed._fetch(url, target, quiet, timeout=1, attempts=2)
        check("a dead server raises", False)
    except RuntimeError as e:
        check("a dead server raises rather than hanging", True)
        check("the error says resuming is possible", "resumes" in str(e))
    check("nothing half-written is presented as the model", not target.exists())

print("\n" + ("DOWNLOAD OK " if ok else "DOWNLOAD FAILED"))
sys.exit(0 if ok else 1)
