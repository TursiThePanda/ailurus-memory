"""The memory as ONE web service: the admin web at `/`, MCP at `/mcp`, one port.

Until 1.2.0 these were two processes on two ports (server.py :8080, webapp.py :8081),
each unlocking its own copy of the store. Now one process unlocks once and serves both,
so a reverse proxy has a single upstream and the network settings have one owner.

How the two are put together, and the trap that shapes it: the MCP SDK's
streamable_http_app() returns a Starlette app whose LIFESPAN starts the session
manager. Starlette does not run the lifespan of a MOUNTED sub-application, so mounting
that app under ours would leave the session manager unstarted and every MCP request
failing. So we take only the session manager from the SDK, serve its ASGI handler at
/mcp ourselves, and run `session_manager.run()` in OUR lifespan.

In front of everything sits Gate: the DNS-rebinding Host check, the client allow-lists
(admin and MCP separately), the trusted-proxy handling, and the security headers for
the admin pages. It reads the settings on every request, so an allow-list changed in the
admin web applies to the next request without a restart. The SDK's own Host check is
therefore switched off explicitly -- it is fixed at start and Gate does the same check,
for both halves, dynamically.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from ai_memory.server import netconf
from ai_memory.core import paths # uvicorn waits this long for open requests on shutdown. An MCP session is a long-lived
# stream, so without a bound a connected agent holds a stop open until the service
# manager kills us (systemd gave up after 46 s once; `docker stop` kills at 10 s).
GRACEFUL_SHUTDOWN_SECONDS = 3

SECURITY_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy",
     b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
     b"script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"cache-control", b"no-store"),
]


class Gate:
    def __init__(self, app, store) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cfg = netconf.config(self.store)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        peer = (scope.get("client") or (None, None))[0]
        client, proto, proxied = netconf.resolve_client(cfg, peer, headers)
        path = scope.get("path", "")

        if path == "/healthz":
            return await self.app(scope, receive, send)
        if not netconf.host_allowed(cfg, headers.get("host")):
            return await _deny(scope, receive, send, 421,
                               "Host not allowed. Add it under Settings -> Network.")

        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        why = (netconf.mcp_refusal(cfg, client) if is_mcp
               else netconf.admin_refusal(cfg, peer, client))
        if why:
            return await _deny(scope, receive, send, 403, why)

        # What the rest of the app sees: the real client, and https when a trusted
        # proxy terminated TLS for us (so cookies and redirects come out right).
        scope = dict(scope)
        scope["client"] = (client, (scope.get("client") or (None, 0))[1])
        if proxied and proto in ("http", "https"):
            scope["scheme"] = proto
        scope.setdefault("state", {})
        scope["state"] = {**scope["state"], "socket_peer": peer, "proxied": proxied}

        if is_mcp:
            return await self.app(scope, receive, send)

        https = scope.get("scheme") == "https"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                hs = [h for h in message.get("headers", [])
                      if h[0].lower() not in {k for k, _ in SECURITY_HEADERS}]
                hs += SECURITY_HEADERS
                if https:
                    hs.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": hs}
            await send(message)

        return await self.app(scope, receive, send_with_headers)


async def _deny(scope, receive, send, status: int, text: str):
    await PlainTextResponse(text + "\n", status_code=status)(scope, receive, send)


def build(store):
    """The whole ASGI application around an unlocked store."""
    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
    from mcp.server.transport_security import TransportSecuritySettings

    from ai_memory.server import server
    from ai_memory.web import webapp
    server.STORE = store
    server.MODE_HTTP = True
    webapp.STORE = store
    # Called for its side effect of creating the session manager; the Starlette app it
    # returns is discarded on purpose (see the module docstring).
    server.mcp.streamable_http_app(transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    sm = server.mcp.session_manager
    mcp_endpoint = server.BearerAuthMiddleware(StreamableHTTPASGIApp(sm), store)

    async def healthz(_request):
        # Reaching this code means the store is unlocked: nothing listens before that.
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with sm.run():
            yield

    inner = Starlette(routes=[
        Route("/healthz", healthz),
        Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        Mount("/", app=webapp.make_app(store)),
    ], lifespan=lifespan)
    return Gate(inner, store)


def write_runtime(cfg: dict) -> None:
    """Where we listen, in plain text, for `ai-memory status` and the tray -- they run
    without the passphrase and cannot read the encrypted settings."""
    try:
        d = paths.ensure(paths.data_dir() / "run")
        scheme = "https" if cfg["tls"] != "off" else "http"
        host = "127.0.0.1" if cfg["bind"] in (netconf.ALL, "::") else cfg["bind"]
        (d / "app.json").write_text(json.dumps(
            {"port": cfg["port"], "scheme": scheme, "host": host, "pid": os.getpid()}))
    except OSError:
        pass


def main() -> None:
    import uvicorn

    from ai_memory.features import export
    from ai_memory.server import server
    from ai_memory.server import tls
    from ai_memory.core.store import SecureStore
    from ai_memory.core.unlock import get_passphrase

    db = str(paths.db_path())
    os.makedirs(os.path.dirname(db), exist_ok=True)
    store = SecureStore(db, get_passphrase())
    server.STORE = store
    server.MODE_HTTP = True
    server._install_dynamic_instructions()
    # The export schedule has exactly one owner, or two processes race to write the
    # same destination. With one process there is only one candidate.
    export.Scheduler(store, db).start()

    cfg = netconf.config(store)
    names = list(cfg["allowed_hosts"])
    if cfg["bind"] not in (netconf.ALL, "::"):
        names.append(cfg["bind"])
    ssl = tls.ensure(cfg["tls"], names)
    app = build(store)
    write_runtime(cfg)
    scheme = "https" if ssl else "http"
    print(f"[ai-memory] unlocked, store: {db}")
    print(f"[ai-memory] listening on {scheme}://{cfg['bind']}:{cfg['port']}  "
          f"(admin /, MCP /mcp)")
    if cfg["bind"] != netconf.LOCAL and not ssl and not cfg["trusted_proxies"]:
        print("[ai-memory] WARNING: on the network over plain HTTP with no TLS proxy "
              "configured -- passphrase and keys travel in cleartext", file=sys.stderr)
    uvicorn.run(app, host=cfg["bind"], port=int(cfg["port"]), log_level="warning",
                ssl_certfile=ssl[0] if ssl else None, ssl_keyfile=ssl[1] if ssl else None,
                proxy_headers=False, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
