# Ailurus Memory in Docker

Ailurus Memory is a self-hosted memory for AI agents. Claude Code, LM Studio, Gemini CLI
and anything else that speaks MCP can read and write the same set of notes ("records").
Everything is stored in one encrypted SQLite file. Each agent has its own key and its own
permissions, and an admin web lets you read and edit everything yourself.

This page covers running it as a container. The program inside is still called
`ai-memory`, and so are its commands and its `AI_MEMORY_*` settings.

- Image: `ghcr.io/tursithepanda/ailurus-memory` (linux/amd64 and linux/arm64, so a
  Raspberry Pi 4/5 with a 64-bit OS works)
- One port, **8080**: the admin web is at `/`, MCP is at `/mcp`, `/healthz` is the health
  check
- HTTPS by default, with a self-signed certificate made on the first start
- The embedding model for search by meaning is built into the image. It works offline,
  and the container never downloads anything at runtime.

---

## 1. Before you start: the passphrase

All data is encrypted with a key derived from a **master passphrase**. It is never
stored by the program and it is not in any backup. **If you lose it, the data and every
backup of it are unreadable for good.** No recovery exists, by design. Put the
passphrase in a password manager before you go on.

The first passphrase you give a new, empty volume creates the database. After that the
same passphrase unlocks it.

---

## 2. Run it

Get the example [`docker-compose.yml`](../../docker-compose.yml) and change at least
`AI_MEMORY_ALLOWED_HOSTS` to the name your clients will use (the example uses
`memory.example.org`). Then:

```bash
docker compose up -d
```

The example uses **host networking** (`network_mode: host`) on purpose. Access to the
admin web is decided by the client's IP address. On Docker's default bridge network,
every request seems to come from the bridge gateway, which has a private address. The
app could then not tell your laptop from a stranger on the internet. With host
networking it sees the real address. It also means `ports:` does not apply: the app
listens directly on port 8080 of the host. Host networking works only on Linux hosts.
Docker Desktop on macOS/Windows does not support it the same way.

### Settings you give on the first start

These environment variables are **seeds**. The first start copies them into the
encrypted settings. After that the **admin web owns them**, and changing the variable
does nothing. Change them in the admin web instead.

| Variable | Meaning | Default |
|---|---|---|
| `AI_MEMORY_ALLOWED_HOSTS` | Host names clients use in the URL, comma-separated. Requests for any other `Host` are refused (protection against DNS rebinding). | localhost only |
| `AI_MEMORY_ADMIN_ALLOW` | IPs/CIDRs allowed to open the admin web. **Always restricted; "anywhere" is not accepted.** | localhost + private ranges (`127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16, fc00::/7, fe80::/10`) |
| `AI_MEMORY_MCP_ALLOW` | IPs/CIDRs allowed to reach `/mcp`. Optional. | anywhere (every request still needs an agent key) |
| `AI_MEMORY_TRUSTED_PROXIES` | Reverse proxies whose `X-Forwarded-For`/`-Proto` headers are believed. | none |
| `AI_MEMORY_TLS` | `self-signed` or `off` (only behind your own TLS proxy). | `self-signed` |
| `AI_MEMORY_PORT` | Port to listen on. | `8080` |

The image fixes these paths. You don't need to set them: data in `/data` (with the
config in `/data/config`, including the TLS certificate, so it survives a recreate), the
model in `/models`, backups in `/backup`.

The container runs as uid **10001**, not root. A named volume (as in the example) gets
the right owner automatically. For a bind mount, prepare the directory first:
`sudo chown 10001:10001 ./data`. (Rootless Podman maps uids, so use
`podman unshare chown 10001:10001 ./data` there.)

---

### In Portainer (as a stack)

Portainer → **Stacks** → **Add stack** → **Web editor**, name it `ailurus-memory`, paste
this and **Deploy the stack**. It is the same as `docker-compose.yml`, trimmed to what a
stack needs; change the host name and the admin allow-list to yours.

```yaml
services:
  ailurus-memory:
    image: ghcr.io/tursithepanda/ailurus-memory:latest
    container_name: ailurus-memory
    restart: unless-stopped
    network_mode: host          # the app must see real client IPs (see section 2)
    stdin_open: true            # so the console can unlock it
    tty: true
    init: true
    environment:
      AI_MEMORY_ALLOWED_HOSTS: "memory.example.org,localhost,127.0.0.1"
      AI_MEMORY_ADMIN_ALLOW: "127.0.0.1/32,::1/128,192.0.2.0/24"   # your LAN
      # AI_MEMORY_PORT: "8080"
      # Unattended unlock (b) below): uncomment this AND the passphrase volume line.
      # AI_MEMORY_PASSPHRASE_FILE: /run/secrets/ai_memory_passphrase
    volumes:
      - ailurus-data:/data
      # - /srv/ailurus-memory/passphrase.txt:/run/secrets/ai_memory_passphrase:ro
volumes:
  ailurus-data:
```

Portainer's web editor has no directory next to the stack file, so Compose
`secrets: file:` with a relative path does not work there. For unattended unlock, put the
passphrase in a file on the host (`chown 10001:10001`, `chmod 400`), mount it read-only as
shown, and set `AI_MEMORY_PASSPHRASE_FILE`. Avoid putting the passphrase itself into the
stack's environment variables: Portainer stores and shows them in its UI.

To unlock by hand instead: **Containers** → `ailurus-memory` → **Console** → **Attach**,
type the passphrase (twice on the very first start) and press Enter, then close the
console. The first agent key: **Console** → **Exec** with `/bin/sh`, then
`ai-memory-manage add-agent claude-code`.

## 3. Unlock it

A started container is **locked** until it has the passphrase. You can give it the
passphrase in either of two ways.

### a) By hand, every start (most secure)

The passphrase exists only in your head and in the container's RAM.

```bash
docker attach ailurus-memory
```

Type the passphrase and press Enter (on the very first start it is asked twice, because a typo would lock the new memory for good). (If the prompt was printed before you attached, you
will not see it. Type anyway.) Then **detach with Ctrl-P Ctrl-Q**. Ctrl-C would stop
the container. The compose file sets `stdin_open: true` and `tty: true` for this. With
plain `docker run`, use `-it --init` (`--init`, so that `docker stop` works while the
container is still waiting for the passphrase).

The cost: after every restart (host reboot, upgrade) the memory stays locked, and agents
get errors, until you attach and unlock it again.

### b) Unattended, from a Docker secret

For a server that must come back by itself after a reboot. Put the passphrase alone in
a file, make it readable by the container user only, and uncomment the `secrets:` parts
and `AI_MEMORY_PASSPHRASE_FILE` in the compose file:

```bash
printf '%s' 'your passphrase' > ai_memory_passphrase.txt
sudo chown 10001:10001 ai_memory_passphrase.txt
sudo chmod 400 ai_memory_passphrase.txt
```

Be clear about what this trades away: the passphrase now sits on the same machine as the
data it protects. Anyone who can read that file and the volume has everything. Use a
file, not an `AI_MEMORY_PASSPHRASE=` environment variable, because environment variables
show up in `docker inspect`. See [SECURITY.md](../../SECURITY.md).

### Is it up?

```bash
docker ps        # STATUS shows (healthy) once the memory is unlocked
docker logs ailurus-memory
```

The health check asks `/healthz`, which answers 200 only when the store is unlocked. A
container that waits for its passphrase shows `starting` for up to 10 minutes and then
`unhealthy`.

---

## 4. The first agent key

Every AI client gets its own key. Make the first one from the command line:

```bash
docker exec -it ailurus-memory ai-memory-manage add-agent claude-code
```

It asks for the master passphrase and prints the key **once**. Copy it now, because it
cannot be read back later (you can only rotate it). After that you can create and manage
keys in the admin web.

The admin web is at `https://memory.example.org:8080/` (or `https://localhost:8080/` on
the host itself). Log in with the master passphrase. Your browser will warn about the
self-signed certificate the first time. That is expected (see section 6).

---

## 5. Connect your AI

Use the URL `https://memory.example.org:8080/mcp` and the agent's key.

### Claude Code

```bash
claude mcp add --transport http ai-memory https://memory.example.org:8080/mcp \
  --header "Authorization: Bearer YOUR-KEY-HERE"
```

With the self-signed certificate, Claude Code refuses the connection until you tell it
to trust that certificate. Get the certificate (section 6) and start Claude Code with:

```bash
export NODE_EXTRA_CA_CERTS=/path/to/ailurus-memory.pem
claude
```

To make it permanent, put the variable into the `env` block of `~/.claude/settings.json`
rather than your shell profile. Background agents only see it there.

```json
{ "env": { "NODE_EXTRA_CA_CERTS": "/path/to/ailurus-memory.pem" } }
```

Claude Code also trusts the operating system's certificate store, so adding the
certificate there (section 6) works too.

### LM Studio

`~/.lmstudio/mcp.json`:

```json
{ "mcpServers": { "ai-memory": {
  "url": "https://memory.example.org:8080/mcp",
  "headers": { "Authorization": "Bearer YOUR-KEY-HERE" } } } }
```

Two caveats:

- Some LM Studio versions do not send the `Authorization` header for HTTP MCP servers
  ([lmstudio-bug-tracker#1892](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1892)).
  The fallback is the key in the URL, `.../mcp?key=YOUR-KEY-HERE`. This is **off by
  default** for new installations, because a key in a URL ends up in logs and in
  history. Switch it on in the admin web only if you need it.
- LM Studio must trust the self-signed certificate. Add it to the operating system's
  trust store (section 6). If it still refuses, put a reverse proxy with a real
  certificate in front (section 7).

### Gemini CLI and others

Anything that takes an `mcpServers` block with an HTTP URL and headers works the same
way. For Gemini CLI, use `"httpUrl"` instead of `"url"`.

---

## 6. The self-signed certificate

On the first start the container generates a certificate for the configured host names
and keeps it in `/data/config`. Clients have to be told to trust it. To get a copy:

- download it from the admin web, or
- fetch it from the running server:

  ```bash
  openssl s_client -connect memory.example.org:8080 -servername memory.example.org </dev/null \
    | openssl x509 > ailurus-memory.pem
  openssl x509 -in ailurus-memory.pem -noout -fingerprint -sha256
  ```

  Fetching it over the network trusts whatever answered. Check that the fingerprint
  matches the one the admin web shows before you rely on it.

Adding it to the operating system's trust store:

```bash
# Debian / Ubuntu
sudo cp ailurus-memory.pem /usr/local/share/ca-certificates/ailurus-memory.crt
sudo update-ca-certificates
# Fedora / RHEL
sudo cp ailurus-memory.pem /etc/pki/ca-trust/source/anchors/ailurus-memory.pem
sudo update-ca-trust
```

On Windows: double-click the file → *Install Certificate* → *Local Machine* → *Trusted
Root Certification Authorities*. On macOS: open it in Keychain Access and set it to
*Always Trust*.

The certificate must contain the host name the client uses. If you add a host name later
in the admin web, generate a new certificate there and distribute it again.

---

## 7. Behind your own reverse proxy (optional)

If you already run Caddy, nginx or Traefik with a real certificate, let the proxy handle
TLS:

- set TLS to off (`AI_MEMORY_TLS=off` on the first start, or in the admin web), and let
  the app listen only on loopback or on the proxy's network;
- add the proxy's address to the trusted proxies, or every client will look like the
  proxy;
- the proxy must pass the original `Host` header (or the name it uses must be in the
  allowed host names).

The app does not obtain Let's Encrypt certificates itself. That is the proxy's job.

---

## 8. Backups

The database is already encrypted, so a backup can go anywhere, including an untrusted
disk or a cloud folder. **The passphrase is not in it.** Keep the passphrase separately,
or the backups are useless.

- Mount a directory at `/backup` (see the compose file), then turn on the scheduled
  export in the admin web (*Settings*). It runs only while the memory is unlocked.
- A one-off copy, any time, with no passphrase needed:

  ```bash
  docker exec ailurus-memory python /app/backup.py --dest /backup --keep 14
  ```

  It takes a consistent snapshot of the live database and checks it before it publishes
  it.
- To restore, stop the container, put the backup file into the volume as `memory.db`,
  start it again and unlock it with the passphrase that was in use when the backup was
  made.

---

## 9. Upgrading

```bash
docker compose pull
docker compose up -d
```

The data lives on the volume and is kept. Make a backup first anyway. After the restart
the memory is locked again, unless you use the passphrase secret. Agents reconnect by
themselves once it is unlocked. Their keys live in the database and survive any
restart. Read [CHANGELOG.md](../../CHANGELOG.md) before you jump a minor version.

Prefer pinning a version (`ghcr.io/tursithepanda/ailurus-memory:1.2.0`) over `latest`
on a server you do not watch.

---

## 10. Checking the image is genuine

Every published image is signed with [cosign](https://docs.sigstore.dev/) in "keyless"
mode. No key file is involved: the signature is tied to the GitHub Actions workflow that
built the image, in this repository, at that tag. To check it:

```bash
cosign verify ghcr.io/tursithepanda/ailurus-memory:1.2.0 \
  --certificate-identity "https://github.com/TursiThePanda/ailurus-memory/.github/workflows/build-docker.yml@refs/tags/v1.2.0" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

Replace both `1.2.0` with the version you pull. To accept any release tag of this
repository instead of one exact tag:

```bash
cosign verify ghcr.io/tursithepanda/ailurus-memory:latest \
  --certificate-identity-regexp '^https://github\.com/TursiThePanda/ailurus-memory/\.github/workflows/build-docker\.yml@refs/tags/v' \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

The image also carries an SBOM and SLSA build provenance:

```bash
docker buildx imagetools inspect ghcr.io/tursithepanda/ailurus-memory:1.2.0 --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/tursithepanda/ailurus-memory:1.2.0 --format '{{ json .Provenance }}'
```

---

## 11. Building it yourself

```bash
docker build -t ailurus-memory .                 # the image
docker build --target test -t ailurus-test .     # the image + the whole test suite
```

Every Python dependency is pinned by version and SHA-256 in `docker/requirements.lock`
(regenerate it with `docker/update-lock.sh`). The base image is pinned by digest. The
embedding model is pinned by its HuggingFace commit and SHA-256.

## If something goes wrong

- **Agents get errors after a restart:** the memory is locked. `docker attach` and
  unlock it, or use the passphrase secret.
- **"421 Misdirected Request" / host not allowed:** the name in the client's URL is not
  in the allowed host names. Add it in the admin web.
- **The admin web says access denied:** your IP is not in the admin allow-list. From the
  host itself (`https://localhost:8080/`) you can always get in.
- **Certificate errors in a client:** see section 6. The certificate must name the host
  the client uses.
- **Permission denied on /data:** the directory is not owned by uid 10001 (section 2).
