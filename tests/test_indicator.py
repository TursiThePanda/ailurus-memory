"""The Linux tray indicator: what it reports, and what it must never do.

The behaviour under test is small but easy to get wrong in ways nobody notices. An
indicator that says "Running" while half the service is dead is worse than no indicator,
because it is consulted precisely when something feels off. And quitting it must leave
the memory alone: on Windows the tray owns the processes and Quit stops them, so anyone
who has seen that one will assume the same here, where systemd owns them instead.

GUI code is not touched -- gi lives in the system python, these tests run in the venv.
That separation is the reason indicator.py imports gi inside run() and keeps its state
logic in plain functions.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

if sys.platform != "linux":
    # The indicator is the LINUX front end -- it needs AF_UNIX for its single-instance
    # lock and AppIndicator to draw. Windows has the tray (test_tray.py) instead. Same
    # rule as test_tray, which skips on a Linux install without the optional extra: a
    # test that fails where its feature was never meant to run teaches people to ignore
    # red tests. Found by running the suite on Windows, 2026-08-30.
    print(f"SKIP: the tray indicator is Linux-only (this is {sys.platform}).")
    sys.exit(0)

from ai_memory.desktop import indicator
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def main():
    check("both halves up reads as running", "Running" in indicator.state_label(True, True))
    check("both halves down reads as stopped", "Stopped" in indicator.state_label(False, False))
    # The case that matters: neither "Running" nor "Stopped" would be true.
    for mcp, web in ((True, False), (False, True)):
        label = indicator.state_label(mcp, web)
        check(f"half up (mcp={mcp}, web={web}) says so explicitly",
              "HALF UP" in label and "DOWN" in label, label)
    check("a half-up state does not claim to be running",
          "Running —" not in indicator.state_label(True, False))

    check("the icon is the live one only when both halves are up",
          indicator.icon_name(True, True) == "ai-memory"
          and indicator.icon_name(True, False) == "ai-memory-off"
          and indicator.icon_name(False, False) == "ai-memory-off")
    for name in ("ai-memory", "ai-memory-off"):
        check(f"{name}.svg exists to be drawn", (Path(HERE) / "ai_memory" / "assets" / f"{name}.svg").exists())

    # Probing is a connect attempt, so a closed port reads as down without waiting.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        open_port = s.getsockname()[1]
        check("an open port reads as up", indicator.port_up(open_port))
    check("a closed port reads as down", not indicator.port_up(open_port))

    # Single instance, held by an abstract socket so it cannot go stale.
    lock_name = f"\0ai-memory-indicator-test-{os.getpid()}"
    first = indicator.claim_single_instance(lock_name)
    check("the first instance claims the lock", first is not None)
    check("a second instance is refused",
          indicator.claim_single_instance(lock_name) is None)
    if first:
        first.close()
    second = indicator.claim_single_instance(lock_name)
    check("closing it frees the lock for the next one", second is not None)
    if second:
        second.close()

    body = (Path(HERE) / "ai_memory" / "desktop" / "indicator.py").read_text()
    quit_line = [l for l in body.splitlines() if "quit_item = " in l]
    check("the Quit item says it leaves the memory running",
          quit_line and "leaves the memory running" in quit_line[0], str(quit_line))
    # Stated as a test so a later edit has to argue with it, not just forget.
    check("quitting never stops the service",
          "Gtk.main_quit" in body and "systemctl(\"stop\")" not in body.split("quit_item")[1])

    check("it imports nothing from this project (it runs on the system python)",
          not any(l.startswith(("import cli", "import paths", "from cli", "from paths",
                                "import store", "from store"))
                  for l in body.splitlines()))
    # A container image has no system python at all (python:*-slim installs it under
    # /usr/local), and no desktop for an indicator to sit on -- nothing to check there.
    if os.path.exists("/usr/bin/python3"):
        r = subprocess.run(["/usr/bin/python3", "-c",
                            f"import sys; sys.path.insert(0, {os.path.join(HERE, 'ai_memory', 'desktop')!r}); import indicator"],
                           capture_output=True, text=True)
        check("the system python can import it", r.returncode == 0, r.stderr[-200:])
    else:
        print("  skip the system python can import it -- there is no /usr/bin/python3")

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
