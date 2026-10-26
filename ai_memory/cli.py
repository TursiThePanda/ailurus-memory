"""One entry point for running ai-memory, on any platform.

Replaces the launch layer that was twelve shell and systemd files:
`start.sh`, `stop.sh`, `run-server.sh`, `run-web.sh`, `service-run.sh`,
`launch-gui.sh`. Those encoded the same handful of decisions in bash, which is the
only genuinely Linux-bound part of the project.

    ai-memory setup     first run: passphrase, an agent, and the config to paste
    ai-memory start     start it in the background, one passphrase prompt
    ai-memory stop      stop it
    ai-memory status    what is running, and where
    ai-memory run       run it in the FOREGROUND (for a service manager)
    ai-memory tray      run from the system tray (the Windows way)
    ai-memory network reset   back to localhost-only, no TLS (after a bad allow-list)

Since 1.2.0 there is ONE process on ONE port (app.py: admin at /, MCP at /mcp).

The one design constraint worth restating, because it drives everything here: the
passphrase is asked ONCE and handed to the two children through their inherited
environment and nothing else. Never a file, never a service-manager environment
block, never `systemctl show-environment`. Two independently started services would
have to ask twice; that is why one parent owns the prompt and spawns both.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from ai_memory.core import paths
HOST = os.environ.get("AI_MEMORY_HOST", "127.0.0.1")
# One port since 1.2.0. AI_MEMORY_MCP_PORT is still accepted (it named the port that
# kept its job); AI_MEMORY_WEB_PORT no longer means anything and says so.
PORT = int(os.environ.get("AI_MEMORY_PORT") or os.environ.get("AI_MEMORY_MCP_PORT") or "8080")
if os.environ.get("AI_MEMORY_MCP_PORT") and not os.environ.get("AI_MEMORY_PORT"):
    print("ai-memory: AI_MEMORY_MCP_PORT is deprecated, use AI_MEMORY_PORT", file=sys.stderr)
if os.environ.get("AI_MEMORY_WEB_PORT"):
    print("ai-memory: AI_MEMORY_WEB_PORT is ignored since 1.2.0 -- the admin web is on "
          "the same port as MCP", file=sys.stderr)
MCP_PORT = WEB_PORT = PORT  # names the tray and older callers still use

HERE = Path(__file__).resolve().parent          # the ai_memory package
ROOT = HERE.parent                                # what holds it: the checkout
ASSETS = HERE / "assets"
# PyInstaller sets sys.frozen. It matters here because the two halves are spawned as
# separate processes: unfrozen that means [python, "server.py"], but inside a bundle
# there IS no server.py on disk and sys.executable is the bundle itself. A frozen build
# therefore re-invokes ITSELF with a hidden subcommand instead.
FROZEN = getattr(sys, "frozen", False)
# Where to run child processes FROM. Not HERE when frozen: HERE is then the bundle's
# _MEI extraction directory, which PyInstaller DELETES when that process exits. A
# child left with its working directory inside a deleted folder fails in ways that
# look nothing like the cause -- on Windows the DLL search path includes the working
# directory, so loading _sqlite3.pyd blew up with "No module named '_sqlite3'".
# The directory holding the executable is stable for as long as the program exists.
WORKDIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
RUN_DIR = paths.data_dir() / "run"
LOG_DIR = paths.data_dir() / "logs"

# (label, module, port, url suffix) -- a tuple of one since 1.2.0
SERVICES = (
    ("app", "ai_memory.server.app", PORT, ""),
)


def runtime() -> dict:
    """Where the running app actually listens. The port and scheme live in the
    encrypted settings, which this side cannot read without the passphrase, so the app
    writes them here in plain text when it starts (app.write_runtime)."""
    import json
    try:
        return json.loads((RUN_DIR / "app.json").read_text())
    except (OSError, ValueError):
        return {"port": PORT, "scheme": "http", "host": HOST}


def base_url() -> str:
    r = runtime()
    return f"{r.get('scheme', 'http')}://{r.get('host', HOST)}:{r.get('port', PORT)}"


# --------------------------------------------------------------------------- ports

def port_up(port: int) -> bool:
    """`ss -ltn | grep` was the Linux-only way to ask this. A connect attempt works
    everywhere and, unlike a bind test, does not race with the thing starting up."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, port)) == 0


def wait_for_app(timeout: float = 30.0) -> bool:
    """Until the app answers on the port it wrote into its runtime file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_up(runtime().get("port", PORT)):
            return True
        time.sleep(0.25)
    return False


def wait_for(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_up(port):
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------- pids

def pid_file(label: str) -> Path:
    return RUN_DIR / f"{label}.pid"


def read_pid(label: str) -> int | None:
    try:
        return int(pid_file(label).read_text().strip())
    except (OSError, ValueError):
        return None


def alive(pid: int) -> bool:
    """Signal 0 asks 'may I signal this process' without sending anything. On Windows
    there is no such thing, so ask the task list instead."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def terminate(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


# ------------------------------------------------------------------- the passphrase

def obtain_passphrase() -> str:
    """One prompt, verified before anything is spawned.

    Checking up front matters: without it a typo produces two processes crash-looping
    against the same wrong key, and the real message is buried in two log files."""
    from ai_memory.core.unlock import PassphraseCancelled, get_passphrase

    from ai_memory.core.unlock import passphrase_from_file
    db = paths.db_path()
    pw = os.environ.get("AI_MEMORY_PASSPHRASE") or passphrase_from_file()
    if not pw:
        try:
            pw = get_passphrase()
        except PassphraseCancelled:
            sys.exit("Passphrase entry was cancelled -- the memory stays locked.")
        if not pw:
            sys.exit("No passphrase given -- the memory stays locked.")
        # A NEW memory is created with whatever was typed, and nothing can tell a typo
        # from the intended passphrase afterwards: the data would be locked for good.
        # So the first passphrase is asked twice. (From a variable or a secret file the
        # value is already written down somewhere, which is its own confirmation.)
        if not db.exists():
            try:
                again = get_passphrase("Repeat the new passphrase: ")
            except PassphraseCancelled:
                again = None
            if again != pw:
                sys.exit("The two passphrases differ -- nothing was created.")
    # Verified below whatever its source. It used to be trusted when it came from the
    # environment, which defeated the point: a typo produced two children crash-looping
    # in two separate log files instead of one clear message here.

    if db.exists():
        from ai_memory.core.store import SecureStore, WrongPassphrase
        try:
            SecureStore(str(db), pw).db.close()
        except WrongPassphrase:
            sys.exit("Wrong passphrase -- the memory stays locked.")
    return pw


def child_command(label: str, module: str) -> list[str]:
    """How to launch one half as its own process, frozen or not."""
    if FROZEN:
        return [sys.executable, f"_serve-{label}"]
    # -m with the checkout as the working directory: the modules live in a package
    return [sys.executable, "-m", module]


def child_env(label: str, port: int, passphrase: str) -> dict:
    # clean_env(), not dict(os.environ): the two halves are separate processes of the
    # same frozen bundle and must unpack their own copy -- see clean_env().
    env = clean_env()
    env["AI_MEMORY_PASSPHRASE"] = passphrase
    # First-start seeds only (netconf._seed); the stored settings win afterwards.
    env["HOST"] = HOST
    env["PORT"] = str(port)
    return env


# -------------------------------------------------------------------- the commands

def cmd_tray(_args) -> int:
    _hide_console()      # explicit `ai-memory tray` from a double-click, too
    from ai_memory.desktop import tray
    return tray.run()


def cmd_setup(args) -> int:
    from ai_memory.desktop import wizard
    url = f"{base_url()}/mcp"
    rc = wizard.run_gui(url) if getattr(args, "gui", False) else wizard.run(url)
    if rc == 0 and getattr(args, "then_tray", False):
        return cmd_tray(None)
    return rc


def cmd_start(_args) -> int:
    # A missing database is a first run, not an error. Sending them to the wizard is
    # the difference between "it works" and "it started and I cannot reach it".
    if not paths.db_path().exists():
        print(f"No database at {paths.db_path()}.")
        print("Run  ai-memory setup  first -- it creates it and prints the client config.")
        return 1
    if port_up(runtime().get("port", PORT)):
        print(f"Already running: {base_url()} (admin) and {base_url()}/mcp. Nothing to do.")
        return 0

    paths.ensure(RUN_DIR)
    paths.ensure(LOG_DIR)
    passphrase = obtain_passphrase()

    # detach so the processes outlive the terminal -- setsid on POSIX, its Windows
    # equivalent otherwise
    if sys.platform == "win32":
        detach = {"creationflags": subprocess.DETACHED_PROCESS
                  | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        detach = {"start_new_session": True}

    for label, module, port, _suffix in SERVICES:
        if port_up(port):
            print(f"{label}: already running on :{port}")
            continue
        log = open(LOG_DIR / f"{label}.log", "ab")
        proc = subprocess.Popen(
            child_command(label, module),
            cwd=str(WORKDIR), env=child_env(label, port, passphrase),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **detach)
        pid_file(label).write_text(str(proc.pid))

    del passphrase

    ok = True
    for label, _module, port, suffix in SERVICES:
        if wait_for_app():
            print(f"  OK   admin {base_url()}   MCP {base_url()}/mcp")
        else:
            ok = False
            print(f"  FAIL {label} did not come up -- see {LOG_DIR / (label + '.log')}")
    print("   stop with: ai-memory stop")
    return 0 if ok else 1


def cmd_stop(_args) -> int:
    stopped = []
    for label, _module, port, _suffix in SERVICES:
        pid = read_pid(label)
        if pid and alive(pid):
            terminate(pid)
            stopped.append(label)
        pid_file(label).unlink(missing_ok=True)

    for label, _module, port, _suffix in SERVICES:
        port = runtime().get("port", port)
        for _ in range(20):
            if not port_up(port):
                break
            time.sleep(0.25)
        else:
            print(f"  WARNING {label} is still answering on :{port}")

    print("ai-memory stopped." if stopped else "ai-memory was not running.")
    return 0


def cmd_status(_args) -> int:
    print(f"database    {paths.db_path()}")
    print(f"logs        {LOG_DIR}")
    port = runtime().get("port", PORT)
    pid = read_pid("app")
    up = port_up(port)
    state = "running" if up else "stopped"
    detail = f"pid {pid}" if pid and alive(pid) else "no pid file"
    print(f"{'admin':11} {state:8} {base_url()}   ({detail})")
    print(f"{'mcp':11} {state:8} {base_url()}/mcp")
    return 0 if up else 1


# How long a half gets to exit on its own before it is killed. Measured, not guessed:
# a stop took 46 seconds and systemd gave up on it with `Failed with result 'timeout'`
# (2026-08-30). SIGTERM makes uvicorn finish its in-flight requests, and an MCP session
# is a long-lived stream -- so a connected agent, doing nothing wrong, holds the whole
# shutdown open. Graceful first, then insist.
STOP_GRACE_SECONDS = 5.0


def terminate_all(procs, grace: float = STOP_GRACE_SECONDS) -> None:
    """SIGTERM everything, wait up to `grace` in total, then SIGKILL what is left.

    The grace is shared across the children rather than given to each, so the worst case
    stays bounded no matter how many halves there are."""
    for _label, p in procs:
        if p.poll() is None:
            p.terminate()
    deadline = time.monotonic() + grace
    for _label, p in procs:
        try:
            p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def cmd_run(_args) -> int:
    """Foreground supervisor -- what `service-run.sh` did, for systemd, the Windows
    tray, or anything else that wants to own the processes rather than detach them.

    If the child dies, so does this supervisor, and the service manager sees it."""
    paths.ensure(RUN_DIR)
    paths.ensure(LOG_DIR)
    # Stop must work while we still WAIT for the passphrase, too: a locked container
    # otherwise ignored `docker stop` until it was killed 10 s later. Replaced by the
    # child-aware handler below once there is a child.
    def _stop_waiting(*_a):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _stop_waiting)
    passphrase = obtain_passphrase()

    procs = []
    for label, module, port, _suffix in SERVICES:
        procs.append((label, subprocess.Popen(
            child_command(label, module),
            cwd=str(WORKDIR), env=child_env(label, port, passphrase))))
    del passphrase

    def shutdown(*_a):
        terminate_all(procs)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            for label, p in procs:
                if p.poll() is not None:
                    print(f"{label} exited ({p.returncode}) -- stopping the other half",
                          file=sys.stderr)
                    shutdown()
                    for _l, q in procs:
                        q.wait()
                    return p.returncode or 1
            time.sleep(0.5)
    finally:
        shutdown()


def _own_console() -> bool:
    """Were we double-clicked from Explorer rather than run from a shell?

    If this process is the ONLY one attached to its console, the console was created
    for us and dies with us -- so anything printed vanishes before it can be read.
    That is exactly what a user sees when they double-click the exe: a window that
    flashes and disappears, with no way to tell whether anything happened.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        buf = (ctypes.c_uint * 4)()
        return ctypes.windll.kernel32.GetConsoleProcessList(buf, 4) <= 1
    except Exception:
        return False


def trace(message: str) -> None:
    """Append one line to a startup log, best effort, never raising.

    A graphical start has no console to print to and no terminal to leave a traceback
    in, so a crash before the first window appears is completely silent -- which is
    exactly the failure that has been chased here twice. This leaves a trail that
    survives it. It is cheap and it runs on every start, graphical or not.
    """
    try:
        d = paths.ensure(paths.data_dir() / "logs")
        with open(d / "startup.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        pass


def _relaunch_detached(args: list[str]) -> bool:
    """Start ourselves again with NO console at all, and let this process end.

    This replaces trying to detect a double-click and hide the console window we were
    given. Detection proved unreliable, and hiding is dangerous when wrong: hide the
    wrong window and the user's own terminal disappears. Relaunching sidesteps both --
    the new process simply has no console, and it behaves identically whether the
    first one came from Explorer or from a shell.
    """
    if sys.platform != "win32":
        return False
    try:
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(child_command_self(args), creationflags=flags,
                         cwd=str(WORKDIR), env=clean_env(), close_fds=True)
        return True
    except Exception:
        trace("relaunch failed\n" + __import__("traceback").format_exc())
        return False


def clean_env(base: dict | None = None) -> dict:
    """A copy of the environment with PyInstaller's internals stripped out.

    A onefile bundle unpacks itself into a temporary _MEI directory and records that
    in the environment (`_MEIPASS2`, `_PYI_*`). A child of the frozen program inherits
    those, concludes it is ALREADY unpacked, and reuses the parent's directory instead
    of making its own.

    While the parent is alive that works, which is why `start` was fine. When the
    parent hands over and exits, PyInstaller deletes that directory -- and the child is
    left running out of a folder that no longer exists. It surfaced as
    "ModuleNotFoundError: No module named '_sqlite3'", which points nowhere near the
    cause.
    """
    env = dict(base if base is not None else os.environ)
    for key in list(env):
        if key.startswith("_PYI") or key.startswith("_MEIPASS"):
            del env[key]
    return env


def child_command_self(args: list[str]) -> list[str]:
    """Ourselves, plus arguments -- frozen or running from source."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, "-m", "ai_memory.cli", *args]


def _hide_console() -> None:
    """Hide the console window -- ONLY ever our own.

    This is what lets a single executable be both a command-line tool and a tray app.
    The binary is built for the console subsystem, so `status` and friends print
    normally and the shell waits for them. When it is double-clicked instead, Windows
    creates a console for it; going on to show a tray icon with that black window
    sitting behind it looks broken, so we hide it.

    Guarded by _own_console(): if we were started FROM a shell, GetConsoleWindow()
    returns that shell's window, and hiding it would make the user's terminal vanish.
    """
    if sys.platform != "win32" or not _own_console():
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _gui_possible() -> bool:
    """Is there a desktop to draw on, and something to draw with?"""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    if sys.platform == "linux":
        return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    return True


def _default_command() -> str:
    """What running the program with NO arguments should do.

    Deliberately NOT conditional on how it was launched. The first version of this
    asked _own_console() -- "am I the only process attached to my console?", the
    classic double-click test -- and it was WRONG on Windows 11, where Explorer opens
    a console program inside Windows Terminal, which is attached too. The result was
    that a double-click printed `status` into a window that then closed, which is
    precisely the "nothing happened" the user saw twice.

    So: no database is a first run, otherwise run the thing. Where no desktop exists
    (a headless Linux box) fall back to the printing commands.
    """
    gui = _gui_possible()
    if not paths.db_path().exists():
        return "setup-gui" if gui else "setup"
    return "tray" if gui else "status"


def _serve(label: str) -> int:
    """Run one half IN THIS PROCESS. Only used by a frozen build re-invoking itself;
    hidden from --help because typing it by hand is never the right thing to do."""
    # "mcp" and "web" are the labels of the two-port era; both mean the one app now.
    from ai_memory.server import app
    app.main()
    return 0


def cmd_network(args) -> int:
    if args.action != "reset":
        return 2
    from ai_memory.core.store import SecureStore
    from ai_memory.server import netconf
    pw = obtain_passphrase()
    store = SecureStore(str(paths.db_path()), pw)
    netconf.reset(store)
    store.close()
    print("Network settings reset: listening on 127.0.0.1 only, no TLS, admin web from "
          "this machine only. Restart ai-memory for it to take effect.")
    return 0


# ------------------------------------------------------------------ linux install

SYSTEMD_UNIT = """\
[Unit]
Description=AI Memory (admin web + MCP on :{mcp_port})
Documentation=file:{repo}/README.md
# Both of these matter. After= keeps the unit from starting before the Plasma session
# exists -- the passphrase prompt is a desktop dialog, and one drawn with no session to
# draw into never appears. PartOf= ties it to the session's lifetime, so logging out
# takes the memory down with it and the key does not outlive the session in RAM.
After={target}
PartOf=graphical-session.target

[Service]
Type=simple
Environment=AI_MEMORY_DB={db}
# The passphrase is NOT here, and must not be: a unit file is world-readable on disk,
# and anything in Environment= also shows up in `systemctl --user show-environment`.
# `ai-memory run` asks for it once at startup and hands it to both children through
# its inherited environment -- see the module docstring in cli.py.
WorkingDirectory={repo}
ExecStart={python} -m ai_memory.cli run
# Long enough for the dialog to sit waiting while the user is still logging in.
TimeoutStartSec=330
# The supervisor kills its own children after STOP_GRACE_SECONDS, so this is only the
# backstop for a supervisor that is itself wedged. Before it was set, a stop took 46 s
# and systemd gave up with 'timeout' -- SIGTERM makes uvicorn drain in-flight requests
# and an MCP session is a long-lived stream, so any connected agent held it open.
TimeoutStopSec=15
# on-failure only: a cancelled or wrong passphrase exits non-zero, and retrying that
# in a loop would throw dialogs at the user until they get it right or kill the unit.
Restart=on-failure
RestartSec=5
StartLimitBurst=2

[Install]
WantedBy={target}
"""

DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=AI Memory
Comment=Shared encrypted memory for AI assistants
Exec={python} -m ai_memory.cli start
Path={repo}
Icon=ai-memory
Terminal=false
Categories=Utility;
Actions=Admin;Stop;

[Desktop Action Admin]
Name=Admin web
Exec=xdg-open http://{host}:{web_port}

[Desktop Action Stop]
Name=Stop
Exec={python} -m ai_memory.cli stop
"""


INDICATOR_AUTOSTART = """\
[Desktop Entry]
Type=Application
Name=AI Memory indicator
Comment=Shows whether the memory is running
# The SYSTEM python on purpose, not the venv: the indicator needs `gi`, which an
# isolated venv does not have and cannot easily get. It imports nothing from this
# project, so it does not need the venv for anything else. See indicator.py.
Exec={system_python} {repo}/ai_memory/desktop/indicator.py
Icon=ai-memory
Terminal=false
X-GNOME-Autostart-enabled=true
"""


# ------------------------------------------------------------------- autostart
#
# Two independent things start at login on Linux, and conflating them is the mistake
# this is written to avoid: the SERVICE (the systemd unit, which is what actually runs
# the memory) and the INDICATOR (the tray icon, which only watches). The icon does not
# start the server -- so someone who wants the memory started by hand still wants the
# icon, greyed out, with "Start" in its menu. On Windows they are one thing, because
# there the tray IS the supervisor.
#
# Default is on. Not everyone wants a background service they did not ask for, and the
# answer to that is a switch, not a policy.

INDICATOR_AUTOSTART_FILE = Path.home() / ".config" / "autostart" / "ai-memory-indicator.desktop"


def _win_startup_dir() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")


def _win_startup_entries() -> tuple[Path, Path]:
    """The installer's shortcut, and ours. The installer writes a .lnk, which needs
    Windows APIs to create; we cannot make one, but we CAN disable it by renaming, and
    a .cmd we write ourselves works just as well when there is no .lnk to begin with."""
    d = _win_startup_dir()
    return d / "AI Memory.lnk", d / "ai-memory-autostart.cmd"


def _desktop_entry_hidden(path: Path) -> bool:
    """XDG's way of saying 'the user turned this off': Hidden=true, file kept."""
    if not path.exists():
        return True
    return any(l.strip().lower() == "hidden=true" for l in
               path.read_text(encoding="utf-8").splitlines())


def autostart_status() -> dict:
    """What starts at login. None means 'not applicable on this platform'."""
    if sys.platform == "win32":
        lnk, cmd = _win_startup_entries()
        on = lnk.exists() or cmd.exists()
        return {"service": on, "indicator": None}
    r = subprocess.run(["systemctl", "--user", "is-enabled", "ai-memory.service"],
                       capture_output=True, text=True)
    return {"service": r.stdout.strip() == "enabled",
            "indicator": INDICATOR_AUTOSTART_FILE.exists()
                         and not _desktop_entry_hidden(INDICATOR_AUTOSTART_FILE)}


def set_autostart(part: str, enabled: bool) -> str:
    """Turn one part on or off. Returns a line describing what happened."""
    if sys.platform == "win32":
        lnk, cmd = _win_startup_entries()
        disabled_lnk = lnk.with_suffix(".lnk.disabled")
        if enabled:
            if disabled_lnk.exists():
                disabled_lnk.rename(lnk)
            elif not lnk.exists():
                paths.ensure(cmd.parent)
                cmd.write_text(f'@start "" "{sys.executable}" tray\r\n', encoding="utf-8")
            return "autostart: on"
        if lnk.exists():
            lnk.rename(disabled_lnk)
        cmd.unlink(missing_ok=True)
        return "autostart: off"

    if part == "service":
        action = "enable" if enabled else "disable"
        r = subprocess.run(["systemctl", "--user", action, "ai-memory.service"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return f"service: could not {action} it ({r.stderr.strip() or r.returncode})"
        return f"service: {'starts at login' if enabled else 'manual only'}"

    # The indicator. Hidden=true rather than deleting the file, because that is what
    # XDG autostart means by "off" and because it survives `install-service` -- which
    # checks for it, so reinstalling never silently re-enables what the user turned off.
    if not INDICATOR_AUTOSTART_FILE.exists():
        if not enabled:
            return "indicator: not installed"
        _, _, indicator_text = _texts()
        paths.ensure(INDICATOR_AUTOSTART_FILE.parent)
        INDICATOR_AUTOSTART_FILE.write_text(indicator_text, encoding="utf-8")
        return "indicator: starts at login"
    lines = [l for l in INDICATOR_AUTOSTART_FILE.read_text(encoding="utf-8").splitlines()
             if l.strip().lower() not in ("hidden=true", "hidden=false")]
    if not enabled:
        lines.append("Hidden=true")
    INDICATOR_AUTOSTART_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"indicator: {'starts at login' if enabled else 'manual only'}"


def cmd_autostart(args) -> int:
    parts = ("service",) if sys.platform == "win32" else ("service", "indicator")
    if args.state is None:
        status = autostart_status()
        for part in parts:
            on = status[part]
            print(f"{part:10} {'on -- starts at login' if on else 'off -- manual only'}")
        print("\nChange it with:  ai-memory autostart on|off  [--service|--indicator]")
        return 0
    chosen = [p for p in parts
              if (p == "service" and args.service) or (p == "indicator" and args.indicator)]
    for part in (chosen or list(parts)):
        print(set_autostart(part, args.state == "on"))
    return 0


ICON_NAME = "ai-memory"


def install_icons() -> list[Path]:
    """Put the icon in the user's icon THEME, and return what was written.

    The reason this is not just a file path in the .desktop entry: `kdialog --icon` and
    a desktop entry's `Icon=` want a THEME NAME, not a path. Passing a path works in some
    places and is silently ignored in others -- the passphrase dialog came up with no
    icon at all (2026-08-30) until the icon was installed properly and referred to as
    `ai-memory`. A theme name also survives moving the checkout, which a path does not.
    """
    written = []
    for src, rel in ((ASSETS / "ai-memory.svg", f"scalable/apps/{ICON_NAME}.svg"),
                     (ASSETS / "ai-memory-64.png", f"64x64/apps/{ICON_NAME}.png")):
        if not src.exists():
            continue
        dest = Path.home() / ".local" / "share" / "icons" / "hicolor" / rel
        paths.ensure(dest.parent)
        dest.write_bytes(src.read_bytes())
        written.append(dest)
    if written and shutil.which("gtk-update-icon-cache"):
        # Not fatal if it fails: without the cache the icon is still found, just slower.
        subprocess.run(["gtk-update-icon-cache", "-f", "-t",
                        str(Path.home() / ".local" / "share" / "icons" / "hicolor")],
                       capture_output=True)
    return written


def _install_target() -> str:
    """What the unit hangs off. Plasma gets its own target because that is what this
    was built and tested against; anything else uses the generic one. Guessing wrong is
    cheap to fix (`systemctl --user edit`) and the alternative is asking a question
    nobody installing this can answer."""
    return ("plasma-workspace.target" if os.environ.get("KDE_FULL_SESSION")
            or "plasma" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            else "graphical-session.target")


def _texts() -> tuple[str, str]:
    """Unit and .desktop, filled in from where this checkout ACTUALLY is.

    Both files used to be committed with the author's own paths baked in --
    `%h/projects/ai-memory-mcp` in the unit, the author's home directory outright in the .desktop
    -- so they worked for exactly one person and one directory. Generating them is the
    whole point of this command: the interpreter is the venv running it, the repo is
    where this file lives.
    """
    fields = dict(repo=str(ROOT), python=sys.executable, db=str(paths.db_path()),
                  mcp_port=MCP_PORT, web_port=WEB_PORT, host=HOST,
                  target=_install_target(),
                  system_python=_system_python())
    return (SYSTEMD_UNIT.format(**fields), DESKTOP_ENTRY.format(**fields),
            INDICATOR_AUTOSTART.format(**fields))


def _system_python() -> str:
    """The interpreter the indicator runs on. NOT sys.executable, which is the venv's
    and has no `gi`. Falls back to the venv's own path only so the generated file is
    never syntactically broken -- on a machine without a system python3 the indicator
    was never going to work anyway."""
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if Path(candidate).exists():
            return candidate
    return sys.executable


def _has_gi() -> bool:
    """Whether the SYSTEM python can import gi. Asked by running it, not by looking for
    a package: the distribution name differs everywhere and the import is the thing
    that actually has to work."""
    try:
        r = subprocess.run([_system_python(), "-c", "import gi"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cmd_install_service(args) -> int:
    unit_text, desktop_text, indicator_text = _texts()
    if getattr(args, "print", False):
        print(unit_text)
        print(desktop_text)
        print(indicator_text)
        return 0
    if sys.platform != "linux":
        print("Only Linux has the systemd user unit. On Windows run `ai-memory tray`,")
        print("or the installer, which registers autostart itself.")
        return 1

    unit = Path.home() / ".config" / "systemd" / "user" / "ai-memory.service"
    desktop = Path.home() / ".local" / "share" / "applications" / "ai-memory.desktop"
    had_unit = unit.exists()
    was_enabled = autostart_status()["service"] if had_unit else False
    indicator = Path.home() / ".config" / "autostart" / "ai-memory-indicator.desktop"
    targets = [(unit, unit_text), (desktop, desktop_text)]
    # The indicator is Linux-only and needs gi; write its autostart entry only where it
    # stands a chance, and say so rather than leaving a file that will never work.
    if _has_gi():
        if _desktop_entry_hidden(indicator) and indicator.exists():
            # Turned off with `ai-memory autostart off --indicator`. Reinstalling is not
            # a request to undo that -- an installer that quietly re-enables autostart is
            # the behaviour everyone hates in other software.
            print(f"  (leaving {indicator.name} disabled -- you turned it off;"
                  f" `ai-memory autostart on --indicator` puts it back)")
        else:
            targets.append((indicator, indicator_text))
    else:
        print("  (skipping the tray indicator: the system python has no `gi` --"
              " install python3-gobject and re-run to get it)")
    for icon in install_icons():
        print(f"wrote {icon}")
    for path, text in targets:
        paths.ensure(path.parent)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")

    # Same rule as the indicator: a reinstall must not undo `autostart off --service`.
    # A unit that was already there and is not enabled was switched off on purpose; one
    # that did not exist a moment ago is a first install and should start at login.
    commands = [["systemctl", "--user", "daemon-reload"]]
    if had_unit and not was_enabled:
        print("  (leaving the service disabled -- you turned it off;"
              " `ai-memory autostart on --service` puts it back)")
    else:
        commands.append(["systemctl", "--user", "enable", "ai-memory.service"])
    for cmd in commands:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # Not fatal: the files are correct and can be enabled by hand. A container
            # or an ssh session without a user bus is the usual reason.
            print(f"  ({' '.join(cmd)} failed: {r.stderr.strip() or r.returncode})")
            return 0
    if had_unit and not was_enabled:
        print("installed. Start it now with `ai-memory start`.")
    else:
        print("enabled. It starts with your desktop session;"
              " `ai-memory start` runs it now.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Everything this program creates -- the database, its backups, the TLS key -- is
    # for the owner only. The database used to be created 0644.
    if os.name == "posix":
        os.umask(0o077)
    if argv and argv[0].startswith("_serve-"):
        return _serve(argv[0].removeprefix("_serve-"))

    from ai_memory.core.version import __version__
    parser = argparse.ArgumentParser(prog="ai-memory", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"ai-memory {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (
            ("setup", cmd_setup, "first run: passphrase, agent, client config"),
            ("start", cmd_start, "start it in the background"),
            ("stop", cmd_stop, "stop it"),
            ("status", cmd_status, "show what is running"),
            ("run", cmd_run, "run it in the foreground (service manager)"),
            ("network", cmd_network, "network settings: `network reset`"),
            ("tray", cmd_tray, "run from the system tray (the Windows way)"),
            ("install-service", cmd_install_service,
             "write the systemd user unit and desktop entry for THIS checkout"),
            ("autostart", cmd_autostart,
             "show or change what starts when you log in")):
        sp = sub.add_parser(name, help=help_)
        if name == "install-service":
            sp.add_argument("--print", action="store_true",
                            help="show what would be written, write nothing")
        if name == "autostart":
            sp.add_argument("state", nargs="?", choices=("on", "off"),
                            help="omit to show what is set now")
            sp.add_argument("--service", action="store_true",
                            help="only the memory itself (the systemd unit)")
            sp.add_argument("--indicator", action="store_true",
                            help="only the tray icon (Linux; it never starts the server)")
        if name == "network":
            sp.add_argument("action", choices=("reset",))
        if name == "setup":
            sp.add_argument("--gui", action="store_true",
                            help="ask in dialog windows instead of the terminal")
            sp.add_argument("--then-tray", action="store_true",
                            help=argparse.SUPPRESS)  # internal: first-run handover
        sp.set_defaults(func=fn)
    trace(f"start argv={argv!r} frozen={FROZEN} own_console={_own_console()} "
          f"stdin_tty={sys.stdin.isatty() if sys.stdin else None} db={paths.db_path()}")

    if not argv:
        default = _default_command()
        trace(f"no arguments -> default command '{default}'")
        # Graphical work belongs in a process with no console, so it cannot flash a
        # window or be killed silently behind one. Hand it over and step aside.
        if default in ("tray", "setup-gui") and sys.platform == "win32":
            handover = ["tray"] if default == "tray" else ["setup", "--gui", "--then-tray"]
            if _relaunch_detached(handover):
                trace(f"handed over to a console-less process: {handover}")
                return 0
        if default == "setup-gui":
            # Double-clicked with no database. Set it up, then GO ON INTO THE TRAY:
            # finishing the wizard and exiting leaves the user staring at a closed
            # window with nothing running, which is what they reported the first time.
            _hide_console()
            try:
                rc = cmd_setup(argparse.Namespace(gui=True))
                return rc if rc != 0 else cmd_tray(None)
            except Exception:
                import traceback
                from ai_memory.desktop import tray
                trace("FIRST RUN FAILED\n" + traceback.format_exc())
                tray.notify("AI Memory failed to start", traceback.format_exc()[-1500:])
                return 1
        if default == "tray":
            _hide_console()
            argv = ["tray"]
        else:
            argv = [default]
            print(f"(no command given -- running '{argv[0]}'; "
                  f"'ai-memory --help' lists the rest)\n")

    args = parser.parse_args(argv)
    trace(f"running '{args.command}'")
    rc = args.func(args)
    trace(f"'{args.command}' returned {rc}")

    # Keep a double-clicked window open long enough to be read.
    if _own_console():
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
