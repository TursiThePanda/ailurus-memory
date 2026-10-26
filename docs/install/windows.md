# Ailurus Memory

A private, encrypted memory that several AI assistants can share.

Instead of explaining your setup, your projects and your preferences again in every new
chat, the assistant reads and writes a memory that lives on **your** machine. Everything
in it is encrypted with a passphrase only you know. Nothing is sent anywhere: the program
listens on your own computer and never makes an outbound connection.

---

## 1. Installing it

Double-click **ailurus-memory-VERSION-windows-setup.exe** (VERSION is the release
number, for example 1.2.2).

(Rather not install anything — to try it first, or to carry it on a USB stick? Take the
portable zip released beside this one, the file whose name ends in
**-windows-portable.zip**. Same program, unpacked instead of installed, and everything it
stores stays in the folder you unpack. It has its own README.)

It installs for your user only, so it never asks for an administrator password and it
touches nothing outside your own profile. It puts AI Memory in your Start menu and offers
to start it when you log in.

### Windows will warn you, and that is expected

The program is not signed with a paid certificate, so Windows shows a blue
**"Windows protected your PC"** box. The Run button is hidden:

    click "More info"  ->  then "Run anyway"

Your antivirus may also stop it. The reason is the packaging format, not the contents —
one file that unpacks itself is common for malware, so scanners are suspicious of it by
default. If it gets quarantined, allow it by name.

### Starting it

**Start menu → AI Memory.** (Start it from the Start menu, not from a remote session —
a program launched over remote access can end up drawing its windows where you cannot
see them.)

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

    C:\Users\<you>\AppData\Local\ai-memory

That one folder is everything — the encrypted database and its logs. Back it up and you
have backed up the lot. (Section 4 sets that up properly, on a schedule.)

To remove the program: Settings → Apps → AI Memory → Uninstall. **That leaves the folder
above alone**, on purpose — uninstalling an application should not delete your data. If
you really want it gone, delete that folder by hand, and know that no backup of it can be
read without your passphrase either.

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

What it writes is the **encrypted database**, not a readable dump. It carries no
passphrase, so a copy of it is useless to anyone who does not have yours. That is what
makes it safe to put on a NAS or in cloud storage.

Which is also the trap: **the passphrase is not in the backup.** A backup plus a forgotten
passphrase is worth nothing. Password manager. Today.

---

## 5. If something goes wrong

The program writes what it did to:

    C:\Users\<you>\AppData\Local\ai-memory\logs\startup.log

Send that file. It records how it was started, which database it found, and where it
stopped — usually enough to say what happened without guessing.

You can also run it from a terminal, where it prints instead of using windows. The
program is at `%LOCALAPPDATA%\Programs\AI Memory\ai-memory.exe`:

    ai-memory.exe status       what is running, and where the data is
    ai-memory.exe start        start it without the tray icon
    ai-memory.exe stop         stop it
    ai-memory.exe setup        show the client configuration again
    ai-memory.exe autostart    whether it starts when you log in (on/off)
