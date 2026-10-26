"""Docker HEALTHCHECK: healthy only while the store is unlocked and serving.

/healthz answers 200 only when the memory is unlocked, so a container that is still
waiting for its passphrase (tty unlock via `docker attach`) reports "starting" and then
"unhealthy" instead of pretending to be fine.

The image serves HTTPS with a self-signed certificate by default, and plain HTTP when
the operator turned TLS off (own proxy in front). The probe cannot know which, so it
tries HTTPS first -- without verifying the certificate, because it only talks to
127.0.0.1 inside the container -- and falls back to HTTP.

Standard library only; no curl in the image.
"""
import os
import ssl
import sys
import urllib.request

PORT = os.environ.get("AI_MEMORY_PORT", "8080")
# The port can be changed in the admin web; the app writes the one it really listens
# on into <data>/run/app.json at start, so prefer that over the env seed.
try:
    import json
    _rt = json.load(open(os.path.join(os.environ.get("AI_MEMORY_DATA_DIR", "/data"),
                                      "run", "app.json")))
    PORT = str(_rt.get("port", PORT))
except (OSError, ValueError):
    pass


def probe(url: str, ctx=None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=4, context=ctx) as resp:
            return resp.status == 200
    except Exception:
        return False


def main() -> int:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if probe(f"https://127.0.0.1:{PORT}/healthz", ctx):
        return 0
    if probe(f"http://127.0.0.1:{PORT}/healthz"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
