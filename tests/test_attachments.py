"""Test of file attachments (store.attach / attachments / attachment / detach).

Two properties matter more than the happy path.

First, an attachment must never be reachable when its record is not. Attachments have
no ACL of their own — they inherit the record's — so a file that outlived a `share`
restriction, or its record's deletion, would be a hole in the per-record permissions.

Second, the bytes must survive a round trip exactly. An attachment is usually the one
copy of something that cannot be reconstructed from prose (a photo of a wired board,
a screenshot of an error that no longer reproduces), so "nearly" is not good enough.
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

PW = "attachment-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


PNG = bytes.fromhex("89504e470d0a1a0a") + os.urandom(4096)  # header + noise

# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)
    s.write(content="A record with a picture.", description="d", agent="claude",
            namespace="shared", name="thing", tags=["network"], new_tags=["network"])

    # --- attach / list / get -------------------------------------------------
    meta = s.attach("shared", "thing", data=PNG, filename="board.png", agent="claude",
                    mime="image/png", description="the wired board")
    check("attaching returns metadata", meta["filename"] == "board.png")
    check("size is recorded", meta["bytes"] == len(PNG))
    check("a checksum is recorded", len(meta["sha256"]) == 64)
    check("the author is recorded", meta["author"] == "claude")

    listed = s.attachments("shared", "thing")
    check("the attachment is listed", len(listed) == 1)
    check("listing carries no bytes", "data" not in listed[0])

    got_meta, got_data = s.attachment("shared", "thing", meta["id"])
    check("the bytes come back EXACTLY", got_data == PNG)
    check("metadata comes back with them", got_meta["description"] == "the wired board")

    # --- encryption ----------------------------------------------------------
    # The whole point of this store is that the disk holds ciphertext. A filename is as
    # revealing as a description, so neither may be readable in the file.
    raw = open(os.path.join(tmp, "memory.db"), "rb").read()
    check("the attached bytes are not on disk in the clear", PNG[8:] not in raw)
    check("the filename is not on disk in the clear", b"board.png" not in raw)

    # --- several attachments -------------------------------------------------
    second = s.attach("shared", "thing", data=b"schematic", filename="wiring.pdf",
                      agent="claude", mime="application/pdf")
    check("a record can hold several attachments",
          len(s.attachments("shared", "thing")) == 2)
    check("ids are distinct", second["id"] != meta["id"])

    # --- detach --------------------------------------------------------------
    s.detach("shared", "thing", second["id"], agent="claude")
    left = s.attachments("shared", "thing")
    check("detaching removes exactly one", [a["id"] for a in left] == [meta["id"]])

    gone = False
    try:
        s.attachment("shared", "thing", second["id"])
    except FileNotFoundError:
        gone = True
    check("a detached attachment cannot be read", gone)

    # --- attachments follow the record's visibility ---------------------------
    s.write(content="secret", description="d", agent="claude", namespace="shared",
            name="private", tags=["network"], _share=["other-agent"])
    s.attach("shared", "private", data=b"x", filename="x.txt", agent="claude",
             _trusted=True)

    hidden = False
    try:
        s.attachments("shared", "private", agent="claude")
    except FileNotFoundError:
        hidden = True
    check("an agent excluded by `share` cannot even list the attachments", hidden)
    check("...and gets the same error as a missing record, so it cannot be probed",
          hidden)
    check("the user (agent=None) still can",
          len(s.attachments("shared", "private", agent=None)) == 1)

    # --- deleting the record takes its files with it -------------------------
    before = s.attachment_totals()["count"]
    s.delete("shared", "private", agent="claude", _trusted=True)
    check("deleting a record deletes its attachments",
          s.attachment_totals()["count"] == before - 1)

    # --- a move must NOT lose them -------------------------------------------
    # memory_move is write-then-delete, and delete drops attachments, so without
    # move_attachments a promotion to `shared` would silently throw the files away.
    s.write(content="moving house", description="d", agent="claude",
            namespace="claude", name="movable", tags=["network"])
    s.attach("claude", "movable", data=b"important", filename="keep.txt", agent="claude")
    s.write(content="moving house", description="d", agent="claude",
            namespace="shared", name="movable", tags=["network"], _trusted=True)
    s.move_attachments("claude", "movable", "shared", "movable")
    s.delete("claude", "movable", agent="claude")
    survived = s.attachments("shared", "movable")
    check("a moved record keeps its attachments", len(survived) == 1)
    _, data = s.attachment("shared", "movable", survived[0]["id"])
    check("...with their bytes intact", data == b"important")

    # --- totals --------------------------------------------------------------
    totals = s.attachment_totals()
    check("totals report a count and a size",
          totals["count"] == 2 and totals["bytes"] > len(PNG))

    # --- attaching to a record that does not exist ---------------------------
    refused = False
    try:
        s.attach("shared", "no-such-record", data=b"x", filename="x", agent="claude")
    except FileNotFoundError:
        refused = True
    check("cannot attach to a record that does not exist", refused)

print("\n" + ("ATTACHMENTS OK " if ok else "ATTACHMENTS FAILED"))
sys.exit(0 if ok else 1)
