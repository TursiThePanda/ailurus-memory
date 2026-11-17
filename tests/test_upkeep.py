"""Test of memory upkeep (features/upkeep.py) and its wiring into the MCP tools.

What it has to guarantee, in the owner's words (2026-11-17): the memory is for the AI,
so the AI keeps it in order -- safe fixes on its own, questions only when the user
brings the subject up -- and the owner merely has the option to look and overrule.
Equally important: OFF by default, nothing said to an agent while it is off, and a
decision made once is not raised again by the next agent.
"""

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sys
import tempfile

from ai_memory.core.store import SecureStore
from ai_memory.features import upkeep
from ai_memory.server import server

PW = "upkeep-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def ids(hints, check_name):
    return [h["id"] for h in hints if h["check"] == check_name]


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)
    server.STORE = s
    server.MODE_HTTP = False
    upkeep.invalidate()

    s.write(content="Runs on the box, see [[target-recrd]].", description="the router",
            agent="claude", namespace="shared", name="router")          # typo link, untagged
    s.write(content="The target.", description="a target record", agent="claude",
            namespace="shared", name="target-record", tags=["network"], new_tags=["network"])
    s.write(content="Wiring is AWAITING VERIFICATION.", description="the fan",
            agent="claude", namespace="shared", name="fan", tags=["network"])  # pending
    s.write(content="Fine.", description="a healthy record", agent="claude",
            namespace="shared", name="healthy", tags=["network"])

    # --- off by default ---
    check("off by default", upkeep.enabled(s) is False)
    check("off: memory_read carries no upkeep",
          "upkeep" not in server.memory_read("shared", "router"))
    check("off: instructions say nothing", "MEMORY UPKEEP" not in server._build_instructions())

    # --- on ---
    upkeep.set_enabled(s, True)
    check("on: instructions carry the paragraph",
          "MEMORY UPKEEP" in server._build_instructions())
    check("the paragraph limits questions to the user's own topic",
          "ONLY when the user themselves brings up" in upkeep.agent_notice(s))

    hints = server.memory_read("shared", "router").get("upkeep", [])
    link = [h for h in hints if h["check"] == "link"]
    check("a typo link is a safe fix with a suggestion",
          bool(link) and link[0]["fix"] == "safe" and link[0]["suggestion"] == "target-record")
    check("an untagged record is a safe fix", any(h["check"] == "untagged" and h["fix"] == "safe"
                                                  for h in hints))
    fan = server.memory_read("shared", "fan").get("upkeep", [])
    check("a pending marker asks the user", any(h["check"] == "pending" and h["fix"] == "ask_user"
                                                for h in fan))
    check("a healthy record carries nothing",
          "upkeep" not in server.memory_read("shared", "healthy"))
    check("never more than MAX_HINTS", len(hints) <= upkeep.MAX_HINTS)

    # --- fix first, report second (the natural order) ---
    link_id = link[0]["id"]
    out = server.memory_edit("shared", "router", "[[target-recrd]]", "[[target-record]]")
    check("the edit itself goes through", out["changed"] is True)
    check("after the fix the link is no longer offered",
          not ids(out.get("upkeep", []), "link"))
    res = server.memory_lint(resolve=[{"id": link_id, "outcome": "fixed"}])["resolve"]
    check("'fixed' is accepted after the finding is already gone", res["resolved"] == [link_id])
    check("the overview counts the fix", upkeep.summary(s)["fixed_recently"] == 1)

    # --- not_a_problem: hidden until the record changes ---
    fan_id = ids(fan, "pending")[0]
    server.memory_lint(resolve=[{"id": fan_id, "outcome": "not_a_problem"}])
    check("a decided item is not offered again",
          not ids(server.memory_read("shared", "fan").get("upkeep", []), "pending"))
    lint_out = server.memory_lint()
    check("memory_lint leaves it out and says so",
          fan_id not in [f["id"] for f in lint_out["findings"]] and lint_out["dismissed"] >= 1)
    server.memory_update("shared", "fan", content="Wiring is AWAITING VERIFICATION. Again.")
    check("not_a_problem comes back once the record changes",
          fan_id in ids(server.memory_read("shared", "fan").get("upkeep", []), "pending"))

    # --- user_said_keep: holds for good ---
    server.memory_lint(resolve=[{"id": fan_id, "outcome": "user_said_keep"}])
    server.memory_update("shared", "fan", content="Wiring is AWAITING VERIFICATION. Third.")
    check("user_said_keep survives a change of the record",
          not ids(server.memory_read("shared", "fan").get("upkeep", []), "pending"))

    # --- bad input ---
    res = server.memory_lint(resolve=[{"id": "nope", "outcome": "fixed"},
                                      {"id": fan_id, "outcome": "whatever"}])["resolve"]
    check("unknown ids and outcomes are rejected, nothing recorded",
          not res["resolved"] and len(res["rejected"]) == 2)

    # --- lint output carries id + fix ---
    check("every lint finding has an id and a fix",
          all("id" in f and "fix" in f for f in server.memory_lint()["findings"]))

    # --- owner overrules ---
    upkeep.overrule(s, fan_id, "reopen")
    check("reopen brings it back",
          fan_id in ids(server.memory_read("shared", "fan").get("upkeep", []), "pending"))
    upkeep.overrule(s, fan_id, "keep")
    check("keep from the admin is permanent",
          upkeep.decisions(s)[fan_id]["outcome"] == "user_said_keep"
          and upkeep.decisions(s)[fan_id]["by"] == "owner")

    # --- permissions (HTTP mode) ---
    s.add_agent("reader", {"shared": "r", "*": "none"})
    s.write(content="No tags here.", description="another untagged", agent="claude",
            namespace="shared", name="plain")
    server.MODE_HTTP = True
    token = server.current_agent.set("reader")
    try:
        plain = server.memory_read("shared", "plain").get("upkeep", [])
        pid = ids(plain, "untagged")[0]
        res = server.memory_lint(resolve=[{"id": pid, "outcome": "not_a_problem"}])["resolve"]
        check("an agent without write cannot decide for everyone",
              not res["resolved"] and "cannot write" in res["rejected"][0]["reason"])
    finally:
        server.current_agent.reset(token)
        server.MODE_HTTP = False

    # --- overlaps never name a record the agent cannot read ---
    from ai_memory.features import semantic
    real = semantic.overlaps
    semantic.overlaps = lambda *a, **k: [
        {"a": "shared/router", "b": "secret/thing", "score": 0.9},
        {"a": "shared/router", "b": "shared/plain", "score": 0.8}]
    try:
        found = upkeep._overlap_findings(s, None, lambda ns: ns == "shared")
        others = {f["other"] for f in found}
        check("an overlap with an unreadable namespace is dropped", "secret/thing" not in others)
        check("a readable overlap is offered both ways and asks the user",
              {"shared/plain", "shared/router"} <= others
              and all(f["fix"] == "ask_user" for f in map(upkeep._decorate, found)))
    finally:
        semantic.overlaps = real

    # --- off again: silence, but decisions are kept ---
    upkeep.set_enabled(s, False)
    check("off again: nothing rides along",
          "upkeep" not in server.memory_read("shared", "plain"))
    check("decisions survive switching off", fan_id in upkeep.decisions(s))
    s.close()

print("\nUPKEEP OK" if ok else "\nUPKEEP FAILED")
sys.exit(0 if ok else 1)
