"""Reading a long record without paying for all of it.

Records here are meant to be thorough — the value is in the detail that would
otherwise be re-derived. But an agent that wants one threshold pays the same context
cost as one that wants the whole history, and the biggest records are exactly the
ones consulted most often.

So a record is addressable by its `##` headings. Nothing about how records are
written changes: the split is derived from the markdown that is already there, and a
record with no headings behaves exactly as before.

The rule that shapes everything below: **never silently return less than was asked
for.** Every partial response says so, and says what it left out, so an agent can
tell "the record does not mention X" apart from "you did not read the part with X".
"""
from __future__ import annotations

import re

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Above this many characters a full read is refused in favour of the outline. Picked
# from the real store: the largest records run 8-15k, and a 12k body is roughly 3k
# tokens — worth one deliberate decision rather than an accident.
FULL_READ_LIMIT = 12_000


def split(content: str) -> tuple[str, list[dict]]:
    """(preamble, sections). Preamble is whatever precedes the first heading.

    Only the shallowest heading level present is treated as a section boundary, so a
    record built from `##` splits on `##` and its `###` subheadings stay inside their
    parent — splitting on both would scatter one topic across several fragments.
    """
    heads = list(HEADING_RE.finditer(content))
    if not heads:
        return content, []

    # Descend to the first level that actually divides the record. Plenty of records
    # open with a single `#` title and put everything under `##`; splitting on the
    # shallowest level then yields ONE section holding the whole body, which is the
    # same as not splitting at all. Found on the real store, where the largest record
    # (24k characters) came back as a single section.
    levels = sorted({len(m.group(1)) for m in heads})
    for level in levels:
        at_level = [m for m in heads if len(m.group(1)) == level]
        if len(at_level) > 1:
            heads = at_level
            break
    else:
        heads = [m for m in heads if len(m.group(1)) == levels[0]]
    preamble = content[:heads[0].start()].strip()
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
        out.append({"title": m.group(2).strip(),
                    "body": content[m.start():end].rstrip()})
    return preamble, out


def outline_of(sections_: list[dict]) -> list[dict]:
    return [{"title": s["title"], "chars": len(s["body"])} for s in sections_]


def shape(rec: dict, *, section: str | None = None, outline: bool = False) -> dict:
    """Return `rec` whole, as an outline, or narrowed to one section."""
    content = rec.get("content") or ""
    preamble, secs = split(content)
    titles = outline_of(secs)
    base = {k: v for k, v in rec.items() if k != "content"}
    if secs:
        base["sections"] = titles

    if outline:
        return {**base, "content": preamble,
                "note": "Outline only. Call memory_read again with section=\"<title>\" "
                        "for the part you need, or outline=false for everything."}

    if section is not None:
        want = section.strip().lower()
        hit = ([s for s in secs if s["title"].lower() == want]
               or [s for s in secs if want in s["title"].lower()])
        if not hit:
            return {**base, "content": preamble,
                    "note": f"No section matches {section!r}. The headings above are "
                            f"everything this record has; omit `section` to read it whole."}
        body = "\n\n".join(s["body"] for s in hit)
        return {**base,
                "content": f"{preamble}\n\n{body}".strip() if preamble else body,
                "note": f"Section {hit[0]['title']!r} of {len(secs)} (plus the text "
                        f"above the first heading). Other sections were NOT read."}

    if len(content) > FULL_READ_LIMIT:
        if secs:
            return {**base, "content": preamble,
                    "note": f"This record is {len(content)} characters, over the "
                            f"{FULL_READ_LIMIT} full-read limit, so only the outline and "
                            f"the text above the first heading were returned. Call again "
                            f"with section=\"<title>\" for the part you need."}
        # No headings at all: there is nothing to address, and truncating would hand
        # back a silently incomplete record — the one outcome this module exists to
        # prevent. Return it whole and say why it cost what it did; lint's
        # `unstructured` check nags about the same records so they eventually get
        # headings and become cheap to read.
        return {**base, "content": content,
                "note": f"This record is {len(content)} characters and has no headings, "
                        f"so it could not be read in parts and was returned whole. "
                        f"Adding `##` headings to it would make future reads cheaper."}

    return {**base, "content": content}
