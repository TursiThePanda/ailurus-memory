"""Every filesystem location the project uses, in one place.

The Python core is portable; only the paths were Linux-bound, hardcoded as
`~/.local/share` and `~/.cache` in five files. `platformdirs` gives the right
directory on each OS -- and on Linux it returns **exactly** the paths that were
hardcoded before, so switching to it does not move an existing installation:

    data   ~/.local/share/ai-memory   %LOCALAPPDATA%\\ai-memory
    cache  ~/.cache/ai-memory         %LOCALAPPDATA%\\ai-memory\\Cache
    config ~/.config/ai-memory        %LOCALAPPDATA%\\ai-memory

Every value stays overridable by its environment variable, which is what the tests
and the dev worktree rely on.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP = "ai-memory"

# appauthor=False matters on WINDOWS ONLY, and it is not cosmetic. platformdirs builds
# <base>/<author>/<appname>, and when no author is given it falls back to the app name
# as the author -- so the default would be ...\AppData\Local\ai-memory\ai-memory.
# False drops the author segment entirely. Linux and macOS ignore the argument, so this
# changes nothing there. (Verified by reading platformdirs' _append_parts; the Windows
# implementation cannot be exercised from Linux -- it raises NotImplementedError.)
AUTHOR = False


PORTABLE_MARKER = "ai-memory.portable"


def app_dir() -> Path:
    """The directory the program is running from -- the bundle for a frozen build,
    this source tree otherwise."""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # ai_memory/core/paths.py -> the directory that holds the ai_memory package
    return Path(__file__).resolve().parents[2]


def portable_dir() -> Path | None:
    """PORTABLE MODE: keep everything in a `data` folder beside the executable.

    Switched on by an `ai-memory.portable` file sitting next to the binary, and NOT by
    guessing. Guessing would be wrong in the case that matters most: installed into
    Program Files, the folder is not writable by an ordinary user, and Windows would
    silently redirect the writes into VirtualStore -- a database that appears to work
    and then cannot be found. A shared machine would also have every user fighting over
    one file.

    So the ZIP for someone who just wants to unpack and run ships the marker, and the
    installer does not. Unpack it anywhere and everything stays in that one folder,
    which is exactly what "for clarity" should mean; install it properly and the data
    lives in the per-user location where an installed application's data belongs.
    """
    try:
        here = app_dir()
    except Exception:
        return None
    return here / "data" if (here / PORTABLE_MARKER).exists() else None


def _env(name: str, default: Path) -> Path:
    """An override wins if it is set and non-empty. expanduser() because a value
    typed into a config file or a service unit may well contain a ~."""
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def data_dir() -> Path:
    return _env("AI_MEMORY_DATA_DIR",
                portable_dir() or Path(user_data_dir(APP, AUTHOR)))


def config_dir() -> Path:
    return _env("AI_MEMORY_CONFIG_DIR",
                portable_dir() or Path(user_config_dir(APP, AUTHOR)))


def db_path() -> Path:
    return _env("AI_MEMORY_DB", data_dir() / "memory.db")


def models_dir() -> Path:
    """The sentence-transformer model for the semantic layer. Under data, not cache:
    it is a slow, large download and a cache cleaner is entitled to delete cache."""
    # Named after the model: two models must never share a directory, and an upgrade
    # should leave the old files identifiable rather than half-overwritten.
    return _env("AI_MEMORY_MODEL_DIR",
                data_dir() / "models" / "paraphrase-multilingual-MiniLM-L12-v2")


def attachments_dir() -> Path:
    base = portable_dir() or Path(user_cache_dir(APP, AUTHOR))
    return _env("AI_MEMORY_ATTACHMENT_DIR", base / "attachments")


def backup_dest() -> Path:
    """Deliberately a LOCAL default, not a NAS mount.

    It used to default to the author's own NAS mount, which means nothing
    on anyone else's machine. His NAS destination is now explicit configuration
    (AI_MEMORY_BACKUP_DEST in the service unit) instead of a default baked into the
    code -- see the note in project-ai-memory-backup. A local default is also the safe
    failure mode: backup.py refuses to run if the destination is missing, so a copy
    never quietly lands inside an unmounted mount point.
    """
    return _env("AI_MEMORY_BACKUP_DEST", data_dir() / "backups")


def install_mode() -> str:
    """How this instance runs: 'portable' (marker beside the binary), 'installed'
    (a frozen build without the marker) or 'source' (python from a checkout)."""
    import sys
    if portable_dir() is not None:
        return "portable"
    return "installed" if getattr(sys, "frozen", False) else "source"


def describe() -> list[tuple[str, Path, str | None]]:
    """Every location as (label, resolved path, overriding variable or None), for the
    admin web. Nobody could tell from the UI which build they were looking at or where
    its data actually was -- it came up explaining installed vs. portable to a user.

    The attachment directory holds only DECRYPTED READ-OUT COPIES: the attachments
    themselves live encrypted inside the database and are in every backup with it.
    """
    rows = [("Program", app_dir(), None),
            ("Data", data_dir(), "AI_MEMORY_DATA_DIR"),
            ("Config", config_dir(), "AI_MEMORY_CONFIG_DIR"),
            ("Database", db_path(), "AI_MEMORY_DB"),
            ("Embedding model", models_dir(), "AI_MEMORY_MODEL_DIR"),
            ("Attachment read-out (decrypted copies)", attachments_dir(),
             "AI_MEMORY_ATTACHMENT_DIR"),
            ("Backup script destination", backup_dest(), "AI_MEMORY_BACKUP_DEST")]
    return [(label, path, var if var and os.environ.get(var) else None)
            for label, path, var in rows]


def ensure(path: Path) -> Path:
    """Create the directory a file will live in (or the directory itself)."""
    path.mkdir(parents=True, exist_ok=True)
    return path
