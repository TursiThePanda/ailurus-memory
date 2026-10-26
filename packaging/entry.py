"""PyInstaller entry point. Imports the CLI as ai_memory.cli instead of running cli.py
as __main__, so the frozen program has exactly one copy of that module (the tray and
the wizard import it by name too)."""
import sys

from ai_memory.cli import main

sys.exit(main())
