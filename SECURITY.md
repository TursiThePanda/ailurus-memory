# Security

Ailurus Memory (`ai-memory`) holds whatever your AI agents and you decide to remember,
which is often personal. This page says what the design protects, what it does not, and
how to report a problem.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private
reporting instead: on the repository page, go to *Security* → *Report a vulnerability*
(<https://github.com/TursiThePanda/ailurus-memory/security/advisories/new>).

Please include the version (`ai-memory --version`, or the image tag), how it is deployed
(installer, portable, Docker, behind a proxy or not), and the steps to reproduce. This
is a one-person project, so replies can take a few days. A fix ships as a new release,
and the advisory is published together with it.

Supported versions: only the latest release gets fixes.

## Threat model

### What the encryption protects

- **The disk and every copy of it.** The whole memory (records, history, agents, keys,
  permissions, attachments and the search vectors) is one SQLite file whose content is
  AES-GCM encrypted. Record names and tags are found through a blind index, not stored
  in clear. Someone who gets the file, a backup or a stolen disk gets ciphertext, plus
  this metadata, which is stored in clear: namespace names, agent names (the labels you
  give the keys), the history's timestamps and kinds of change (which agent changed
  something in which namespace, and when, but not what), read counters, and how many
  records exist and roughly how large they are.
- **Backups on untrusted storage.** A backup is a copy of that encrypted file, so it can
  go to a NAS, a USB disk or a cloud folder you do not fully trust.
- The key is derived from the **master passphrase** with scrypt and is kept only in RAM.
  The passphrase is never written to disk by the program, unless you opt in to one of
  the two unattended modes below.

### What it does NOT protect

- **A running, unlocked instance.** While the memory is unlocked, the decryption key is
  in the process's memory. Anyone who can read that process can read everything: root on
  the host, the container runtime, a debugger, a memory dump, a swap file or hibernation
  image. Encryption at rest is not access control at runtime.
- **The unattended start options.** If you give the passphrase through a Docker secret
  file (`AI_MEMORY_PASSPHRASE_FILE`), an environment variable, or the OS keyring
  ("remember the passphrase"), it lives on the same machine as the data. The encryption
  then protects a copy of the file that leaves the machine, not the machine itself.
  Prefer the secret file over an environment variable: environment variables are visible
  in `docker inspect` and in `/proc`.
- **What an agent is allowed to see.** An agent key with read access to a namespace can
  read all of it, and whatever an agent reads goes to its model provider. Give each agent
  only the namespaces it needs.
- **A lost passphrase.** No recovery exists, by design. Without the passphrase the data
  and all backups are permanently unreadable.

### The admin web is the master passphrase

Logging in to the admin web takes the master passphrase. Once logged in you can read and
edit every record, create and revoke every agent key, and change the passphrase. Treat
access to it like access to the passphrase:

- it is restricted by client IP, and **this restriction cannot be switched off**. By
  default only localhost and private network ranges may connect;
- wrong passphrases are throttled;
- sessions expire after a period of inactivity and after a fixed maximum;
- serve it over HTTPS (the default in Docker, with a self-signed certificate), or behind
  a TLS proxy you trust. Over plain HTTP, the passphrase crosses the network in clear
  text.

Do not expose the admin web to the internet. If you must reach it remotely, use a VPN.

### MCP endpoint and agent keys

- Every MCP request needs an agent key (`Authorization: Bearer ...`). Keys are random
  and are stored only as a keyed hash (HMAC blind index), never in clear. A key is shown once when it is created, and it can be
  rotated or revoked at any time from the admin web.
- Permissions are per namespace (`none` / `r` / `rw`), plus per-record restrictions that
  only the user can set. An agent cannot widen its own access.
- Requests whose `Host` is not in the allowed host names are refused, which blocks DNS
  rebinding attacks from a browser.
- The key-in-URL fallback (`?key=`, for clients that cannot send headers) leaks keys
  into logs and history. It is off by default for new installations.
- An optional IP allow-list can restrict `/mcp` further.

### Network position

- In Docker, use host networking (as in the example compose file) so that the IP
  allow-lists see real client addresses. On a bridge network every client looks like the
  bridge gateway.
- Behind a reverse proxy, list the proxy under trusted proxies. Forwarded headers from
  anyone else are ignored, so a client cannot pick its own address.
- The program makes **no outbound connections at runtime**. The only network download is
  the embedding model, and only when you run `ai-memory-manage embed --setup` yourself. The
  container image already contains the model.

### Supply chain

- Container images are built in GitHub Actions from a tagged commit, with an SBOM and
  SLSA provenance attached, and they are signed with cosign (keyless). See
  [docs/install/docker.md](docs/install/docker.md#10-checking-the-image-is-genuine) for
  the `cosign verify` command.
- The image's Python dependencies are pinned by hash, its base image is pinned by
  digest, and every GitHub Action is pinned by commit SHA.
- The Windows executables are **not code-signed**. Windows SmartScreen warns about them.
  Check every download against the release's `SHA256SUMS` (see the README).
