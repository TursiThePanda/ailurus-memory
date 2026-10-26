"""Test of the encrypted store: CRUD, cross-agent, history/revert, auth/ACL,
and above all — that there is NO plaintext on disk."""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import tempfile

from ai_memory.core.store import SecureStore, WrongPassphrase

# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    db = os.path.join(tmp, "memory.db")
    PW = "spravne-master-heslo"
    s = SecureStore(db, PW)

    # write (claude into shared) with secret content
    SECRET = "SECRET-CONTENT-must-not-appear-in-file"
    s.write(content=SECRET, description="Moje grafika", agent="claude",
            namespace="shared", name="my-gpu", type="fact", tags=["hw"], new_tags=["hw"])
    s.write(content="LM Studio na 1234", description="endpoint", agent="opencode",
            namespace="shared", name="lmstudio")
    print("write OK")

    # cross-agent read + update with provenance
    got = s.read("shared", "my-gpu")
    assert SECRET in got["content"] and got["author"] == "claude"
    upd = s.update(namespace="shared", name="my-gpu", agent="opencode",
                   description="Moje GPU (upraveno)")
    assert upd["author"] == "claude" and upd["updated_by"] == "opencode"
    print("cross-agent read/update + provenience OK")

    # search / list
    hits = s.search("grafika hw")
    assert hits and hits[0]["name"] == "my-gpu", hits
    assert len(s.list("shared")) == 2
    print("search/list OK")

    # historie + revert
    s.update(namespace="shared", name="my-gpu", agent="claude", content="verze 2")
    s.update(namespace="shared", name="my-gpu", agent="claude", content="verze 3")
    h = s.history("shared", "my-gpu")
    assert len(h) >= 4, h  # write + update(desc) + v2 + v3
    print("record history:", len(h), "actions:", [e["action"] for e in h])
    # revert to the very first version (SECRET content)
    s.revert("shared", "my-gpu", 0, agent="claude")
    assert SECRET in s.read("shared", "my-gpu")["content"]
    print("revert na v0 OK (obsah obnoven)")

    # wrong passphrase
    s.db.close()
    try:
        SecureStore(db, "spatne-heslo")
        raise SystemExit("WRONG PASSPHRASE WAS ACCEPTED!")
    except WrongPassphrase:
        print("wrong passphrase rejected OK")
    s = SecureStore(db, PW)

    # agents / keys / ACL
    kc = s.add_agent("claude-http", {"claude-http": "rw", "shared": "rw", "*": "r"})
    kl = s.add_agent("lmstudio-local", {"lmstudio-local": "rw", "shared": "rw", "*": "none"})
    assert s.verify_key(kc) == "claude-http"
    assert s.verify_key(kl) == "lmstudio-local"
    assert s.verify_key("aim_spatny") is None
    assert s.can("claude-http", "shared", "w")
    assert s.can("claude-http", "lmstudio-local", "r")
    assert not s.can("lmstudio-local", "claude-http", "r")   # * = none
    assert not s.can(None, "shared", "r")
    kc2 = s.rotate_key("claude-http")
    assert s.verify_key(kc) is None and s.verify_key(kc2) == "claude-http"
    print("auth/keys/ACL/rotation OK")

    # === PASSPHRASE CHANGE (envelope encryption: re-wraps the DEK, data untouched) ===
    NEWPW = "nove-master-heslo-2"
    s.change_passphrase(NEWPW)
    s.db.close()
    try:
        SecureStore(db, PW)  # the old password must no longer work
        raise SystemExit("OLD PASSWORD STILL WORKED AFTER THE CHANGE!")
    except WrongPassphrase:
        print("after change: old password rejected OK")
    s = SecureStore(db, NEWPW)  # the new password works and the data is intact
    assert SECRET in s.read("shared", "my-gpu")["content"]
    assert s.verify_key(kc2) == "claude-http"
    print("after change: new password works, data and keys intact OK")
    s.db.close()

    # === PROOF OF ENCRYPTION: the raw file contains NO plaintext ===
    raw = open(db, "rb").read()
    assert SECRET.encode() not in raw, "CONTENT IS READABLE IN THE FILE!"
    assert b"Moje grafika" not in raw, "DESCRIPTION IS READABLE IN THE FILE!"
    assert b"my-gpu" not in raw, "NAME IS READABLE IN THE FILE!"
    assert kc2.encode() not in raw, "API KEY IS READABLE IN THE FILE!"
    print("no plaintext on disk (content/description/name/key) OK")

print("\nALL PASSED")
