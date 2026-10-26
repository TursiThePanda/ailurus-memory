"""Test of backup.py.

The failure mode a backup script has to be built against is not "it crashed" — that
one announces itself. It is the copy that exists, looks the right size, and does not
open when you finally need it. So most of this checks that a bad copy is never left
behind and never counted as success.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from ai_memory.features import backup
from ai_memory.core.store import SecureStore

PW = "backup-test-passphrase"
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
    tmp = Path(tmp)
    db = tmp / "memory.db"
    s = SecureStore(str(db), PW)
    s.write(content="Something worth keeping.", description="a record", agent="claude",
            namespace="shared", name="keeper", tags=["network"], new_tags=["network"])

    dest = tmp / "nas" / "ai-memory"

    # --- the happy path ------------------------------------------------------
    res = backup.run(str(db), str(dest), keep=3)
    check("a backup succeeds", res["ok"] and res["error"] is None)
    check("the destination is created if missing", dest.is_dir())
    copies = list(dest.glob("memory-*.db"))
    check("exactly one copy is written", len(copies) == 1)
    check("the copy is not empty", copies[0].stat().st_size > 0)
    check("no .part file is left behind", not list(dest.glob(".*.part")))

    # --- the copy is genuinely usable ----------------------------------------
    reopened = SecureStore(str(copies[0]), PW)
    check("the copy opens with the same passphrase",
          reopened.read("shared", "keeper")["content"] == "Something worth keeping.")
    # Close it, and close every store opened on a backup below. On Windows an open file
    # cannot be deleted, so leaving these open made rotate() unable to remove the copies
    # and the rotation checks failed -- against perfectly correct rotation code.
    reopened.close()

    wrong = False
    try:
        SecureStore(str(copies[0]), "not-the-passphrase").close()
    except Exception:
        wrong = True
    check("the copy is still encrypted — a wrong passphrase fails", wrong)

    # --- verification --------------------------------------------------------
    good, detail, count = backup.verify(copies[0])
    check("verify() passes a real copy", good)
    # the count is returned separately from the text, so a caller wanting its own
    # wording does not end up saying "96 records records" — the admin web did
    check("verify() returns the record count as a number", isinstance(count, int) and count > 0)

    junk = tmp / "junk.db"
    junk.write_bytes(b"this is not a database")
    good, detail, count = backup.verify(junk)
    check("verify() rejects a file that is not a database", not good)
    check("...and the count is zero, not a stale number", count == 0)

    # A copy missing the wrapped key is the nastiest case: SQLite says it is fine and
    # the data is there, but nothing can ever decrypt it again.
    keyless = tmp / "keyless.db"
    backup.snapshot(str(db), keyless)
    con = sqlite3.connect(str(keyless))
    con.execute("DELETE FROM meta WHERE k='dek_ct'")
    con.commit()
    con.close()
    good, detail, count = backup.verify(keyless)
    check("verify() rejects a copy whose wrapped key is missing", not good)
    check("...and says why", "unrecoverable" in detail)

    # --- rotation ------------------------------------------------------------
    for _ in range(4):
        backup.run(str(db), str(dest), keep=3)
    names = sorted(p.name for p in dest.glob("memory-*.db"))
    check("rotation keeps exactly `keep` copies", len(names) == 3)
    check("rotation deletes the OLDEST, keeping the newest",
          names == sorted(names)[-3:])

    # --- failure modes -------------------------------------------------------
    missing = backup.run(str(tmp / "nope.db"), str(dest), keep=3)
    check("a missing source fails loudly", not missing["ok"] and "no database" in missing["error"])
    check("a missing source writes nothing", len(list(dest.glob("memory-*.db"))) == 3)

    # The dangerous one: destination unavailable (NAS not mounted). It must fail,
    # not quietly write to the local disk.
    blocked = tmp / "blocked"
    blocked.write_text("I am a file, not a directory")
    res = backup.run(str(db), str(blocked / "sub"), keep=3)
    check("an unusable destination fails instead of writing somewhere else",
          not res["ok"] and res["error"] is not None)
    check("the failure mentions the mount, since that is the usual cause",
          "mounted" in (res["error"] or ""))

    # --- backing up a live database ------------------------------------------
    # The real case: the server is running and holds the file open in WAL mode.
    s.write(content="Written while the store is open.", description="live write",
            agent="claude", namespace="shared", name="live", tags=["network"])
    res = backup.run(str(db), str(dest), keep=9)
    check("a live, open database can be backed up", res["ok"])
    newest = Path(res["destination"])
    # Rotation deletes by sorted name, so the file just written must sort last.
    # It did not, before this was fixed: after a rotation freed the low suffixes, a
    # new backup took an old-sorting name and would have been deleted first.
    check("the newest backup sorts last, so rotation cannot eat it",
          sorted(dest.glob("memory-*.db"))[-1] == newest)
    live_copy = SecureStore(str(newest), PW)
    check("the snapshot contains writes made moments earlier",
          live_copy.read("shared", "live")["content"] == "Written while the store is open.")
    live_copy.close()
    s.close()

print("\n" + ("BACKUP OK " if ok else "BACKUP FAILED"))
sys.exit(0 if ok else 1)
