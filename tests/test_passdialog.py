"""The passphrase prompt: where the passphrase goes, and what the window says it is.

Neither toolkit can be driven headlessly here, and that is fine, because the properties
worth protecting are not pixels:

  * the passphrase never leaves the process -- no subprocess, no pipe, no argv;
  * a dismissed prompt is distinguishable from an unavailable one, since one means
    "the user said no" and the other means "try another way";
  * a Qt that imports but cannot draw falls through to tkinter instead of taking the
    prompt down with it;
  * the window identifies the application, which is the only defence a user has against
    typing their master passphrase into somebody else's grey box.
"""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory.desktop import passdialog
from ai_memory.core import unlock
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def main():
    src = open(os.path.join(HERE, "ai_memory", "desktop", "passdialog.py"), encoding="utf-8").read()
    # The whole point of this module. A pipe to another program is how it used to work.
    check("the dialog module spawns nothing",
          "subprocess" not in src and "Popen" not in src and "os.system" not in src)
    check("...and does not import os.exec* either", "execv" not in src)

    check("an icon file is there to draw", passdialog.ICON_SVG.exists()
          and passdialog.ICON_PNG.exists())
    # Drawn INSIDE the window: under Wayland a window icon is not a thing, so a prompt
    # that only set one would be anonymous exactly where it matters.
    check("the Qt path puts the icon in the layout, not just on the window",
          "row.addWidget(picture)" in src)
    check("the tk path does the same", "ttk.Label(frame, image=icon)" in src)
    check("both paths show the application name",
          src.count("f\"<b>{title}</b>\"") == 1 and "text=title" in src)

    check("available() names a real toolkit or nothing",
          passdialog.available() in ("qt", "tk", None))

    # Cancelled must survive: it means the user said no, and falling through to another
    # prompt would ask them again after they declined.
    with mock.patch.object(passdialog, "available", lambda: "qt"), \
         mock.patch.object(passdialog, "_qt_ask",
                           mock.Mock(side_effect=passdialog.Cancelled("no"))), \
         mock.patch.object(passdialog, "_tk_ask", mock.Mock(return_value="should-not")):
        try:
            passdialog.ask("x")
            check("a dismissed Qt prompt is not retried in tkinter", False)
        except passdialog.Cancelled:
            check("a dismissed Qt prompt is not retried in tkinter", True)

    # A broken Qt, however, is exactly when the fallback should happen.
    with mock.patch.object(passdialog, "available", lambda: "qt"), \
         mock.patch.object(passdialog, "_qt_ask",
                           mock.Mock(side_effect=RuntimeError("no display"))), \
         mock.patch.object(passdialog, "_tk_ask", mock.Mock(return_value="from-tk")):
        check("a Qt that cannot draw falls through to tkinter",
              passdialog.ask("x") == "from-tk")

    with mock.patch.object(passdialog, "available", lambda: None):
        check("no toolkit at all reports None rather than raising",
              passdialog.ask("x") is None)

    # And the caller has to translate one refusal into the other, or a dismissed dialog
    # would look like a missing one and the next method would prompt again.
    with mock.patch.object(unlock.passdialog, "ask",
                           mock.Mock(side_effect=passdialog.Cancelled("no"))):
        try:
            unlock._desktop_prompt("x")
            check("unlock turns Cancelled into PassphraseCancelled", False)
        except unlock.PassphraseCancelled:
            check("unlock turns Cancelled into PassphraseCancelled", True)

    with mock.patch.object(unlock.passdialog, "ask", mock.Mock(return_value="secret")):
        check("a passphrase from the dialog is passed straight back",
              unlock._desktop_prompt("x") == "secret")

    usrc = open(os.path.join(HERE, "ai_memory", "core", "unlock.py"), encoding="utf-8").read()
    body = usrc[usrc.index("def _desktop_prompt"):]
    check("our own window is tried before kdialog/zenity",
          body.index("passdialog.ask") < body.index('"kdialog"'))

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
