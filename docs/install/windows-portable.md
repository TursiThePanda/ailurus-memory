# Ailurus Memory — portable

A private, encrypted memory that several AI assistants can share.

Instead of explaining your setup, your projects and your preferences again in every new
chat, the assistant reads and writes a memory that lives on **your** machine. Everything
in it is encrypted with a passphrase only you know. Nothing is sent anywhere: the program
listens on your own computer and never makes an outbound connection.

This is the **portable** build: nothing is installed, and everything the program ever
writes stays inside this one folder. If you would rather have it in the Start menu with
your data in the usual per-user place, take the installer instead —
`ailurus-memory-<version>-windows-setup.exe`,
released beside this zip.

---

## 1. Running it

Unpack this zip somewhere you will find again — the Desktop, Documents, or a USB stick.
Then double-click **ai-memory.exe**. There is no installer and no administrator password.

### The `ai-memory.portable` file is the switch

The empty file named **ai-memory.portable** next to the exe is what makes this build
portable. While it is there, the program keeps the encrypted database, the logs and the
settings in a **`data`** folder beside the exe. Delete that file and the same exe stores
its data in `%LOCALAPPDATA%\ai-memory` instead, like an installed copy — and it will look
as if your memory vanished, when it is only in the other place. So: keep the exe and the
marker together, and move the whole folder rather than just the exe.

**Do not unpack it into `C:\Program Files`.** That folder is not writable by an ordinary
user, and Windows quietly redirects the writes into a per-user VirtualStore copy — a
database that appears to work and then cannot be found. Anywhere in your own profile, or
a removable drive, is fine.

### Windows will warn you, and that is expected

The program is not signed with a paid certificate, so Windows shows a blue
**"Windows protected your PC"** box. The Run button is hidden:

    click "More info"  ->  then "Run anyway"

Your antivirus may also stop it. The reason is the packaging format, not the contents —
one file that unpacks itself is common for malware, so scanners are suspicious of it by
default. If it gets quarantined, allow it by name.

### Then

1. A window appears asking for a **master passphrase**. Choose one and confirm it.
2. **Put that passphrase in a password manager before you go any further.** It is never
   stored anywhere — not by this program, not in any backup. If you lose it, every copy
   of your memory becomes permanently unreadable. There is no recovery, by design.
3. A window shows the settings to paste into your AI client. Use the **Copy everything**
   button; you can get this text back later (see below), but the API key in it is shown
   **once** and cannot be read out of the database afterwards.
4. Close that window. An icon appears in the notification area, bottom right. That is the
   program running.

Right-click the icon for the admin web, or to quit. Quitting stops the memory: the
encryption key exists only in memory, so nothing is left unlocked behind you.

Your memory lives in:

    <the folder you unpacked>\data

That one folder is everything — the encrypted database and its logs. Back it up and you
have backed up the lot. (Section 4 sets that up properly, on a schedule.)

To remove the program: delete the folder. **Unlike uninstalling the installed version,
that deletes your memory with it**, because here they are in the same place. Copy `data`
out first if you might want it back.

### Starting it at login

    ai-memory.exe autostart on

That writes an entry into your Startup folder pointing at this exe **where it is now**.
Move the folder and the entry points at nothing. On a USB stick, do not turn it on at
all: at login the drive may not be there.

### Running it next to an installed copy

Both use port 8080 (the memory and the admin web share it), so only one of them can run at
a time. Quit the one in the notification area before starting the other, or give the
portable one its own port:

    set AI_MEMORY_PORT=8090
    ai-memory.exe

and use that number everywhere the sections below say 8080.

---

## 2. Connecting your AI

**Read this part first, because it decides what is possible.**

The program listens on **127.0.0.1** — your own machine only. That is what keeps a private
memory private. It also means:

    WORKS DIRECTLY, because it runs on your machine:
        Gemini CLI
        Claude Code
        Claude Desktop, LM Studio, Cursor

    DOES NOT, because it runs in someone else's cloud:
        ChatGPT (web)
        Gemini (web app)

A website cannot reach a server on your computer. ChatGPT does support MCP — Settings →
Apps → Advanced settings → Developer mode, on Plus and above — but only for servers it can
reach over **HTTPS from the internet**. Connecting it would mean publishing your memory to
the internet with a certificate and authentication in front of it. That is a real project
and a real decision about your private data, not a config line. OpenAI's "Secure MCP
Tunnel" exists for this case if you ever want it.

**So if you mostly use ChatGPT or Gemini in the browser, start with Gemini CLI** — same
model, runs locally, and it can use this memory today.

### Gemini CLI

Edit `~/.gemini/settings.json` (on Windows: `C:\Users\<you>\.gemini\settings.json`) and add
your server under `mcpServers`. Take the URL and key from the setup window:

    {
      "mcpServers": {
        "ai-memory": {
          "httpUrl": "http://127.0.0.1:8080/mcp",
          "headers": { "Authorization": "Bearer YOUR-KEY-HERE" }
        }
      }
    }

Then start `gemini` and ask it what memories it can see.

### Claude Code

One command, no file editing:

    claude mcp add ai-memory --transport http http://127.0.0.1:8080/mcp --header "Authorization: Bearer YOUR-KEY-HERE"

### Anything else that takes an `mcpServers` block

Claude Desktop keeps its config at `%APPDATA%\Claude\claude_desktop_config.json`,
LM Studio at `~/.lmstudio/mcp.json`. The setup window printed a ready-made block for both.

A client config points at a **port**, not at this folder, so moving the folder does not
break it — but the client can only reach the memory while this exe is running.

---

## 3. The admin web

Right-click the tray icon and choose **Open admin web**, or go to
<http://127.0.0.1:8080>. Log in with your master passphrase. There you can:

- **read and edit the memories** — this is the only place the content is ever decrypted
  for a human to read
- **manage agents and their keys** — one key per AI, each with its own permissions, and
  any of them revocable on its own
- **Settings** — a scheduled export, whether to remember your passphrase, and whether the
  server should be reachable from your network

### Losing a key

You cannot look up an existing API key: the database keeps only a one-way fingerprint of
it, which is why a stolen copy of your memory yields no keys at all. If you lose one,
rotate it in the admin web — that issues a new key and invalidates the old one everywhere.

---

## 4. Backing it up

**Settings → Scheduled export.** Give it a folder — a network drive, a synced folder,
anywhere — and it writes a verified copy on a schedule, keeping as many as you ask for.
A missed run (the machine was off) happens as soon as you start it again.

Point it **outside** this folder. A backup that lives inside the thing it is backing up
is gone with it the moment the folder is deleted or the stick is lost.

What it writes is the **encrypted database**, not a readable dump. It carries no
passphrase, so a copy of it is useless to anyone who does not have yours. That is what
makes it safe to put on a NAS or in cloud storage.

Which is also the trap: **the passphrase is not in the backup.** A backup plus a forgotten
passphrase is worth nothing. Password manager. Today.

---

## 5. If something goes wrong

The program writes what it did to:

    <the folder you unpacked>\data\logs\startup.log

Send that file. It records how it was started, which database it found, and where it
stopped — usually enough to say what happened without guessing.

You can also run it from a terminal, where it prints instead of using windows. Open the
folder, type `cmd` in the address bar, and:

    ai-memory.exe status       what is running, and where the data is
    ai-memory.exe start        start it without the tray icon
    ai-memory.exe stop         stop it
    ai-memory.exe setup        show the client configuration again
    ai-memory.exe autostart    whether it starts when you log in (on/off)

`status` prints the database path, which is the quickest way to tell whether the portable
marker is doing its job: it should name the `data` folder beside the exe, not one under
`AppData`.
