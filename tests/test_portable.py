"""Portable mode: the marker file next to the binary decides where everything lives.

The portable ZIP ships an empty `ai-memory.portable` beside the executable, and paths.py
then keeps the database, the config, the logs and the attachments in a `data` folder in
that same directory. Nothing else switches it on -- guessing "am I in a writable folder?"
would put the database in Windows' VirtualStore when the program was installed into
Program Files, where it appears to work and then cannot be found.

Two things are worth a test rather than an inspection. First, that ALL of the paths move
together: one that stayed behind would split a user's memory across two places without
saying so. Second, that an environment variable still wins over the marker -- the tests,
the systemd unit and anyone with the data on another disk depend on that override, and
the portable branch sits between it and the default.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory.core import paths
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def clean_env():
    """No AI_MEMORY_* override in scope: they beat everything this file is testing, and
    the suite itself is usually run with some of them set."""
    return mock.patch.dict(os.environ, {k: "" for k in os.environ if k.startswith("AI_MEMORY_")})


def main():
    with tempfile.TemporaryDirectory() as tmp, clean_env():
        here = Path(tmp)
        with mock.patch.object(paths, "app_dir", lambda: here):
            check("without the marker, portable mode is off", paths.portable_dir() is None)
            default_data = paths.data_dir()
            check("...and the data dir is the per-user one",
                  here not in default_data.parents and default_data != here / "data",
                  str(default_data))

            (here / paths.PORTABLE_MARKER).touch()
            data = here / "data"
            check("an empty marker file switches it on", paths.portable_dir() == data)

            # Every path the program writes to, in one list: whichever one is forgotten
            # in a future refactor is the one that silently keeps writing to AppData.
            for name, got in (("data", paths.data_dir()),
                              ("config", paths.config_dir()),
                              ("db", paths.db_path()),
                              ("models", paths.models_dir()),
                              ("attachments", paths.attachments_dir()),
                              ("backups", paths.backup_dest())):
                check(f"{name} lands beside the binary",
                      got == data or data in got.parents, str(got))

            with mock.patch.dict(os.environ, {"AI_MEMORY_DATA_DIR": str(here / "elsewhere")}):
                check("an environment override still beats the marker",
                      paths.data_dir() == here / "elsewhere")

            (here / paths.PORTABLE_MARKER).unlink()
            check("removing the marker switches it back off",
                  paths.portable_dir() is None and paths.data_dir() == default_data)

    # app_dir() is what the marker is looked for next to. Frozen, that is the directory
    # of the exe itself -- NOT sys._MEIPASS, the temporary unpack dir a onefile build
    # runs from, which is deleted on exit and would make portable mode lose the store on
    # every run.
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "ai-memory"
        exe.touch()
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "executable", str(exe)), \
             mock.patch.object(sys, "_MEIPASS", str(Path(tmp) / "_MEI12345"), create=True):
            # resolve() both sides: on Windows the temp dir can come back as an 8.3 short
            # name (RUNNER~1) while app_dir() resolves the exe to its long name
            check("frozen, app_dir is the exe's own directory",
                  paths.app_dir() == Path(tmp).resolve())

    failed = [label for label, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
