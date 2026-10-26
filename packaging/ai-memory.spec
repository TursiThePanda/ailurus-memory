import sys

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build. Produces ONE executable that is the whole application.

Two things here are not boilerplate:

1. `hiddenimports`. The two halves are started as separate processes which re-invoke
   this same binary (`cli._serve`), and the admin web reaches its modules through
   Starlette at request time. PyInstaller's static analysis does not see either route,
   so anything only imported that way has to be named or it is silently left out --
   and the failure shows up at runtime, on the user's machine, not at build time.

2. The semantic layer is deliberately EXCLUDED. onnxruntime/numpy/tokenizers are large
   native dependencies; without them duplicate detection and meaning-search stop
   working, which a tester can live with. Everything else works.
"""

block_cipher = None

import os
# This file lives in packaging/; every path below is relative to the checkout.
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# Shared by BOTH builds. Declared once as plain input lists: Analysis turns `datas`
# into a 3-tuple TOC, so feeding `a.datas` to the second Analysis fails with
# "too many values to unpack" -- the input format and the result format differ.
# Both images: the PNG is the tkinter dialog's and the tray's, the SVG is what the
# admin web inlines into its header. A frozen build without the SVG loses the icon
# in the web UI while everything else looks fine.
# Shipped where the code looks for them: ai_memory/assets next to the package.
DATAS = [(os.path.join(ROOT, 'ai_memory', 'assets', f), os.path.join('ai_memory', 'assets'))
         for f in ('ai-memory-64.png', 'ai-memory.svg', 'ailurus.svg', 'ram-favicon.svg')]
HIDDEN = [
    # reached only by the frozen binary re-invoking itself
    'ai_memory.server.app', 'ai_memory.server.server', 'ai_memory.server.tls',
    'ai_memory.web.webapp',
    # pulled in dynamically by those
    'ai_memory.core.store', 'ai_memory.core.crypto', 'ai_memory.features.lint',
    'ai_memory.features.sections', 'ai_memory.web.i18n', 'ai_memory.core.unlock',
    'ai_memory.core.paths', 'ai_memory.desktop.wizard', 'ai_memory.manage',
    'ai_memory.features.backup', 'ai_memory.features.embed', 'ai_memory.features.semantic',
    'ai_memory.features.export', 'ai_memory.core.keystore', 'ai_memory.server.netconf',
    # keyring picks its backend at runtime, so static analysis finds none of them
    'keyring.backends.Windows', 'keyring.backends.SecretService',
    'keyring.backends.macOS', 'keyring.backends.chainer', 'keyring.backends.fail',
    # the passphrase dialog and the version string
    'ai_memory.desktop.passdialog', 'ai_memory.core.version',
    # uvicorn picks its implementation at runtime
    'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
]
# PySide6 is excluded ON PURPOSE. passdialog falls back to tkinter, which looks
# native on Windows anyway, and bundling Qt would add ~230 MB to a 38 MB binary
# for a dialog that already works.
# LINUX HAS A TRAY ICON TOO -- it is just not this one. `tray.py` (pystray) is the
# Windows front end and OWNS the two processes; `indicator.py` (gi + AppIndicator3) is
# the Linux one and merely reports state, because systemd owns the processes there. The
# Linux one cannot be bundled at all: it needs the SYSTEM python's `gi`, which a venv
# does not have, so it ships with the source and install.sh sets it up. Naming pystray
# on Linux therefore only produces a misleading `ERROR: Hidden import 'PIL.Image' not
# found` in a build that is entirely correct.
# On Windows it is the opposite: building without the [tray] extra installed produces an
# exe with NO TRAY, which is the only way that build is ever started -- and PyInstaller
# says so with one ERROR line and carries on to report success. That happened on
# 2026-08-30. A build that silently ships a broken product is worse than one that stops.
if sys.platform == "win32":
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            f"This build needs the tray extra ({e.name} is missing).\n"
            f"    pip install -e '.[tray]'\n"
            f"Without it the exe builds and starts nothing when double-clicked.")
    HIDDEN += ['ai_memory.desktop.tray', 'pystray', 'PIL.Image']

EXCLUDES = ['onnxruntime', 'numpy', 'tokenizers', 'PySide6', 'shiboken6']

a = Analysis(
    [os.path.join(SPECPATH, 'entry.py')],
    pathex=[ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='ai-memory',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # ONE executable, console subsystem. A windowed build would have no stdin for
    # `setup` and no stdout for `status`; a second, windowed binary just to carry the
    # tray put two exes in front of the user, who then has to guess which to click.
    # Instead the binary hides its OWN console when it was double-clicked and is about
    # to go graphical -- see cli._hide_console().
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows only; PyInstaller ignores it elsewhere with a warning.
    icon=os.path.join(ROOT, 'ai_memory', 'assets', 'ai-memory.ico'),
)

