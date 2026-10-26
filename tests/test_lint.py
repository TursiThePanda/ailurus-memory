"""Test of the deterministic hygiene checks (lint.py).

The checks exist to catch what we demonstrably got wrong by hand: on 2026-07-19 the
memory held three [[links]] pointing at records that had been renamed or never
written, and nobody noticed until someone followed one.

What matters as much as finding problems is NOT inventing them — a linter that cries
about healthy records gets ignored, and then it catches nothing at all.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sys
import tempfile

from ai_memory.features import lint
from ai_memory.core.store import SecureStore

PW = "lint-test-passphrase"
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

    s.write(content="Points at [[good]] and at [[ghost]].\nAWAITING VERIFICATION.",
            description="a record with problems", agent="claude",
            namespace="shared", name="bad", tags=["network"], new_tags=["network"])
    s.write(content="Nothing wrong here, mentions [[bad]].", description="a healthy record",
            agent="claude", namespace="shared", name="good", tags=["network"])
    s.write(content="", description="", agent="claude", namespace="shared", name="hollow")

    findings = lint.run(s.dump())

    # broken links
    check("finds a link to a record that does not exist", "bad" in names_for(findings, "link"))
    check("does not flag a link that resolves", len(names_for(findings, "link")) == 1)
    check("a broken link counts as broken",
          all(f["severity"] == "broken" for f in findings if f["check"] == "link"))

    # pending markers
    check("finds a leftover pending marker", "bad" in names_for(findings, "pending"))
    check("does not flag a healthy record as pending",
          "good" not in names_for(findings, "pending"))

    # empty / description
    check("finds an empty record", "hollow" in names_for(findings, "empty"))
    check("finds a missing description", "hollow" in names_for(findings, "no-description"))
    check("does not call a written record empty", "good" not in names_for(findings, "empty"))

    # tags
    check("finds an untagged record", names_for(findings, "untagged") == {"hollow"})

    # single-use tags are opt-in: on a real 40-record store they were 9 of 19 findings
    # and none were actionable, which teaches you to skim past the ones that matter
    s.write(content="Only this record uses that tag.", description="a lone tag",
            agent="claude", namespace="shared", name="lone",
            tags=["solitary"], new_tags=["solitary"])
    default_run = lint.run(s.dump())
    opted_in = lint.run(s.dump(), include_optional=True)
    check("single-use tags are silent by default",
          names_for(default_run, "lonely-tag") == set())
    check("--all surfaces them", "lone" in names_for(opted_in, "lonely-tag"))
    check("the optional check adds nothing else",
          {f["check"] for f in opted_in} - {f["check"] for f in default_run} == {"lonely-tag"})

    # ordering and summary
    check("worst findings come first", findings[0]["severity"] == "broken")
    summary = lint.summarize(findings)
    check("the summary counts everything", summary["total"] == len(findings))
    check("the summary splits by severity", set(summary["by_severity"]) <= {"broken", "stale", "hygiene"})

    # prose must not trip the marker check — only shouted markers count
    s.write(content="There is remaining work to do and a todo list somewhere.",
            description="lower case prose", agent="claude", namespace="shared",
            name="prose", tags=["network"])
    check("lower-case prose is not a pending marker",
          "prose" not in names_for(lint.run(s.dump()), "pending"))

    # a clean store reports nothing
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp2:
        s2 = SecureStore(os.path.join(tmp2, "memory.db"), PW)
        s2.write(content="All good, see [[second]].", description="the first one",
                 agent="claude", namespace="shared", name="first",
                 tags=["network"], new_tags=["network"])
        s2.write(content="Also good.", description="the second one", agent="claude",
                 namespace="shared", name="second", tags=["network"])
        check("a clean store produces no findings", lint.run(s2.dump()) == [])

    # lint must never write
    before = {r["name"]: r["content"] for r in s.dump()}
    lint.run(s.dump())
    check("lint does not modify anything",
          {r["name"]: r["content"] for r in s.dump()} == before)

print("\n" + ("LINT OK " if ok else "LINT FAILED"))
sys.exit(0 if ok else 1)
