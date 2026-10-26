"""Test of the surgical-edit guard (the logic behind memory_edit).

The need is real: a record over the full-read limit cannot be pulled into context, so
fixing one renamed [[link]] in it had no safe route — memory_update needs the whole
content, and writing back a partially-read record would truncate it.

A blind find-and-replace is the obvious answer and the dangerous one. The guard here is
the occurrence count: if the record does not contain exactly what the caller believed,
nothing is written. These tests are almost entirely about that refusal, because a
silent over-replace in a record nobody has read is precisely the failure that would not
be noticed until the record was wrong.
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

PW = "edit-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def edit(store, ns, name, find, replace, expect=1):
    """The same logic memory_edit applies, exercised without an MCP server."""
    content = store.read(ns, name)["content"]
    found = content.count(find)
    if found != expect:
        return {"changed": False, "found": found, "expected": expect}
    store.update(namespace=ns, name=name, agent="claude",
                 content=content.replace(find, replace))
    return {"changed": True, "replacements": found}


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)
    body = ("See [[old-name]] for details.\n\n"
            "The threshold is 6 W.\n"
            "Another mention of the word threshold, deliberately.\n")
    s.write(content=body, description="d", agent="claude", namespace="shared",
            name="big", tags=["network"], new_tags=["network"])

    # --- the happy path ------------------------------------------------------
    res = edit(s, "shared", "big", "[[old-name]]", "[[new-name]]")
    check("a single expected occurrence is replaced", res["changed"])
    after = s.read("shared", "big")["content"]
    check("the link is fixed", "[[new-name]]" in after)
    check("nothing else moved", after.count("threshold") == 2 and "6 W" in after)

    # --- the guard -----------------------------------------------------------
    res = edit(s, "shared", "big", "threshold", "limit", expect=1)
    check("a wrong occurrence count changes NOTHING", not res["changed"])
    check("...and reports the real count", res["found"] == 2)
    check("...leaving the record untouched",
          s.read("shared", "big")["content"] == after)

    res = edit(s, "shared", "big", "threshold", "limit", expect=2)
    check("the caller can opt into replacing both", res["changed"])
    check("...and both are replaced",
          s.read("shared", "big")["content"].count("limit") == 2)

    res = edit(s, "shared", "big", "nowhere-in-the-text", "x")
    check("text that is not there changes nothing", not res["changed"])
    check("...and says it found none", res["found"] == 0)

    # --- editing does not disturb the rest of the record ---------------------
    s.mark_verified("shared", "big", agent="claude")
    before = s.read("shared", "big")
    edit(s, "shared", "big", "6 W", "250 W")
    now = s.read("shared", "big")
    check("an edit clears `verified`, because the text it vouched for changed",
          now["verified"] is None and before["verified"] is not None)
    check("author and creation date survive an edit",
          now["author"] == before["author"] and now["created"] == before["created"])

print("\n" + ("EDIT OK " if ok else "EDIT FAILED"))
sys.exit(0 if ok else 1)
