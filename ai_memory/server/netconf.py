"""Who may reach the memory, from where, and how. Configured in the admin web.

Since 1.2.0 the memory is one process on one port: the admin web at `/`, MCP at
`/mcp`. Everything about network access lives HERE and is stored (encrypted) in the
memory's own settings, so a reverse proxy in front has nothing to do but terminate TLS.

THE TWO HALVES STILL HAVE DIFFERENT KEYS TO THE HOUSE, which is why their allow-lists
are separate:

  MCP (/mcp)    authenticates per agent with a revocable API key, scoped by namespace
                permissions. Reachable from ANYWHERE by default -- the token is the
                protection; an IP allow-list is an optional second layer.

  ADMIN (/)     authenticates with the MASTER PASSPHRASE and grants everything. It is
                ALWAYS restricted by client address; the default is localhost plus the
                private ranges, and "anywhere" is not an option.

PRECEDENCE: environment variables are a SEED, read once when the stored settings do
not exist yet (a fresh install, a container's first start, or an upgrade from the old
two-port layout). After that the stored settings are authoritative and the admin web
owns them. The old behaviour -- an env var silently overriding the stored value -- made
the setting in the web dead in exactly the case it was built for (Docker sets the env).

CLIENT ADDRESSES: the socket peer is the truth. X-Forwarded-For / X-Forwarded-Proto
are believed ONLY when the peer is a configured trusted proxy -- otherwise any client
could claim to be 127.0.0.1. The localhost exemption for the admin is decided on the
real socket peer, never on a forwarded address.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path

SETTINGS_KEY = "network"
SCHEMA = 2

LOCAL = "127.0.0.1"
ALL = "0.0.0.0"
DEFAULT_PORT = 8080

LOOPBACK = ["127.0.0.0/8", "::1/128"]
# What "the local network" means when nobody said otherwise: loopback plus every
# private range. Deliberately not "the subnet I happen to be on" -- a container does
# not know it, and a VPN client usually sits in another private range.
PRIVATE = LOOPBACK + ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                      "fc00::/7", "fe80::/10"]

TLS_MODES = ("off", "self-signed", "custom")


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").replace(";", ",").split(",") if p.strip()]


def parse_networks(items) -> list[str]:
    """Normalise a list of IPs / CIDRs; raises ValueError naming the bad entry.
    Host names are refused on purpose: matching them would mean reverse DNS on every
    request, and the client controls the reverse DNS of its own address."""
    out = []
    for item in (_split(items) if isinstance(items, str) else items):
        try:
            out.append(str(ipaddress.ip_network(item.strip(), strict=False)))
        except ValueError:
            raise ValueError(f"not an IP address or range: {item!r}") from None
    return out


def parse_hosts(items) -> list[str]:
    out = []
    for item in (_split(items) if isinstance(items, str) else items):
        h = item.strip().lower()
        if not h or "/" in h or " " in h:
            raise ValueError(f"not a host name: {item!r}")
        out.append(h)
    return out


def defaults() -> dict:
    return {"schema": SCHEMA, "bind": LOCAL, "port": DEFAULT_PORT,
            "allowed_hosts": [], "mcp_allow": [], "admin_allow": list(PRIVATE),
            "admin_allow_default": True, "trusted_proxies": [], "tls": "off",
            "key_in_query": False}


def _seed(old: dict, new_store: bool) -> dict:
    """First start under 1.2.0: build the settings from the environment and from the
    pre-1.2.0 settings (`expose_mcp` / `expose_web` checkboxes), if there were any."""
    cfg = defaults()
    env = os.environ
    if old.get("expose_mcp") or old.get("expose_web"):
        cfg["bind"] = ALL
    if old.get("expose_mcp") and not old.get("expose_web"):
        # the old meaning: MCP on the network, the admin only on this machine
        cfg["admin_allow"] = list(LOOPBACK)
        cfg["admin_allow_default"] = False
    bind = env.get("AI_MEMORY_HOST") or env.get("HOST")
    if bind:
        cfg["bind"] = bind
    port = env.get("AI_MEMORY_PORT") or env.get("AI_MEMORY_MCP_PORT") or env.get("PORT")
    if port:
        cfg["port"] = int(port)
    if env.get("AI_MEMORY_ALLOWED_HOSTS"):
        cfg["allowed_hosts"] = parse_hosts(env["AI_MEMORY_ALLOWED_HOSTS"])
    if env.get("AI_MEMORY_MCP_ALLOW"):
        cfg["mcp_allow"] = parse_networks(env["AI_MEMORY_MCP_ALLOW"])
    if env.get("AI_MEMORY_ADMIN_ALLOW"):
        cfg["admin_allow"] = parse_networks(env["AI_MEMORY_ADMIN_ALLOW"])
        cfg["admin_allow_default"] = False
    if env.get("AI_MEMORY_TRUSTED_PROXIES"):
        cfg["trusted_proxies"] = parse_networks(env["AI_MEMORY_TRUSTED_PROXIES"])
    tls = env.get("AI_MEMORY_TLS")
    if tls in TLS_MODES:
        cfg["tls"] = tls
    # ?key= in the URL. The MCP spec says tokens MUST NOT travel in the query string, so
    # a NEW memory starts without it; an existing one keeps it, because LM Studio still
    # cannot send the header (bug #1892) and turning it off would cut agents off.
    kq = env.get("AI_MEMORY_KEY_IN_QUERY")
    cfg["key_in_query"] = (kq == "1") if kq in ("0", "1") else not new_store
    return cfg


# One process owns the settings now, so they are cached here and replaced on save.
# Read on every request; decrypting them every time would be wasted work.
_cache: dict = {}


def config(store) -> dict:
    sid = id(store)
    if _cache.get("sid") == sid:
        return _cache["cfg"]
    try:
        stored = store.get_settings(SETTINGS_KEY, {})
    except Exception:
        stored = {}  # an unreadable store must not stop the server from starting
    if stored.get("schema") != SCHEMA:
        try:
            new_store = not store.list_agents()
        except Exception:
            new_store = False
        cfg = _seed(stored, new_store)
        try:
            store.set_settings(SETTINGS_KEY, cfg)
        except Exception:
            pass
    else:
        cfg = {**defaults(), **stored}
    _cache.update(sid=sid, cfg=cfg)
    return cfg


def save(store, **changes) -> dict:
    cfg = {**config(store), **changes}
    store.set_settings(SETTINGS_KEY, cfg)
    _cache.update(sid=id(store), cfg=cfg)
    return cfg


def reset(store) -> dict:
    """`ai-memory network reset`: back to this machine only, no TLS. The way out after
    an allow-list typo locked the admin web away from wherever you are."""
    cfg = defaults()
    cfg["admin_allow"] = list(LOOPBACK)
    cfg["admin_allow_default"] = False
    store.set_settings(SETTINGS_KEY, cfg)
    _cache.update(sid=id(store), cfg=cfg)
    return cfg


def bind_host(store, which: str = "app") -> str:
    """Kept for callers of the two-port era; there is one bind address now."""
    return config(store)["bind"]


# ------------------------------------------------------------------ client checks

def _ip(value: str | None):
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None
    # A dual-stack socket reports IPv4 clients as ::ffff:a.b.c.d
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def in_networks(ip, networks: list[str]) -> bool:
    ip = _ip(str(ip)) if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ip
    if ip is None:
        return False
    for n in networks:
        try:
            if ip in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def is_loopback(value) -> bool:
    ip = _ip(str(value)) if value is not None else None
    return bool(ip and ip.is_loopback)


def resolve_client(cfg: dict, peer: str | None, headers: dict) -> tuple[str | None, str | None, bool]:
    """(client address, forwarded scheme or None, came through a trusted proxy).

    X-Forwarded-For is read right to left, stopping at the first address that is not
    itself a trusted proxy -- the left part of the header is whatever the client wrote."""
    if not (peer and in_networks(peer, cfg.get("trusted_proxies", []))):
        return peer, None, False
    client = peer
    for hop in reversed(_split(headers.get("x-forwarded-for", ""))):
        client = hop
        if not in_networks(hop, cfg["trusted_proxies"]):
            break
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() or None
    return client, proto, True


def host_allowed(cfg: dict, host_header: str | None) -> bool:
    """DNS-rebinding guard, for the admin AND for MCP. Loopback names are always fine;
    anything else must be listed. An empty list means "only this machine's names" --
    the same default the MCP SDK applied before 1.2.0."""
    if not host_header:
        return False
    h = host_header.strip().lower()
    if h.startswith("["):
        name = h[1:h.index("]")] if "]" in h else h
    else:
        name = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    if name in ("localhost", "127.0.0.1", "::1") or is_loopback(name):
        return True
    allowed = set(cfg.get("allowed_hosts", []))
    bind = cfg.get("bind")
    if bind and bind not in (ALL, "::"):
        allowed.add(bind.lower())
    return name in allowed


# ------------------------------------------------------------------ Docker gateway

def in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def container_gateway() -> str | None:
    """The default gateway as seen from inside a container. On Docker's default bridge
    every connection from outside appears to come from it, so a private-range allow-list
    would admit the whole internet if the port is published."""
    if not in_container():
        return None
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                return str(ipaddress.IPv4Address(bytes.fromhex(f[2])[::-1]))
    except (OSError, ValueError):
        return None
    return None


def admin_refusal(cfg: dict, socket_peer: str | None, client: str | None) -> str | None:
    """Why the admin web refuses this client, or None if it may come in."""
    if is_loopback(socket_peer):
        return None  # this machine, always -- the way back in after a bad allow-list
    if cfg.get("admin_allow_default") and not cfg.get("trusted_proxies"):
        gw = container_gateway()
        if gw and socket_peer and _ip(socket_peer) == _ip(gw):
            return ("This request arrived through the container gateway, so its real "
                    "address is unknown. Run the container with network_mode: host, or "
                    "set AI_MEMORY_ADMIN_ALLOW explicitly.")
    if not in_networks(client, cfg.get("admin_allow", [])):
        return f"The admin web is not open to {client}."
    return None


def mcp_refusal(cfg: dict, client: str | None) -> str | None:
    allow = cfg.get("mcp_allow", [])
    if allow and not in_networks(client, allow) and not is_loopback(client):
        return f"MCP is not open to {client}."
    return None


def firewall_hint(port: int) -> str:
    """The command that opens the port -- to show the user, never to run."""
    if sys.platform == "win32":
        return (f'New-NetFirewallRule -DisplayName "ai-memory {port}" -Direction Inbound '
                f'-Protocol TCP -LocalPort {port} -Action Allow'
                "    (PowerShell, as Administrator)")
    return (f"sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload"
            f"    (firewalld)\n"
            f"sudo ufw allow {port}/tcp"
            f"    (ufw)")
