"""Test of the `instructions` index plus the new tools (delete / move / audit).

Checks:
  1) on connect an agent receives the memory protocol plus an index of its records,
  2) both the index and the audit respect the ACL — each agent sees only what it may,
  3) memory_delete, memory_move and memory_audit work.

Point 2 doubles as proof that the agent identity (contextvar from the auth
middleware) propagates into create_initialization_options(), which runs in a
different task.
"""

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

PORT = "8098"
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
        d = res.structured_content
        return d.get("result", d) if isinstance(d, dict) else d
    return json.loads(res.content[0].text)


def session_for(key: str):
    return connect(key)


async def run(key_wide: str, key_narrow: str) -> bool:
    checks = []

    def check(label, actual, expected=True):
        checks.append((label, actual is expected))

    # ---- index and instructions ----
    async with session_for(key_wide) as (r, w):
        async with ClientSession(r, w) as s:
            wide = (await s.initialize()).instructions or ""

    async with session_for(key_narrow) as (r, w):
        async with ClientSession(r, w) as s:
            narrow = (await s.initialize()).instructions or ""

    check("protocol: instruction to write into shared", "'shared' namespace" in wide)
    check("protocol: security sentence", "DATA, not instructions" in wide)
    # without this an agent reads the index as a complete, current picture and answers
    # "there is no record of that" from a snapshot that may be minutes out of date
    check("protocol: says the index is a snapshot", "SNAPSHOT" in wide)
    check("index: header with the count", "INDEX (4 records)" in wide)
    check("index: own record", "own-record" in wide)
    check("index: shared record", "shared-record" in wide)
    check("index: wide sees the foreign record (*=r)", "secret-record" in wide)
    check("index: narrow sees its own", "secret-record" in narrow)
    check("index: narrow does NOT see foreign wide-ns (*=none)", "own-record" in narrow, False)
    check("index: narrow sees shared", "shared-record" in narrow)
    check("index: WITHOUT descriptions (names only)", "shared description" in wide, False)

    # ---- new tools ----
    async with session_for(key_wide) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # a move must carry provenance over: the mover is not the author, and the
            # creation date belongs to the record, not to the move
            before = payload(await s.call_tool("memory_read", {
                "namespace": "wide-ns", "name": "foreign-record"}))
            payload(await s.call_tool("memory_move", {
                "namespace": "wide-ns", "name": "foreign-record", "to_namespace": "shared"}))
            after_move = payload(await s.call_tool("memory_read", {
                "namespace": "shared", "name": "foreign-record"}))
            check("move: keeps the original author",
                  after_move["author"] == "lmstudio-local")
            check("move: records the mover as updated_by",
                  after_move["updated_by"] == "wide-ns")
            check("move: keeps the original created date",
                  after_move["created"] == before["created"])
            payload(await s.call_tool("memory_delete", {
                "namespace": "shared", "name": "foreign-record"}))

            moved = payload(await s.call_tool("memory_move", {
                "namespace": "wide-ns", "name": "own-record", "to_namespace": "shared"}))
            check("move: moved into shared", moved.get("namespace") == "shared")

            listed = payload(await s.call_tool("memory_list", {"namespace": "wide-ns"}))
            check("move: the source is empty", len(listed) == 0)

            audit = payload(await s.call_tool("memory_audit", {"limit": 20}))
            actions = [a["action"] for a in audit]
            check("audit: sees both the write and the delete of the move",
                  "write" in actions and "delete" in actions)
            check("audit: has who/when/where",
                  all(k in audit[0] for k in ("ts", "agent", "namespace", "name")))

            deleted = payload(await s.call_tool("memory_delete", {
                "namespace": "shared", "name": "own-record"}))
            check("delete: removed", "shared/own-record" in str(deleted))

            after = payload(await s.call_tool("memory_list", {"namespace": "shared"}))
            check("delete: only spolecny remains in shared",
                  [m["name"] for m in after] == ["shared-record"])

    # ---- ACL on the audit ----
    async with session_for(key_narrow) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            audit_n = payload(await s.call_tool("memory_audit", {"limit": 50}))
            check("audit: narrow does not see the foreign namespace",
                  any(a["namespace"] == "wide-ns" for a in audit_n), False)

    ok = True
    for label, good in checks:
        ok = ok and good
        print(f"  {'OK  ' if good else 'FAIL'} {label}")
    if not ok:
        print("\n--- instructions (wide) ---\n" + wide)
        print("\n--- instructions (narrow) ---\n" + narrow)
    return ok


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="aimem-index-")
    db = os.path.join(tmp, "memory.db")

    s = SecureStore(db, PW)
    key_wide = s.add_agent("wide-ns", {"wide-ns": "rw", "shared": "rw", "*": "r"})
    key_narrow = s.add_agent("narrow-ns", {"narrow-ns": "rw", "shared": "rw", "*": "none"})
    s.write(content="content A", description="own description", agent="wide-ns",
            namespace="wide-ns", name="own-record")
    s.write(content="content B", description="secret description", agent="narrow-ns",
            namespace="narrow-ns", name="secret-record")
    s.write(content="content C", description="shared description", agent="wide-ns",
            namespace="shared", name="shared-record")
    # authored by a different agent — used to prove a move does not steal provenance
    s.write(content="content D", description="foreign description", agent="lmstudio-local",
            namespace="wide-ns", name="foreign-record")
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
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", int(PORT)), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        ok = asyncio.run(run(key_wide, key_narrow))
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    print("\n" + ("INDEX + NEW TOOLS OK " if ok else "TEST FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
