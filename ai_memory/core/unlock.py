"""Obtaining the master passphrase at startup (the key never sits on disk).

Order: env AI_MEMORY_PASSPHRASE -> a desktop prompt -> systemd-ask-password -> an
interactive prompt (getpass).

The desktop prompt is per-platform: kdialog/zenity on Linux, tkinter everywhere else.
tkinter ships with CPython, so Windows and macOS need no extra dependency and no
external binary -- which also keeps it out of the way of the PyInstaller bundle.

The desktop prompt sits ahead of systemd-ask-password on purpose. `systemd-ask-password`
only works when an agent is watching the directory the query lands in, and the agents
that ship with systemd — console, plymouth, wall — watch the SYSTEM one. A **user**
unit asks in /run/user/<uid>/systemd/ask-password, where nobody is listening, so on a
desktop session it does not fail: it waits out its timeout in silence and the service
looks like it hung. Asking the desktop directly is the thing that actually works, and
the systemd path stays as the fallback for a system unit or a text console.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ai_memory.desktop import passdialog
class PassphraseCancelled(Exception):
    """The user dismissed the prompt. Distinct from 'no prompt was available': a
    cancelled dialog means stop, while a missing one means try the next method."""


def _graphical() -> bool:
    """On Linux a desktop is only there if the session says so. Windows and macOS
    always have one -- there is no headless variant to detect."""
    if sys.platform != "linux":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


# The same icon the tray, the .desktop entry and the admin web use. Asking for the
# master passphrase is the single most security-relevant moment in this program, and a
# nameless grey box asking for a password is exactly what a phishing dialog looks like.
# An icon the user recognises is not decoration here; it is the only thing distinguishing
# this prompt from one anybody could put on the screen.
ICON_PNG = Path(__file__).resolve().parents[1] / "assets" / "ai-memory-64.png"


def _set_tk_icon(root, tk) -> None:
    """Best effort, never fatal. A missing or unreadable icon must not stop the user
    from unlocking their memory."""
    try:
        if ICON_PNG.exists():
            root.iconphoto(True, tk.PhotoImage(file=str(ICON_PNG)))
    except Exception:
        pass


def _tk_prompt(prompt: str) -> str | None:
    """tkinter dialog -- the portable fallback. None = tkinter is not available
    (a Python built without it, or no display); a dismissed dialog raises."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        return None
    try:
        root = tk.Tk()
        root.withdraw()  # only the dialog should be visible, not an empty parent window
        _set_tk_icon(root, tk)
        try:
            pw = simpledialog.askstring("AI Memory", prompt, show="*", parent=root)
        finally:
            root.destroy()
    except Exception:
        return None  # no display, or tkinter cannot start -- let the caller try next
    if pw is None:
        raise PassphraseCancelled("the passphrase prompt was dismissed")
    return pw


def _desktop_prompt(prompt: str) -> str | None:
    """A dialog. None = nothing available here, try something else.

    OUR OWN WINDOW FIRST, on every platform (passdialog: Qt if PySide6 is installed,
    otherwise tkinter/ttk). Two reasons, in order. The passphrase stays inside this
    process instead of being read back from another program over a pipe. And the window
    carries the application's icon and name where the user can see them, which a generic
    password box cannot -- under Wayland it CANNOT, whatever --icon is passed to it,
    because there is no window-icon protocol at all.

    kdialog/zenity remain behind it as a fallback for a machine with no working GUI
    toolkit in the venv, which is how this used to work everywhere."""
    try:
        pw = passdialog.ask(prompt)
    except passdialog.Cancelled as e:
        raise PassphraseCancelled(str(e)) from None
    if pw is not None:
        return pw

    if sys.platform != "linux":
        return _tk_prompt(prompt)
    icon = str(ICON_PNG) if ICON_PNG.exists() else None
    for exe, args in (("kdialog", ["--password", prompt, "--title", "AI Memory"]
                       + (["--icon", icon] if icon else [])),
                      ("zenity", ["--password", "--title=AI Memory"]
                       + ([f"--window-icon={icon}"] if icon else []))):
        path = shutil.which(exe)
        if not path:
            continue
        try:
            out = subprocess.run([path, *args], capture_output=True, text=True,
                                 timeout=300)
        except Exception:
            continue
        if out.returncode == 0:
            # only the trailing newline the tool adds — a passphrase is allowed to
            # start or end with a space, and .strip() would silently mangle it
            return out.stdout.rstrip("\n")
        raise PassphraseCancelled("the passphrase prompt was dismissed")
    return _tk_prompt(prompt)


def read_passphrase(prompt: str) -> str:
    """getpass, but honouring redirected stdin.

    On Windows getpass picks `win_getpass` whenever `sys.stdin is sys.__stdin__` --
    and a PIPE does not change that -- then reads keystrokes from the console through
    msvcrt, ignoring the redirected input entirely. Piped input therefore hangs
    forever rather than being read. POSIX getpass falls back to stdin with a warning;
    Windows has no such fallback, so this supplies it. Verified on Windows 11: piped
    getpass never returns.
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            raise EOFError("no passphrase on stdin")
        return line.rstrip("\n").rstrip("\r")
    # A real terminal: getpass is right here -- it turns the echo off. (This line was
    # once `return read_passphrase(prompt)`, which recursed until the stack blew. It
    # survived 18 green tests on two platforms because EVERY test feeds the passphrase
    # through a pipe, so this branch never ran. test_unlock.py now covers it.)
    return getpass.getpass(prompt)


def passphrase_from_file() -> str | None:
    """AI_MEMORY_PASSPHRASE_FILE: a Docker / systemd secret. Preferred over the plain
    variable in containers, where the environment shows up in `docker inspect`.
    Only the first line counts, without its line ending."""
    path = os.environ.get("AI_MEMORY_PASSPHRASE_FILE")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().rstrip("\r\n") or None
    except OSError as e:
        raise SystemExit(f"AI_MEMORY_PASSPHRASE_FILE={path} cannot be read: {e}") from None


def get_passphrase(prompt: str = "AI-memory master passphrase: ",
                   prefer_dialog: bool = False) -> str:
    """`prefer_dialog` is for callers with no usable terminal even though one appears
    to exist. The tray is exactly that case: started by a double-click it OWNS a
    console, which it hides -- so `sys.stdin.isatty()` is True and the tty heuristic
    below picks getpass, which then reads from a window nobody can see. The caller
    knows it is graphical; the heuristic cannot.
    """
    pw = os.environ.get("AI_MEMORY_PASSPHRASE") or passphrase_from_file()
    if pw:
        return pw
    # If the user asked for it to be remembered, this is what makes an unattended
    # start possible at all -- on both platforms, the same way. It is opt-in and can
    # be revoked from the admin web; see the warning at the top of keystore.py.
    from ai_memory.core import keystore
    pw = keystore.load()
    if pw:
        return pw
    if prefer_dialog:
        pw = _desktop_prompt(prompt)
        if pw is not None:
            return pw
    if not sys.stdin.isatty():
        if _graphical():
            pw = _desktop_prompt(prompt)
            if pw is not None:
                return pw
        exe = shutil.which("systemd-ask-password") if sys.platform == "linux" else None
        if exe:
            try:
                out = subprocess.run([exe, "--timeout=300", prompt],
                                     capture_output=True, text=True, timeout=310)
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip()
            except Exception:
                pass
    return read_passphrase(prompt)
