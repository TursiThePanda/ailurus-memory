#!/usr/bin/env bash
# Install ai-memory on Linux, from wherever this checkout happens to be.
#
# It exists because the manual rebuild on 2026-08-30 took nine steps that are the same
# nine steps every time, and because two files used to have the author's own paths baked
# into them -- so a checkout anywhere else produced a unit pointing at a directory that
# did not exist. `ai-memory install-service` generates both from the real paths now; this
# script is the part that has to run BEFORE there is an `ai-memory` to run: the venv.
#
# Safe to re-run. It never touches the database, and neither does --uninstall.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # this script lives in packaging/
VENV="$REPO/.venv"
PY="$VENV/bin/python"
EXTRAS=""
WITH_SERVICE=1
AUTOSTART=1
UNINSTALL=0

usage() {
    cat <<'EOF'
usage: ./packaging/install.sh [--semantic] [--qt] [--tray]
                    [--no-service] [--no-autostart] [--uninstall]

  --semantic    also install the local embedding model's dependencies (~200 MB:
                onnxruntime, tokenizers, numpy). Needed for memory_semantic_search
                and duplicate detection; the model itself is a separate download.
  --qt          draw the passphrase dialog with Qt instead of tkinter (~230 MB,
                PySide6). Cosmetic only: it looks native in KDE, while the built-in
                tkinter one looks native on Windows. The passphrase stays inside the
                process either way.
  --tray        also install pystray + pillow. On Linux the tray does NOT work under
                Wayland (pystray picks its xorg backend) -- this is for testing it.
  --no-service  do not write or enable the systemd user unit, the desktop entry or
                the tray indicator's autostart.
  --no-autostart
                install everything, but do not start any of it at login. The memory
                then waits for `ai-memory start` (or the tray icon's Start). Change
                your mind later with `ai-memory autostart on`.
  --uninstall   remove the unit, the desktop entry and the venv. NEVER the database.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --semantic)   EXTRAS="${EXTRAS}semantic," ;;
        --qt)         EXTRAS="${EXTRAS}qt," ;;
        --tray)       EXTRAS="${EXTRAS}tray," ;;
        --no-service) WITH_SERVICE=0 ;;
        --no-autostart) AUTOSTART=0 ;;
        --uninstall)  UNINSTALL=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$UNINSTALL" = 1 ]; then
    systemctl --user disable --now ai-memory.service 2>/dev/null || true
    rm -fv "$HOME/.config/systemd/user/ai-memory.service" \
           "$HOME/.local/share/applications/ai-memory.desktop"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf "$VENV"
    echo
    echo "Removed. YOUR MEMORY IS UNTOUCHED and still encrypted at:"
    echo "  ${AI_MEMORY_DB:-$HOME/.local/share/ai-memory/memory.db}"
    echo "Delete that yourself if you really mean to -- no backup of it can be read"
    echo "without the master passphrase either."
    exit 0
fi

# --- python ------------------------------------------------------------------------
# 3.11 is the floor: the code uses tomllib and `X | None` annotations at runtime.
command -v python3 >/dev/null || { echo "python3 is not installed." >&2; exit 1; }
python3 - <<'EOF' || { echo "Python 3.11 or newer is required." >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF

# --- venv --------------------------------------------------------------------------
if [ ! -x "$PY" ]; then
    echo "==> creating the virtualenv"
    python3 -m venv "$VENV"
fi
echo "==> installing dependencies${EXTRAS:+ (extras: ${EXTRAS%,})}"
"$PY" -m pip install --quiet --upgrade pip
if [ -n "$EXTRAS" ]; then
    "$PY" -m pip install --quiet -e "$REPO[${EXTRAS%,}]"
else
    "$PY" -m pip install --quiet -e "$REPO"
fi

# --- service -----------------------------------------------------------------------
if [ "$WITH_SERVICE" = 1 ]; then
    echo "==> writing the systemd user unit, desktop entry and tray indicator"
    "$PY" "$REPO/cli.py" install-service
    if [ "$AUTOSTART" = 0 ]; then
        echo "==> turning autostart off (as asked)"
        "$PY" "$REPO/cli.py" autostart off
    fi
fi

# --- what is left, which is the part that needs a human --------------------------
DB="${AI_MEMORY_DB:-$HOME/.local/share/ai-memory/memory.db}"
echo
if [ -f "$DB" ]; then
    echo "A database is already here: $DB"
    echo "Nothing else is needed -- start it with:"
    echo
    echo "  $VENV/bin/ai-memory start"
else
    echo "Installed. One step is left, and it needs you at the keyboard because it sets"
    echo "the master passphrase."
    echo
    echo "WRITE THE PASSPHRASE DOWN SOMEWHERE SAFE FIRST. It is never stored -- not by"
    echo "this program, not in any backup. Without it the memory and every copy of it"
    echo "are permanently unreadable, by design and with no way back."
    echo
    # Offer to run it here rather than leaving a command to copy. Only when there is a
    # terminal to answer with: piped into a script, this must not sit waiting forever.
    if [ -t 0 ] && [ -t 1 ]; then
        printf "Run the setup now? [Y/n] "
        read -r answer
        case "$answer" in
            [Nn]*) echo; echo "Later, then:  $VENV/bin/ai-memory setup" ;;
            *)     echo; exec "$VENV/bin/ai-memory" setup ;;
        esac
    else
        echo "Run it when you are at a terminal:"
        echo
        echo "  $VENV/bin/ai-memory setup"
    fi
fi
if [ -n "${EXTRAS##*semantic*}" ]; then :; else
    echo
    echo "For search by meaning, download the model once (asks for the passphrase):"
    echo "  $VENV/bin/ai-memory-manage embed --setup"
fi
