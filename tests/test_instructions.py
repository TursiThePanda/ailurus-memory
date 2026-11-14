"""Test of the editable AI instructions (1.3.0).

Checks:
  1) the store-level rules: default until edited, saving the default clears the
     override, the length limits, the per-agent addendum, the SECURITY detector;
  2) end to end over HTTP: an agent receives the owner's text plus ITS OWN addendum and
     never another agent's, the index is still appended, and a change made while the
     server runs reaches the next connection without a restart.
"""

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from ai_memory.core.store import SecureStore
from ai_memory.features import instructions as ai

PORT = "8093"
URL = f"http://127.0.0.1:{PORT}/mcp"
PW = "throwaway-test-passphrase"

checks: list[tuple[str, bool]] = []


def check(label, ok):
    checks.append((label, bool(ok)))


@contextlib.asynccontextmanager
async def connect(key: str):
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}) as hc:
        async with streamable_http_client(URL, http_client=hc) as (r, w):
            yield r, w


async def instructions_for(key: str) -> str:
    async with connect(key) as (r, w):
        async with ClientSession(r, w) as s:
            return (await s.initialize()).instructions or ""


def unit(store: SecureStore) -> None:
    check("unit: default in force before any edit", ai.general(store) == ai.DEFAULT)
    check("unit: not custom before any edit", not ai.is_custom(store))
    check("unit: the default keeps the SECURITY paragraph", ai.security_intact(ai.DEFAULT))
    check("unit: the default carries the one-subject rule", "ONE RECORD = ONE SUBJECT" in ai.DEFAULT)

    ai.save_general(store, "Custom text.\r\nSecond line.")
    check("unit: own text saved, CRLF normalised",
          ai.general(store) == "Custom text.\nSecond line.")
    check("unit: custom flag", ai.is_custom(store))
    check("unit: security detector notices the paragraph is gone",
          not ai.security_intact(ai.general(store)))

    rewrapped = ai.DEFAULT.replace("\n", " \n  ")
    ai.save_general(store, rewrapped)
    check("unit: saving the default (re-wrapped) clears the override", not ai.is_custom(store))

    try:
        ai.save_general(store, "x" * (ai.MAX_CHARS + 1))
        check("unit: over-long general text refused", False)
    except ValueError:
        check("unit: over-long general text refused", True)

    ai.save_agent(store, "a1", "Only for a1.")
    check("unit: addendum for a1", "FOR YOU (a1)\nOnly for a1." in ai.for_agent(store, "a1"))
    check("unit: no addendum for a2", "FOR YOU" not in ai.for_agent(store, "a2"))
    try:
        ai.save_agent(store, "a1", "y" * (ai.MAX_AGENT_CHARS + 1))
        check("unit: over-long addendum refused", False)
    except ValueError:
        check("unit: over-long addendum refused", True)
    ai.forget_agent(store, "a1")
    check("unit: forget_agent removes the addendum", "FOR YOU" not in ai.for_agent(store, "a1"))
    ai.reset_general(store)
    check("unit: reset back to the default", ai.general(store) == ai.DEFAULT)


async def e2e(db: str, key_a: str, key_b: str) -> None:
    a = await instructions_for(key_a)
    b = await instructions_for(key_b)
    check("e2e: own general text reaches the agent", a.startswith("OWNER TEXT"))
    check("e2e: agent A gets its addendum", "FOR YOU (agent-a)\nA-ONLY" in a)
    check("e2e: agent B does not get A's addendum", "A-ONLY" not in b)
    check("e2e: agent B gets its own", "FOR YOU (agent-b)\nB-ONLY" in b)
    check("e2e: the index is still appended", "INDEX (1 records)" in a and "seed" in a)
    check("e2e: the default is NOT sent once overridden", "HOW TO USE IT" not in a)

    # change while the server runs: the next connection must see it, no restart
    s = SecureStore(db, PW)
    ai.reset_general(s)
    ai.save_agent(s, "agent-b", "")
    s.close()
    a2 = await instructions_for(key_a)
    b2 = await instructions_for(key_b)
    check("e2e: reset reaches the next connection", "HOW TO USE IT" in a2)
    check("e2e: A's addendum survives the reset of the general text", "A-ONLY" in a2)
    check("e2e: B's removed addendum is gone", "FOR YOU" not in b2)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="aimem-instr-")
    db = os.path.join(tmp, "memory.db")
    s = SecureStore(db, PW)
    unit(s)
    key_a = s.add_agent("agent-a", {"shared": "rw"})
    key_b = s.add_agent("agent-b", {"shared": "rw"})
    s.write(content="c", description="d", agent="agent-a", namespace="shared", name="seed")
    ai.save_general(s, "OWNER TEXT for every agent.")
    ai.save_agent(s, "agent-a", "A-ONLY")
    ai.save_agent(s, "agent-b", "B-ONLY")
    s.close()

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
        asyncio.run(e2e(db, key_a, key_b))
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    ok = all(good for _, good in checks)
    for label, good in checks:
        print(f"  {'OK  ' if good else 'FAIL'} {label}")
    print("\n" + ("AI INSTRUCTIONS OK" if ok else "TEST FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
