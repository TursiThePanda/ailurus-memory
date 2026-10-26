"""First-run setup: passphrase, an agent, and the config the user has to paste.

The last part is the point. An installer can create a database and a key, but it
cannot wire the user's MCP client up for them -- every client keeps its config
somewhere else, and some of them are edited by hand. So the wizard ends by printing
a ready-made snippet per client, with the real key and the real URL already in it.
Without that a new user gets a running server and no way to reach it.

Two paths, because this is also how the author rebuilt their own machine:

  NEW      no database -> set a passphrase, create the store, create one agent
  ADOPT    a database is already there (a restored backup) -> unlock it, show what
           is inside, and DO NOT touch the passphrase or invent a second agent

The adopt path has one hard limit, and it is by design: **an API key cannot be read
back out of the store.** Only a one-way blind index of it is kept (store.add_agent),
which is why a stolen database yields no keys. So after a restore the existing keys
come from the client configs the user already has -- and if those are gone, the only
way forward is to rotate, which invalidates the key everywhere it was in use.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ai_memory.core import paths
DEFAULT_AGENT = "claude-code"


# --------------------------------------------------------------- config locations

def client_config_paths() -> dict[str, Path | None]:
    """Where each client keeps its MCP config on THIS platform. Checked 2026-08-29;
    Claude Code is not here because it is configured with a command, not a file."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        desktop = appdata / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        desktop = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        desktop = home / ".config" / "Claude" / "claude_desktop_config.json"
    return {"Claude Desktop": desktop, "LM Studio": home / ".lmstudio" / "mcp.json"}


def _block(url: str, key: str, in_url: bool) -> str:
    """The mcpServers JSON, built with json.dumps rather than written out by hand.

    This is text someone pastes into a config file, so an unbalanced brace here costs
    them an evening. Generating it means it cannot be malformed, and test_wizard.py
    parses it back to prove it."""
    entry: dict[str, object] = {"url": f"{url}?key={key}"} if in_url else {
        "url": url, "headers": {"Authorization": f"Bearer {key}"}}
    return json.dumps({"mcpServers": {"ai-memory": entry}}, indent=2)


def snippets(url: str, agent: str, key: str | None) -> str:
    """The paste-ready block. `key=None` means we do not have one to show -- an
    adopted store, where the keys are not recoverable -- so it says so instead of
    printing a convincing placeholder someone would paste verbatim."""
    shown = key or "PUT-THE-EXISTING-KEY-HERE"
    locations = client_config_paths()
    rule = "=" * 72
    out = [
        "", rule, "  CONNECTING YOUR CLIENTS", rule, "",
        f"  Endpoint:  {url}",
        f"  Agent:     {agent}",
    ]
    if key is None:
        out += ["",
                "  NOTE: the key below is a placeholder. Existing keys cannot be read",
                "  back out of the store -- take them from your current client config,",
                "  or rotate the key."]
    out += [
        "", "-- Claude Code " + "-" * 57, "",
        f"  claude mcp add ai-memory --transport http {url} \\",
        f'    --header "Authorization: Bearer {shown}"',
        "", "-- LM Studio " + "-" * 59,
        f"  file: {locations['LM Studio']}", "",
        "  LM Studio does not send the Authorization header for streamable-http",
        "  (lmstudio-bug-tracker#1892), so the key goes in the URL instead -- the",
        "  server accepts that as a fallback:", "",
        _block(url, shown, in_url=True),
        "", "-- Claude Desktop, and anything else taking an mcpServers block " + "-" * 8,
        f"  file: {locations['Claude Desktop']}", "",
        _block(url, shown, in_url=False),
        "", rule, "",
    ]
    return "\n".join(out)


# ------------------------------------------------------------------ the two paths

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    sys.stdout.flush()
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _ask_new_passphrase() -> str:
    """Always flush first. print() into a pipe is block-buffered while getpass writes
    straight to the terminal, so without this the prompt appears BEFORE the warning
    that explains it -- which is exactly backwards for the one question in this
    program that cannot be undone."""
    from ai_memory.core.unlock import read_passphrase
    while True:
        sys.stdout.flush()
        first = read_passphrase("Choose a master passphrase: ")
        if not first:
            print("  Empty. The whole store hangs off this -- try again.")
            continue
        if first != read_passphrase("Repeat it: "):
            print("  They do not match. Again.")
            continue
        return first


def setup_new(db: Path, url: str) -> int:
    from ai_memory.core.store import SecureStore

    print(f"\nNo database at {db} -- setting one up.\n")
    print("  The master passphrase encrypts everything and is NEVER stored, here or")
    print("  in any backup. Lose it and every copy of the memory is scrap. Put it in")
    print("  a password manager before you go any further.\n")

    passphrase = _ask_new_passphrase()
    agent = _ask("\nName for this machine's agent", DEFAULT_AGENT)

    paths.ensure(db.parent)
    store = SecureStore(str(db), passphrase)
    key = store.add_agent(agent)
    store.db.close()

    print(f"\n  Created {db}")
    print(f"  Agent '{agent}' created. Its key is shown ONCE, below -- it cannot be")
    print("  read back out of the store afterwards.")
    print(snippets(url, agent, key))
    print("  Start it with:  ai-memory start\n")
    return 0


def setup_adopt(db: Path, url: str) -> int:
    from ai_memory.core.store import SecureStore, WrongPassphrase
    from ai_memory.core.unlock import read_passphrase

    print(f"\nA database is already here: {db}")
    print("Keeping it. Unlock it with its EXISTING passphrase -- setup never")
    print("re-keys a store that already holds records.\n")

    # getpass, deliberately, NOT unlock.get_passphrase(). That one is built for the
    # background service: with no tty it opens a desktop dialog. The wizard is
    # something a person typed into a terminal, so a popup is wrong here -- and when
    # the input is piped it fails outright instead of reading the passphrase.
    for attempt in range(3):
        sys.stdout.flush()
        passphrase = read_passphrase("Master passphrase: ")
        if not passphrase:
            print("Nothing entered.")
            return 1
        try:
            store = SecureStore(str(db), passphrase)
            break
        except WrongPassphrase:
            print(f"  Wrong passphrase ({2 - attempt} left).")
    else:
        return 1

    agents = store.list_agents()
    records = len(store.list())
    print(f"\n  Unlocked. {records} records, {len(agents)} agents:")
    for a in agents:
        print(f"    - {a['name']}")

    print("\n  API keys CANNOT be printed. The store keeps only a one-way index of")
    print("  them, which is exactly why a stolen database yields no keys. Your")
    print("  existing client configs still have them, so if you kept those, you are")
    print("  done -- just point them at this endpoint.")
    print("\n  If a key is genuinely lost, rotate it (this breaks the old key")
    print("  everywhere it is still configured):")
    print("      ai-memory-manage rotate-key <agent>\n")

    name = _ask("Rotate a key now? agent name, or blank to skip", "")
    key = None
    if name:
        try:
            key = store.rotate_key(name)
            print(f"\n  New key for '{name}' -- shown once:")
        except KeyError:
            print(f"  No such agent: {name}")
            name = agents[0]["name"] if agents else DEFAULT_AGENT
    store.db.close()

    print(snippets(url, name or (agents[0]["name"] if agents else DEFAULT_AGENT), key))
    print("  Start it with:  ai-memory start\n")
    return 0


def run(url: str) -> int:
    db = paths.db_path()
    return setup_adopt(db, url) if db.exists() else setup_new(db, url)


# --------------------------------------------------------------------- the GUI path

def _gui_available() -> bool:
    """tkinter, and somewhere to draw. On Linux a session must actually exist."""
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    if sys.platform == "linux":
        return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    return True


def _gui_error(title: str, message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror(title, message, parent=root)
    finally:
        root.destroy()


def _gui_result_window(text: str, title: str) -> None:
    """The config the user has to paste, in something they can actually copy from.

    This is the part a console does worst: a long block that must be transferred
    verbatim into another program's config file. A selectable text box with a copy
    button is not decoration -- it is the difference between a new user getting
    connected and a new user giving up.
    """
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title(title)
    root.geometry("760x560")

    box = scrolledtext.ScrolledText(root, wrap="none", font=("Consolas", 10))
    box.insert("1.0", text)
    box.configure(state="normal")          # selectable and copyable, deliberately
    box.pack(fill="both", expand=True, padx=10, pady=(10, 4))

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    note = tk.Label(bar, text="", anchor="w")

    def copy_all():
        root.clipboard_clear()
        root.clipboard_append(text)
        note.config(text="Copied to the clipboard.")

    tk.Button(bar, text="Copy everything", command=copy_all).pack(side="left")
    tk.Button(bar, text="Close", command=root.destroy).pack(side="right")
    note.pack(side="left", padx=10)

    root.mainloop()


def _gui_ask(title: str, prompt: str, secret: bool = False, initial: str = "") -> str | None:
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    try:
        return simpledialog.askstring(title, prompt, show="*" if secret else None,
                                      initialvalue=initial, parent=root)
    finally:
        root.destroy()


def setup_new_gui(db: Path, url: str) -> int:
    from ai_memory.core.store import SecureStore

    warning = ("This passphrase encrypts everything, and it is NEVER stored -- not "
               "here, not in any backup.\n\nLose it and every copy of the memory is "
               "scrap. Put it in a password manager first.\n\nChoose a master "
               "passphrase:")
    while True:
        first = _gui_ask("AI Memory - first run", warning, secret=True)
        if first is None:
            return 1
        if not first:
            _gui_error("AI Memory", "An empty passphrase is not allowed.")
            continue
        again = _gui_ask("AI Memory", "Repeat the passphrase:", secret=True)
        if again is None:
            return 1
        if first != again:
            _gui_error("AI Memory", "They do not match. Try again.")
            continue
        break

    agent = _gui_ask("AI Memory", "Name for this machine's agent:",
                     initial=DEFAULT_AGENT) or DEFAULT_AGENT

    paths.ensure(db.parent)
    store = SecureStore(str(db), first)
    key = store.add_agent(agent)
    store.close()

    _gui_result_window(
        f"The memory is ready.\n\nDatabase: {db}\n\n"
        f"The key below is shown ONCE -- it cannot be read back out of the store.\n"
        + snippets(url, agent, key)
        + "\nClose this window and AI Memory starts in the notification area,\n"
          "bottom right. Right-click that icon for the admin web and to quit.\n",
        "AI Memory - connect your clients")
    return 0


def setup_adopt_gui(db: Path, url: str) -> int:
    from ai_memory.core.store import SecureStore, WrongPassphrase

    for _ in range(3):
        pw = _gui_ask("AI Memory",
                      f"A memory already exists at:\n{db}\n\n"
                      "Enter its EXISTING master passphrase:", secret=True)
        if pw is None:
            return 1
        try:
            store = SecureStore(str(db), pw)
            break
        except WrongPassphrase:
            _gui_error("AI Memory", "Wrong passphrase.")
    else:
        return 1

    agents = [a["name"] for a in store.list_agents()]
    records = len(store.list())
    store.close()
    _gui_result_window(
        f"Unlocked. {records} records, {len(agents)} agents: {', '.join(agents)}\n\n"
        "API keys cannot be shown: the store keeps only a one-way index of them, which "
        "is why a stolen database yields no keys. Take them from your existing client "
        "config, or rotate one from the admin web.\n"
        + snippets(url, agents[0] if agents else DEFAULT_AGENT, None),
        "AI Memory - existing memory")
    return 0


def run_gui(url: str) -> int:
    """First run without a terminal -- what the tray calls."""
    db = paths.db_path()
    if not _gui_available():
        return run(url)
    return setup_adopt_gui(db, url) if db.exists() else setup_new_gui(db, url)
