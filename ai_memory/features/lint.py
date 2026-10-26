"""Deterministic hygiene checks over the memory — the curator's bottom layer.

Everything here is a fact, not a judgement: a link points at nothing, a record has
no tags, a record still says "AWAITING VERIFICATION". No model is involved and none
is needed, so these checks work when LM Studio is down, on a NAS, anywhere.

The rule this module lives by: **report, never edit.** A curator that quietly
rewrites records destroys the one thing that makes the memory trustworthy — knowing
who wrote what. Every finding names a record and says what is wrong; the user (or an
agent, with the user watching) decides.

Findings carry a severity so a caller can show the important ones first:
  "broken"  — provably wrong now (a link to a record that does not exist)
  "stale"   — the record says of itself that it is unfinished
  "hygiene" — untagged, orphan tags: nothing is wrong, it just decays
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)

# Facts that rot: an address, a port, a version. A record that holds none of these —
# a decision, a preference, why something was chosen — can sit for years and stay
# true, and asking someone to re-confirm it is how a check earns its way into being
# ignored. Matching is deliberately narrow for the same reason: a bare number is not
# a version, so `1.2` and a year do not count.
VOLATILE_RE = (
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),          # an IPv4 address
    re.compile(r":\d{2,5}\b"),                            # a port
    re.compile(r"\bv?\d+\.\d+\.\d+\b"),                   # a three-part version
    re.compile(r"\b(?:https?|ssh)://", re.IGNORECASE),    # an endpoint
)

# How long a volatile fact may go unconfirmed before it is worth a second look.
# Half a year: long enough that a working setup is not nagged about every quarter,
# short enough to catch the ones nobody has opened since they were written.
STALE_AFTER_DAYS = 180

# How long a record may go unread before it is worth asking whether it earns its place.
UNREAD_AFTER_DAYS = 180

# Units that denote a MEASURED quantity — one that had to be observed, and that can
# drift or be guessed wrong.
#
# Calibrated against the real store, where a first, wider version flagged 21% of all
# records and most of it was noise of one kind: NAMEPLATE values. `12 V`, `5 V`,
# `16 A`, `30 mA`, `1800 mAh` are supply rails, breaker sizes and battery capacities —
# printed on the thing, never measured, and they do not rot. So volts, amps and
# capacity are out entirely; mV stays, because a millivolt figure in these records is
# always a battery reading someone took. Millimetres and prices were never in: a
# caliper reading and a receipt do not drift either.
MEASURED_RE = re.compile(
    r"\b\d{1,6}(?:[.,]\d+)?\s?(?:mW|kW|W|kWh|Wh|mV|"
    r"°C|dBm|dB|kbit/s|Mbit/s|rpm|ot/min|ms|µs|"
    r"%(?![a-z]))\b")

# Even in those units a number can be a specification rather than an observation —
# "rated 3680 W", "max 2500 W". A spec needs no provenance, so nearby wording that
# announces one silences the finding.
SPEC_RE = re.compile(
    r"\b(?:rated|rating|nominal|nameplate|spec|specified|limit|limited|max|maximum|"
    r"min|minimum|capacity|breaker|fuse|supply|threshold|setpoint|hysteresis|"
    r"jmenovit\w*|jisti[čc]\w*|pr[áa]h\w*|hysterez\w*|nastaven\w*|"
    r"[šs]t[íi]tk\w*|maxim\w*|kapacit\w*|nap[áa]jen\w*)",
    re.IGNORECASE)

# What makes a number sourced: a date in any of the forms actually used in these
# records, a bare year, or a word claiming the number was observed.
SOURCED_RE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b\d{1,2}\.\s?\d{1,2}\.\s?(?:19|20)?\d{2}\b|"
    r"\b(?:measured|verified|confirmed|observed|logged|telemetry|"
    r"nam[ěe][řr]en|ov[ěe][řr]en|zm[ěe][řr]en)",
    re.IGNORECASE)

# How far from a number to look for that evidence. Roughly a sentence either side —
# wide enough for "on 2026-08-28 the spin peaked at 199 W", narrow enough that a date
# in a distant paragraph does not vouch for everything below it.
SOURCE_WINDOW = 120

# Markers we write into records ourselves and then never come back to. Deliberately
# upper-case-only: prose like "the remaining work" should not trip this, but a heading
# that shouts "REMAINING" is a promise someone made.
PENDING_MARKERS = (
    "AWAITING", "NOT VERIFIED", "NOT TESTED", "UNVERIFIED", "UNRESOLVED",
    "TODO", "STILL OPEN", "NOT STARTED", "WAITING FOR", "TO VERIFY",
)


def _finding(severity: str, check: str, namespace: str, name: str, detail: str) -> dict:
    return {"severity": severity, "check": check, "namespace": namespace,
            "name": name, "detail": detail}


def check_links(records: list[dict]) -> list[dict]:
    """`[[links]]` pointing at a record that does not exist.

    Links are matched by NAME across every namespace, which is how they are written —
    `[[reference-lmstudio]]`, not `[[shared/reference-lmstudio]]`. A rename therefore
    breaks every link to the old name, which is exactly what this catches.
    """
    known = {r["name"] for r in records}
    out = []
    for r in records:
        for target in dict.fromkeys(LINK_RE.findall(r.get("content") or "")):
            if target not in known:
                out.append(_finding(
                    "broken", "link", r["namespace"], r["name"],
                    f"links to [[{target}]], which does not exist"))
    return out


def check_pending(records: list[dict]) -> list[dict]:
    """Records that still shout about unfinished business."""
    out = []
    for r in records:
        content = r.get("content") or ""
        hits = sorted({m for m in PENDING_MARKERS if m in content})
        if hits:
            out.append(_finding(
                "stale", "pending", r["namespace"], r["name"],
                f"still marked: {', '.join(hits)} — check whether it is still true"))
    return out


def check_tags(records: list[dict]) -> list[dict]:
    """Records with no tags at all — nothing will ever filter to them."""
    return [
        _finding("hygiene", "untagged", r["namespace"], r["name"],
                 "has no tags, so no filter will ever find it")
        for r in records if not r.get("tags")
    ]


def check_lonely_tags(records: list[dict]) -> list[dict]:
    """Tags used by exactly one record — OPT-IN, see below.

    The idea was that a single-use tag is either a typo of a real tag or a category
    that never caught on. The first real run said otherwise: 9 of 19 findings were
    lonely tags and not one was actionable, because in a small memory plenty of
    perfectly good categories legitimately have one member (3d-printing, audit,
    opencode). A check that is nine parts noise trains you to skim past the other ten,
    so it only runs when asked for. It starts earning its place once the taxonomy is
    big enough that a genuine typo hides among the singletons.
    """
    counts = Counter(t for r in records for t in (r.get("tags") or []))
    out = []
    for tag, n in sorted(counts.items()):
        if n == 1:
            owner = next(r for r in records if tag in (r.get("tags") or []))
            out.append(_finding(
                "hygiene", "lonely-tag", owner["namespace"], owner["name"],
                f"tag '{tag}' is used by this record only"))
    return out


def check_empty(records: list[dict]) -> list[dict]:
    """Records with no content, or a description that just repeats the name."""
    out = []
    for r in records:
        if not (r.get("content") or "").strip():
            out.append(_finding("broken", "empty", r["namespace"], r["name"],
                                "has no content"))
        desc = (r.get("description") or "").strip()
        if not desc:
            out.append(_finding("hygiene", "no-description", r["namespace"], r["name"],
                                "has no description, so it is invisible in listings"))
        elif desc.lower().replace(" ", "-") == r["name"].lower():
            out.append(_finding("hygiene", "echo-description", r["namespace"], r["name"],
                                "the description only repeats the name"))
    return out


def _age_days(stamp: str, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 86400


def check_unverified(records: list[dict], *, max_age_days: int = STALE_AFTER_DAYS,
                     now: datetime | None = None) -> list[dict]:
    """Records holding a volatile fact that nobody has confirmed in a long time.

    The server tells every agent that records are a point in time and to verify before
    relying on one. Nothing recorded whether anyone ever did, so the instruction was
    unenforceable — `updated` only says when the text last changed, and for an
    untouched record that is the day it was written. `verified` is the missing half,
    and this check is what makes writing it worth the trouble.

    The clock starts at `verified` when there is one, otherwise at `updated`: a record
    edited last week has effectively been looked at, whatever the stamp says. Silence
    is the intended output on a young memory — nothing here fires until something has
    genuinely been sitting unread for half a year.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for r in records:
        text = f"{r.get('description') or ''}\n{r.get('content') or ''}"
        if not any(rx.search(text) for rx in VOLATILE_RE):
            continue
        basis = r.get("verified") or r.get("updated") or ""
        age = _age_days(basis, now)
        if age is None or age < max_age_days:
            continue
        how = "confirmed" if r.get("verified") else "edited"
        out.append(_finding(
            "stale", "unverified", r["namespace"], r["name"],
            f"holds an address, port or version last {how} {int(age)} days ago — "
            f"re-check it and stamp it with memory_verify"))
    return out


def check_unsourced_numbers(records: list[dict], *, min_hits: int = 2) -> list[dict]:
    """Measured-looking quantities with nothing nearby saying where they came from.

    The failure this exists for, from 2026-08-29: a record stated "spin = 300-500 W"
    as flatly as every real measurement around it. It was an early guess that got
    written down, and months later it was used to argue how to tell wash programs
    apart. The real figure, once measured, was 199-277 W. Nothing was lying — the
    record simply could not say which of its numbers had ever been checked.

    A finding here is NOT an error. It says: this number reads like a measurement and
    carries no date or word claiming it is one. Either add where it came from, or
    mark it as an estimate.

    Kept narrow on purpose, because a noisy check gets ignored (see check_lonely_tags):
      * only units that denote a MEASURED quantity — power, energy, temperature,
        signal, duration. Physical dimensions (mm, cm) and prices are excluded: those
        come off a caliper or a receipt and do not drift.
      * a number counts as sourced if a date, a year or a word like "measured" sits
        within `WINDOW` characters of it — the way people actually write it down.
      * `min_hits` unsourced numbers before the record is reported at all, so a single
        stray figure in prose stays quiet.
    """
    out = []
    for r in records:
        text = f"{r.get('description') or ''}\n{r.get('content') or ''}"
        bare = []
        for m in MEASURED_RE.finditer(text):
            lo = max(0, m.start() - SOURCE_WINDOW)
            around = text[lo:m.end() + SOURCE_WINDOW]
            if SOURCED_RE.search(around):
                continue
            # a specification is not an observation and needs no provenance; look in a
            # tighter window than for dates, since "rated" governs the phrase it is in,
            # not the paragraph
            if SPEC_RE.search(text[max(0, m.start() - 40):m.end() + 40]):
                continue
            bare.append(m.group(0).strip())
            if len(bare) > 12:
                break
        if len(bare) < min_hits:
            continue
        shown = ", ".join(dict.fromkeys(bare))[:160]
        out.append(_finding(
            "hygiene", "unsourced-numbers", r["namespace"], r["name"],
            f"{len(bare)} quantities with no date or source nearby ({shown}) — "
            f"say when they were measured, or mark them as estimates"))
    return out


def check_unstructured(records: list[dict], *, min_chars: int = 12_000) -> list[dict]:
    """Big records with no headings to read them by.

    memory_read can return one `##` section of a record instead of the whole thing,
    which is what keeps a thorough record from being expensive to consult. A long
    record with no headings cannot be split, so every agent that needs one fact out
    of it pays for all of it — and the alternative, truncating, would hand back a
    silently incomplete record.

    The threshold matches sections.FULL_READ_LIMIT: below it a whole read is cheap
    enough that structure is a matter of taste, not cost.
    """
    out = []
    for r in records:
        content = r.get("content") or ""
        if len(content) < min_chars:
            continue
        if HEADING_RE.search(content):
            continue
        out.append(_finding(
            "hygiene", "unstructured", r["namespace"], r["name"],
            f"{len(content)} characters with no headings — every read pays for the "
            f"whole record; add `##` headings so it can be read in parts"))
    return out


def check_unread(records: list[dict], *, min_age_days: int = UNREAD_AFTER_DAYS,
                 since: str | None = None, now: datetime | None = None) -> list[dict]:
    """Records no agent has opened since read counting began.

    A memory decays by accumulating records that are true and useless. `updated` and
    `verified` cannot tell those apart — both look the same on a record nobody has
    ever needed. Read counts can: a record that no model has opened in half a year is
    a candidate for deletion, or for a description that would have made it findable.

    Reported as `hygiene`, never worse: being unread is not evidence of being wrong,
    and some records exist precisely so a rare question can be answered once.

    `since` is when counting started. Records that existed before it, and the window
    right after it, are skipped — otherwise this fires on the entire store on day one.
    """
    now = now or datetime.now(timezone.utc)
    # No start date means we cannot tell "nobody wants this" from "we started counting
    # this morning". Silence is the only honest answer, and it fails safe: a caller
    # that forgets to pass `since` gets nothing, not a finding on every record.
    counting_for = _age_days(since, now) if since else None
    if counting_for is None or counting_for < min_age_days:
        return []
    out = []
    for r in records:
        if r.get("reads"):
            continue
        age = _age_days(r.get("created") or r.get("updated") or "", now)
        if age is None or age < min_age_days:
            continue
        out.append(_finding(
            "hygiene", "unread", r["namespace"], r["name"],
            f"no agent has read this in the {int(min_age_days)} days since counting "
            f"began — either it is dead weight, or its description does not say "
            f"what it is for"))
    return out


CHECKS = (check_links, check_pending, check_tags, check_empty, check_unverified,
          check_unsourced_numbers, check_unread, check_unstructured)
OPTIONAL_CHECKS = (check_lonely_tags,)


def run(records: list[dict], *, include_optional: bool = False,
        reads_since: str | None = None) -> list[dict]:
    """Every check over already-loaded records. Sorted worst first, then by record.

    `include_optional` adds the checks that are too noisy to be worth reading every
    time — see check_lonely_tags for what "too noisy" meant in practice.

    `reads_since` is when read counting started; check_unread stays silent without it,
    since it cannot tell "nobody wants this" from "we only started counting today".
    """
    order = {"broken": 0, "stale": 1, "hygiene": 2}
    checks = CHECKS + (OPTIONAL_CHECKS if include_optional else ())
    findings = []
    for check in checks:
        findings.extend(check(records, since=reads_since) if check is check_unread
                        else check(records))
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["namespace"], f["name"]))
    return findings


def summarize(findings: list[dict]) -> dict:
    """Counts by severity and by check — the one-line version for a handshake or a UI."""
    return {"total": len(findings),
            "by_severity": dict(Counter(f["severity"] for f in findings)),
            "by_check": dict(Counter(f["check"] for f in findings))}
