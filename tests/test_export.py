"""Scheduled export, remembered passphrase, and network exposure.

The three settings added when the backup moved out of systemd and into the admin web.
The properties that matter are not "the form saves": they are that the export stays
ENCRYPTED, that a missed window is caught up rather than skipped, that remembering the
passphrase demands the passphrase, and that nothing becomes network-reachable by
accident.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory.features import backup
from ai_memory.features import export
from ai_memory.server import netconf
from ai_memory.core.store import SecureStore

PW = "export-test-pass"
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label)


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        db = tmp / "memory.db"
        s = SecureStore(str(db), PW)
        s.write(content="Worth exporting.", description="a record", agent="claude",
                namespace="shared", name="keeper", tags=["network"], new_tags=["network"])
        dest = tmp / "share" / "ai-memory"

        # ---- settings round-trip, encrypted -------------------------------------
        cfg = export.config(s)
        check("defaults are off and unconfigured",
              cfg["enabled"] is False and cfg["folder"] == "")
        export.save(s, enabled=True, folder=str(dest), interval_hours=24, keep=3)
        check("settings survive a reload", export.config(s)["folder"] == str(dest))
        row = s.db.execute("SELECT ct FROM settings WHERE k='export'").fetchone()
        check("settings are stored encrypted", str(dest).encode() not in row[0])

        # ---- an export happens, and is still ciphertext --------------------------
        res = export.run_now(s, str(db))
        check("export succeeds", res.get("ok"))
        copies = sorted(dest.glob("memory-*.db"))
        check("a copy is written", len(copies) == 1)
        raw = copies[0].read_bytes()
        check("THE EXPORT IS ENCRYPTED - the plaintext is not in it",
              b"Worth exporting." not in raw)
        reopened = SecureStore(str(copies[0]), PW)
        check("the copy opens with the passphrase",
              reopened.read("shared", "keeper")["content"] == "Worth exporting.")
        reopened.close()
        check("the run is recorded for the UI", export.config(s)["last_run"] != "")
        check("no error recorded on success", export.config(s)["last_error"] == "")

        # ---- catch-up: the systemd Persistent=true behaviour --------------------
        cfg = export.config(s)
        check("not due again immediately", not export.due(cfg))
        future = datetime.now(timezone.utc) + timedelta(hours=25)
        check("DUE once the interval has passed", export.due(cfg, now=future))
        stale = dict(cfg, last_run=(datetime.now(timezone.utc)
                                    - timedelta(days=3)).isoformat(timespec="seconds"))
        check("a machine off for three days is due at once, not skipped",
              export.due(stale))
        check("disabled is never due", not export.due(dict(cfg, enabled=False)))
        check("enabled but unconfigured is never due", not export.due(dict(cfg, folder="")))

        # ---- failures are reported, not raised ----------------------------------
        export.save(s, folder="")
        r = export.run_now(s, str(db))
        check("an unconfigured folder reports instead of raising",
              "folder" in export.config(s)["last_error"])
        export.save(s, folder=str(dest))

        # ---- rotation is honoured ------------------------------------------------
        for _ in range(4):
            export.run_now(s, str(db))
        check("keep is honoured", len(list(dest.glob("memory-*.db"))) == 3)

        # ---- network: the rules themselves live in test_network.py ----------------
        for var in ("HOST", "AI_MEMORY_HOST", "PORT", "AI_MEMORY_PORT"):
            os.environ.pop(var, None)
        check("nothing is exposed by default", netconf.bind_host(s) == netconf.LOCAL)
        check("the firewall hint names the port, and is never run",
              "8080" in netconf.firewall_hint(8080))

        s.close()

    # ---- remembering the passphrase -------------------------------------------
    from ai_memory.core import keystore
    ok, _detail = keystore.available()
    if ok:
        had = keystore.load()
        try:
            keystore.save("round-trip-check")
            check("the passphrase round-trips through the OS credential store",
                  keystore.load() == "round-trip-check")
            keystore.forget()
            check("forgetting really removes it", not keystore.is_remembered())
        finally:
            if had:
                keystore.save(had)
    else:
        print("  SKIPPED  no credential store on this machine")

    # --- backing up because something CHANGED, not only because time passed --------
    #
    # The worry on both sides: a record written just now should not wait an interval to
    # be safe, and a busy afternoon must not fill the retention window with one copy per
    # write and evict every older version.
    now = datetime.now(timezone.utc)
    base = dict(export.DEFAULTS, enabled=True, folder="/somewhere",
                interval_hours=24, on_change_minutes=15)

    def at(minutes_ago, token):
        c = dict(base, last_run=(now - timedelta(minutes=minutes_ago)).isoformat(),
                 last_token=token)
        return c

    check("no change and well within the interval: nothing to do",
          not export.due(at(60, "h5:m3"), now, token="h5:m3"))
    check("a change, once the coalescing delay has passed: back up",
          export.due(at(20, "h5:m3"), now, token="h6:m4"))
    check("a change moments ago: wait, so a batch of writes makes ONE copy",
          not export.due(at(2, "h5:m3"), now, token="h6:m4"))
    check("the interval alone still fires, change or no change",
          export.due(at(25 * 60, "h5:m3"), now, token="h5:m3"))
    check("a caller with no store falls back to the interval only",
          not export.due(at(60, "h5:m3"), now) and export.due(at(25 * 60, "h5:m3"), now))
    check("never exported yet: back up whatever the token says",
          export.due(dict(base, last_run="", last_token=""), now, token="h9:m9"))
    # The periodic run is a switch now, and turning it off must not turn the change
    # trigger off with it.
    no_clock = dict(base, periodic=False)
    check("with the schedule off, a change still triggers a copy",
          export.due(dict(at(20, "h5:m3"), periodic=False), now, token="h6:m4"))
    check("...but the clock alone no longer does",
          not export.due(dict(at(25 * 60, "h5:m3"), periodic=False), now, token="h5:m3"))
    check("...and with it on, the clock does",
          export.due(dict(at(25 * 60, "h5:m3"), periodic=True), now, token="h5:m3"))

    # And the warning must not nag about age when nothing has changed -- which only
    # became possible once the periodic run could be switched off.
    quiet = dict(base, last_run=(now - timedelta(days=9)).isoformat(),
                 last_token="h5:m3", periodic=False)
    check("an untouched memory is not 'stale' however old the copy is",
          not export.staleness(quiet, now, token="h5:m3")["stale"])
    check("...but the moment something changes, it is",
          export.staleness(quiet, now, token="h6:m4")["stale"])

    check("disabled beats everything",
          not export.due(dict(at(20, "h5:m3"), enabled=False), now, token="h6:m4"))

    # --- staleness: the warning that the backup stopped happening -----------------
    #
    # The property being protected: a backup that quietly stops is found out when it is
    # needed, which is the one moment it cannot be fixed. The property being protected
    # FROM: warning about a backup nobody asked for, because that is how a real warning
    # gets trained away.
    def cfg(**kw):
        c = dict(export.DEFAULTS, enabled=True, folder="/somewhere", interval_hours=24)
        c.update(kw)
        return c

    def ago(**kw):
        return (now - timedelta(**kw)).isoformat()

    st = export.staleness(cfg(), now)
    check("a configured export with no run yet is flagged", st["stale"]
          and st["state"] == "never")
    check("...and says so in words",
          export.staleness_message(st) == "No backup has ever been taken.")

    check("a fresh backup is not flagged",
          not export.staleness(cfg(last_run=ago(hours=3)), now)["stale"])
    # One missed daily run is a laptop that was off, not a problem worth a banner.
    check("one missed daily run is not yet a warning",
          not export.staleness(cfg(last_run=ago(hours=30)), now)["stale"])
    check("two missed daily runs are",
          export.staleness(cfg(last_run=ago(hours=52)), now)["stale"])

    st = export.staleness(cfg(last_run=ago(days=9)), now)
    check("a nine-day-old backup is stale", st["state"] == "stale")
    check("...and the message says nine days", "9 days" in export.staleness_message(st))

    # A SHORT interval must not inherit a long floor: hourly backups that stop should be
    # noticed within a day, not after two. This is the case that made the floor 26h.
    hourly = dict(cfg(interval_hours=1), last_run=ago(hours=20))
    check("hourly: a 20-hour-old backup is not yet flagged",
          not export.staleness(hourly, now)["stale"])
    check("hourly: a 30-hour-old backup IS flagged",
          export.staleness(dict(cfg(interval_hours=1), last_run=ago(hours=30)), now)["stale"])
    check("hourly: and it is nowhere near two days",
          export.staleness(cfg(interval_hours=1), now)["threshold_hours"] < 48)

    # The threshold follows the interval, so "late" means something appropriate to how
    # often the user asked for it.
    weekly = export.staleness(cfg(interval_hours=168, last_run=ago(days=9)), now)
    check("nine days is NOT late for a weekly export", not weekly["stale"])
    check("...but three weeks is",
          export.staleness(cfg(interval_hours=168, last_run=ago(days=21)), now)["stale"])

    check("an export that is switched off is never flagged",
          not export.staleness(cfg(enabled=False, last_run=ago(days=90)), now)["stale"])
    check("...nor is one with no destination set",
          not export.staleness(cfg(folder="", last_run=ago(days=90)), now)["stale"])
    check("a healthy backup produces no message at all",
          export.staleness_message(export.staleness(cfg(last_run=ago(hours=2)), now)) == "")
    # A last_run somebody hand-edited into nonsense must read as "no backup", not crash.
    check("an unparsable last_run degrades to 'never'",
          export.staleness(cfg(last_run="whenever"), now)["state"] == "never")

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
