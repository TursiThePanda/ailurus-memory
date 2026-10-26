# Building and testing on Windows

The project has never actually run on Windows. Everything platform-specific in it is,
until this document is worked through, an **assumption**: eight `sys.platform` branches,
a tkinter passphrase dialog, pystray's win32 backend, `tasklist`/`taskkill`,
`DETACHED_PROCESS`, and the `%LOCALAPPDATA%` paths. This is the checklist that turns
those into facts.

Work through it in order. Each step says what it proves, so a failure tells you what
is actually broken rather than just that something is.

## 0. What to install in the VM

- **Python 3.12** from python.org — tick *Add python.exe to PATH*.
  (Not the Microsoft Store build: it sandboxes `%LOCALAPPDATA%`, which is exactly the
  directory this program stores its database in.)
- **Inno Setup 6** from jrsoftware.org — only needed for step 5.

Nothing else. No compiler, no Visual Studio.

## 1. Set it up

    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\pip install -e ".[tray]"
    .venv\Scripts\pip install pyinstaller

## 2. Run the test suite

    set AI_MEMORY_PASSPHRASE=test-pass
    for %f in (test_*.py) do .venv\Scripts\python %f

All 17 must pass. They start real servers on localhost, so this is the first genuine
proof the project works on Windows at all.

**Watch for:** anything mentioning an encoding error, a path with `ai-memory\ai-memory`
in it, or a test that hangs instead of failing (a hang usually means a passphrase
prompt appeared where none was expected).

## 3. Check the paths are what we think

    .venv\Scripts\python -c "import paths; print(paths.db_path()); print(paths.attachments_dir())"

Expected:

    C:\Users\<you>\AppData\Local\ai-memory\memory.db
    C:\Users\<you>\AppData\Local\ai-memory\Cache\attachments

**If you see `ai-memory\ai-memory` anywhere, `appauthor=False` in paths.py is not
taking effect** — that is the one thing about the Windows paths that was reasoned out
from platformdirs' source rather than executed, because the Windows implementation
cannot run on Linux.

## 4. The wizard, and the dialog

    .venv\Scripts\python -m ai_memory.cli setup

This is the first time the **tkinter** dialog path gets exercised for real. Then:

    .venv\Scripts\python -m ai_memory.cli start
    .venv\Scripts\python -m ai_memory.cli status
    .venv\Scripts\python -m ai_memory.cli stop

`start` proves `DETACHED_PROCESS`, `status` proves the `tasklist` liveness check, and
`stop` proves `taskkill`. Then the tray, which is the whole reason Windows gets special
treatment:

    .venv\Scripts\python -m ai_memory.cli tray

An icon should appear in the notification area. Right-click it: the menu must say
**Running**, "Open admin web" must open :8080, and **Quit must actually stop the
process** — check with `cli.py status` afterwards, it should say stopped.

## 5. Build the executable and the installer

    .venv\Scripts\pyinstaller --clean --noconfirm ai-memory.spec
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss

Both land in `dist\`.

**Then test the BUILT binary, not the source** — copy `dist\ai-memory.exe` alone into an
empty folder on a machine (or a fresh VM snapshot) with no Python at all, and repeat
step 4 with `ai-memory.exe` in place of `.venv\Scripts\python -m ai_memory.cli`. A bundle that
works where it was built and dies elsewhere is the classic PyInstaller failure, and it
is the exact experience a new user would otherwise be the first to have.

## 6. Only then, the installer

Run `dist\ai-memory-setup.exe`. It should install per-user without an admin prompt,
put two Start-menu entries there, optionally register the login autostart, and offer to
run `setup` at the end.

## What is deliberately NOT built on Windows

The semantic layer (`onnxruntime`, `numpy`, `tokenizers`) is excluded from the bundle.
`memory_semantic_search` and `memory_similar` will answer "the local embedding model is
not set up"; everything else works. That is by design, not a fault.
