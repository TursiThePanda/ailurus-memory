"""Tray application — the way ai-memory is meant to be run on Windows.

Autostart cannot be unified in code: Linux has systemd user units, Windows has Task
Scheduler. The tray sidesteps the disagreement. It also matches a fact about this
program that a background service never could: **the passphrase has to be typed after
login anyway**, so nothing useful can start before a user is there. A service that
launches at boot and then sits waiting for a dialog was always the wrong model.

On Linux the systemd unit stays the supported route (`ai-memory run`) — pystray picks
its xorg backend under Wayland, where it cannot reach the tray, and the appindicator
backend needs the distribution's PyGObject, which an isolated venv does not have. The
tray is not blocked from running there; it is simply not the recommended path.

The tray OWNS the two processes: quitting it stops the memory. That is deliberate —
the key lives in RAM only, and an icon that disappears while the server keeps running
would be lying about the state of the machine.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ai_memory import cli
from ai_memory.core import paths
ICON = Path(__file__).resolve().parents[1] / "assets" / "ai-memory-64.png"


def load_icon():
    """The rendered PNG, or a plain square if it is missing. A tray app that refuses
    to start because an icon file is absent would be an absurd way to lose a session."""
    from PIL import Image
    try:
        return Image.open(ICON)
    except Exception:
        return Image.new("RGBA", (64, 64), (109, 94, 252, 255))


class Supervisor:
    """Starts the two halves and keeps them; stops them on the way out."""

    def __init__(self) -> None:
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self.procs) and all(p.poll() is None for _l, p in self.procs)

    def start(self, passphrase: str) -> bool:
        paths.ensure(cli.LOG_DIR)
        self.procs = []
        for label, module, port, _suffix in cli.SERVICES:
            log = open(cli.LOG_DIR / f"{label}.log", "ab")
            self.procs.append((label, subprocess.Popen(
                cli.child_command(label, module), cwd=str(cli.WORKDIR),
                env=cli.child_env(label, port, passphrase),
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)))
        ok = cli.wait_for_app()
        if not ok:
            self.error = f"did not come up -- see {cli.LOG_DIR}"
        return ok

    def stop(self) -> None:
        for _label, p in self.procs:
            if p.poll() is None:
                p.terminate()
        for _label, p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []


def build_menu(sup: Supervisor, on_quit):
    from pystray import Menu, MenuItem

    def state(_item) -> str:
        return "Running" if sup.running else "Stopped"

    return Menu(
        MenuItem(state, None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Open admin web",
                 lambda: webbrowser.open(cli.base_url())),
        MenuItem("Open log folder", lambda: _open_folder(cli.LOG_DIR)),
        Menu.SEPARATOR,
        MenuItem("Quit (stops the memory)", on_quit),
    )


def _open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606  -- the Windows way to open a folder
    else:
        subprocess.Popen(["xdg-open", str(path)])


def notify(title: str, message: str) -> None:
    """Best effort. A failed notification must never take the app down with it."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror(title, message, parent=root)
        finally:
            root.destroy()
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)


def run() -> int:
    """Wrapper that makes failures VISIBLE.

    A graphical start hides its console, so an unhandled exception used to take the
    process down without leaving anything on screen -- the reported symptom was
    "the window vanished and there is no icon". Anything that goes wrong here has to
    end up in a dialog, because there is nowhere else for the user to look.
    """
    try:
        return _run()
    except Exception:
        import traceback
        cli.trace("TRAY FAILED\n" + traceback.format_exc())
        notify("AI Memory failed to start", traceback.format_exc()[-1500:])
        return 1


def _run() -> int:
    # Same convention as the semantic layer: an absent optional dependency produces an
    # instruction, not a traceback. This is the one command that can be typed on a box
    # where the extra was never installed.
    try:
        import pystray  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as exc:
        msg = (f"The tray needs an optional dependency that is not installed "
               f"({exc.name}).\n\n    pip install -e '.[tray]'")
        if sys.platform == "linux":
            msg += ("\n\nOn Linux the supported route is the systemd unit "
                    "(`ai-memory run`); pystray cannot reach the tray under Wayland.")
        # notify(), not stderr: when the tray was started by a double-click the console
        # is hidden, and anything printed there is never seen.
        notify("AI Memory", msg)
        print(msg, file=sys.stderr)
        return 1

    db = paths.db_path()
    cli.trace("tray: starting")
    if not db.exists():
        # First run, with no terminal anywhere in sight. Telling the user to go and
        # type a command is how an installer loses somebody; run the wizard instead.
        from ai_memory.desktop import wizard
        if wizard.run_gui(f"{cli.base_url()}/mcp") != 0 or not db.exists():
            return 1

    from pystray import Icon

    from ai_memory.core.store import SecureStore, WrongPassphrase
    from ai_memory.core.unlock import PassphraseCancelled, get_passphrase

    # No terminal here, so this deliberately goes through unlock.py's dialog route —
    # tkinter on Windows. Verified before starting anything, exactly as the CLI does.
    try:
        passphrase = get_passphrase(prefer_dialog=True)
    except PassphraseCancelled:
        return 1
    if not passphrase:
        return 1
    try:
        SecureStore(str(db), passphrase).db.close()
    except WrongPassphrase:
        notify("AI Memory", "Wrong passphrase -- the memory stays locked.")
        return 1

    cli.trace("tray: passphrase accepted, starting the two halves")
    sup = Supervisor()
    icon: Icon | None = None

    def quit_all(*_a):
        sup.stop()
        if icon is not None:
            icon.stop()

    icon = Icon("ai-memory", load_icon(), "AI Memory", build_menu(sup, quit_all))

    def boot():
        if not sup.start(passphrase):
            notify("AI Memory", f"Failed to start: {sup.error}")
            quit_all()
            return
        # If a half dies on its own, take the other down and drop the icon: a tray
        # icon claiming "Running" over a dead server is worse than no icon.
        while sup.procs:
            if not sup.running:
                notify("AI Memory", "A component stopped -- shutting down.")
                quit_all()
                return
            time.sleep(1.0)

    threading.Thread(target=boot, daemon=True).start()
    icon.run()          # must own the main thread on Windows and macOS
    sup.stop()
    return 0
