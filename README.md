# Ailurus Memory

<img src="assets/ailurus.svg" alt="" width="96" align="right">

A shared, **encrypted** memory for your AI agents (Claude Code, LM Studio, anything that
speaks MCP), self-hosted, with an admin web to see and edit what they remember.
(*Ailurus* is the red panda's genus; the command is `ai-memory`.)

- **One encrypted file.** Records, history, agents and keys live in one SQLite file that is
  ciphertext on disk. The key comes from your passphrase and exists only in RAM.
- **Per-agent keys and permissions** per namespace (none / read / read-write), revocable.
- **History and revert**, duplicate warnings, verification stamps, semantic search that
  understands Czech as well as English, attachments.
- **One port:** the admin web at `/`, MCP at `/mcp`. Who may connect (IP ranges, host
  names, trusted proxy) and HTTPS (self-signed or your own certificate) are set in the
  admin web.
- **Backups are just copies of that file**, safe to put anywhere: without the passphrase
  they are unreadable.

## Quick start

**Docker** (amd64 and arm64, HTTPS with a self-signed certificate out of the box):

```bash
curl -O https://raw.githubusercontent.com/TursiThePanda/ailurus-memory/master/docker-compose.yml
docker compose up -d && docker attach ailurus-memory     # first start: type a new passphrase twice, then Ctrl-P Ctrl-Q
docker exec -it ailurus-memory ai-memory-manage add-agent claude   # prints the agent key
```

Details: [docs/install/docker.md](docs/install/docker.md).

**Windows:** download `ailurus-memory-<version>-windows-setup.exe` (or the portable zip)
from [Releases](https://github.com/TursiThePanda/ailurus-memory/releases) and run it
([step by step](docs/install/windows.md)).
SmartScreen may warn about an unsigned program: *More info → Run anyway*.

**Linux:** the portable tarball from Releases, or from source with `./packaging/install.sh` (systemd
unit and tray indicator) — see the [guide](docs/user-guide.md#installation-linux).

## Connect an agent

```bash
claude mcp add ai-memory --transport http https://<your-host>:8080/mcp \
  --header "Authorization: Bearer <agent-key>"
```

Then open `https://<your-host>:8080/` and sign in with the passphrase to manage agents,
browse and edit memories, and set network access and HTTPS.

## Documentation

| | |
|---|---|
| [docs/user-guide.md](docs/user-guide.md) | everything: installation, configuration, clients, all MCP tools, backups, semantic search, tests |
| [docs/install/docker.md](docs/install/docker.md) | Docker and Portainer: running, unlocking, upgrades, verifying the image signature |
| [docs/install/windows.md](docs/install/windows.md) | Windows installer, step by step |
| [docs/install/windows-portable.md](docs/install/windows-portable.md) | Windows without installing |
| [SECURITY.md](SECURITY.md) | what the encryption does and does not protect, reporting a problem |
| [CHANGELOG.md](CHANGELOG.md) | what changed in each release |

## Licence

© 2026 David Urban. [PolyForm Noncommercial 1.0.0](LICENSE): free to use, study and change
for personal, hobby, educational and other non-commercial purposes. **Commercial use —
by or for a company, or reselling it — needs my written permission**; ask in
[Issues](https://github.com/TursiThePanda/ailurus-memory/issues).
