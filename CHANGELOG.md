# Changelog

All notable changes to Ailurus Memory (`ai-memory`). Versions follow
[semantic versioning](https://semver.org/). Releases before 1.2.0 were published from a
private repository; their entries are summaries of its history.

## [1.4.0] — 2026-11-14

### Added
- **Update check, off by default.** Settings → *Update check* asks GitHub once a day
  which version is the latest. When a newer one exists, the admin web shows a banner,
  and — unless switched off — every agent's instructions get an `UPDATE AVAILABLE`
  paragraph, so the AI you talk to mentions the new version once per session. Nothing is
  downloaded or installed. It is the only outbound connection the program opens; only a
  version number is taken from the answer, so it cannot be used to inject text into the
  agents' instructions.

## [1.3.0] — 2026-11-14

### Added
- **AI instructions in the admin web.** A new **AI instructions** page edits the text
  every agent receives on connect, which until now was fixed in the code. Everything is
  editable, the SECURITY paragraph included; the page warns while that paragraph is
  missing or changed. Each agent can also get its own **addendum**, sent to that agent
  only under `FOR YOU (<agent name>)`, and **Preview** shows exactly what an agent
  receives. Changes apply from each client's next connection, with no restart. The text
  is stored encrypted in the database, so it travels with backups. Until you save your
  own text the built-in default is used and follows new releases.

### Changed
- **The default instructions now say "one record = one subject".** Point 4 used to say
  only "prefer updating an existing record over creating a second one on the same topic",
  which one agent read as "put everything done on this machine into the machine's
  record", until that record reached 61 000 characters. It now asks for one record per
  device, project, problem or decision, a link from the machine record instead of a copy,
  a split at ~12k characters, and a short status above the first heading.

## [1.2.2] — 2026-10-26

The first release with the container image (`ghcr.io/tursithepanda/ailurus-memory`).
1.2.1's image build passed its tests but failed the workflow's last check, which still
imported the pre-package module names. No change to the program itself.

## [1.2.1] — 2026-10-26

First public release, as **Ailurus Memory**, under the PolyForm Noncommercial 1.0.0
licence. It carries everything listed under 1.2.0 below, plus: the code is one
`ai_memory` package (`core`, `server`, `web`, `features`, `desktop`) with packaging,
scripts and docs in their own folders; a one-page README with the full text in
`docs/user-guide.md`; a Portainer stack in the Docker guide; Windows test fixes.

## [1.2.0] — tagged, never released

The tag's builds failed on Windows-only test issues before a release was made; 1.2.1 is
the same work with those fixed.

The first public release, under the outside name **Ailurus Memory**. The command, the
data directories, the `AI_MEMORY_*` settings and the MCP server name stay `ai-memory`,
so existing installations and client registrations keep working without a migration.

### Changed
- **One process, one port, one origin.** The MCP server and the admin web run as one
  process on one port (default 8080): the admin web at `/`, MCP at `/mcp`. The memory is
  unlocked once, for both. The separate admin port (8081) is gone. MCP clients keep
  their `/mcp` URL and only need the port changed if it was not 8080; bookmarks of the
  admin web change to the main port.
- **New look.** The admin web has a refreshed design with the red panda logo, light and
  dark themes, and still no external fonts or scripts. The same logo is used for the
  tray, the favicon and the Windows icon.
- The release assets are named `ailurus-memory-<version>-…`.

### Added
- **Network settings in the admin web.** Bind address and port, allowed host names
  (DNS-rebinding protection for both MCP and the admin web), client IP allow-lists for
  the admin web and for MCP, and trusted reverse proxies. The admin allow-list is
  mandatory and cannot be set to "anywhere". Localhost is always allowed, so a typo
  cannot lock you out. `ai-memory network reset` returns to localhost-only.
- **Native TLS.** HTTPS without a proxy, with a generated self-signed certificate or your
  own uploaded one. Session cookies are `Secure` whenever TLS is on.
- **Docker image** for linux/amd64 and linux/arm64 on
  `ghcr.io/tursithepanda/ailurus-memory`: the embedding model built in, HTTPS by default,
  unlock from a Docker secret or interactively with `docker attach`, health check on
  `/healthz`, non-root. Signed with cosign (keyless), with an SBOM and build provenance.
  See [docs/install/docker.md](docs/install/docker.md).
- **Login throttling** in the admin web: repeated wrong passphrases are slowed down. An
  optional HTTPS-only session cookie (`AI_MEMORY_WEB_HTTPS_ONLY`).
- **Installation block** on the admin Settings page: which build is running and where
  its data lives.
- **Every release is built in CI**: Windows installer, Windows portable zip, Linux
  portable tar and `SHA256SUMS`, all attached to the GitHub Release.
- `SECURITY.md` (threat model, private vulnerability reporting), this changelog, and a
  `LICENSE` file: PolyForm Noncommercial 1.0.0 (free for personal and non-commercial use; commercial use only with the author's written permission).

## [1.1.1] — 2026-09-22

### Fixed
- Saving the autostart switch in the admin web returned a server error on every
  platform.
- The Windows CI workflow failed on its first run (test paths, the `status` exit code).

### Changed
- The README lists and describes all 21 MCP tools (it named 16).
- Test files moved into `tests/`.

## [1.1.0] — 2026-08-30

### Changed
- **Multilingual embedding model** (`paraphrase-multilingual-MiniLM-L12-v2`). Search by
  meaning now works across languages, for example a Czech query against English records.
  Existing vectors are marked stale and rebuilt with `ai-memory-manage embed --index`.
- Backups run when something changed, not only on a timer. The scheduled copy is
  optional, and a backup that has stopped is reported in the admin web and by
  `memory_lint`.

### Added
- The application icon, and the passphrase dialog drawn in-process (the passphrase no
  longer leaves the process).
- `install.sh` offers to run the setup and lists its options.

### Fixed
- The Windows build fails instead of shipping an executable without a tray.
- Build inputs (installer version, bundled assets) had drifted from the code.
- Tests that had only ever run on Linux now pass on Windows.

## [1.0.0] — 2026-08-30

The first release for other people's machines.

### Added
- **Encrypted store:** one AES-GCM SQLite file, key derived from a master passphrase
  (scrypt) and kept in RAM only; history and revert inside the database.
- **MCP server** with per-agent API keys and per-namespace permissions (`none`/`r`/`rw`),
  per-record restrictions, a handshake index, tag taxonomy, `memory_lint` hygiene checks,
  section reads and `memory_edit` for large records, attachments stored encrypted in the
  same file, verification stamps.
- **Semantic layer:** an embedded ONNX model for duplicate detection and search by
  meaning, with encrypted vectors and no network access at runtime.
- **Admin web:** passphrase login, agent and key management, permission matrix, memory
  browser and editor, tag administration, dark mode, English and Czech.
- **Runs elsewhere:** Linux installer (`install.sh`, systemd user unit, tray indicator),
  Windows installer and tray, portable mode, macOS paths; consistent backups of the
  live database.

[1.2.2]: https://github.com/TursiThePanda/ailurus-memory/releases/tag/v1.2.2
[1.2.1]: https://github.com/TursiThePanda/ailurus-memory/releases/tag/v1.2.1
