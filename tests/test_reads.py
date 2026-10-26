"""Test of read counting and the two checks built on it (store.reads / lint).

A memory decays by filling up with records that are true and useless, and neither
`updated` nor `verified` can spot those — both look identical on a record nobody has
ever needed. Read counts can. The risk is the opposite one: a check that fires on the
whole store the day it ships gets switched off, so most of this file is about staying
quiet when there is nothing to say.
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

PW = "reads-test-passphrase"
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


def names_for(findings, check_name):
    return {f["name"] for f in findings if f["check"] == check_name}


def by_name(records, name):
    return next(r for r in records if r["name"] == name)


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s = SecureStore(os.path.join(tmp, "memory.db"), PW)
    s.write(content="Body one.", description="the read one", agent="claude",
            namespace="shared", name="popular", tags=["network"], new_tags=["network"])
    s.write(content="Body two.", description="the ignored one", agent="claude",
            namespace="shared", name="ignored", tags=["network"])

    # --- counting ------------------------------------------------------------
    check("a fresh record has no reads", by_name(s.dump(), "popular")["reads"] == 0)

    s.read("shared", "popular", agent="claude")
    s.read("shared", "popular", agent="lmstudio-local")
    rec = by_name(s.dump(), "popular")
    check("reads accumulate", rec["reads"] == 2)
    check("the last reader is recorded", rec["last_read_by"] == "lmstudio-local")
    check("the last read is timestamped", (rec["last_read"] or "").startswith("20"))
    check("an unread record stays at zero", by_name(s.dump(), "ignored")["reads"] == 0)

    # The user browsing the admin web must not make records look alive — that is the
    # exact signal being measured.
    s.read("shared", "ignored", agent=None)
    check("a read by the user (admin web) is not counted",
          by_name(s.dump(), "ignored")["reads"] == 0)

    # Listing and searching are not reads: seeing a summary is not needing the record.
    s.list("shared")
    s.search("ignored")
    check("list and search do not count as reads",
          by_name(s.dump(), "ignored")["reads"] == 0)

    check("counting has a start date", s.reads_since().startswith("20"))

    # --- check_unread --------------------------------------------------------
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    fresh_since = now.isoformat()
    long_since = (now - timedelta(days=400)).isoformat()

    records = s.dump()
    for r in records:  # pretend both records are old enough to judge
        r["created"] = old

    check("silent while counting has not been running long enough",
          lint.check_unread(records, since=fresh_since) == [])
    check("silent when the start date is unknown (fails safe)",
          lint.check_unread(records, since=None) == [])

    found = names_for(lint.check_unread(records, since=long_since), "unread")
    check("reports a record nobody has read", "ignored" in found)
    check("does not report a record that was read", "popular" not in found)
    check("being unread is only hygiene, never broken",
          all(f["severity"] == "hygiene"
              for f in lint.check_unread(records, since=long_since)))

    young = [{**r, "created": now.isoformat()} for r in records]
    check("a record written yesterday is not called unread",
          lint.check_unread(young, since=long_since) == [])

    # --- check_unsourced_numbers ---------------------------------------------
    s.write(content=("The spin peaks at 300 W and heating runs at 2100 W, "
                     "with 45 % humidity."),
            description="numbers from nowhere", agent="claude",
            namespace="shared", name="guessy", tags=["network"])
    s.write(content=("Measured 2026-08-28: spin peaked at 199 W and heating held "
                     "1738 W for 21 min."),
            description="numbers with a date", agent="claude",
            namespace="shared", name="sourced", tags=["network"])
    s.write(content="The case is 57.8 mm wide and cost 5850 Kč.",
            description="dimensions and money", agent="claude",
            namespace="shared", name="physical", tags=["network"])
    s.write(content="A single stray 40 W bulb, mentioned once in passing.",
            description="one number only", agent="claude",
            namespace="shared", name="onenumber", tags=["network"])

    findings = lint.check_unsourced_numbers(s.dump())
    flagged = names_for(findings, "unsourced-numbers")
    check("flags measured-looking numbers with no date or source", "guessy" in flagged)
    check("does not flag numbers next to a date", "sourced" not in flagged)
    check("does not flag physical dimensions or prices", "physical" not in flagged)
    check("stays quiet about a single stray number", "onenumber" not in flagged)
    check("names the offending numbers so they can be found",
          any("300 W" in f["detail"] for f in findings))
    check("unsourced numbers are hygiene, not an error",
          all(f["severity"] == "hygiene" for f in findings))

    # --- the whole run still behaves -----------------------------------------
    everything = lint.run(s.dump(), reads_since=s.reads_since())
    check("run() passes the start date through, so unread stays quiet on a young store",
          names_for(everything, "unread") == set())
    before = {r["name"]: r["content"] for r in s.dump()}
    lint.run(s.dump(), reads_since=s.reads_since())
    check("lint still writes nothing",
          {r["name"]: r["content"] for r in s.dump()} == before)

print("\n" + ("READS OK " if ok else "READS FAILED"))
sys.exit(0 if ok else 1)
