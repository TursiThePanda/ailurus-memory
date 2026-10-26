#!/usr/bin/env python3
"""Back up the encrypted store to another filesystem (typically the NAS).

Three things make this simpler than it looks, and one makes it subtler.

Simple: the whole memory is ONE file; the file is already encrypted, so the target
does not have to be trusted; and no passphrase is needed to copy it, which is why
this can run unattended from a timer while the server holds the key in RAM.

Subtle: the database is live and in WAL mode, so `cp` can capture a torn snapshot —
a copy that looks fine and fails to open when you need it. SQLite's backup API takes
a consistent snapshot of a database that is being written to, which is what this uses.

The copy is made and verified LOCALLY first, then moved to the destination. Running
SQLite operations directly against a CIFS mount is asking for trouble (locking over
SMB), and a half-written file on the NAS is worse than no new file at all.

    python -m ai_memory.features.backup --dest /mnt/backup/ai-memory --keep 14   # dest is REQUIRED for a NAS

Exit code is 0 only if a verified copy landed at the destination.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ai_memory.core import paths
DEFAULT_DB = str(paths.db_path())
# NOT a NAS mount any more -- see paths.backup_dest(). A NAS destination is explicit
# configuration now (AI_MEMORY_BACKUP_DEST), because a default naming one person's
# mount point is wrong on every other machine.
DEFAULT_DEST = str(paths.backup_dest())
DEFAULT_KEEP = int(os.environ.get("AI_MEMORY_BACKUP_KEEP", "14"))
PREFIX = "memory-"
SUFFIX = ".db"


def snapshot(src: str, out: Path) -> None:
    """A consistent copy of a live database, via SQLite's own backup API.

    Opened READ-WRITE on purpose, and this is not a detail. The store runs in WAL
    mode, so recent writes live in `memory.db-wal` until a checkpoint folds them in.
    A read-only connection cannot attach to the WAL's shared-memory index, so it sees
    only the last checkpointed state — the backup then succeeds, verifies, and is
    silently missing the newest records. Caught by test_backup, which writes a record
    and then looks for it in the copy.

    Nothing here writes to the source; the connection is opened, read and closed.
    """
    con = sqlite3.connect(src)
    try:
        dst = sqlite3.connect(str(out))
        try:
            con.backup(dst)
        finally:
            dst.close()
    finally:
        con.close()


def verify(path: Path) -> tuple[bool, str, int]:
    """Check the copy is a usable database — without the passphrase.

    Returns (ok, human-readable detail, record count). The count is separate from the
    text ON PURPOSE: callers that want to say "96 records" in their own wording used to
    reuse the detail string and got "96 records records" (the admin web, 2026-08-30).

    Structure is all that can be checked here, and that is the point: the payload is
    ciphertext, so a backup script that could read it would be a hole in the design.
    What CAN be established is that the file opens, passes SQLite's own integrity
    check, and still holds the encrypted rows and the wrapped key — a copy missing
    the `meta` rows would be unrecoverable no matter how intact the rest is.
    """
    try:
        # immutable=1, not just mode=ro: opening a WAL database read-only still makes
        # SQLite create `-shm` and `-wal` side files next to it. On the NAS that leaves
        # litter beside every backup it inspects, and rotation would not clean it up.
        # A finished snapshot never has a pending WAL, so declaring it immutable is
        # both safe and the point — this file is not going to change while we read it.
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as e:
        return False, f"will not open: {e}", 0
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            return False, f"integrity_check said: {ok}", 0
        n = con.execute("SELECT count(*) FROM memories").fetchone()[0]
        keys = {r[0] for r in con.execute("SELECT k FROM meta")}
        if not {"salt", "dek_nonce", "dek_ct"} <= keys:
            return False, "the wrapped master key is missing — this copy is unrecoverable", 0
        if n == 0:
            return False, "no records in the copy", 0
        return True, f"{n} records", n
    except sqlite3.Error as e:
        return False, f"unreadable: {e}", 0
    finally:
        con.close()


def rotate(dest: Path, keep: int) -> list[str]:
    """Delete the oldest copies beyond `keep`. Sorted by name, which sorts by date
    because the timestamp is ISO and fixed-width."""
    existing = sorted(p for p in dest.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())
    dropped = []
    for old in existing[:max(0, len(existing) - keep)]:
        try:
            old.unlink()
            dropped.append(old.name)
            # sweep any -wal/-shm left beside it by an older version, or by a tool that
            # opened the copy without immutable=1
            for extra in dest.glob(f"{old.name}-*"):
                extra.unlink(missing_ok=True)
        except OSError as e:
            print(f"warning: could not remove {old.name}: {e}", file=sys.stderr)
    return dropped


def run(src: str, dest_dir: str, keep: int) -> dict:
    started = datetime.now(timezone.utc)
    result = {"started": started.isoformat(timespec="seconds"), "source": src,
              "destination": None, "bytes": 0, "records": None, "kept": keep,
              "removed": [], "ok": False, "error": None}

    if not Path(src).exists():
        result["error"] = f"no database at {src}"
        return result

    dest = Path(dest_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # The usual cause is the NAS not being mounted. Say so plainly: a backup that
        # silently writes into an empty mount point on the local disk is the worst
        # possible outcome — it looks like it works and protects nothing.
        result["error"] = (f"cannot use {dest}: {e}. Is the NAS mounted?")
        return result
    if not os.access(dest, os.W_OK):
        result["error"] = f"{dest} is not writable — is the NAS mounted read-only?"
        return result

    # Second-resolution keeps the filename readable, but two runs inside the same
    # second would then collide and silently overwrite — which quietly breaks `keep`
    # (you ask for 14 copies and get 13). A suffix costs nothing and makes every run
    # produce exactly one new file.
    stamp = started.strftime("%Y-%m-%dT%H%M%SZ")
    final = dest / f"{PREFIX}{stamp}{SUFFIX}"
    # The suffix must sort AFTER the plain name, because rotation deletes by sorted
    # order and would otherwise treat the second copy of a second as the oldest file
    # in the directory. '_' (0x5F) sorts after '.' (0x2E); '-' does not. Zero-padded
    # so _02 stays before _10.
    #
    # It must also be higher than every suffix already present, not merely unused:
    # rotation frees up low numbers, so "first gap" would hand a brand-new backup an
    # old-sorting name (_02 next to a surviving _07) and the next rotation would
    # delete the newest file first.
    # The test is "does anything with this timestamp already exist", not "is this
    # exact name free". After a rotation the plain name can be free while _01.._03
    # survive; taking it back would again produce a new file that sorts before older
    # ones. Numbering only restarts once every copy of that second is gone.
    same = list(dest.glob(f"{PREFIX}{stamp}*{SUFFIX}"))
    if same:
        used = [0]
        for p in same:
            tail = p.name[len(PREFIX) + len(stamp):-len(SUFFIX)]
            if tail.startswith("_") and tail[1:].isdigit():
                used.append(int(tail[1:]))
        final = dest / f"{PREFIX}{stamp}_{max(used) + 1:02d}{SUFFIX}"

    with tempfile.TemporaryDirectory(prefix="ai-memory-backup-") as tmp:
        staged = Path(tmp) / "memory.db"
        try:
            snapshot(src, staged)
        except sqlite3.Error as e:
            result["error"] = f"snapshot failed: {e}"
            return result

        good, detail, count = verify(staged)
        if not good:
            result["error"] = f"the copy did not verify ({detail}) — nothing was written"
            return result
        result["records"] = count
        result["bytes"] = staged.stat().st_size

        # onto the NAS under a temporary name, then rename: an interrupted transfer
        # leaves a .part file rather than a truncated backup that looks legitimate
        part = dest / f".{final.name}.part"
        try:
            shutil.copyfile(staged, part)
            os.replace(part, final)
        except OSError as e:
            result["error"] = f"could not write to {dest}: {e}"
            part.unlink(missing_ok=True)
            return result

    result["destination"] = str(final)
    result["removed"] = rotate(dest, keep)
    result["ok"] = True
    result["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"source database (default: {DEFAULT_DB})")
    ap.add_argument("--dest", default=DEFAULT_DEST, help=f"destination directory (default: {DEFAULT_DEST})")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"how many copies to keep (default: {DEFAULT_KEEP})")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    res = run(args.db, args.dest, args.keep)

    if args.json:
        print(json.dumps(res, indent=2))
    elif res["ok"]:
        mb = res["bytes"] / 1_048_576
        print(f"backed up {res['records']} records ({mb:.1f} MB) -> {res['destination']}")
        if res["removed"]:
            print(f"removed {len(res['removed'])} old copies: {', '.join(res['removed'])}")
    else:
        print(f"BACKUP FAILED: {res['error']}", file=sys.stderr)

    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
