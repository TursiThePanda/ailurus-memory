"""Test of the semantic layer: encrypted vectors, incremental indexing, overlap.

Two properties matter more than retrieval quality here:

1. **Vectors are encrypted.** An embedding can be partially inverted, so a plaintext
   vector table beside an encrypted payload would quietly undo the encryption.
2. **`share` reaches the vectors too.** A record hidden from an agent must not come
   back through a similarity hit — the name alone is the leak we are preventing.

Skipped (not failed) when the optional model is absent: the whole point of the design
is that everything else works without it.
"""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import sys
import tempfile

from ai_memory.features import embed
from ai_memory.core.store import SecureStore

PW = "semantic-test-passphrase"
ok = True

if not embed.is_ready():
    print("SKIPPED -- the embedding model is not set up "
          "(python manage.py embed --setup)")
    sys.exit(0)

from ai_memory.features import semantic # noqa: E402  (only meaningful once the model is present)


def check(label: str, cond: bool) -> None:
    global ok
    ok = ok and cond
    print(f"  {'OK  ' if cond else 'FAIL'} {label}")


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    path = os.path.join(tmp, "memory.db")
    s = SecureStore(path, PW)

    s.write(content="The bedroom Midea unit runs a night cooling automation.",
            description="Midea AC in the bedroom, night cooling",
            agent="claude", namespace="shared", name="bedroom-climate")
    s.write(content="The office Midea AC holds temperature from a Shelly sensor.",
            description="Home Assistant automation for the office air conditioner",
            agent="claude", namespace="shared", name="office-climate")
    s.write(content="Enrolled TPM2 so the root disk unlocks without a passphrase.",
            description="TPM2 auto-unlock of the LUKS root disk",
            agent="claude", namespace="shared", name="luks-tpm")

    result = semantic.reindex(s)
    check("indexes every record", result["indexed"] == 3)
    check("a second pass has nothing to do", semantic.reindex(s)["indexed"] == 0)
    check("coverage is reported", s.vector_coverage() == (3, 3))

    # --- the security property: nothing readable on disk ---
    with open(path, "rb") as f:
        raw = f.read()
    check("no record text in the file", b"night cooling automation" not in raw)
    check("no vector table header in the clear", b"bedroom-climate" not in raw)

    # --- similarity is meaningful ---
    hits = semantic.similar_to(s, "shared", "bedroom-climate", threshold=0.2)
    names = [h["name"] for h in hits]
    check("the other climate record is the closest", names and names[0] == "office-climate")
    check("the unrelated record scores lower",
          all(h["score"] < 0.5 for h in hits if h["name"] == "luks-tpm"))

    # --- semantic search finds what a word search cannot ---
    # "chilly weather indoors" shares no word — nor any substring, which the keyword
    # search matches on: "cool" would have hit "cooling" and made this prove nothing
    query = "chilly weather indoors"
    found = semantic.search(s, query, limit=2, threshold=0.15)
    check("finds records sharing no words with the query",
          bool(found) and {h["name"] for h in found} <= {"bedroom-climate", "office-climate"})
    check("the keyword search finds nothing for the same query", s.search(query) == [])

    # --- staleness, detected by fingerprint rather than timestamp ---
    # this edit lands in the same second as the indexing above, which is exactly the
    # case a timestamp-based check used to miss
    s.update(namespace="shared", name="luks-tpm", agent="user", _trusted=True,
             content="Rewritten to be about air conditioning in the bedroom instead.")
    check("an edit in the same second is still seen as stale",
          semantic.index_status(s)["stale"] == 1)
    check("reindex picks up only the changed one", semantic.reindex(s)["indexed"] == 1)
    check("nothing is stale afterwards", semantic.index_status(s)["stale"] == 0)
    check("an edit that does not touch the embedded text costs nothing",
          semantic.reindex(s)["indexed"] == 0)

    # --- per-record permissions reach the vectors ---
    s.update(namespace="shared", name="office-climate", agent="user", _trusted=True,
             share=["lmstudio-local"], _set_share=True)
    check("a restricted record's vector is hidden",
          ("shared", "office-climate") not in s.vectors(agent="claude"))
    check("the allowed agent still sees it",
          ("shared", "office-climate") in s.vectors(agent="lmstudio-local"))
    check("similarity does not leak the restricted record",
          all(h["name"] != "office-climate"
              for h in semantic.similar_to(s, "shared", "bedroom-climate",
                                           threshold=0.1, agent="claude")))
    check("the user still sees everything", ("shared", "office-climate") in s.vectors())

    # --- deleting a record takes its vector with it ---
    s.delete("shared", "luks-tpm", agent="user", _trusted=True)
    check("deleting a record drops its vector", s.vector_coverage() == (2, 2))

    # --- overlap detection ---
    pairs = semantic.overlaps(s, threshold=0.9)
    check("a high threshold does not invent pairs", pairs == [])

# --- the duplicate warning at write time -----------------------------------------
#
# memory_overlaps only ever found duplicates after the fact, and only when someone
# remembered to run it. The same comparison at write time is what actually prevents
# the fork: the agent is told while it still has the context to merge the two.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    from ai_memory.server import server
    server.STORE = SecureStore(os.path.join(tmp, "memory.db"), PW)

    first = server.memory_write(
        content="The bedroom Midea unit pre-cools at 22:00 and stops at 02:00.",
        description="Midea AC in the bedroom, night cooling",
        namespace="shared", name="bedroom-climate")
    check("the first record has nothing to warn about", "similar" not in first)
    check("writing indexes the record straight away",
          ("shared", "bedroom-climate") in server.STORE.vectors())

    second = server.memory_write(
        content="Night cooling for the bedroom air conditioner: pre-cool, sleep, off.",
        description="Bedroom air conditioning at night",
        namespace="shared", name="bedroom-night-ac")
    check("a near-duplicate is reported on the way in", "similar" in second)
    check("the warning names the record to merge with",
          second.get("similar") and second["similar"][0]["name"] == "bedroom-climate")
    check("the write still happened",
          server.STORE.read("shared", "bedroom-night-ac")["content"].startswith("Night"))

    unrelated = server.memory_write(
        content="Enrolled TPM2 so the root disk unlocks without typing a passphrase.",
        description="TPM2 auto-unlock of the LUKS root disk",
        namespace="shared", name="luks-tpm")
    check("an unrelated record is not accused", "similar" not in unrelated)

    # updating is the behaviour the warning pushes towards, so it must not nag
    updated = server.memory_update(
        namespace="shared", name="bedroom-climate",
        content="Night cooling, rewritten: pre-cool 22:00, sleep 00:00, off 02:00.")
    check("updating never warns", "similar" not in updated)
    check("updating keeps the vector current",
          semantic.index_status(server.STORE)["stale"] == 0)

# --- vectors produced by a DIFFERENT model ----------------------------------------
#
# 1.1.0 replaced the embedding model with a multilingual one. Two models produce 384
# numbers each and no shared meaning between them, so an old vector is not a worse
# match -- it is a meaningless one, and ranking it returns confident nonsense. The
# index has to refuse such vectors and be able to say how many are waiting.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    s3 = SecureStore(os.path.join(tmp, "memory.db"), PW)
    s3.write(content="The bedroom unit pre-cools at 22:00.", description="night cooling",
             agent="claude", namespace="shared", name="one")
    s3.write(content="Night cooling for the bedroom AC.", description="bedroom at night",
             agent="claude", namespace="shared", name="two")
    semantic.reindex(s3)

    check("a fresh index records the model on every vector",
          semantic.stale_count(s3.vectors()) == 0)
    check("...and every vector is usable", len(semantic.usable(s3.vectors())) == 2)

    kept = s3.vectors()[("shared", "one")]
    # written the way a pre-1.1.0 version did it: same shape, no model recorded
    s3.set_vector("shared", "one", kept["v"], kept["fp"])
    check("a vector with no model recorded counts as stale",
          semantic.stale_count(s3.vectors()) == 1)
    check("...and is not offered to a search",
          ("shared", "one") not in semantic.usable(s3.vectors()))

    s3.set_vector("shared", "one", kept["v"], kept["fp"], model="some-other-model")
    check("a vector from another model counts as stale too",
          semantic.stale_count(s3.vectors()) == 1)
    check("...and is not offered to a search either",
          ("shared", "one") not in semantic.usable(s3.vectors()))
    check("a search still works on the vectors that remain",
          isinstance(semantic.search(s3, "bedroom cooling", threshold=0.1), list))

    check("reindex notices it without --force", semantic.reindex(s3)["indexed"] == 1)
    check("...and nothing is stale afterwards",
          semantic.stale_count(s3.vectors()) == 0)


print("\n" + ("SEMANTIC OK " if ok else "SEMANTIC FAILED"))
sys.exit(0 if ok else 1)
