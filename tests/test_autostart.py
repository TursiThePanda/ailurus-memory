"""What starts at login, and the promise that reinstalling does not change your mind.

Two independent switches on Linux and one on Windows, because on Linux systemd runs the
memory and the tray icon only watches it, while on Windows the tray IS the supervisor.
The shape of the API is the same on both -- `autostart_status()` and
`set_autostart(part, enabled)` -- so nothing above this has to branch on the platform.

The behaviour worth testing is not the happy path but the refusal: `install-service`
must not silently re-enable something the user switched off. Software that quietly puts
its autostart back on every update is the thing everyone hates, and it is one forgotten
line away at all times.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory import cli
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def main():
    # The indicator half is XDG, so it exists only on Linux; set_autostart takes the
    # Windows branch elsewhere and writes no .desktop file at all. The Windows half
    # below patches sys.platform and therefore runs everywhere.
    if sys.platform != "linux":
        print("  SKIPPED  the indicator autostart entry is Linux-only")
    else:
      with tempfile.TemporaryDirectory() as tmp:
        entry = Path(tmp) / "ai-memory-indicator.desktop"
        with mock.patch.object(cli, "INDICATOR_AUTOSTART_FILE", entry):
            check("a missing entry counts as off", cli._desktop_entry_hidden(entry))

            cli.set_autostart("indicator", True)
            check("turning it on writes the entry", entry.exists())
            check("...and it is not hidden", not cli._desktop_entry_hidden(entry))

            cli.set_autostart("indicator", False)
            check("turning it off marks it Hidden=true",
                  cli._desktop_entry_hidden(entry))
            # XDG's way, and the reason install-service can see the choice: the file
            # stays, so a reinstall has something to notice.
            check("...without deleting the file", entry.exists())
            check("...keeping the Exec line intact", "indicator.py" in entry.read_text())

            cli.set_autostart("indicator", False)
            check("turning it off twice is not an error and adds no duplicate",
                  entry.read_text().count("Hidden=true") == 1)

            cli.set_autostart("indicator", True)
            check("turning it back on clears Hidden", not cli._desktop_entry_hidden(entry))
            check("...leaving exactly one Exec line",
                  entry.read_text().count("Exec=") == 1)

    # The service half talks to systemctl, which is Linux's; on Windows set_autostart
    # takes the shortcut branch instead and never shells out at all.
    if sys.platform != "linux":
        print("  SKIPPED  systemctl is Linux-only")
    else:
        _check_systemctl()

    _check_windows_branch()

    src = (Path(HERE) / "ai_memory" / "cli.py").read_text()
    check("install-service checks for a hidden indicator entry before rewriting it",
          "_desktop_entry_hidden(indicator)" in src)
    check("install-service only enables the unit on a first install",
          "had_unit and not was_enabled" in src)

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


def _check_systemctl():
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="enabled\n", stderr="")

    with mock.patch.object(cli.subprocess, "run", fake_run):
        cli.set_autostart("service", True)
        cli.set_autostart("service", False)
    check("service on/off maps to systemctl enable/disable",
          calls == [["systemctl", "--user", "enable", "ai-memory.service"],
                    ["systemctl", "--user", "disable", "ai-memory.service"]], str(calls))


def _check_windows_branch():
    # Windows: one switch, and the installer's .lnk is disabled by renaming rather than
    # deleted -- we cannot create a .lnk again, so throwing it away would be one-way.
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        with mock.patch.object(cli.sys, "platform", "win32"), \
             mock.patch.object(cli, "_win_startup_dir", lambda: startup):
            status = cli.autostart_status()
            check("windows reports one switch, not two",
                  status["service"] is False and status["indicator"] is None, str(status))

            cli.set_autostart("service", True)
            check("with no installer shortcut, it writes its own entry",
                  (startup / "ai-memory-autostart.cmd").exists())
            check("...and reads back as on", cli.autostart_status()["service"])

            cli.set_autostart("service", False)
            check("turning it off removes it", not any(startup.iterdir()))

            lnk = startup / "AI Memory.lnk"
            lnk.write_text("pretend shortcut")
            cli.set_autostart("service", False)
            check("an installer shortcut is renamed, not deleted",
                  not lnk.exists() and (startup / "AI Memory.lnk.disabled").exists())
            cli.set_autostart("service", True)
            check("...and comes back on the way in", lnk.exists()
                  and lnk.read_text() == "pretend shortcut")
            check("...with no stray .cmd beside it",
                  not (startup / "ai-memory-autostart.cmd").exists())

if __name__ == "__main__":
    sys.exit(main())
