"""Upkeep: the agents that use the memory keep it in order -- OFF by default.

The checks in lint.py and the look-alike pairs from semantic.overlaps() find the
problems. Until 1.6.1 an agent only saw them when somebody asked for a lint run, as one
long list, the same false alarms every time. This module puts each problem in front of
the agent at the moment it is working with that record, says what the agent may do
about it, and remembers what was decided so nobody is asked twice.

Each open item carries a `fix`:
  safe      -- the agent fixes it on its own, as part of its work (a broken link with
               an obvious target, missing tags or description, a long record without
               headings). Every edit lands in history and can be reverted.
  ask_user  -- a judgement about the owner's facts: are these two records one subject,
               is this still true, should this go. The agent raises it ONLY when the
               user brings up that subject (the owner's decision, 2026-11-17).
  note      -- nothing to do now; worth keeping in mind on the next write.

Decisions (`fixed`, `not_a_problem`, `user_said_keep`) are stored in the encrypted
settings table, so they are in every backup and every agent sees them. The first two
hold until one of the records involved changes; `user_said_keep` holds for good.

Nothing here edits a record. The agent does, with the ordinary tools, in a conversation
the owner is part of -- which is what keeps this on the right side of the 2026-08-30
decision against a curator that acts on its own.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone

from ai_memory.features import lint

SETTINGS_KEY = "upkeep"
DECISIONS_KEY = "upkeep_decisions"

OUTCOMES = ("fixed", "not_a_problem", "user_said_keep")

# Overlap pairs worth an `ask_user`. Higher than memory_overlaps' 0.65 on purpose: that
# tool is a magnifying glass the user points; this one speaks up unasked, so it should
# only do so when a pair is close. Related-but-distinct pairs on the real store scored
# up to ~0.79, so some questions will still be "no, two subjects" -- answered once.
OVERLAP_THRESHOLD = 0.7

# How many items ride along on one response. A record with ten problems would otherwise
# turn every read of it into a to-do list.
MAX_HINTS = 3

# Findings are computed over the whole store; recomputing them on every read would mean
# decrypting every record per read. Cached per agent until the store changes, and for
# CACHE_SECONDS at most (verification stamps do not move the change token).
CACHE_SECONDS = 300

FIX_BY_CHECK = {
    "untagged": "safe",
    "no-description": "safe",
    "echo-description": "safe",
    "unstructured": "safe",
    "empty": "ask_user",
    "pending": "ask_user",
    "unverified": "ask_user",
    "overlap": "ask_user",
    "unsourced-numbers": "note",
    "lonely-tag": "note",
}
# Not offered as hints at all: a record being read is, by definition, not unread.
SKIP_CHECKS = {"unread"}

SEVERITY_ORDER = {"broken": 0, "stale": 1, "hygiene": 2}

_cache: dict[str | None, tuple[str, float, list[dict]]] = {}
_cache_lock = threading.Lock()

# Every finding handed out recently, by id. The natural order for an agent is "fix it,
# then say so" -- and by then the finding is gone from a fresh computation, so `fixed`
# is checked against what was handed out, not against what is open now.
_seen: dict[str, dict] = {}
SEEN_MAX = 5000

# How long a `fixed` decision is kept after its record changed: it no longer hides
# anything (the record changed, so it does not hold), it only feeds "fixed by agents
# this week" on the admin overview.
FIXED_LOG_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- configuration ----------
def config(store) -> dict:
    cfg = store.get_settings(SETTINGS_KEY, {})
    cfg.setdefault("enabled", False)
    return cfg


def enabled(store) -> bool:
    try:
        return bool(config(store)["enabled"])
    except Exception:
        return False


def set_enabled(store, on: bool) -> dict:
    cfg = config(store)
    cfg["enabled"] = bool(on)
    store.set_settings(SETTINGS_KEY, cfg)
    invalidate()
    return cfg


def agent_notice(store) -> str:
    """The paragraph appended to every agent's instructions while upkeep is on."""
    if not enabled(store):
        return ""
    return (
        "MEMORY UPKEEP\n"
        "A record you read or write may come back with an `upkeep` list: problems the "
        "server found in it. Each item has an `id` and a `fix`:\n"
        "- `safe`: fix it yourself as part of your work, without asking (memory_edit or "
        "memory_update; a broken link usually names its `suggestion`). Then report it "
        "with memory_lint(resolve=[{\"id\": \"<id>\", \"outcome\": \"fixed\"}]). If on a "
        "closer look it is not a problem, resolve it as \"not_a_problem\".\n"
        "- `ask_user`: a judgement only the user can make (one subject or two, still "
        "true, delete). Raise it ONLY when the user themselves brings up the subject of "
        "that record -- never as an opener, never as an aside to an unrelated question. "
        "Act on the answer; if they want it left as it is, resolve it as "
        "\"user_said_keep\".\n"
        "- `note`: nothing to do now; keep it in mind the next time you write that "
        "record.\n"
        "Do not spend a conversation on upkeep unless the user asks for it.")


# ---------- identity ----------
def finding_id(check: str, record: str, key: str = "") -> str:
    """Stable short id. `key` separates several findings of one check on one record
    (a link's target, an overlap's other record); the human-readable detail is NOT part
    of it, because its wording (a suggestion, a count) may change while the problem
    stays the same."""
    return hashlib.sha256(f"{check}|{record}|{key}".encode()).hexdigest()[:10]


def fix_for(finding: dict) -> str:
    if finding["check"] == "link":
        return "safe" if finding.get("suggestion") else "ask_user"
    return FIX_BY_CHECK.get(finding["check"], "note")


def _decorate(f: dict) -> dict:
    record = f"{f['namespace']}/{f['name']}"
    key = f.get("target") or f.get("other") or ""
    if f["check"] == "unsourced-numbers":
        key = ""  # its detail lists the numbers; the problem is the record as a whole
    out = dict(f)
    out["id"] = finding_id(f["check"], record, key)
    out["fix"] = fix_for(f)
    out["records"] = sorted({record, *([f["other"]] if f.get("other") else [])})
    return out


# ---------- computing the findings ----------
def _overlap_findings(store, agent, readable) -> list[dict]:
    try:
        from ai_memory.features import semantic
        pairs = semantic.overlaps(store, threshold=OVERLAP_THRESHOLD, limit=500, agent=agent)
    except Exception:  # no model installed, no vectors: overlaps are a bonus
        return []
    out = []
    for p in pairs:
        if not (readable(p["a"].split("/", 1)[0]) and readable(p["b"].split("/", 1)[0])):
            continue
        for me, other in ((p["a"], p["b"]), (p["b"], p["a"])):
            ns, name = me.split("/", 1)
            out.append({"severity": "stale", "check": "overlap", "namespace": ns,
                        "name": name, "other": other, "score": p["score"],
                        "detail": f"{p['score']:.2f} similar to {other} -- one subject or "
                                  f"two? If one, fold it into the better record."})
    return out


def _compute(store, agent, readable) -> list[dict]:
    records = [r for r in store.dump(agent=agent) if readable(r["namespace"])]
    found = [f for f in lint.run(records, reads_since=store.reads_since())
             if f["check"] not in SKIP_CHECKS]
    found += _overlap_findings(store, agent, readable)
    return [_decorate(f) for f in found]


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def all_findings(store, agent: str | None = None, readable=None) -> list[dict]:
    """Every finding the agent may see, decorated with id/fix/records, decisions NOT
    applied. Cached per agent (see CACHE_SECONDS)."""
    readable = readable or (lambda ns: True)
    token = store.change_token()
    with _cache_lock:
        hit = _cache.get(agent)
        if hit and hit[0] == token and time.monotonic() - hit[1] < CACHE_SECONDS:
            return hit[2]
    found = _compute(store, agent, readable)
    with _cache_lock:
        _cache[agent] = (token, time.monotonic(), found)
        for f in found:
            _seen[f["id"]] = f
        while len(_seen) > SEEN_MAX:
            _seen.pop(next(iter(_seen)))
    return found


# ---------- decisions ----------
def decisions(store) -> dict:
    return store.get_settings(DECISIONS_KEY, {})


_versions_cache: tuple[str, dict] | None = None


def _updated(store) -> dict[str, str]:
    """A version stamp of every record, by "ns/name": a hash of what it says.

    Not the `updated` time: that has one-second resolution, and "decide, then edit"
    inside one second is exactly what a fast agent does -- the change would go unseen
    and a not_a_problem would hide a record that has since changed. Cached by the
    store's change token; no read is counted."""
    global _versions_cache
    token = store.change_token()
    with _cache_lock:
        if _versions_cache and _versions_cache[0] == token:
            return _versions_cache[1]
    out = {}
    for r in store.dump():
        blob = "\x1f".join([r.get("updated", ""), r.get("description", ""),
                            ",".join(r.get("tags") or []), r.get("content", "")])
        out[f"{r['namespace']}/{r['name']}"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    with _cache_lock:
        _versions_cache = (token, out)
    return out


def _stamps(updated: dict, records: list[str]) -> dict:
    return {rec: updated.get(rec, "") for rec in records}


def _holds(updated: dict, d: dict) -> bool:
    """A decision still applies: kept for good, or none of its records changed since."""
    if d.get("outcome") == "user_said_keep":
        return True
    stamps = d.get("stamps", {})
    return bool(stamps) and _stamps(updated, list(stamps)) == stamps


def apply_decisions(store, found: list[dict]) -> tuple[list[dict], int]:
    """Drop the findings a still-valid decision covers. Returns (open, hidden)."""
    found = [f if "id" in f else _decorate(f) for f in found]
    decided = decisions(store)
    if not decided:
        return found, 0
    updated = _updated(store)
    out, hidden = [], 0
    for f in found:
        d = decided.get(f["id"])
        if d and _holds(updated, d):
            hidden += 1
            continue
        out.append(f)
    return out, hidden


def open_findings(store, agent: str | None = None, readable=None) -> tuple[list[dict], int]:
    """(findings nobody has decided, how many were hidden by a decision)."""
    return apply_decisions(store, all_findings(store, agent, readable))


def hints_for(store, namespace: str, name: str, agent: str | None = None,
              readable=None) -> list[dict]:
    """The `upkeep` list for one record, or [] when upkeep is off or all is well."""
    if not enabled(store):
        return []
    try:
        found, _ = open_findings(store, agent, readable)
    except Exception:  # upkeep must never break a read
        return []
    mine = [f for f in found if f["namespace"] == namespace and f["name"] == name
            and f["check"] != "lonely-tag"]
    mine.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9),
                             ("safe", "ask_user", "note").index(f["fix"])))
    keep = ("id", "check", "fix", "detail", "target", "suggestion", "other")
    return [{k: f[k] for k in keep if k in f} for f in mine[:MAX_HINTS]]


def resolve(store, items: list[dict], agent: str, agent_may_write=None,
            readable=None) -> dict:
    """Record decisions. Only ids of findings this agent can currently see are accepted,
    and only on records it may write -- a decision hides the item from everyone."""
    agent_may_write = agent_may_write or (lambda ns: True)
    visible = {f["id"]: f for f in all_findings(store, agent, readable)}
    decided = decisions(store)
    updated = _updated(store)
    done, rejected = [], []
    for item in items or []:
        fid, outcome = str(item.get("id", "")), str(item.get("outcome", ""))
        f = visible.get(fid)
        if f is None and outcome == "fixed":
            with _cache_lock:
                f = _seen.get(fid)  # fixed first, reported second: already gone
            if f is not None and not all(readable(r.split("/", 1)[0]) if readable else True
                                         for r in f["records"]):
                f = None
        if outcome not in OUTCOMES:
            rejected.append({"id": fid, "reason": f"outcome must be one of {list(OUTCOMES)}"})
        elif f is None:
            rejected.append({"id": fid, "reason": "no such open item (already fixed, or "
                                                  "not visible to you)"})
        elif not all(agent_may_write(r.split("/", 1)[0]) for r in f["records"]):
            rejected.append({"id": fid, "reason": "you cannot write the record(s) involved"})
        else:
            decided[fid] = {"outcome": outcome, "by": agent, "at": _now(),
                            "check": f["check"], "records": f["records"],
                            "detail": f["detail"][:300],
                            "stamps": _stamps(updated, f["records"])}
            done.append(fid)
    if done:
        store.set_settings(DECISIONS_KEY, _prune(updated, decided))
        invalidate()
    return {"resolved": done, "rejected": rejected}


def _prune(updated: dict, decided: dict, keep: int = 2000) -> dict:
    """Drop decisions that no longer hold (their record changed), then cap the size."""
    cutoff = datetime.now(timezone.utc).timestamp() - FIXED_LOG_DAYS * 86400

    def recent_fix(d):
        try:
            return (d.get("outcome") == "fixed"
                    and datetime.fromisoformat(d.get("at", "")).timestamp() >= cutoff)
        except ValueError:
            return False

    live = {k: v for k, v in decided.items() if _holds(updated, v) or recent_fix(v)}
    if len(live) > keep:
        live = dict(sorted(live.items(), key=lambda kv: kv[1].get("at", ""))[-keep:])
    return live


def overrule(store, fid: str, action: str, by: str = "owner") -> None:
    """The owner's override from the admin web: `reopen` forgets a decision,
    `keep` turns it (or an open item) into a permanent user_said_keep."""
    decided = decisions(store)
    if action == "reopen":
        decided.pop(fid, None)
    elif action == "keep":
        f = next((x for x in all_findings(store) if x["id"] == fid), None)
        base = decided.get(fid) or ({"check": f["check"], "records": f["records"],
                                     "detail": f["detail"][:300]} if f else None)
        if base is None:
            return
        decided[fid] = {**base, "outcome": "user_said_keep", "by": by, "at": _now(),
                        "stamps": _stamps(_updated(store), base["records"])}
    store.set_settings(DECISIONS_KEY, decided)
    invalidate()


def summary(store, days: int = 7) -> dict:
    """For the admin overview: open items by fix, and what agents fixed lately."""
    found, hidden = open_findings(store)
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    recent = 0
    for d in decisions(store).values():
        try:
            when = datetime.fromisoformat(d.get("at", "")).timestamp()
        except ValueError:
            continue
        if d.get("outcome") == "fixed" and when >= cutoff:
            recent += 1
    by_fix = {k: sum(1 for f in found if f["fix"] == k) for k in ("safe", "ask_user", "note")}
    return {"open": len(found), "by_fix": by_fix, "decided": hidden,
            "fixed_recently": recent, "days": days}
