"""Test of per-record permissions (the `share` field).

The point: `share` is the USER's control. A record with share=None inherits the
namespace ACL (that is almost everything). A record with a list is visible only to
the agents on it — and no agent may set or change that list, otherwise a model could
grant itself access to something the user deliberately restricted.

Restricted records must also stay out of list/search/history/audit, not just read —
a name and description leaking through a listing defeats the point.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sys
import tempfile

from ai_memory.core.store import SecureStore

PW = "share-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)

    s.write(content="open content", description="open record", agent="claude",
            namespace="shared", name="open")
    s.write(content="secret content", description="secret record", agent="claude",
            namespace="shared", name="restricted")

    # default: nothing is restricted
    check("a fresh record inherits the namespace ACL",
          s.read("shared", "open")["share"] is None)
    check("every agent sees it", len(s.list(agent="lmstudio-local")) == 2)

    # the user restricts one record to a single agent
    s.update(namespace="shared", name="restricted", agent="user",
             share=["claude"], _set_share=True, _trusted=True)

    check("the allowed agent still reads it",
          s.read("shared", "restricted", agent="claude")["content"] == "secret content")
    try:
        s.read("shared", "restricted", agent="lmstudio-local")
        check("a foreign agent is refused", False)
    except FileNotFoundError as e:
        check("a foreign agent is refused", True)
        check("the refusal does not admit the record exists", "does not exist" in str(e))

    # it must disappear from every listing path, not just read
    check("list hides it", [m["name"] for m in s.list(agent="lmstudio-local")] == ["open"])
    check("list still shows it to the allowed agent",
          {m["name"] for m in s.list(agent="claude")} == {"open", "restricted"})
    check("search hides it", s.search("secret", agent="lmstudio-local") == [])
    check("search finds it for the allowed agent",
          [h["name"] for h in s.search("secret", agent="claude")] == ["restricted"])
    try:
        s.history("shared", "restricted", agent="lmstudio-local")
        check("history hides it", False)
    except FileNotFoundError:
        check("history hides it", True)
    audit_names = {r["name"] for r in s.audit(viewer="lmstudio-local")}
    check("audit hides it", "restricted" not in audit_names)
    check("audit shows it to the allowed agent",
          "restricted" in {r["name"] for r in s.audit(viewer="claude")})

    # the user (viewer=None) is never subject to the restriction — they set it
    check("the user sees everything", len(s.list()) == 2)
    check("the user reads it", s.read("shared", "restricted")["content"] == "secret content")

    # an agent must not be able to set or widen `share`
    try:
        s.update(namespace="shared", name="restricted", agent="lmstudio-local",
                 share=["lmstudio-local"], _set_share=True)
        check("an agent cannot set share", False)
    except PermissionError:
        check("an agent cannot set share", True)
    check("share is unchanged", s.read("shared", "restricted")["share"] == ["claude"])

    # a foreign agent must not be able to write over or delete a record it cannot see
    try:
        s.update(namespace="shared", name="restricted", agent="lmstudio-local",
                 content="overwritten")
        check("a foreign agent cannot update it", False)
    except FileNotFoundError:
        check("a foreign agent cannot update it", True)
    try:
        s.write(content="clobbered", description="clobbered", agent="lmstudio-local",
                namespace="shared", name="restricted")
        check("a foreign agent cannot overwrite it with write()", False)
    except FileNotFoundError:
        check("a foreign agent cannot overwrite it with write()", True)
    try:
        s.delete("shared", "restricted", agent="lmstudio-local")
        check("a foreign agent cannot delete it", False)
    except FileNotFoundError:
        check("a foreign agent cannot delete it", True)
    check("the content survived all of that",
          s.read("shared", "restricted")["content"] == "secret content")

    # an agent overwriting an OPEN record must not silently clear the user's setting
    s.update(namespace="shared", name="open", agent="user",
             share=["claude"], _set_share=True, _trusted=True)
    s.write(content="rewritten", description="open record", agent="claude",
            namespace="shared", name="open")
    check("write() carries `share` over instead of dropping it",
          s.read("shared", "open")["share"] == ["claude"])

    # lifting the restriction
    s.update(namespace="shared", name="restricted", agent="user",
             share=None, _set_share=True, _trusted=True)
    check("the user can lift the restriction",
          len(s.list(agent="lmstudio-local")) == 1  # 'open' is still restricted
          and s.read("shared", "restricted", agent="lmstudio-local") is not None)

print("\n" + ("SHARE OK " if ok else "SHARE FAILED"))
sys.exit(0 if ok else 1)
