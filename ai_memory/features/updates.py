"""Checking for a newer release — OFF by default, the one outbound connection there is.

Ailurus Memory otherwise never opens a connection to the outside, and that stays the
default: the check runs only after the owner turns it on in the admin web (Settings ->
Update check). When on, it asks GitHub's public API once a day for the latest release
of REPO and remembers the answer in the encrypted settings table.

When a newer version exists, two things happen:
* the admin web shows a banner on every page;
* unless the owner switched it off, every agent's instructions get an UPDATE AVAILABLE
  paragraph, so the AI the owner actually talks to mentions it once in a session.

Nothing is downloaded or installed — this only tells.

Security: the paragraph goes into the instructions of EVERY agent, so nothing GitHub
returns is copied into it verbatim. Only a version string that matches VERSION_RE is
kept, and the release URL is built from it here, never taken from the response. A
compromised or spoofed API answer can therefore at most claim a wrong version number.

The request carries a generic User-Agent ("ailurus-memory/<version>") and nothing about
the instance or its owner.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

from ai_memory.core.version import __version__

SETTINGS_KEY = "update_check"
REPO = os.environ.get("AI_MEMORY_UPDATE_REPO", "TursiThePanda/ailurus-memory")
VERSION_RE = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
INTERVAL = timedelta(hours=24)
TICK_SECONDS = 15 * 60
TIMEOUT_SECONDS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse(version: str) -> tuple[int, int, int] | None:
    m = VERSION_RE.match((version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: str | None, current: str = __version__) -> bool:
    a, b = parse(latest or ""), parse(current)
    return bool(a and b and a > b)


def release_url(version: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/v{version}"


def config(store) -> dict:
    cfg = store.get_settings(SETTINGS_KEY, {})
    cfg.setdefault("enabled", False)
    cfg.setdefault("tell_agents", True)
    cfg.setdefault("latest", None)
    cfg.setdefault("last_check", None)
    cfg.setdefault("last_error", "")
    return cfg


def save(store, **changes) -> dict:
    cfg = config(store)
    cfg.update(changes)
    store.set_settings(SETTINGS_KEY, cfg)
    return cfg


def available(store) -> str | None:
    """The newer version to announce, or None. None whenever the check is off — an old
    answer must not keep nagging after the owner switched the feature off."""
    try:
        cfg = config(store)
    except Exception:
        return None
    if not cfg["enabled"]:
        return None
    return cfg["latest"] if newer(cfg["latest"]) else None


def agent_notice(store) -> str:
    """The paragraph appended to every agent's instructions, or ''."""
    latest = available(store)
    if not latest or not config(store)["tell_agents"]:
        return ""
    return ("UPDATE AVAILABLE\n"
            f"A newer version of this memory server is available: Ailurus Memory {latest} "
            f"(this server runs {__version__}). Tell the user once, briefly, at a natural "
            "point in this session — not in every reply. Release notes: "
            f"{release_url(latest)}. Do not try to update the server yourself.")


def fetch_latest(timeout: float = TIMEOUT_SECONDS) -> str:
    """Ask GitHub for the latest (non-pre-release) version. Raises on any failure."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"ailurus-memory/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed https URL)
        data = json.loads(r.read(200_000).decode("utf-8"))
    tag = str(data.get("tag_name", ""))
    v = parse(tag)
    if not v:
        raise ValueError(f"unexpected release tag {tag[:40]!r}")
    return ".".join(map(str, v))


def check_now(store, fetch=None) -> dict:
    try:
        latest = (fetch or fetch_latest)()
    except Exception as exc:
        return save(store, last_check=_now().isoformat(timespec="seconds"),
                    last_error=f"{type(exc).__name__}: {exc}"[:300])
    return save(store, latest=latest, last_check=_now().isoformat(timespec="seconds"),
                last_error="")


def due(cfg: dict, now: datetime | None = None) -> bool:
    if not cfg.get("enabled"):
        return False
    if not cfg.get("last_check"):
        return True
    try:
        last = datetime.fromisoformat(cfg["last_check"])
    except ValueError:
        return True
    return (now or _now()) - last >= INTERVAL


class Scheduler:
    """One background thread in the app process, like the export schedule."""

    def __init__(self, store) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ai-memory-update-check")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if due(config(self.store)):
                    check_now(self.store)
            except Exception:  # a failed check must never take the server down
                pass
            self._stop.wait(TICK_SECONDS)
