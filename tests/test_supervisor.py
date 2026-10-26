"""Stopping the two halves: gracefully, but within a bounded time.

Written after a real stop took 46 seconds and systemd killed the unit with
`Failed with result 'timeout'`. The supervisor sent SIGTERM and then waited with no
limit. SIGTERM tells uvicorn to finish its in-flight requests, and an MCP session is a
long-lived stream, so a connected agent -- doing nothing wrong at all -- can hold the
shutdown open indefinitely. Asking politely is right; waiting forever is not.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from ai_memory import cli
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def spawn(ignore_term: bool):
    """A child that either dies on SIGTERM or deliberately ignores it."""
    code = ("import signal, time\n"
            + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
            + "time.sleep(300)\n")
    return subprocess.Popen([sys.executable, "-c", code])


def main():
    # The ordinary case: a child that respects SIGTERM is gone at once, and the call
    # must not sit out the grace period waiting for something already dead.
    procs = [("a", spawn(False)), ("b", spawn(False))]
    t0 = time.monotonic()
    cli.terminate_all(procs, grace=5.0)
    elapsed = time.monotonic() - t0
    check("a well-behaved child is stopped", all(p.poll() is not None for _l, p in procs))
    check("...without waiting out the grace period", elapsed < 2.0, f"{elapsed:.1f}s")

    # The case that caused the timeout: a child that will not go on SIGTERM.
    stubborn = [("stuck", spawn(True))]
    t0 = time.monotonic()
    cli.terminate_all(stubborn, grace=1.0)
    elapsed = time.monotonic() - t0
    check("a child ignoring SIGTERM is killed anyway",
          stubborn[0][1].poll() is not None)
    check("...within roughly the grace period", elapsed < 4.0, f"{elapsed:.1f}s")
    check("...and it was a kill, not a clean exit",
          stubborn[0][1].returncode != 0, str(stubborn[0][1].returncode))

    # The grace is shared, not per-child, so the worst case does not grow with the
    # number of halves.
    many = [(str(i), spawn(True)) for i in range(3)]
    t0 = time.monotonic()
    cli.terminate_all(many, grace=1.0)
    elapsed = time.monotonic() - t0
    check("three stuck children still take about one grace period", elapsed < 4.0,
          f"{elapsed:.1f}s")
    check("...and all of them are gone", all(p.poll() is not None for _l, p in many))

    check("the default grace is short enough for a service manager",
          0 < cli.STOP_GRACE_SECONDS <= 15)

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
