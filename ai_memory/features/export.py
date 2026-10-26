"""Scheduled and on-demand export of the store, driven from the admin web.

This replaces the systemd timer + unit that used to do the backup, and it does so on
BOTH platforms identically -- that is the point. A timer is Linux plumbing; Windows
would have needed Task Scheduler, and the project would then have had two mechanisms
doing one job, differing exactly where a user is least able to cope with it.

WHAT IS EXPORTED IS THE ENCRYPTED DATABASE, not a readable dump. That is what makes it
safe to drop on a NAS or ship offsite: the payload is ciphertext, the passphrase is not
in it, and the copy is verified structurally without ever being decrypted. A plaintext
export would be a different feature with very different warnings on it.

The catch-up behaviour is deliberate. systemd's `Persistent=true` re-ran a timer that
had been missed while the machine was off; an in-process scheduler would silently lose
that. So the due-check is based on the STORED timestamp of the last successful export,
not on how long this process has been running -- a machine that was off for three days
exports as soon as it comes back.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_memory.features import backup
SETTINGS_KEY = "export"

DEFAULTS = {
    "enabled": False,
    "folder": "",          # empty = not configured; never guessed
    # Whether to ALSO copy on a clock. Changes are the primary trigger; this is the
    # belt to that pair of braces -- the only thing that would still produce a copy if
    # change detection were ever wrong. On by default for that reason.
    "periodic": True,
    "interval_hours": 24,
    "keep": 14,
    # Minutes to wait after a change before backing up. The point of it is coalescing:
    # a working session writes many records in a row, and a copy per write would fill
    # the whole retention window in an afternoon and push out every older version.
    "on_change_minutes": 15,
    "last_run": "",        # ISO, last SUCCESSFUL export
    "last_token": "",      # store.change_token() as of that export
    "last_error": "",
    "last_detail": "",
}

# How often the thread wakes to ask whether an export is due. Not the export interval,
# and it bounds how late a change-triggered copy can be: the MCP server and the admin web
# are separate processes, so a write cannot ring a bell here -- this thread finds out by
# looking. Sixty seconds is cheap (two counts on an open database) and keeps "back it up
# when something changes" honest.
TICK_SECONDS = 60


def config(store) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(store.get_settings(SETTINGS_KEY, {}))
    return cfg


def save(store, **changes) -> dict:
    cfg = config(store)
    cfg.update(changes)
    store.set_settings(SETTINGS_KEY, cfg)
    return cfg


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def due(cfg: dict, now: datetime | None = None, token: str | None = None) -> bool:
    """Is an export owed?

    TWO REASONS TO BACK UP, and they answer different worries.

    A CHANGE, once `on_change_minutes` have passed since the last copy. This is what
    makes the backup follow the work rather than a clock: after a session that adds
    records, the newest one is safe within minutes instead of within an interval. The
    delay is not politeness, it is coalescing -- one copy per write would fill the
    retention window in an afternoon and evict every older version, which is the exact
    opposite of what a backup is for.

    THE INTERVAL, if `periodic` is on. That is the periodic run as it always was, and it
    defaults to on because it is the only thing that would still produce a copy if change
    detection were ever wrong: a mechanism that fires only on a signal cannot tell
    "nothing happened" from "I stopped noticing". Turning it off is reasonable once the
    change trigger is trusted — it stops identical copies accumulating on a quiet day.

    Timing is measured from the last SUCCESSFUL export, so a missed window is caught up
    rather than skipped. `token` is store.change_token(); without it (callers that have
    no store handy) only the interval applies.
    """
    if not cfg.get("enabled") or not cfg.get("folder"):
        return False
    last = cfg.get("last_run") or ""
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
    except ValueError:
        return True
    now = now or datetime.now(timezone.utc)
    age_seconds = (now - prev).total_seconds()

    if cfg.get("periodic", True) and age_seconds >= float(cfg["interval_hours"]) * 3600:
        return True
    if token is not None and token != cfg.get("last_token", ""):
        gap = float(cfg.get("on_change_minutes", DEFAULTS["on_change_minutes"])) * 60
        return age_seconds >= gap
    return False


# A backup nobody has taken for a fortnight is not a backup, and the way that happens is
# never a decision -- it is silence. This is the one property of the whole project that
# cannot be allowed to rot quietly, and it got MORE fragile when the schedule moved out of
# systemd: the timer ran without a passphrase and caught up missed days on its own, while
# this scheduler lives inside the admin web and only exists once the memory is unlocked.
# So: no unlock, no backup, and nothing to notice it. Hence a warning that is shown rather
# than waited for.
#
# The threshold is derived from the interval instead of being a constant, because "late"
# means something different for a daily export than a weekly one: twice the interval.
#
# The floor exists so that a machine which was simply off overnight does not raise an
# alarm. It was 48 hours, chosen while thinking only of daily exports -- and that is too
# generous the moment somebody picks a SHORT interval. One user set theirs to hourly on
# 2026-08-30; at a 48-hour floor, hourly backups could stop and say nothing for two days,
# which is the exact failure this warning exists to catch. 26 hours keeps one quiet night
# quiet while still noticing within a day.
STALE_MULTIPLIER = 2
STALE_FLOOR_HOURS = 26


def staleness(cfg: dict, now: datetime | None = None, token: str | None = None) -> dict:
    """How overdue the backup is. Reports; decides nothing.

    `stale` is only ever True when an export is actually configured and enabled --
    warning somebody about a backup they deliberately do not run would be noise, and
    noise is how a real warning gets ignored later.

    `token` is store.change_token(). With it, a memory that has not changed since the
    last copy is never stale however old that copy is -- there is nothing to back up, and
    saying "your backup is 9 days old" to someone who has written nothing for nine days
    is the kind of warning people learn to close without reading. This matters more since
    the periodic run became optional: with only change-triggered copies, an untouched
    memory would otherwise start complaining after a day.
    """
    now = now or datetime.now(timezone.utc)
    enabled = bool(cfg.get("enabled")) and bool((cfg.get("folder") or "").strip())
    interval = float(cfg.get("interval_hours") or 24)
    threshold_h = max(interval * STALE_MULTIPLIER, STALE_FLOOR_HOURS)
    last = cfg.get("last_run") or ""

    age_h = None
    if last:
        try:
            age_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        except ValueError:
            age_h = None

    unchanged = token is not None and token == cfg.get("last_token", "")

    if not enabled:
        state = "off"
    elif age_h is None:
        state = "never"
    elif unchanged:
        state = "ok"          # nothing has happened; an old copy is still a current one
    elif age_h > threshold_h:
        state = "stale"
    else:
        state = "ok"

    return {"state": state,
            "stale": state in ("stale", "never"),
            "enabled": enabled,
            "age_hours": age_h,
            "age_days": None if age_h is None else age_h / 24,
            "threshold_hours": threshold_h,
            "last_run": last,
            "last_error": cfg.get("last_error", "")}


def staleness_message(st: dict) -> str:
    """One sentence, or empty when there is nothing to say."""
    if st["state"] == "never":
        return "No backup has ever been taken."
    if st["state"] == "stale":
        days = st["age_days"]
        return (f"The last backup is {days:.0f} days old"
                if days >= 1 else
                f"The last backup is {st['age_hours']:.0f} hours old")
    return ""


def run_now(store, db_path: str, cfg: dict | None = None) -> dict:
    """Take one export. Returns the result and records it in the settings, so the web
    UI can always say what happened last without keeping state in memory."""
    cfg = cfg or config(store)
    folder = (cfg.get("folder") or "").strip()
    if not folder:
        return save(store, last_error="no destination folder is configured",
                    last_detail="")

    # Read the token BEFORE the copy: anything written while it runs must count as
    # unbacked-up, or a record created mid-export would look covered by it.
    token = store.change_token()
    res = backup.run(db_path, folder, int(cfg.get("keep") or 14))
    if res.get("ok"):
        save(store, last_run=_now(), last_error="", last_token=token,
             last_detail=f"{res.get('records', '?')} records -> {res.get('destination', folder)}")
    else:
        save(store, last_error=res.get("error") or "failed", last_detail="")
    return res


class Scheduler:
    """One background thread, owned by the admin web.

    Exactly one process must own the schedule or two of them race to write the same
    destination. The admin web is where the setting lives and where the result is
    shown, so it owns it too.
    """

    def __init__(self, store, db_path: str) -> None:
        self.store = store
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="ai-memory-export")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # A first check straight away: this is the catch-up for a machine that was off.
        while not self._stop.is_set():
            try:
                cfg = config(self.store)
                if due(cfg, token=self.store.change_token()):
                    run_now(self.store, self.db_path, cfg)
            except Exception as exc:  # a broken export must never kill the web
                try:
                    save(self.store, last_error=f"scheduler: {exc}")
                except Exception:
                    pass
            self._stop.wait(TICK_SECONDS)
