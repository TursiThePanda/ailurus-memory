"""Network access rules (netconf.py) and the combined app's Gate (app.py).

The rules decide who reaches the master-passphrase admin web, so each one is pinned
here: the seed-then-store precedence, migration from the two-port settings, the
trusted-proxy handling of X-Forwarded-For, the Host check, the per-half allow-lists,
and the localhost way back in.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import tempfile

from ai_memory.server import netconf
from ai_memory.core.store import SecureStore

failed = 0


def check(label, ok, detail=""):
    global failed
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))
    failed += 0 if ok else 1


SEED_VARS = ("HOST", "AI_MEMORY_HOST", "PORT", "AI_MEMORY_PORT", "AI_MEMORY_MCP_PORT",
             "AI_MEMORY_ALLOWED_HOSTS", "AI_MEMORY_ADMIN_ALLOW", "AI_MEMORY_MCP_ALLOW",
             "AI_MEMORY_TRUSTED_PROXIES", "AI_MEMORY_TLS", "AI_MEMORY_KEY_IN_QUERY")


def fresh_store(tmp, name, settings=None, agent=False):
    netconf._cache.clear()
    s = SecureStore(os.path.join(tmp, name), "pw-network-test")
    if agent:
        s.add_agent("someone", {"*": "r"})
    if settings is not None:
        s.set_settings(netconf.SETTINGS_KEY, settings)
    return s


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    for v in SEED_VARS:
        os.environ.pop(v, None)

    # ---- seeding and precedence ------------------------------------------------
    s = fresh_store(tmp, "a.db")
    cfg = netconf.config(s)
    check("a new memory listens on localhost only", cfg["bind"] == netconf.LOCAL)
    check("...admin defaults to localhost + private ranges",
          "192.168.0.0/16" in cfg["admin_allow"] and cfg["admin_allow_default"])
    check("...MCP has no IP list by default (the token protects it)", cfg["mcp_allow"] == [])
    check("...and a NEW memory does not accept ?key=", cfg["key_in_query"] is False)
    os.environ["AI_MEMORY_HOST"] = "10.9.9.9"
    netconf._cache.clear()
    check("after the first start the STORED value wins over the env",
          netconf.config(s)["bind"] == netconf.LOCAL)
    os.environ.pop("AI_MEMORY_HOST")
    s.close()

    os.environ.update(AI_MEMORY_HOST="0.0.0.0", AI_MEMORY_PORT="9443",
                      AI_MEMORY_ADMIN_ALLOW="10.0.0.0/24", AI_MEMORY_TLS="self-signed",
                      AI_MEMORY_ALLOWED_HOSTS="memory.example.org")
    s = fresh_store(tmp, "b.db")
    cfg = netconf.config(s)
    check("env seeds bind, port, TLS and hosts on first start",
          cfg["bind"] == "0.0.0.0" and cfg["port"] == 9443 and cfg["tls"] == "self-signed"
          and cfg["allowed_hosts"] == ["memory.example.org"])
    check("...and an explicit admin list replaces the default",
          cfg["admin_allow"] == ["10.0.0.0/24"] and not cfg["admin_allow_default"])
    for v in SEED_VARS:
        os.environ.pop(v, None)
    s.close()

    # ---- migration from the two-port settings -----------------------------------
    s = fresh_store(tmp, "c.db", {"expose_mcp": True, "expose_web": False}, agent=True)
    cfg = netconf.config(s)
    check("old expose_mcp -> listens on the network", cfg["bind"] == netconf.ALL)
    check("...but the admin stays on this machine, as before",
          cfg["admin_allow"] == netconf.LOOPBACK)
    check("an EXISTING memory keeps ?key= (LM Studio cannot send the header)",
          cfg["key_in_query"] is True)
    s.close()

    # ---- parsing ----------------------------------------------------------------
    check("IPs and ranges are normalised",
          netconf.parse_networks("192.168.1.7, 10.0.0.0/8") == ["192.168.1.7/32", "10.0.0.0/8"])
    try:
        netconf.parse_networks("laptop.local")
        check("host names are refused in an allow-list", False)
    except ValueError:
        check("host names are refused in an allow-list", True)

    # ---- client address and proxies ---------------------------------------------
    cfg = {**netconf.defaults(), "trusted_proxies": ["192.168.1.1/32"]}
    c, proto, prox = netconf.resolve_client(cfg, "203.0.113.5", {"x-forwarded-for": "127.0.0.1"})
    check("X-Forwarded-For from an UNtrusted peer is ignored", c == "203.0.113.5" and not prox)
    c, proto, prox = netconf.resolve_client(
        cfg, "192.168.1.1", {"x-forwarded-for": "127.0.0.1, 198.51.100.7",
                             "x-forwarded-proto": "https"})
    check("from a trusted proxy the right-most untrusted hop is the client",
          c == "198.51.100.7" and proto == "https" and prox)

    # ---- admin and MCP refusals -------------------------------------------------
    d = netconf.defaults()
    check("admin: LAN client is let in by default", netconf.admin_refusal(d, "192.168.5.5", "192.168.5.5") is None)
    check("admin: internet client is refused by default",
          netconf.admin_refusal(d, "203.0.113.5", "203.0.113.5") is not None)
    strict = {**d, "admin_allow": ["10.1.0.0/16"], "admin_allow_default": False}
    check("admin: the real socket peer 127.0.0.1 always gets in",
          netconf.admin_refusal(strict, "127.0.0.1", "127.0.0.1") is None)
    check("admin: a FORWARDED 127.0.0.1 does not get the localhost pass",
          netconf.admin_refusal(strict, "192.168.1.1", "127.0.0.1") is not None)
    check("admin: IPv4-mapped IPv6 addresses match IPv4 ranges",
          netconf.admin_refusal(d, "::ffff:192.168.0.10", "::ffff:192.168.0.10") is None)
    check("MCP: open to anywhere without a list", netconf.mcp_refusal(d, "203.0.113.5") is None)
    check("MCP: an IP list, when set, is enforced",
          netconf.mcp_refusal({**d, "mcp_allow": ["10.0.0.0/8"]}, "203.0.113.5") is not None)

    # ---- Host header ------------------------------------------------------------
    h = {**d, "allowed_hosts": ["memory.example.org"]}
    check("Host: localhost forms always pass",
          all(netconf.host_allowed(h, x) for x in ("127.0.0.1:8080", "localhost", "[::1]:8080")))
    check("Host: a listed name passes with or without a port",
          netconf.host_allowed(h, "memory.example.org") and netconf.host_allowed(h, "Memory.Example.org:8443"))
    check("Host: anything else is refused (DNS rebinding)", not netconf.host_allowed(h, "evil.example"))

if failed:
    print(f"\n{failed} FAILED"); sys_exit = 1
else:
    print("\nNETWORK OK"); sys_exit = 0
raise SystemExit(sys_exit)
