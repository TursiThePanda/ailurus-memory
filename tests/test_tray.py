"""The tray app, exercised headlessly through pystray's dummy backend.

What matters here is not the drawing -- it is that the tray really supervises: it
starts both halves, reports their state honestly, and takes everything down when it
quits or when a half dies. A tray icon claiming "Running" over a dead server is the
failure this guards against.

SKIPS when pystray/pillow are absent, which on Linux is the NORMAL install: the tray is
the Windows front end, `[tray]` is an optional extra, and the systemd unit is the
supported route here. Failing instead of skipping is what a plain `pip install -r
requirements.txt` on Linux used to get -- a red test for a feature deliberately not
installed (found 2026-08-30 by the from-scratch reinstall).
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("PYSTRAY_BACKEND", "dummy")

try:
    import PIL  # noqa: F401
    import pystray  # noqa: F401
except ImportError as e:
    print(f"SKIP: the tray extra is not installed ({e.name}).")
    print("      Install it with:  pip install -e '.[tray]'   -- on Windows, or to test")
    print("      the tray on Linux, where it is not the supported way to run this.")
    sys.exit(0)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label)


def main():
    data = tempfile.mkdtemp()
    os.environ["AI_MEMORY_DATA_DIR"] = data
    os.environ["AI_MEMORY_PORT"] = "8092"  # one port since 1.2.0: admin / and MCP /mcp

    from ai_memory import cli
    from ai_memory.core import paths
    from ai_memory.desktop import tray
    from ai_memory.core.store import SecureStore

    # a store to supervise
    paths.ensure(paths.db_path().parent)
    s = SecureStore(str(paths.db_path()), "traypass")
    s.add_agent("t")
    s.db.close()

    check("icon loads (the rendered PNG is there)", tray.load_icon().size == (64, 64))

    sup = tray.Supervisor()
    check("nothing running before start", not sup.running)

    started = sup.start("traypass")
    check("supervisor started the app", started)
    check("state reports running", sup.running)
    check("the app answers (admin and MCP share the port)", cli.port_up(8092))

    # the menu must reflect reality, not a cached string
    menu = tray.build_menu(sup, lambda: None)
    labels = [str(i.text) for i in menu]
    check("menu shows Running while up", "Running" in labels)
    check("menu offers the admin web", any("admin web" in l for l in labels))
    check("menu offers quit", any("Quit" in l for l in labels))

    # the app dying must be visible in the state
    _label, proc = sup.procs[0]
    proc.terminate()
    proc.wait(timeout=10)
    for _ in range(20):
        if not sup.running:
            break
        time.sleep(0.25)
    check("a dead app makes state report NOT running", not sup.running)
    check("menu stops claiming Running", "Stopped" in [str(i.text) for i in tray.build_menu(sup, lambda: None)])

    sup.stop()
    for _ in range(20):
        if not cli.port_up(8092):
            break
        time.sleep(0.25)
    check("stop() leaves nothing listening", not cli.port_up(8092))
    check("no child processes left", not sup.procs)

    bad = [l for l, ok in checks if not ok]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
