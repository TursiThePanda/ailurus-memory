"""Semantic layer: indexing records and finding ones that overlap.

This is the curator's second layer. The first (lint.py) finds facts — a link that
points at nothing. This one finds *resemblance*, which is a judgement with a number
attached, so it is deliberately presented as "these two look related, go look" rather
than as a verdict.

It degrades: with the model missing, everything here raises EmbeddingUnavailable and
the caller falls back to the deterministic checks, which always work.
"""
from __future__ import annotations

import hashlib

from ai_memory.features import embed
def fingerprint(record: dict) -> str:
    """Hash of the exact text that gets embedded, AND of the model that embeds it.

    This is the staleness signal. It used to be the record's `updated` timestamp,
    which has second resolution — an edit in the same second as the indexing pass
    slipped through and left a vector describing text that no longer existed. A hash
    of the input also means an edit that does not change the embedded text costs
    nothing to re-index.

    The model id joined it in 1.1.0, when the model changed for the first time. Without
    it, swapping models leaves every old vector looking current: same 384 numbers, same
    fingerprint, no shared meaning — and a search would rank them against new vectors and
    return confident nonsense. With it, changing the model marks the whole index stale,
    which is exactly what it is.
    """
    material = f"{embed.MODEL_ID}\n{embed.text_for(record)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def reindex(store, *, force: bool = False, progress=None) -> dict:
    """Embed records that have no vector, or whose vector is out of date.

    Returns counts rather than the vectors themselves — this is a maintenance
    operation, and 400 records worth of floats is not something to hand back.
    """
    records = store.dump()
    known = store.vectors()
    # Three reasons to embed a record: it has no vector, its text changed, or its
    # vector came from a different model. The last one is checked explicitly rather
    # than left to the fingerprint — the model id is part of that hash, so in practice
    # they agree, but a rebuild must not depend on two things agreeing when one of them
    # can be checked directly.
    todo = [r for r in records
            if force
            or (r["namespace"], r["name"]) not in known
            or known[(r["namespace"], r["name"])]["fp"] != fingerprint(r)
            or known[(r["namespace"], r["name"])].get("m") != embed.MODEL_ID]

    if not todo:
        return {"indexed": 0, "skipped": len(records), "total": len(records)}

    batch = 16  # small: this may share a NAS with other containers
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vectors = embed.embed([embed.text_for(r) for r in chunk])
        for record, vector in zip(chunk, vectors):
            store.set_vector(record["namespace"], record["name"], vector,
                             fingerprint(record), model=embed.MODEL_ID)
        if progress:
            progress(min(i + batch, len(todo)), len(todo))

    return {"indexed": len(todo), "skipped": len(records) - len(todo), "total": len(records)}


def usable(vectors: dict) -> dict:
    """Only the vectors this model produced.

    Anything else is not a worse match, it is a meaningless one — a different model's
    384 numbers share no space with these. Dropping them silently is right for a search
    (fewer results, all valid); what must NOT happen is ranking them. `stale_count`
    below is how the user finds out an index needs rebuilding.
    """
    return {k: v for k, v in vectors.items() if v.get("m") == embed.MODEL_ID}


def stale_count(vectors: dict) -> int:
    """How many stored vectors came from another model (or from before 1.1.0)."""
    return sum(1 for v in vectors.values() if v.get("m") != embed.MODEL_ID)


def _cosine(a: list[float], b: list[float]) -> float:
    # the stored vectors are already L2-normalised, so a dot product is the cosine
    return sum(x * y for x, y in zip(a, b))


def similar_to(store, namespace: str, name: str, *, limit: int = 5,
               threshold: float = 0.4, agent: str | None = None) -> list[dict]:
    """Records resembling one specific record, most alike first."""
    vectors = usable(store.vectors(agent=agent))
    target = vectors.get((namespace, name))
    if target is None:
        raise LookupError(
            f"{namespace}/{name} has no vector yet — run `ai-memory-manage embed --index`")
    hits = [
        {"namespace": ns, "name": nm, "score": round(_cosine(target["v"], data["v"]), 3)}
        for (ns, nm), data in vectors.items()
        if (ns, nm) != (namespace, name) and _cosine(target["v"], data["v"]) >= threshold
    ]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def overlaps(store, *, threshold: float = 0.65, limit: int = 20,
             agent: str | None = None) -> list[dict]:
    """Pairs of records that look like they cover the same ground.

    The default was 0.75 until the first run over a real store, where the one genuine
    duplicate scored 0.698 and would have been missed — while a merely related pair
    (two records about the same air conditioner) scored 0.737. Textual resemblance and
    actual redundancy are not the same thing, so no threshold separates them: this is
    a magnifying glass, not a verdict, which is also why it reports and never merges.
    """
    vectors = usable(store.vectors(agent=agent))
    keys = sorted(vectors)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            score = _cosine(vectors[a]["v"], vectors[b]["v"])
            if score >= threshold:
                pairs.append({"a": f"{a[0]}/{a[1]}", "b": f"{b[0]}/{b[1]}",
                              "score": round(score, 3)})
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs[:limit]


def search(store, query: str, *, limit: int = 10, threshold: float = 0.25,
           agent: str | None = None) -> list[dict]:
    """Semantic search: finds records by meaning, where the word search finds none.

    Complements `store.search()` rather than replacing it — exact terms (an IP, an
    entity id, a token) are what the keyword search is good at, and this is worse at
    them.
    """
    vectors = usable(store.vectors(agent=agent))
    if not vectors:
        return []
    q = embed.embed([query])[0].tolist()
    hits = [
        {"namespace": ns, "name": nm, "score": round(_cosine(q, data["v"]), 3)}
        for (ns, nm), data in vectors.items() if _cosine(q, data["v"]) >= threshold
    ]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def index_status(store) -> dict:
    """How much of the store is indexed and how much has drifted since."""
    records = store.dump()
    known = store.vectors()
    missing = stale = 0
    for r in records:
        entry = known.get((r["namespace"], r["name"]))
        if entry is None:
            missing += 1
        elif entry["fp"] != fingerprint(r):
            stale += 1
    return {"total": len(records), "indexed": len(records) - missing,
            "missing": missing, "stale": stale,
            "up_to_date": len(records) - missing - stale}
