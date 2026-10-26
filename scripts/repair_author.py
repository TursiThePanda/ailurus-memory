"""One-off repair: restore the original author of a record.

Needed because memory_move used to recreate the record at the target, which made the
moving agent its author and reset `created` to the moment of the move. The bug is
fixed (store.write now takes _author/_created), but records moved before the fix keep
the wrong provenance — and nothing in the normal API can set `author`.

    python scripts/repair_author.py lmstudio-local hardware-info-gpu lmstudio-local

Requires the master passphrase (env AI_MEMORY_PASSPHRASE or a prompt).
"""
import os
import sys

# run as `python scripts/repair_author.py` from the checkout: make the package importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_memory.core.store import SecureStore, WrongPassphrase
from ai_memory.core.unlock import get_passphrase
from ai_memory.manage import DB


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: python scripts/repair_author.py <namespace> <name> <author>")
    namespace, name, author = sys.argv[1:]

    try:
        s = SecureStore(DB, get_passphrase())
    except WrongPassphrase as e:
        raise SystemExit(f"ERROR: {e}")

    with s._lock:
        p = s._get_payload(namespace, name)
        if p is None:
            raise SystemExit(f"ERROR: no such memory: {namespace}/{name}")
        old = p.get("author")
        if old == author:
            print(f"{namespace}/{name}: author is already '{author}', nothing to do.")
            return
        p["author"] = author
        nonce, ct = s.cipher.encrypt(p)
        s.db.execute("UPDATE memories SET nonce=?, ct=? WHERE namespace=? AND name_idx=?",
                     (nonce, ct, namespace, s._idx(namespace, name)))
        s._log("repair-author", "user", namespace, name, p)
        s.db.commit()
    print(f"{namespace}/{name}: author '{old}' -> '{author}'")


if __name__ == "__main__":
    main()
