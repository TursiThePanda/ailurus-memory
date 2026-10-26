"""Test of the verification stamp and the staleness check built on it.

The memory tells every agent to verify a stored fact before relying on it. Nothing
recorded whether anyone ever did, which made the instruction unenforceable: `updated`
says when the text last changed, so a record written once and never opened again looks
exactly like one confirmed yesterday.

Two properties carry the feature, and both are about NOT lying:

1. **A stamp cannot outlive what it vouched for.** Rewriting the content clears it.
   A stamp that survives the text it confirmed is worse than no stamp at all.
2. **The check stays quiet unless something is genuinely old.** The lonely-tag check
   taught this the hard way — nine parts noise, and the ten useful findings get
   skimmed past with the rest.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from ai_memory.features import lint
from ai_memory.core.store import SecureStore

PW = "verify-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def names_for(findings, check_name):
    return {f["name"] for f in findings if f["check"] == check_name}


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)

    s.write(content="The controller answers at 192.0.2.251 over the REST API.",
            description="Router access", agent="claude", namespace="shared",
            name="router", tags=["network"], _trusted=True)
    s.write(content="He prefers being told the tradeoff before the recommendation.",
            description="How the user likes answers", agent="claude", namespace="shared",
            name="preferences", tags=["workflow"], _trusted=True)

    # --- the stamp itself ---
    fresh = s.read("shared", "router")
    check("a new record carries no verification", fresh["verified"] is None)

    s.mark_verified("shared", "router", agent="claude")
    stamped = s.read("shared", "router")
    check("verifying records when", bool(stamped["verified"]))
    check("verifying records who", stamped["verified_by"] == "claude")
    check("verifying does not touch the content",
          stamped["content"] == fresh["content"])
    check("verifying is not an edit", stamped["updated"] == fresh["updated"])
    check("verifying writes no history entry",
          len(s.history("shared", "router")) == len(s.history("shared", "preferences")))

    # --- a stamp must not outlive the text it vouched for ---
    s.update(namespace="shared", name="router", agent="claude",
             tags=["network", "router"], _trusted=True)
    check("re-tagging keeps the stamp", s.read("shared", "router")["verified"] is not None)

    s.update(namespace="shared", name="router", agent="claude",
             content="Moved: the controller now answers at 192.0.2.9.", _trusted=True)
    check("rewriting the content clears the stamp",
          s.read("shared", "router")["verified"] is None)

    s.mark_verified("shared", "router", agent="claude")
    s.write(content="Rewritten from scratch, still about 192.0.2.9.",
            description="Router access", agent="claude", namespace="shared",
            name="router", tags=["network"], _trusted=True)
    check("overwriting through write() clears the stamp",
          s.read("shared", "router")["verified"] is None)

    try:
        s.mark_verified("shared", "no-such-record", agent="claude")
        check("verifying a missing record fails", False)
    except FileNotFoundError:
        check("verifying a missing record fails", True)

    # --- the staleness check ---
    records = s.dump()
    check("nothing is stale in a memory written today",
          lint.check_unverified(records) == [])

    # pretend we are looking at this store half a year from now
    later = datetime.now(timezone.utc) + timedelta(days=200)
    aged = lint.check_unverified(records, now=later)
    check("an unconfirmed address is flagged once it ages",
          names_for(aged, "unverified") == {"router"})
    check("a record holding no volatile fact is never flagged",
          "preferences" not in names_for(aged, "unverified"))
    check("the finding says what to do",
          bool(aged) and "memory_verify" in aged[0]["detail"])

    # a fresh stamp resets the clock, an old one does not. Note the two horizons: the
    # stamp is written today, so it is only "fresh" when seen from inside the window —
    # looking from +200 the confirmation is itself 200 days old and stale again, which
    # is the whole point of dating it rather than storing a boolean.
    s.mark_verified("shared", "router", agent="claude")
    soon = datetime.now(timezone.utc) + timedelta(days=100)
    check("a recent confirmation silences the check",
          lint.check_unverified(s.dump(), now=soon) == [])
    much_later = datetime.now(timezone.utc) + timedelta(days=400)
    stale_again = lint.check_unverified(s.dump(), now=much_later)
    check("but only until the stamp itself is old",
          names_for(stale_again, "unverified") == {"router"})
    check("an aged stamp is reported as confirmed, not merely edited",
          bool(stale_again) and "confirmed" in stale_again[0]["detail"])

    # --- it is wired into the default run, not opt-in ---
    check("the check runs by default",
          "unverified" in {f["check"] for f in lint.run(s.dump())} or
          lint.check_unverified(s.dump()) == [])
    check("check_unverified is in the default set",
          lint.check_unverified in lint.CHECKS)

print("\n" + ("VERIFY OK " if ok else "VERIFY FAILED"))
sys.exit(0 if ok else 1)
