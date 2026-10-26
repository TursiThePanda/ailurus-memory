"""E2E test of the secured HTTP server over the encrypted store.
Starts the server as a subprocess (temp DB + throwaway passphrase) and checks auth + ACL."""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import contextlib

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from ai_memory.core.store import SecureStore

def _free_port() -> str:
    """A port nobody is using, asked of the OS rather than picked by hand.

    It was 8099, and on 2026-08-30 that was busy for an unrelated reason -- the test
    then failed deep inside an MCP call with "Server returned an error response", which
    says nothing about a port. A test that can fail for a reason unrelated to the code
    is a test that gets ignored the day it is right."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])


PORT = _free_port()
URL = f"http://127.0.0.1:{PORT}/mcp"


# mcp 2.x: streamable_http_client() lost its `headers=` argument and no longer yields a
# session-id callback (2-tuple now, not 3). Headers go through a pre-built http client --
# and one the CALLER supplies is deliberately not closed by the SDK, so this owns it.
@contextlib.asynccontextmanager
async def connect(key: str | None = None):
    headers = {"Authorization": f"Bearer {key}"} if key else None
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(URL, http_client=http_client) as (r, w):
            yield r, w
PW = "throwaway-test-passphrase"


def payload(res):
    if res.structured_content is not None:
        return res.structured_content
    return json.loads(res.content[0].text)


async def run_client(no_key_first, key):
    # without a key -> 401
    try:
        async with connect() as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
        print("  NO-KEY REQUEST SUCCEEDED (WRONG!)"); return False
    except Exception:
        print("  request without a key rejected OK")
    # with a key
    ok = True
    async with connect(key) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            hits = payload(await s.call_tool("memory_search", {"query": "seed"}))
            hits = hits.get("result", hits) if isinstance(hits, dict) else hits
            print("  search sees shared:", [h["name"] for h in hits])
            w1 = payload(await s.call_tool("memory_write",
                {"content": "from the local agent", "description": "own write"}))
            print("  write into own ns:", w1["namespace"])
            res = await s.call_tool("memory_write",
                {"content": "x", "description": "pokus", "namespace": "claude-http"})
            denied = res.is_error or "access denied" in (res.content[0].text if res.content else "")
            print("  write into foreign 'claude-http':", "DENIED OK" if denied else "SUCCEEDED (WRONG!)")
            ok = ok and denied
    return ok


def one_origin_checks() -> bool:
    """Since 1.2.0 the admin web and MCP share the port: / is the admin, /mcp is MCP."""
    import urllib.error
    import urllib.request
    base = f"http://127.0.0.1:{PORT}"
    ok = True
    with urllib.request.urlopen(base + "/healthz") as r:
        good = r.status == 200
        print("  /healthz answers 200:", "OK" if good else "WRONG"); ok &= good
    with urllib.request.urlopen(base + "/login") as r:
        body = r.read().decode()
        good = r.status == 200 and "csrf" in body
        print("  admin login page on the same port:", "OK" if good else "WRONG"); ok &= good
        good = r.headers.get("X-Frame-Options") == "DENY"
        print("  admin pages carry security headers:", "OK" if good else "WRONG"); ok &= good
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/mcp", method="POST", data=b"{}",
                                                      headers={"Content-Type": "application/json"}))
        good = False
    except urllib.error.HTTPError as e:
        good = e.code == 401 and e.headers.get("WWW-Authenticate") == "Bearer"
    print("  401 carries WWW-Authenticate: Bearer:", "OK" if good else "WRONG"); ok &= good
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/login",
                                                      headers={"Host": "evil.example"}))
        good = False
    except urllib.error.HTTPError as e:
        good = e.code == 421
    print("  a foreign Host header is refused (421):", "OK" if good else "WRONG"); ok &= good
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="aimem-http-")
    db = os.path.join(tmp, "memory.db")
    # create the DB + agent + seed data
    s = SecureStore(db, PW)
    key = s.add_agent("lmstudio-local", {"lmstudio-local": "rw", "shared": "rw", "*": "none"})
    s.add_agent("claude-http", {"claude-http": "rw", "shared": "rw", "*": "r"})
    s.write(content="seed obsah", description="seed", agent="claude-http", namespace="shared", name="seed")
    s.db.close()

    # The network settings are seeded from the environment on first start, so a test
    # run inside the Docker image (which sets AI_MEMORY_TLS=self-signed, HOST=0.0.0.0)
    # would get an HTTPS server on all interfaces. Pin what the test talks to.
    seed = {k: v for k, v in os.environ.items()
            if (not k.startswith("AI_MEMORY_") or k == "AI_MEMORY_MODEL_DIR")
            and k not in ("HOST", "PORT")}
    env = {**seed, "AI_MEMORY_TLS": "off", "AI_MEMORY_HOST": "127.0.0.1",
           "AI_MEMORY_DATA_DIR": tmp, "AI_MEMORY_CONFIG_DIR": os.path.join(tmp, "config"),
           "AI_MEMORY_DB": db, "AI_MEMORY_PASSPHRASE": PW,
           "TRANSPORT": "streamable-http", "HOST": "127.0.0.1", "PORT": PORT}
    proc = subprocess.Popen([sys.executable, "-m", "ai_memory.server.server"], env=env,
                            cwd=_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for the port
        import socket
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", int(PORT)), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        ok = asyncio.run(run_client(True, key))
        ok = one_origin_checks() and ok
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    print("\n" + ("HTTP E2E OK " if ok else "HTTP E2E SELHAL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
