"""Test of section-addressable reads (sections.py).

Why this exists: the largest records are the ones consulted most often, and pulling
one in whole to get a single threshold costs the same as reading the lot. Splitting
them is only safe if a partial answer can never be mistaken for a complete one — a
model that reads half a record and concludes "it does not say" is worse than one that
paid for the whole thing. Hence the rule these tests enforce: every partial response
carries a note saying it is partial.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys

from ai_memory.features import sections
ok = True


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


DOC = """Status line before any heading.

## Hardware
The board is 30.5 mm wide.

### A subheading that must NOT split
Still hardware.

## Firmware
PlatformIO, env microespink.

## Open
Nothing.
"""

rec = {"name": "thing", "namespace": "shared", "description": "d", "content": DOC}

pre, secs = sections.split(DOC)
check("preamble is the text above the first heading", pre.startswith("Status line"))
check("splits on the shallowest heading level only", [s["title"] for s in secs]
      == ["Hardware", "Firmware", "Open"])
check("a subheading stays inside its parent section",
      "A subheading" in secs[0]["body"])

# --- whole read ---------------------------------------------------------------
whole = sections.shape(dict(rec))
check("a full read still returns the entire content", whole["content"] == DOC)
check("a full read advertises the sections", [s["title"] for s in whole["sections"]]
      == ["Hardware", "Firmware", "Open"])
check("a full read carries no partial-read note", "note" not in whole)

# --- outline ------------------------------------------------------------------
out = sections.shape(dict(rec), outline=True)
check("outline drops the bodies", "PlatformIO" not in out["content"])
check("outline keeps the preamble", "Status line" in out["content"])
check("outline says it is an outline", "note" in out and "Outline" in out["note"])
check("outline reports section sizes", all(s["chars"] > 0 for s in out["sections"]))

# --- one section --------------------------------------------------------------
one = sections.shape(dict(rec), section="firmware")
check("section match is case-insensitive", "PlatformIO" in one["content"])
check("a section read excludes the other sections", "30.5 mm" not in one["content"])
check("a section read keeps the preamble", "Status line" in one["content"])
check("a section read says the rest was not read",
      "NOT read" in one.get("note", ""))

part = sections.shape(dict(rec), section="firm")
check("a substring matches the heading", "PlatformIO" in part["content"])

miss = sections.shape(dict(rec), section="nonexistent")
check("an unmatched section does not silently return everything",
      "PlatformIO" not in miss["content"])
check("an unmatched section says so, and how to read it whole",
      "No section matches" in miss.get("note", ""))

# --- the size limit -----------------------------------------------------------
big = {**rec, "content": "## A\n" + ("x" * (sections.FULL_READ_LIMIT + 10)) + "\n## B\nshort"}
cut = sections.shape(dict(big))
check("an oversized record comes back as an outline", "x" * 50 not in cut["content"])
check("the cut is explained, with the limit and the way to get the rest",
      "over the" in cut.get("note", "") and "section=" in cut.get("note", ""))
check("the oversized outline still lists every section",
      [s["title"] for s in cut["sections"]] == ["A", "B"])

exact = sections.shape(dict(big), section="B")
check("an oversized record can still be read one section at a time",
      "short" in exact["content"])

# --- a record whose only top-level heading is a single title ------------------
# The real store's largest record opens with one `#` and puts everything under `##`.
# Splitting on the shallowest level gave ONE section holding the whole body.
TITLED = """# The Whole Thing

Intro paragraph.

## First
aaa

## Second
bbb
"""
_, tsecs = sections.split(TITLED)
check("descends past a lone title to the level that actually divides",
      [s["title"] for s in tsecs] == ["First", "Second"])

# --- a big record with NO headings must not be truncated ----------------------
formless = sections.shape({**rec, "content": "x" * (sections.FULL_READ_LIMIT + 500)})
check("an oversized record without headings is returned WHOLE, not cut",
      len(formless["content"]) == sections.FULL_READ_LIMIT + 500)
check("...and explains why it was expensive",
      "no headings" in formless.get("note", ""))

# --- records with no headings at all ------------------------------------------
flat = sections.shape({**rec, "content": "Just one paragraph, no headings."})
check("a record without headings is returned unchanged",
      flat["content"] == "Just one paragraph, no headings.")
check("a record without headings advertises no sections", "sections" not in flat)

# --- metadata survives ---------------------------------------------------------
meta = sections.shape({**rec, "author": "claude", "verified": "2026-08-29T10:00:00+00:00"},
                      section="Open")
check("metadata is preserved on a partial read",
      meta["author"] == "claude" and meta["verified"].startswith("2026-08-29"))

print("\n" + ("SECTIONS OK " if ok else "SECTIONS FAILED"))
sys.exit(0 if ok else 1)
