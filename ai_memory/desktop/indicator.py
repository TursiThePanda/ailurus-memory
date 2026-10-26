#!/usr/bin/python3
"""A tray indicator for Linux: is the memory running, and a way to start or stop it.

DELIBERATELY NOT THE WINDOWS TRAY. On Windows `tray.py` OWNS the two processes and
quitting it stops them, because there is nothing else to supervise them. Here systemd
owns them. So this is an indicator, not a supervisor: quitting it changes nothing about
whether the memory is running, and the menu says so in as many words. An icon that
silently killed the server on exit would repeat, backwards, the confusion the Windows
tray was built to avoid.

WHY IT RUNS ON THE SYSTEM PYTHON, NOT THE VENV. The 2026-08-29 conclusion was that a
Linux tray was impossible: pystray picks its xorg backend under Wayland, and its
appindicator backend needs `gi`, which an isolated venv does not have. Both are true and
neither matters here. `gi` and `AppIndicator3-0.1.typelib` ARE installed system-wide,
and an indicator that only reads a state needs nothing from the venv -- so it runs on
/usr/bin/python3 and imports nothing from this project. That is the entire trick, and it
is why this file uses only the standard library plus gi. Verified on Plasma 6.7.4 under
Wayland, 2026-08-30.

Run it:      /usr/bin/python3 indicator.py
Autostart:   ai-memory install-service   (writes ~/.config/autostart/ai-memory-indicator.desktop)
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
HOST = os.environ.get("AI_MEMORY_HOST", "127.0.0.1")
# One port since 1.2.0 (admin at /, MCP at /mcp). The app writes where it really
# listens into <data>/run/app.json; this runs on the SYSTEM python without the
# project's dependencies, so the data dir is worked out by hand (Linux only anyway).
MCP_PORT = WEB_PORT = int(os.environ.get("AI_MEMORY_PORT")
                          or os.environ.get("AI_MEMORY_MCP_PORT") or "8080")
SCHEME = "http"
try:
    import json as _json
    _data = os.environ.get("AI_MEMORY_DATA_DIR") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "ai-memory")
    _rt = _json.loads(Path(_data, "run", "app.json").read_text())
    MCP_PORT = WEB_PORT = int(_rt.get("port", MCP_PORT))
    SCHEME = _rt.get("scheme", SCHEME)
except (OSError, ValueError):
    pass
UNIT = os.environ.get("AI_MEMORY_UNIT", "ai-memory.service")
POLL_SECONDS = 5

# One instance, without a pid file to go stale: an abstract socket disappears with the
# process that holds it, however it dies.
LOCK_NAME = "\0ai-memory-indicator"


def port_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def probe() -> tuple[bool, bool]:
    return port_up(MCP_PORT), port_up(WEB_PORT)


def state_label(mcp: bool, web: bool) -> str:
    """What the first menu line says. A half-up state gets its own wording because it
    is the one worth acting on -- 'Running' over a dead MCP server would be a lie, and
    'Stopped' would send the user to start something that is already half there."""
    if mcp and web:
        return f"Running — MCP :{MCP_PORT}, web :{WEB_PORT}"
    if mcp or web:
        return f"HALF UP — MCP {'up' if mcp else 'DOWN'}, web {'up' if web else 'DOWN'}"
    return "Stopped — the memory is locked"


def icon_name(mcp: bool, web: bool) -> str:
    return "ai-memory" if (mcp and web) else "ai-memory-off"


def systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args, UNIT], check=False)


def claim_single_instance(name: str = LOCK_NAME) -> socket.socket | None:
    """Hold the abstract socket, or None if somebody else already does. `name` is a
    parameter so the tests can take their own lock instead of fighting the indicator
    the user has running -- which is exactly what they did the first time."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(name)
    except OSError:
        return None
    return s


def run() -> int:
    lock = claim_single_instance()
    if lock is None:
        print("An ai-memory indicator is already running.")
        return 0

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppInd
    from gi.repository import GLib, Gtk

    ind = AppInd.Indicator.new_with_path(
        "ai-memory", "ai-memory-off",
        AppInd.IndicatorCategory.APPLICATION_STATUS, str(ASSETS))
    ind.set_status(AppInd.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    state = Gtk.MenuItem(label="…")
    state.set_sensitive(False)
    menu.append(state)
    menu.append(Gtk.SeparatorMenuItem())

    web = Gtk.MenuItem(label="Admin web")
    web.connect("activate", lambda *_: subprocess.Popen(
        ["xdg-open", f"{SCHEME}://{HOST}:{WEB_PORT}"]))
    menu.append(web)

    start = Gtk.MenuItem(label="Start (asks for the passphrase)")
    start.connect("activate", lambda *_: systemctl("start"))
    menu.append(start)

    stop = Gtk.MenuItem(label="Stop")
    stop.connect("activate", lambda *_: systemctl("stop"))
    menu.append(stop)

    menu.append(Gtk.SeparatorMenuItem())
    # Spelled out on the item itself. On Windows quitting the tray stops the memory;
    # here it does not, and a user who has seen the other one will assume it does.
    quit_item = Gtk.MenuItem(label="Quit indicator (leaves the memory running)")
    quit_item.connect("activate", lambda *_: Gtk.main_quit())
    menu.append(quit_item)

    menu.show_all()
    ind.set_menu(menu)

    def refresh() -> bool:
        mcp_up, web_up = probe()
        state.set_label(state_label(mcp_up, web_up))
        ind.set_icon_full(icon_name(mcp_up, web_up), state_label(mcp_up, web_up))
        start.set_sensitive(not (mcp_up and web_up))
        stop.set_sensitive(mcp_up or web_up)
        return True

    refresh()
    GLib.timeout_add_seconds(POLL_SECONDS, refresh)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
