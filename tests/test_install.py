"""The Linux install: generated unit and desktop entry, and install.sh's guard rails.

The bug this guards against is not a crash. Both files were committed with the author's
own paths inside them -- `%h/projects/ai-memory-mcp` in the unit, the author's home directory in
the .desktop -- so they installed cleanly for anybody and then pointed at a directory
that did not exist on their machine. Nothing fails loudly; the service just never comes
up. So the checks here are about WHERE the paths come from: this checkout and this
interpreter, never a constant.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory import cli
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def _usable_bash() -> bool:
    """Windows ships C:\\Windows\\System32\\bash.exe as the WSL launcher, so `which`
    finds a bash that exits with an error when no distribution is installed. Ask it to
    do something and see, rather than trusting the name (found on the Windows VM,
    2026-08-30)."""
    if not shutil.which("bash"):
        return False
    try:
        r = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, text=True,
                           timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "ok"


def main():
    unit, desktop, autostart = cli._texts()

    check("unit runs the package from THIS checkout",
          f"ExecStart={sys.executable} -m ai_memory.cli run" in unit
          and f"WorkingDirectory={cli.ROOT}" in unit)
    check("unit points the database at the resolved path",
          f"Environment=AI_MEMORY_DB={cli.paths.db_path()}" in unit)
    check("unit is installable (has [Install] and a WantedBy target)",
          "[Install]" in unit and "WantedBy=" in unit)
    # The comment in the unit says the passphrase must not be here; what matters is
    # that no Environment= line actually carries one.
    env_lines = [l for l in unit.splitlines() if l.startswith("Environment=")]
    check("no Environment= line carries a passphrase",
          not any("PASS" in l.upper() for l in env_lines), str(env_lines))
    check("desktop entry starts and stops through the package",
          "-m ai_memory.cli start" in desktop and "-m ai_memory.cli stop" in desktop
          and f"Path={cli.ROOT}" in desktop)

    # The real test of "generated, not hardcoded": move the checkout and the paths move.
    real_here, real_exe = cli.ROOT, sys.executable
    try:
        cli.ROOT = Path("/opt/ai-memory")
        sys.executable = str(Path("/opt/ai-memory/.venv/bin/python"))
        moved_unit, moved_desktop, moved_autostart = cli._texts()
    finally:
        cli.ROOT, sys.executable = real_here, real_exe
    elsewhere = Path("/opt/ai-memory")   # str() of this is \opt\ai-memory on Windows
    check("a checkout somewhere else generates paths there",
          f"WorkingDirectory={elsewhere}" in moved_unit
          and f"Path={elsewhere}" in moved_desktop)
    check("...and keeps none of the original checkout's path",
          str(real_here) not in moved_unit and str(real_here) not in moved_desktop
          and str(real_here) not in moved_autostart)
    # The DATABASE path is still under the user's home and should be -- that is where
    # their memory lives. What must not survive a move is the CHECKOUT path.
    check("no checkout path from this machine is baked in".replace("/", os.sep),
          "/projects/ai-memory-mcp" not in moved_unit
          and "/projects/ai-memory-mcp" not in moved_desktop
          and "/projects/ai-memory-mcp" not in moved_autostart)

    # The indicator is the one thing that must NOT run on the venv interpreter: it needs
    # gi, which the venv does not have. Getting this wrong produces an autostart entry
    # that fails silently at every login. Linux-only: there is no indicator elsewhere,
    # and _system_python() has nothing to find.
    if sys.platform == "linux":
        check("the indicator autostart runs indicator.py on the SYSTEM python",
              f"{cli._system_python()} {cli.ROOT}/ai_memory/desktop/indicator.py" in autostart)
        check("...which is not the venv interpreter",
              cli._system_python() != sys.executable
              or not Path(sys.executable).is_relative_to(cli.ROOT))
    else:
        print("  SKIPPED  the tray indicator is Linux-only")

    # --print must be exactly that: printing, not writing.
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, HOME=tmp, XDG_CONFIG_HOME=f"{tmp}/.config",
                   XDG_DATA_HOME=f"{tmp}/.local/share")
        r = subprocess.run([sys.executable, "-m", "ai_memory.cli", "install-service", "--print"],
                           capture_output=True, text=True, env=env, cwd=HERE)
        check("install-service --print succeeds", r.returncode == 0, r.stderr[-200:])
        check("...prints all three files", "[Unit]" in r.stdout
              and r.stdout.count("[Desktop Entry]") == 2)
        wrote = list(Path(tmp).rglob("ai-memory.*"))
        check("...and writes nothing", not wrote, str(wrote))

    sh = Path(HERE) / "packaging" / "install.sh"
    body = sh.read_text()
    # The generated-unit checks above are platform-independent and worth running
    # everywhere. Actually EXECUTING install.sh needs a shell, so those checks are
    # skipped where there is none rather than failing there.
    if _usable_bash():
        check("install.sh is executable", os.access(sh, os.X_OK))
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        check("install.sh parses", r.returncode == 0, r.stderr[-200:])
        r = subprocess.run(["bash", str(sh), "--help"], capture_output=True, text=True)
        check("install.sh --help works without touching anything", r.returncode == 0)
        r = subprocess.run(["bash", str(sh), "--nonsense"], capture_output=True, text=True)
        check("install.sh rejects an unknown option instead of guessing", r.returncode == 2)
    else:
        print("  SKIPPED  running install.sh -- no bash on this platform")

    check("install.sh sets -euo pipefail", "set -euo pipefail" in body)
    # The one thing it must never do, stated as a test so a later edit has to argue
    # with it: no deletion of the store, in any branch.
    check("install.sh never removes the database",
          not any("memory.db" in line and ("rm " in line or "rm -" in line)
                  for line in body.splitlines()))

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
