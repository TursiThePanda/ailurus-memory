"""Restore the memory from a backup or export file, while it runs.

Backups and exports have always got the memory OUT; until 1.5.0 the way back IN was to
stop the service and overwrite `memory.db` by hand -- shell access to the machine, and
in Docker an easy place to get the volume path, the owner or the WAL side files wrong.

WHAT A RESTORE IS: the file's content REPLACES this memory. It is not a merge. Records,
history, attachments, vectors, tags, agents with their API keys and the AI instructions
all come from the file. Two settings do not -- see LOCAL_SETTINGS.

THE PASSPHRASE STAYS THE SAME. The file is opened with its own passphrase and its data
key is then re-wrapped with THIS memory's passphrase (the data itself is not
re-encrypted -- `change_passphrase` only rewrites the 32-byte key wrapper). So after a
restore the memory opens exactly as before: a Docker secret, the passphrase remembered
in the OS credential store and any unlock routine keep working. Restoring with the
file's passphrase instead would make the next start ask for a passphrase nobody on this
machine may know.

NO RESTART. The prepared copy is poured into the LIVE connection with SQLite's backup
API, under the store's lock, and the store re-reads its key. The MCP server, the admin
web, the auth middleware and both schedulers hold the same store object, so all of them
see the restored memory at once. Swapping the file on disk and restarting was rejected:
on Windows the open file cannot be replaced, and a container without a passphrase
secret would come back LOCKED.

Two steps, and the live memory is not touched before the second:
  1. `prepare()`: check the file, open it with its passphrase, re-wrap it to this
     memory's passphrase -- all on a STAGED COPY -- and say what is inside. It returns
     the file's data key too.
  2. `apply()`: snapshot the current memory, pour the staged copy in, switch the store
     to the file's data key, put this machine's local settings back.

Between the two steps of the admin web the file's data key waits in THIS PROCESS'S
MEMORY (`hold` / `take`, at most STAGE_TTL_SECONDS) -- where the live memory's key
already lives. Not in the session: Starlette's session cookie is signed, not encrypted,
and a passphrase or key in it would travel to the browser in the clear.
"""
from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_memory.core import paths
from ai_memory.core.crypto import Cipher
from ai_memory.core.store import SecureStore, WrongPassphrase
from ai_memory.features import backup

# Attachments live inside the file, so a real memory can be large; anything past this
# is far more likely a wrong file than a memory.
MAX_BYTES = 512 * 1024 * 1024

# Settings that describe THE MACHINE, not the memory: where it listens, who may reach
# it, TLS, and where its exports go. Taken from a file made on another machine they can
# lock the admin out at once -- a Windows backup with "localhost only" restored into a
# container reached over the LAN. So they stay as they are here.
LOCAL_SETTINGS = ("network", "export")

# A staged file nobody confirmed is removed after this long.
STAGE_TTL_SECONDS = 3600


class RestoreError(Exception):
    """A restore that was refused. The live memory has not been changed."""


_held: dict[str, tuple[bytes, float]] = {}


def hold(token: str, dek: bytes) -> None:
    now = time.time()
    for k in [k for k, (_d, exp) in _held.items() if exp < now]:
        _held.pop(k, None)
    _held[token] = (dek, now + STAGE_TTL_SECONDS)


def take(token: str) -> bytes | None:
    item = _held.pop(token, None)
    if item is None or item[1] < time.time():
        return None
    return item[0]


def staging_dir() -> Path:
    return paths.ensure(paths.data_dir() / "restore")


def snapshot_dir() -> Path:
    return paths.ensure(paths.data_dir() / "backups")


def new_stage() -> tuple[str, Path]:
    """A fresh, unguessable staged path. The token is what the confirm step names."""
    cleanup_stale()
    token = secrets.token_hex(16)
    return token, staging_dir() / f"{token}.db"


def stage_path(token: str) -> Path | None:
    """The staged file for a token, or None -- never a path built from arbitrary input."""
    if not token or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        return None
    p = staging_dir() / f"{token}.db"
    return p if p.exists() else None


def discard(path: Path | None) -> None:
    if path is None:
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_stale(now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        entries = list(staging_dir().glob("*.db"))
    except OSError:
        return
    for p in entries:
        try:
            if now - p.stat().st_mtime > STAGE_TTL_SECONDS:
                discard(p)
        except OSError:
            pass


def _checkpoint(path: Path) -> None:
    """Fold any WAL into the main file and leave it in rollback-journal mode, so the
    staged copy is ONE self-contained file before it is read by the backup API."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()


def prepare(staged: Path, file_passphrase: str, current_passphrase: str,
            store: SecureStore) -> tuple[dict, bytes]:
    """Check the staged copy and make it openable with this memory's passphrase.

    Returns (what is inside, the file's data key). Raises RestoreError with a sentence meant for the user. Never touches `store`'s
    data -- `store` is only asked whether `current_passphrase` is its passphrase.
    """
    if not store.verify_master(current_passphrase):
        raise RestoreError("That is not this memory's passphrase.")
    try:
        size = staged.stat().st_size
    except OSError:
        raise RestoreError("The uploaded file is gone.")
    if size == 0:
        raise RestoreError("The file is empty.")
    if size > MAX_BYTES:
        raise RestoreError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    ok, detail, _n = backup.verify(staged)
    if not ok:
        raise RestoreError(f"This is not a usable memory backup: {detail}.")
    try:
        con = sqlite3.connect(str(staged))
        try:
            page = con.execute("PRAGMA page_size").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        raise RestoreError(f"This is not a usable memory backup: {e}.")
    live_page = store.db.execute("PRAGMA page_size").fetchone()[0]
    if page != live_page:
        # A WAL-mode destination cannot change its page size, so the backup API would
        # fail half-way. Every file this program writes uses SQLite's default.
        raise RestoreError(f"The file uses a page size of {page} bytes, this memory "
                           f"{live_page}; it was not written by Ailurus Memory.")

    try:
        with SecureStore(str(staged), file_passphrase or current_passphrase) as s:
            if file_passphrase and file_passphrase != current_passphrase:
                s.change_passphrase(current_passphrase)
            summary = _summary(s)
            dek = s._dek
    except WrongPassphrase:
        raise RestoreError("The passphrase does not open this file. Use the passphrase "
                           "the memory had when the backup was made.")
    except sqlite3.Error as e:
        raise RestoreError(f"The file could not be read: {e}.")

    # The proof that matters before anything is replaced: the staged copy now opens
    # with the CURRENT passphrase. If it did not, the restored memory would be locked.
    try:
        SecureStore(str(staged), current_passphrase).close()
    except WrongPassphrase:
        raise RestoreError("Re-keying the file failed; nothing was changed.")
    _checkpoint(staged)
    return summary, dek


def _summary(s: SecureStore) -> dict:
    db = s.db
    records = db.execute("SELECT count(*) FROM memories").fetchone()[0]
    attachments = db.execute("SELECT count(*) FROM attachments").fetchone()[0]
    newest = db.execute("SELECT max(ts) FROM history").fetchone()[0] or ""
    namespaces = [r[0] for r in db.execute(
        "SELECT namespace, count(*) FROM memories GROUP BY namespace ORDER BY 2 DESC")]
    agents = sorted(a["name"] for a in s.list_agents())
    return {"records": records, "attachments": attachments, "newest": newest,
            "namespaces": namespaces, "agents": agents}


def apply(store: SecureStore, staged: Path, dek: bytes) -> dict:
    """Replace the live memory with the staged copy prepared by `prepare()`.

    `dek` is the data key prepare() returned. Returns {"records", "snapshot"}; the
    snapshot is the memory as it was, a normal backup file this same function can
    restore.
    """
    _checkpoint(staged)
    # The key must open the staged copy, or the restored memory would be unreadable.
    cipher = Cipher(dek)
    try:
        con = sqlite3.connect(str(staged))
        try:
            row = con.execute("SELECT nonce, ct FROM memories LIMIT 1").fetchone()
            records = con.execute("SELECT count(*) FROM memories").fetchone()[0]
        finally:
            con.close()
        cipher.decrypt(row[0], row[1])
    except Exception:
        raise RestoreError("The prepared file does not match its key; start the "
                           "import again. Nothing was changed.")

    keep = {}
    for key in LOCAL_SETTINGS:
        if store.db.execute("SELECT 1 FROM settings WHERE k=?", (key,)).fetchone():
            keep[key] = store.get_settings(key)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snap = snapshot_dir() / f"before-restore-{stamp}.db"
    n = 1
    while snap.exists():  # never overwrite an earlier safety copy
        n += 1
        snap = snapshot_dir() / f"before-restore-{stamp}-{n}.db"
    backup.snapshot(store.path, snap)

    src = sqlite3.connect(str(staged))
    try:
        with store._lock:
            src.backup(store.db)
            store.db.execute("PRAGMA journal_mode=WAL")
            store._dek = dek
            store.cipher = cipher
    finally:
        src.close()
    # An older backup gets the tables added since it was written.
    store._init_schema()

    with store._lock:
        for key in LOCAL_SETTINGS:
            if key not in keep:
                store.db.execute("DELETE FROM settings WHERE k=?", (key,))
        store.db.commit()
    for key, value in keep.items():
        store.set_settings(key, value)

    discard(staged)
    return {"records": records, "snapshot": str(snap)}


def stage_from_file(src: Path) -> tuple[str, Path]:
    """Copy a file on disk into the staging area (the command-line route)."""
    try:
        size = src.stat().st_size
    except OSError as e:
        raise RestoreError(f"Cannot read {src}: {e}.")
    if size > MAX_BYTES:
        raise RestoreError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    token, dst = new_stage()
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)
    return token, dst
