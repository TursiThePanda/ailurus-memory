"""The passphrase prompt, drawn by this application, inside this process.

TWO PROPERTIES, IN THIS ORDER.

**The passphrase never leaves the process.** The prompt used to be `kdialog` or
`zenity` -- a separate program, with the passphrase read back over a pipe. That is not a
hole (same user, never in argv, a pipe is not on the filesystem) and it is what shipped
until 1.0.0, but it is a strictly worse arrangement than not handing the key to anything
at all, and this is the one dialog that asks for the key to everything. Both
implementations here run in-process.

**It must be recognisably this application.** A nameless grey box asking for a password
is precisely what a phishing dialog looks like, and the user is trained to type into it.
So the icon is drawn INSIDE the window, next to the name -- not set as a window icon,
which under Wayland does nothing at all: there is no window-icon protocol, the compositor
derives it from `app_id` and the matching .desktop file. That is why `kdialog --icon`
was ignored no matter what it was passed (tested 2026-08-30 on Plasma 6.7.4).

Qt if PySide6 is installed (`pip install -e '.[qt]'`, ~230 MB, so it is opt-in), else
tkinter/ttk. The choice is cosmetic -- ttk looks native on Windows, Qt looks native in
KDE -- and changes nothing about where the passphrase goes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ICON_SVG = ASSETS / "ai-memory.svg"
ICON_PNG = ASSETS / "ai-memory-64.png"
TITLE = "AI Memory"


class Cancelled(Exception):
    """The user dismissed the prompt -- distinct from 'no GUI was available'."""


def available() -> str | None:
    """Which toolkit would be used, or None if neither can draw anything."""
    try:
        import PySide6  # noqa: F401
        return "qt"
    except ImportError:
        pass
    try:
        import tkinter  # noqa: F401
        return "tk"
    except ImportError:
        return None


def ask(prompt: str, title: str = TITLE) -> str | None:
    """The passphrase, or None if no GUI is available here. Raises Cancelled."""
    kind = available()
    if kind == "qt":
        try:
            return _qt_ask(prompt, title)
        except Cancelled:
            raise
        except Exception:
            # A Qt that imports but cannot open a display must not take the prompt with
            # it -- fall through to tkinter, and then to whatever the caller tries next.
            pass
    if kind in ("qt", "tk"):
        try:
            return _tk_ask(prompt, title)
        except Cancelled:
            raise
        except Exception:
            return None
    return None


def _qt_ask(prompt: str, title: str) -> str:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
                                   QLabel, QLineEdit, QVBoxLayout)

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(title)
    # What Wayland actually uses to find an icon for the window, as opposed to the
    # window-icon call below, which X11 uses and Wayland ignores.
    app.setDesktopFileName("ai-memory")
    if ICON_SVG.exists():
        app.setWindowIcon(QIcon(str(ICON_SVG)))

    dialog = QDialog()
    dialog.setWindowTitle(title)
    row = QHBoxLayout(dialog)
    if ICON_SVG.exists():
        picture = QLabel()
        picture.setPixmap(QPixmap(str(ICON_SVG)).scaled(
            56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        picture.setAlignment(Qt.AlignTop)
        row.addWidget(picture)
    column = QVBoxLayout()
    column.addWidget(QLabel(f"<b>{title}</b>"))
    column.addWidget(QLabel(prompt))
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setMinimumWidth(280)
    column.addWidget(field)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    column.addWidget(buttons)
    row.addLayout(column)
    field.setFocus()

    if dialog.exec() != QDialog.Accepted:
        raise Cancelled("the passphrase prompt was dismissed")
    return field.text()


def _tk_ask(prompt: str, title: str) -> str:
    import tkinter as tk
    from tkinter import ttk

    result: dict[str, str | None] = {"value": None}
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)

    icon = None
    if ICON_PNG.exists():
        try:
            icon = tk.PhotoImage(file=str(ICON_PNG))
            root.iconphoto(True, icon)      # X11/Windows honour this; Wayland ignores it
        except Exception:
            icon = None

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    if icon is not None:
        ttk.Label(frame, image=icon).grid(row=0, column=0, rowspan=4, padx=(0, 14), sticky="n")
    ttk.Label(frame, text=title, font=("", 11, "bold")).grid(row=0, column=1, sticky="w")
    ttk.Label(frame, text=prompt, wraplength=320).grid(row=1, column=1, sticky="w", pady=(2, 6))
    entry = ttk.Entry(frame, show="•", width=34)
    entry.grid(row=2, column=1, sticky="we")
    entry.focus_set()

    def accept(*_a):
        result["value"] = entry.get()
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=3, column=1, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(buttons, text="OK", command=accept).grid(row=0, column=1)
    root.bind("<Return>", accept)
    root.bind("<Escape>", lambda *_a: root.destroy())
    root.eval("tk::PlaceWindow . center")
    root.mainloop()

    if result["value"] is None:
        raise Cancelled("the passphrase prompt was dismissed")
    return result["value"]
