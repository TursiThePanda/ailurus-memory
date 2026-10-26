"""Test of the tag taxonomy: registry, enforced approval, filtering.

The point: a new tag MUST NOT come into existence just because a model made it up.
It has to be declared in `new_tags` (meaning the user approved it). Otherwise the
taxonomy fragments into variants of the same thing depending on which model happens
to be writing.
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

PW = "tags-test-passphrase"
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

    # first write — no tags exist yet, so it must go through new_tags
    s.write(content="a", description="first", agent="claude", namespace="shared",
            name="a", tags=["network"], new_tags=["network"])
    check("a new tag with approval goes through", s.known_tags() == {"network": 1})

    # an existing tag can be used without asking
    s.write(content="b", description="second", agent="claude", namespace="shared",
            name="b", tags=["network"])
    check("existing tag works without new_tags", s.known_tags() == {"network": 2})

    # unknown tag without approval = rejected
    try:
        s.write(content="c", description="third", agent="claude", namespace="shared",
                name="c", tags=["firewall"])
        check("unknown tag rejected", False)
    except ValueError as e:
        check("unknown tag rejected", "firewall" in str(e))
        check("error suggests the existing tags", "network" in str(e))
        check("error explains how to approve a tag", "new_tags" in str(e))

    # partly known set — rejected because of the unknown one
    try:
        s.write(content="d", description="fourth", agent="claude", namespace="shared",
                name="d", tags=["network", "hardware"])
        check("mix of known and unknown rejected", False)
    except ValueError as e:
        check("mix of known and unknown rejected", "hardware" in str(e))

    # update follows the same rule
    try:
        s.update(namespace="shared", name="a", agent="claude", tags=["made-up"])
        check("update with an unknown tag rejected", False)
    except ValueError:
        check("update with an unknown tag rejected", True)

    # the user path (_trusted) bypasses the check — the user is the authority
    s.update(namespace="shared", name="a", agent="user", tags=["network", "personal"],
             _trusted=True)
    check("_trusted bypasses the check (web/user)", "personal" in s.known_tags())

    # filtering
    s.write(content="e", description="fifth", agent="claude", namespace="shared",
            name="e", type="reference", tags=["network"])
    check("filter by tag", {m["name"] for m in s.list(tags=["network"])} == {"a", "b", "e"})
    check("filter by type", [m["name"] for m in s.list(type="reference")] == ["e"])
    check("filter tag AND tag", [m["name"] for m in s.list(tags=["network", "personal"])] == ["a"])
    check("search with a tag filter",
          [h["name"] for h in s.search("fifth", tags=["network"])] == ["e"])
    check("search with a type filter excludes the rest",
          s.search("first", type="reference") == [])

    # renaming and deleting a tag (user administration)
    n = s.rename_tag("network", "networking")
    check("renaming a tag sweeps all records", n == 3 and "networking" in s.known_tags())
    check("the old tag name is gone", "network" not in s.known_tags())

    # renaming onto an existing tag merges the two — record "a" carries both, so it
    # must not end up with the target twice
    s.update(namespace="shared", name="a", agent="user",
             tags=["networking", "personal"], _trusted=True)
    n = s.rename_tag("personal", "networking")
    check("renaming onto an existing tag merges", s.known_tags() == {"networking": 3})
    check("the merge does not duplicate the tag",
          s.read("shared", "a")["tags"] == ["networking"])

    n = s.delete_tag("networking")
    check("deleting a tag", n == 3 and s.known_tags() == {})

print("\n" + ("TAGS OK " if ok else "TAGS FAILED"))
sys.exit(0 if ok else 1)
