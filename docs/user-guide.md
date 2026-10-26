# Ailurus Memory — the full guide

The short version is the [README](../README.md). This is everything else.

## Downloads

Every [release](https://github.com/TursiThePanda/ailurus-memory/releases) carries:

| File | What it is |
|---|---|
| `ailurus-memory-<version>-windows-setup.exe` | Windows installer (per-user, no admin prompt) |
| `ailurus-memory-<version>-windows-portable.zip` | the same program, unpacked instead of installed |
| `ailurus-memory-<version>-linux-portable.tar.gz` | Linux x86_64 binary, portable |
| `SHA256SUMS` | checksums of the three files above |
| `ghcr.io/tursithepanda/ailurus-memory:<version>` | container image, amd64 + arm64. See [docs/install/docker.md](install/docker.md) |

For a full Linux installation (systemd unit, tray indicator), use the source and
`install.sh` (below).

### Windows SmartScreen

The Windows executables are **not code-signed**. A certificate costs money every year,
and a new signature still triggers the warning until it builds a reputation. So the
first time you run the installer, Windows shows *"Windows protected your PC"*. Click
**More info**, check the file name, then click **Run anyway**. Check the checksum
(below) before you do that. SmartScreen cannot tell you the file is genuine, but the
checksum can.

### Checking a download

Download `SHA256SUMS` from the same release and compare:

```bash
# Linux, in the folder with the downloads
sha256sum -c SHA256SUMS --ignore-missing
# macOS
shasum -a 256 -c SHA256SUMS --ignore-missing
```

```powershell
# Windows PowerShell: compare the output with the line for this file in SHA256SUMS
Get-FileHash .\ailurus-memory-1.2.0-windows-setup.exe -Algorithm SHA256
```

Every file must report `OK` (or the hashes must be the same, letter for letter). If one
does not match, do not run it.

The container image is signed instead. See
[docs/install/docker.md](install/docker.md#10-checking-the-image-is-genuine) for
`cosign verify`. To report a security problem, see [SECURITY.md](../SECURITY.md).

## Installation (Linux)
```bash
./packaging/install.sh --semantic          # venv, dependencies, systemd unit, tray indicator
.venv/bin/ai-memory setup        # sets the master passphrase, prints the client config
```
Add `--qt` for a native-looking Qt passphrase dialog in KDE (~230 MB; the built-in one
is drawn with tkinter and is just as safe). `install.sh` is safe to re-run and never
touches the database — `--uninstall` removes the
unit, the desktop entry and the venv, and leaves the store exactly where it is. Drop
`--semantic` to skip onnxruntime and friends (~200 MB) if search by meaning is not wanted;
add `--tray` only to test the Windows-style tray, which does not work under Wayland.

It generates the systemd unit and both desktop entries **from wherever this checkout is**.
That used to be hardcoded, so the files installed cleanly for anybody and then pointed at a
directory that did not exist on their machine. To regenerate them after moving the
checkout: `ai-memory install-service` (`--print` shows them without writing).

Doing it by hand instead:
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[semantic]'     # or -r requirements.txt for the bare core
```

### What starts at login (and turning it off)

Both the memory and the tray icon start with your session by default. They are **two
independent switches on Linux**, because they are two different things: the systemd unit
runs the memory, the icon only watches it. Someone who would rather start the memory by
hand usually still wants the icon — greyed out, with *Start* in its menu.

```bash
ai-memory autostart                      # what is set now
ai-memory autostart off                  # neither starts at login
ai-memory autostart off --service        # only the memory waits to be started
ai-memory autostart on  --indicator      # put the icon back
```
Also in the admin web under *Settings → Autostart*, and `install.sh --no-autostart`
installs everything without starting any of it at login.

On Windows there is one switch, because there the tray IS the supervisor — it owns both
halves, so an icon that does not start means a memory that does not start.

**Reinstalling does not undo your choice.** `install-service` leaves a disabled unit
disabled and a hidden indicator entry hidden, and says so instead of quietly turning
autostart back on. (The indicator is switched off with XDG's `Hidden=true` rather than by
deleting the file, which is exactly what makes that possible.)

### The passphrase prompt

Drawn by this application, in this process: Qt if PySide6 is installed
(`pip install -e '.[qt]'` or `./packaging/install.sh --qt`, ~230 MB, so it is opt-in), otherwise
tkinter/ttk from the standard library. Which one draws it is cosmetic — Qt looks native
in KDE, ttk looks native on Windows — and changes nothing about the two things that
matter.

**The passphrase never leaves the process.** Until 1.0.0 the prompt was `kdialog` or
`zenity`, with the passphrase read back over a pipe. That was not a hole, but handing the
key to everything to another program is strictly worse than not handing it to anything.
They remain as a fallback for a machine with no GUI toolkit in the venv.

**The window says which application is asking.** The icon is drawn INSIDE it, beside the
name — not set as a window icon, which under Wayland does nothing whatsoever: there is no
window-icon protocol, the compositor derives one from `app_id` and the matching .desktop
file. `kdialog --icon` is therefore ignored no matter what you pass it (tested on Plasma
6.7.4). A nameless grey box asking for a password is what a phishing dialog looks like,
and this is the one prompt a user must be able to recognise.

### The tray indicator (Linux)

`indicator.py` puts an icon in the system tray: coloured while the memory is running,
grey while it is not, with the admin web, start and stop in its menu. **Quitting it does
not stop the memory** — systemd owns the processes here, unlike the Windows tray, which
owns them itself.

It runs on the **system** python, not the venv, because it needs `gi` (PyGObject) and an
isolated venv cannot have it. It imports nothing from this project, so that costs nothing.
This is why the 2026-08-29 conclusion "a Linux tray is impossible" was too broad: it is
true of pystray inside the venv, and false of an indicator outside it. Verified on Plasma
6.7.4 under Wayland.

## Running it (one process, one port, all clients through it)
```bash
# start (asks for the master passphrase; the first run sets it twice and creates the DB)
ai-memory start
# admin web: http://127.0.0.1:8080/    MCP endpoint: http://127.0.0.1:8080/mcp    health: /healthz
```

`ai-memory start` runs the single app in the background after one passphrase prompt; `ai-memory run` keeps it in the foreground, `ai-memory stop` stops it, `ai-memory status` says what is running. (Before the package is installed: `python cli.py start`.) Since 1.2.0 the admin web and the MCP endpoint are served by the same process on the same port — there is no longer a separate :8081, and `python webapp.py` / `python server.py` with `HOST`/`PORT` are no longer how it is run.

### Starting at login (systemd user unit)
```bash
ai-memory install-service                   # generates the unit for THIS checkout and enables it
systemctl --user start ai-memory            # asks for the passphrase in a desktop dialog
```
The unit brings up the app from a **single** prompt and is wanted by `plasma-workspace.target` (or `graphical-session.target` elsewhere), so it starts once the desktop session exists and stops when you log out — the key does not outlive the session. `install.sh` runs `install-service` for you.

Two things about that prompt are worth knowing, because both were surprises:

- **It is a desktop dialog (kdialog/zenity), not systemd-ask-password.** The agents that answer `systemd-ask-password` — console, plymouth, wall — watch the *system* query directory. A **user** unit asks in `/run/user/<uid>/systemd/ask-password`, where nothing is listening, so the prompt never appears and the service simply sits there until it times out. `unlock.py` therefore tries a desktop dialog first and keeps systemd-ask-password as the fallback for a console or a system unit.
- **The passphrase reaches the processes only through their environment.** Not the unit file, not an `EnvironmentFile`, not `Environment=` — a unit is world-readable and `Environment=` also shows up in `systemctl --user show-environment`. `ai-memory run` asks once and hands it to the app process, which is the only reason that foreground supervisor exists. For a Docker or systemd secret, `AI_MEMORY_PASSPHRASE_FILE` points at a file holding the passphrase instead.

A wrong or cancelled passphrase exits non-zero with a desktop notification; `Restart=on-failure` with `StartLimitBurst=2` means it gives up rather than throwing dialogs at you in a loop.

The per-half units were dropped in 1.0.0 along with the launch scripts, and since 1.2.0 there is only one process to start anyway.

### Configuration (env)
| variable | default | meaning |
|---|---|---|
| `AI_MEMORY_DB` | `~/.local/share/ai-memory/memory.db` | the encrypted file |
| `AI_MEMORY_PASSPHRASE` | – | the passphrase (otherwise a prompt / systemd-ask-password) |
| `AI_MEMORY_PASSPHRASE_FILE` | – | read the passphrase from this file (Docker / systemd secret) |
| `AI_MEMORY_PORT` | `8080` | the one port (`AI_MEMORY_MCP_PORT` still works, with a deprecation warning; `AI_MEMORY_WEB_PORT` is ignored) |

The network variables (`AI_MEMORY_HOST`, `AI_MEMORY_ALLOWED_HOSTS`, `AI_MEMORY_ADMIN_ALLOW`, `AI_MEMORY_MCP_ALLOW`, `AI_MEMORY_TRUSTED_PROXIES`, `AI_MEMORY_TLS`, `AI_MEMORY_KEY_IN_QUERY`) are described under *Network, access and HTTPS* below. The old `TRANSPORT` / `HOST` / `PORT` variables are gone.

### Portable mode

An empty file named **`ai-memory.portable`** next to the binary moves everything the
program writes — database, config, logs, attachments, backups — into a `data` folder in
that same directory (`paths.py:portable_dir`). It is switched on by that file and nothing
else: guessing from "is this directory writable?" would put the database in Windows'
VirtualStore when installed into `Program Files`, where it appears to work and then cannot
be found. An `AI_MEMORY_*` variable still wins over it.

The released portable archives are the marker plus the ordinary binary, packed by
`./packaging/make-portable.sh` from `releases/v<version>/` — no separate build. Two copies on one
machine now need only different `AI_MEMORY_PORT` values.

## Managing agents, keys and tags (CLI)
```bash
ai-memory-manage add-agent lmstudio-local            # creates an agent + prints the API key (once only)
ai-memory-manage add-agent claude --perm claude=rw --perm shared=rw --perm '*'=r
ai-memory-manage list
ai-memory-manage set-perm lmstudio-local shared rw
ai-memory-manage rotate-key lmstudio-local
ai-memory-manage change-passphrase

ai-memory-manage tag list                            # the taxonomy with record counts
ai-memory-manage tag rename konvence convention      # renaming onto an existing tag merges the two
ai-memory-manage tag delete obsolete                 # removes the tag from every record; records stay
```

## Connecting clients (over HTTP + a Bearer key)
**Claude Code:**
```bash
claude mcp add ai-memory --transport http http://localhost:8080/mcp \
  --header "Authorization: Bearer <the-claude-agent-key>"
```
**LM Studio** (`~/.lmstudio/mcp.json`) — LM Studio has a bug ([#1892](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1892)): for streamable-http it does not send the `Authorization` header. The key is therefore passed in the URL (`?key=`), which the server accepts as a fallback while the `?key=` switch is on (see *Network, access and HTTPS*):
```json
{ "mcpServers": { "ai-memory": {
  "url": "http://localhost:8080/mcp?key=<the-lmstudio-local-agent-key>" } } }
```

## Admin web
Served by the same process as the MCP endpoint, at `http://127.0.0.1:8080/` (log in with the master passphrase; `ai-memory start` brings it up). Sessions last 8 hours at most and expire after 30 minutes idle; after 5 wrong passphrases the login is throttled. There: agent and key management, the permission matrix, and a **memory browser** (the content is decrypted only here, in the browser, never on disk). It filters by namespace, type and tag, searches full text, and every row expands to show the record's parameters. Opening a record gives an editor for its description, type, tags, content and per-record permissions, with the full history underneath. Tags can be renamed or deleted from the UI. Sign-in with your master passphrase, a session cookie, CSRF protection, dark mode, English with a switch to Czech.

## Network, access and HTTPS

By default the app listens on localhost only. Everything about who can reach it lives in the admin web, under **Settings → Network and access**:

- **bind address and port** (default `127.0.0.1:8080`) and the **allowed host names** the app answers to;
- **MCP IP allow-list** — optional; the MCP endpoint is protected by agent tokens anyway;
- **admin IP allow-list** — mandatory; the default is localhost plus the private ranges, and the machine itself always gets in;
- **trusted proxies**, for running behind a reverse proxy;
- the **`?key=` switch** (a key in the URL, for clients like LM Studio that cannot send a header): off for a new memory, kept on for an existing one.

The environment variables `AI_MEMORY_HOST`, `AI_MEMORY_PORT`, `AI_MEMORY_ALLOWED_HOSTS`, `AI_MEMORY_ADMIN_ALLOW`, `AI_MEMORY_MCP_ALLOW`, `AI_MEMORY_TRUSTED_PROXIES`, `AI_MEMORY_TLS` (`off`, `self-signed` or `custom`) and `AI_MEMORY_KEY_IN_QUERY` only **seed** these settings on the first start; afterwards the admin web owns them. If a bad allow-list locks you out, `ai-memory network reset` restores localhost-only without TLS.

**HTTPS:** Settings → HTTPS can generate a self-signed certificate or take an uploaded one; it takes effect after a restart. Clients must then trust a self-signed certificate (for Claude Code, point `NODE_EXTRA_CA_CERTS` at it). For example, with a name such as `memory.example.org` or an address such as `192.0.2.10` added to the allowed host names, clients connect to `https://memory.example.org:8080/mcp`.

### Per-record permissions
By default a record inherits the ACL of its namespace — that is the ordinary case and needs no thought. For a sensitive record the **user** can restrict it to a chosen set of agents, and it then disappears from every path an unlisted agent has: read, list, search, history, the audit and the handshake index. A refused read returns the same error as a missing record, so a restricted record cannot be found by guessing names.

This is deliberately a user-only control: `share` can only be set through the admin web, never over MCP. If an agent could set it, a model could grant itself access to exactly what the user meant to keep from it. Restrictions narrow the namespace ACL, they never widen it.

## MCP tools

21 of them. The descriptions below come from the tool definitions themselves — an agent sees the same text on connect, so this table cannot drift from what the server actually offers (`tests/test_packaging.py` checks that every tool appears here).


**Reading**

| tool | what it does |
|---|---|
| `memory_read` | Read one memory, with metadata and provenance. |
| `memory_list` | List memory summaries (without the body). Optionally filter by type and tags (all given tags must match). You only see namespaces you may read. |
| `memory_search` | Full-text search across memories, optionally narrowed to a type and tags. Returns only records from namespaces you may read. |
| `memory_semantic_search` | Search the memory by MEANING — finds records that share no words with the query. |
| `memory_similar` | Records covering ground similar to this one, by meaning rather than words. |
| `memory_tags` | Existing tags with a record count. Look here BEFORE inventing a new one — a new tag can only be created after the user approves it (the new_tags … |


**Writing**

| tool | what it does |
|---|---|
| `memory_write` | Store a new memory (or overwrite one with the same name). Without a namespace it goes into your own. |
| `memory_update` | Update an existing memory (including a foreign or shared one). Records you as updated_by; the history is kept. The same tag rules as memory_write … |
| `memory_edit` | Replace exact text inside a record, without reading the whole thing. |
| `memory_verify` | Stamp a record as checked against reality, right now. |
| `memory_delete` | Delete a memory. The record's history is kept (you can look it up and restore it via memory_history). |
| `memory_move` | Move a memory to another namespace (typically promoting it to 'shared' so every agent can see it). Requires write permission on both the source and … |


**Files on a record**

| tool | what it does |
|---|---|
| `memory_attach` | Attach a local file to a memory — a diagram, a photo of the built thing, a screenshot of the error, a datasheet extract. |
| `memory_attachments` | What is attached to a record: id, filename, type, size, description. No bytes — use memory_attachment_get to actually retrieve one. |
| `memory_attachment_get` | Write one attachment to disk and return its metadata plus the path. |
| `memory_detach` | Remove an attachment from a record. The bytes are gone from the store; only the metadata survives, in the record's history. |


**Looking back, and hygiene**

| tool | what it does |
|---|---|
| `memory_history` | Version history of one record (who/when/what), oldest to newest. |
| `memory_revert` | Restore a record to the content of the given version (index from memory_history; 0 = oldest, -1 = previous). |
| `memory_audit` | Who/when/where/what across the whole memory, newest first. Unlike memory_history (a single record) this shows activity over the entire store. `since` … |
| `memory_lint` | Hygiene check over the memory: broken [[links]], records still marked as unfinished, untagged records, empty or duplicated descriptions. |
| `memory_overlaps` | Pairs of records that appear to cover the same ground — candidates for merging. |

### Duplicates are caught on the way in
`memory_write` embeds the new record immediately and compares it against everything already stored. If something scores above 0.65 the result carries a `similar` list naming it, and the agent is told to check whether this should have been a `memory_update` instead. The write still happens — a duplicate is a curation problem, not an error.

This is the same comparison `memory_overlaps` does, moved to the moment it is actionable: after the fact, someone has to remember to run the check and then reconstruct why two records exist; at write time the agent still has the context to merge them. Writing also refreshes the record's vector, so the semantic layer no longer needs a manual `ai-memory-manage embed --index` to notice new records.

### Verified vs. updated
### Reading a long record without paying for all of it

Records here are meant to be thorough — the detail is the point. But an agent that wants one threshold pays the same context cost as one that wants everything, and the biggest records are the ones consulted most often. So `memory_read` is section-addressable, using the `##` headings already in the markdown:

- `outline=True` — the headings and their sizes, nothing else
- `section="firmware"` — that section (case-insensitive, substring is enough) plus the text above the first heading, where the status line usually lives
- neither — the whole record; above 12k characters it comes back as an outline instead

A record with no headings behaves exactly as before. **Every partial response says it is partial and lists what it left out**, so a model can tell "the record does not mention X" apart from "you did not read the part with X" — without that guarantee, splitting records would trade context for wrong answers.

### Verification stamps

`memory_verify` stamps a record as *checked against reality, today*. That is a different fact from `updated`, and conflating them is what quietly rots a memory: `updated` says when the text last changed, so a record written once and never opened again is indistinguishable from one confirmed yesterday. Editing the content clears the stamp automatically — a confirmation must not outlive the text it vouched for; re-tagging does not, because it changes no claim.

On connect, the server sends the memory protocol plus an index of record *names* (filtered by the agent's ACL) in the `instructions` field of the `initialize` response, so an agent knows what is stored without fetching everything. Detail is pulled on demand. The index is built at connection time — a record written now becomes visible in the next session.

Tags are a closed taxonomy: an agent may only use a tag that already exists, or one the user explicitly approved and it passes in `new_tags`. This stops the taxonomy fragmenting into `net` / `network` / `networking` depending on which model happens to be writing.

## Hygiene checks
A memory decays quietly: a record gets renamed and every `[[link]]` to it dies, a record says "AWAITING VERIFICATION" for a month, a tag is used once and forgotten. `memory_lint` (MCP) and `ai-memory-manage lint` (CLI) find that class of problem with no model and no network — they work when nothing else is running:

- **broken** — a `[[link]]` pointing at a record that does not exist; an empty record
- **stale** — the record still shouts AWAITING / NOT VERIFIED / TODO / STILL OPEN about itself; or it holds a volatile fact (an IP, a port, a version, an endpoint) that nobody has confirmed in 180 days
- **hygiene** — untagged, no description, a description that only repeats the name; a record no agent has read since counting began; **numbers that look measured but carry no date or source**

The staleness check counts from `verified` when there is one and from `updated` otherwise, and only fires on records that actually contain something perishable — a record about a decision or a preference can sit for years and stay true, and nagging about it is how a check earns its way into being ignored.

**`unsourced-numbers`** exists because of a real failure: a record stated `spin = 300-500 W` as flatly as every measurement around it. It was an early guess someone wrote down, and months later it was used to decide something. The measured figure turned out to be 199–277 W. A record cannot say which of its numbers was ever checked, so this check asks. It is calibrated against the real store — a first, wider version flagged 21% of records, almost all of it nameplate values (`12 V`, `16 A`, `1800 mAh`), which are printed on the thing and do not rot. Volts, amps and capacities are excluded, as are numbers next to words like *rated*, *max*, *threshold* or *setpoint*: a specification is a choice, not an observation. What remains, at ~12% of records, is worth reading.

**`unread`** answers the question `verified` cannot: is anyone still using this? Reads are counted per record (agents only — the user browsing the admin web would make everything look alive, which is the signal being measured; listing and searching do not count either). A record nobody has opened in 180 days is either dead weight or has a description that fails to say what it is for. It stays silent until counting has actually been running that long, so it does not fire on the whole store the day it ships.

`--all` (CLI) / `include_optional` (MCP) adds single-use tags. It is off by default because the first real run made the case against it: 9 of 19 findings were single-use tags and not one was actionable — in a small memory plenty of good categories legitimately have one member, and a check that is mostly noise teaches you to skim past the findings that matter.

It reports and never edits. A curator that quietly rewrites records destroys the one property that makes the memory worth trusting: knowing who wrote what.

## Attachments

A record can carry files — a wiring diagram, a photo of the finished case, a screenshot of an error that no longer reproduces. Content **and** metadata are encrypted like everything else (a filename gives away as much as a description), and they live as blobs in the same SQLite file, so "the whole memory is one file" stays true and the existing backup covers them with no extra work.

```
memory_attach(namespace, name, file_path, description)   # from a local path
memory_attachments(namespace, name)                      # metadata only, no bytes
memory_attachment_get(namespace, name, attachment_id)    # writes it out, returns the PATH
memory_detach(namespace, name, attachment_id)
```

`memory_edit(namespace, name, find, replace, expect=1)` exists for the same reason: a record over the full-read limit cannot be pulled into context, so fixing one renamed `[[link]]` or one wrong number in it had no safe route. It replaces exact text and **refuses to change anything if the occurrence count is not what the caller expected** — a blind replace across a record nobody has read is how a fix silently rewrites three other sentences.

**What belongs in an attachment:** what cannot be reconstructed from prose and has no other home — a screenshot of a message, a PDF receipt, a photo of the wired board, a registry lookup taken on a date. **What does not:** source code that lives in git, and anything else that has a canonical home elsewhere. A copy in the memory would only go stale and then lie.

In the admin web each record has an Attachments panel: upload, download, delete, with images previewed inline.

Three decisions worth knowing:

- **Agents get a path, never the bytes.** An image base64-encoded into a conversation can cost more context than every record in the store put together, and usually the agent only needs to know the file exists. `memory_attachment_get` writes it to `~/.cache/ai-memory/attachments` and hands back the path.
- **Attachments inherit the record's visibility.** There is no separate ACL: a file that outlived its record's `share` restriction, or the record's deletion, would be a hole in the per-record permissions. Deleting a record deletes its files; **a move re-points them first**, or promoting a record to `shared` would silently throw its diagram away.
- **Downloads are always `Content-Disposition: attachment` with `nosniff`.** An attachment is arbitrary user-supplied content; rendering an uploaded `.html` or `.svg` inline would run script in the admin session's origin.

Default cap is 25 MB per file (`AI_MEMORY_MAX_ATTACHMENT_MB`). Remember the store is copied whole by every backup, 14 copies deep, over SMB — a few hundred MB of photos is fine, a video library is not.

## Backup

The whole memory is one encrypted file, so a backup is a copy of that file — and because it is encrypted, **the destination does not have to be trusted**. No passphrase is needed to make one, which is what lets it run unattended from a timer while the server holds the key in RAM.

```bash
python -m ai_memory.features.backup --dest /mnt/backup/ai-memory --keep 14
python -m ai_memory.features.backup --json          # machine-readable, for a UI or a status check
```

Two things it does that a `cp` does not:

- **A consistent snapshot of a live database.** The store runs in WAL mode; a plain copy can capture a torn state that looks fine and refuses to open later. This uses SQLite's backup API. The source is opened read-write on purpose — a read-only connection cannot attach to the WAL index and would silently produce a backup **missing the newest records**.
- **Verify before publishing.** The copy is made and checked locally (opens, `PRAGMA integrity_check`, has records, and still carries the wrapped master key), then moved to the destination under a temporary name and renamed. An interrupted transfer leaves a `.part` file, never a truncated backup that looks legitimate. If the destination is missing or read-only — usually an unmounted NAS — it fails loudly instead of writing to the local disk under the mount point.

**Scheduling it is in the admin web, not in systemd** (since 1.0.0): *Settings* →
destination folder, interval, how many copies to keep, plus a button to export now. It runs
identically on Linux and Windows, which a systemd timer plus Task Scheduler never would —
one job done by two mechanisms differs exactly where a user can least cope with it.

One behavioural difference worth knowing, because it is a real trade: the schedule lives
inside the admin web, which only exists once the passphrase has been typed. A missed window
is caught up (it measures from the last successful export, not a wall-clock slot) but only
after the memory is next unlocked. A day the memory is never started is a day with no
backup. The old systemd timer needed no passphrase and did not have that property.

**A backup that stopped is warned about, not waited for.** Every page of the admin web
carries a banner once the last successful export is older than twice the configured
interval (never less than two days), and `memory_lint` returns the same state in a
`backup` field, so an agent asked to check the memory's health sees it too. Silent when no
export is configured — warning about a backup nobody asked for is how a real warning gets
ignored later.

**The passphrase is not in the backup.** That is the design — the file is unreadable without it — but it means a lost passphrase makes every copy permanently useless. Keep it somewhere that is not this machine.

## Semantic layer (optional)
Duplicate detection and search-by-meaning run a small embedding model
(**paraphrase-multilingual-MiniLM-L12-v2**, ONNX) **inside this application** — no LM
Studio, no cloud, no GPU. One forward pass per record, not token-by-token generation, so
it is fast on weak hardware.

**Multilingual since 1.1.0, and the reason was a measurement.** The previous model was
English-only, while the case that actually arises here is asking in one language about
records written in another. Five queries against real record texts: the old model got 4/5
with Czech scores of 0.144–0.421, the new one 5/5 with 0.487–0.610 — and scores a Czech
question against its English answer at 0.821. Search was quietly useless in the language
it was being used in, and a wrong hit looks exactly like a right one. The cost is size:
**449 MB against 87 MB**, downloaded once.

**Changing the model invalidates the index, deliberately.** Every stored vector records
which model produced it, and search REFUSES vectors from any other model rather than
ranking them: two models give 384 numbers each with no shared meaning, so comparing them
returns confident nonsense. `ai-memory-manage embed` reports how many are waiting; `--index`
rebuilds them, no `--force` needed.

```bash
pip install -e '.[semantic]'
ai-memory-manage embed --setup     # downloads the model — the ONLY command here that uses the network
ai-memory-manage embed --index     # embed new and changed records
ai-memory-manage embed --overlaps  # records that cover the same ground (default threshold 0.65)
ai-memory-manage embed             # status: model present? how much is indexed?
```

The download is a deliberate, user-run step. **Nothing else in this project ever opens an outbound connection** — the server only listens on `127.0.0.1`, and with the model absent the semantic tools return an instruction instead of failing, while everything else keeps working.

Two design points worth knowing:
- **Vectors are encrypted like the payloads.** An embedding is partially invertible, so a plaintext vector table beside an encrypted store would quietly undo the encryption. Per-record `share` restrictions reach the vectors too — a hidden record must not reappear as a similarity hit.
- **Staleness is a hash of the embedded text, not a timestamp.** Timestamps here have second resolution, so an edit landing in the same second as the indexing pass used to slip through and leave a vector describing text that no longer existed.

MCP tools: `memory_similar` (what else covers this ground — worth calling before writing a new record), `memory_semantic_search`, `memory_overlaps`.

## Tests
```bash
python tests/test_store.py   # the encrypted store: CRUD, cross-agent, history/revert, auth/ACL, no plaintext on disk
python tests/test_tags.py    # the tag taxonomy: registry, enforced approval, filtering, rename/delete
python tests/test_share.py   # per-record permissions: hidden from read/list/search/history/audit, user-only
python tests/test_lint.py    # hygiene checks: finds real problems, invents none, never writes
python tests/test_verify.py  # the verification stamp: cleared by an edit, and the staleness check built on it
python tests/test_semantic.py # embeddings: encrypted vectors, incremental indexing, share respected
                        # (skips itself when the optional model is absent)
python tests/test_download.py # model download: resume, a server ignoring Range, a dead server
python tests/test_index.py   # the handshake index, ACL on the index and the audit, delete/move/audit
python tests/test_http.py    # e2e over HTTP: 401 without a key, ACL enforced
python tests/test_web.py     # the admin web: login, permission matrix, filtering, editing, tag admin, CSRF
```

## Roadmap
- **A curator that judges, not just measures** — summarising records that have grown unwieldy, and spotting two records that contradict each other. Needs a generative model; the deterministic and embedding layers deliberately do not.
