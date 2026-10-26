"""AI Memory — an encrypted, portable store (a single SQLite file).

Everything (memories + history + agents/keys/permissions) lives in one encrypted
file. Only ciphertext hits the disk: content, descriptions, names and tags are
AES-GCM; records and keys are looked up through a deterministic blind index (names
do not leak). The namespace stays plaintext (needed for ACL and listings; it is
just an agent name).

The key is derived from the master passphrase (scrypt) entered at startup — it
lives in RAM only. History is a version table inside the DB (audit / revert), with
no dependency on git.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import os

from ai_memory.core.crypto import Cipher, derive_kek, unwrap_dek, wrap_dek

SLUG_RE = re.compile(r"[^a-z0-9]+")
PERMS = ("none", "r", "rw")
KEY_PREFIX = "aim_"


class WrongPassphrase(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str) -> str:
    s = SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return s or "memory"


def _safe(name: str) -> str:
    c = slugify(name)
    if not c or c in {".", ".."}:
        raise ValueError(f"invalid name: {name!r}")
    return c


class SecureStore:
    def __init__(self, path: str, passphrase: str) -> None:
        self.path = str(Path(path).expanduser())
        new = not Path(self.path).exists()
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")  # tolerates the MCP server and the admin web running concurrently
        # The connection is opened BEFORE the passphrase can be checked, so a failed
        # construction must not leave it open. It matters on Windows, where an open
        # handle locks the file: a wrong passphrase left the database undeletable, and
        # every retry leaked another handle -- the wizard's adopt path allows three.
        # On POSIX nothing visible happened, which is why it stood until the project
        # first ran on Windows and test_backup's rotation checks failed on it.
        try:
            self._init_schema()
            if new:
                self._bootstrap(passphrase)
            self.cipher = self._unlock(passphrase)
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        """Release the database file.

        Matters on WINDOWS: an open file cannot be deleted there, so a test that
        leaves a store open makes its own TemporaryDirectory cleanup fail with
        WinError 32. On POSIX the deletion succeeds regardless, which is why this was
        never needed until the project first ran on Windows.
        """
        try:
            self.db.close()
        except Exception:
            pass

    def __enter__(self) -> "SecureStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------- schema / unlock ----------
    def _init_schema(self) -> None:
        c = self.db
        c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB)")
        c.execute("CREATE TABLE IF NOT EXISTS memories ("
                  "namespace TEXT, name_idx TEXT, nonce BLOB, ct BLOB,"
                  " PRIMARY KEY (namespace, name_idx))")
        c.execute("CREATE TABLE IF NOT EXISTS history ("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, namespace TEXT,"
                  " name_idx TEXT, action TEXT, agent TEXT, nonce BLOB, ct BLOB)")
        # Settings the user changes in the admin web (currently the scheduled export).
        # ENCRYPTED like everything else: a backup destination can name an internal
        # share, and this file is deliberately allowed to sit on a NAS and offsite.
        # In the store rather than a config file so "the whole system is one file"
        # stays true -- settings travel with a restored backup.
        c.execute("CREATE TABLE IF NOT EXISTS settings ("
                  "k TEXT PRIMARY KEY, nonce BLOB, ct BLOB)")
        c.execute("CREATE TABLE IF NOT EXISTS agents ("
                  "name TEXT PRIMARY KEY, key_idx TEXT, nonce BLOB, ct BLOB)")
        # Vectors are ENCRYPTED like everything else. An embedding is partially
        # invertible — enough of the source text can be recovered from one that
        # leaving it in the clear beside an encrypted payload would defeat the point
        # of encrypting the store at all.
        c.execute("CREATE TABLE IF NOT EXISTS vectors ("
                  "namespace TEXT, name_idx TEXT, nonce BLOB, ct BLOB,"
                  " PRIMARY KEY (namespace, name_idx))")
        # Attachments: files belonging to a record — a wiring diagram, a photo of the
        # printed case, a screenshot of the error. Content AND metadata are encrypted
        # like everything else; a filename leaks as much as a description would.
        # Stored as blobs in this same file so "the whole memory is one file" stays
        # true, which is what makes backup and moving it trivial.
        c.execute("CREATE TABLE IF NOT EXISTS attachments ("
                  "id TEXT PRIMARY KEY, namespace TEXT, name_idx TEXT,"
                  " meta_nonce BLOB, meta_ct BLOB,"
                  " data_nonce BLOB, data_ct BLOB)")
        c.execute("CREATE INDEX IF NOT EXISTS attachments_owner"
                  " ON attachments (namespace, name_idx)")
        # Read counters. Deliberately NOT in the record payload: that would rewrite
        # (and re-encrypt) a record on every read, turning a read into a write for a
        # fact that is not part of the record's content. Nothing sensitive is stored
        # in the clear here that is not already — `namespace` and `agent` are plaintext
        # in `history` too, and `name_idx` is the blind index, so names still do not leak.
        c.execute("CREATE TABLE IF NOT EXISTS reads ("
                  "namespace TEXT, name_idx TEXT, n INTEGER NOT NULL DEFAULT 0,"
                  " last_ts TEXT, last_agent TEXT,"
                  " PRIMARY KEY (namespace, name_idx))")
        # When counting began. Without it, every record that predates this feature looks
        # unread forever and check_unread would fire on the whole store the day it ships
        # — the noisy-check mistake check_lonely_tags already taught us once.
        if self._meta("reads_since") is None:
            c.execute("INSERT OR IGNORE INTO meta (k,v) VALUES (?,?)",
                      ("reads_since", _now().encode()))
        c.commit()

    def _meta(self, k: str):
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def _bootstrap(self, passphrase: str) -> None:
        # a random data key (DEK) encrypts the data; the KEK from the passphrase only wraps it
        salt = os.urandom(16)
        dek = os.urandom(32)
        kek = derive_kek(passphrase, salt)
        nonce, ct = wrap_dek(kek, dek)
        self.db.execute("INSERT INTO meta (k,v) VALUES (?,?)", ("salt", salt))
        self.db.execute("INSERT INTO meta (k,v) VALUES (?,?)", ("dek_nonce", nonce))
        self.db.execute("INSERT INTO meta (k,v) VALUES (?,?)", ("dek_ct", ct))
        self.db.commit()

    def _unlock(self, passphrase: str) -> Cipher:
        kek = derive_kek(passphrase, bytes(self._meta("salt")))
        try:
            self._dek = unwrap_dek(kek, self._meta("dek_nonce"), self._meta("dek_ct"))
        except Exception:
            raise WrongPassphrase("wrong master passphrase")
        return Cipher(self._dek)

    def verify_master(self, passphrase: str) -> bool:
        try:
            kek = derive_kek(passphrase, bytes(self._meta("salt")))
            unwrap_dek(kek, self._meta("dek_nonce"), self._meta("dek_ct"))
            return True
        except Exception:
            return False

    def change_passphrase(self, new_passphrase: str) -> None:
        """Re-wrap the DEK with a new password (the data is NOT re-encrypted). Running
        processes keep working; the new password applies from the next unlock."""
        with self._lock:
            salt = os.urandom(16)
            kek = derive_kek(new_passphrase, salt)
            nonce, ct = wrap_dek(kek, self._dek)
            self.db.execute("UPDATE meta SET v=? WHERE k=?", (salt, "salt"))
            self.db.execute("UPDATE meta SET v=? WHERE k=?", (nonce, "dek_nonce"))
            self.db.execute("UPDATE meta SET v=? WHERE k=?", (ct, "dek_ct"))
            self.db.commit()

    # ---------- helpers ----------
    def _idx(self, namespace: str, name: str) -> str:
        return self.cipher.blind_index(f"{_safe(namespace)}/{_safe(name)}")

    def _summary(self, ns: str, p: dict) -> dict:
        return {"namespace": ns, "name": p["name"], "description": p.get("description", ""),
                "author": p.get("author", "unknown"), "updated": p.get("updated", ""),
                "updated_by": p.get("updated_by", ""), "type": p.get("type", "note"),
                "tags": p.get("tags", []), "share": p.get("share"),
                "verified": p.get("verified"), "verified_by": p.get("verified_by")}

    # ---------- per-record permissions ----------
    @staticmethod
    def can_record(agent: str | None, p: dict) -> bool:
        """Whether `agent` may see this particular record.

        `share` is None for the overwhelming majority of records — they inherit the
        namespace ACL and this returns True. A list restricts the record to exactly
        those agents, on top of (never instead of) the namespace ACL: being on the
        list does not grant access to a namespace the agent cannot read.

        `agent is None` is the user acting through the admin web — the user set these
        restrictions and is not subject to them.
        """
        share = p.get("share")
        if share is None or agent is None:
            return True
        return agent in share

    def _get_payload(self, namespace: str, name: str):
        idx = self._idx(namespace, name)
        row = self.db.execute("SELECT nonce, ct FROM memories WHERE namespace=? AND name_idx=?",
                              (_safe(namespace), idx)).fetchone()
        if not row:
            return None
        return self.cipher.decrypt(row[0], row[1])

    def _log(self, action: str, agent: str, namespace: str, name: str, payload: dict | None) -> None:
        body = payload if payload is not None else {"name": _safe(name), "content": None}
        nonce, ct = self.cipher.encrypt(body)
        self.db.execute(
            "INSERT INTO history (ts, namespace, name_idx, action, agent, nonce, ct)"
            " VALUES (?,?,?,?,?,?,?)",
            (_now(), _safe(namespace), self._idx(namespace, name), action, agent, nonce, ct))

    # ---------- tags ----------
    def known_tags(self) -> dict[str, int]:
        """Existing tags with a record count. Derived from the data — a tag comes into
        existence with its first use and disappears with its last."""
        counts: dict[str, int] = {}
        with self._lock:
            rows = self.db.execute("SELECT nonce, ct FROM memories").fetchall()
        for nonce, ct in rows:
            for t in self.cipher.decrypt(nonce, ct).get("tags", []):
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items()))

    def _check_tags(self, tags: list[str] | None, new_tags: list[str] | None) -> None:
        """A new tag may only appear if the caller declares it in `new_tags` (meaning the
        user approved it). Prevents the taxonomy from fragmenting into 'net' / 'network' /
        'networking' depending on which model happens to be writing."""
        if not tags:
            return
        known = self.known_tags()
        allowed = set(new_tags or [])
        unknown = [t for t in tags if t not in known and t not in allowed]
        if unknown:
            raise ValueError(
                f"unknown tags: {', '.join(sorted(unknown))}. "
                f"Existing: {', '.join(known) or '(none yet)'}. "
                f"If the user approved them, repeat the call with new_tags={sorted(unknown)}.")

    def rename_tag(self, old: str, new: str) -> int:
        """Rename a tag across all records (user authority, no validation).

        Renaming onto an existing tag merges the two, so the result is deduplicated —
        a record carrying both would otherwise end up with the new tag twice."""
        n = 0
        for m in self.list():
            tags = m.get("tags") or []
            if old in tags:
                renamed = [new if t == old else t for t in tags]
                self.update(namespace=m["namespace"], name=m["name"], agent="user",
                            tags=list(dict.fromkeys(renamed)), _trusted=True)
                n += 1
        return n

    def delete_tag(self, tag: str) -> int:
        """Remove a tag from all records (user authority)."""
        n = 0
        for m in self.list():
            tags = m.get("tags") or []
            if tag in tags:
                self.update(namespace=m["namespace"], name=m["name"], agent="user",
                            tags=[t for t in tags if t != tag], _trusted=True)
                n += 1
        return n

    # ---------- CRUD ----------
    def write(self, *, content: str, description: str, agent: str,
              namespace: str | None = None, name: str | None = None,
              type: str = "note", tags: list[str] | None = None,
              new_tags: list[str] | None = None, _trusted: bool = False,
              _action: str = "write", _author: str | None = None,
              _created: str | None = None, _share: list[str] | None = None) -> dict:
        """`_author`/`_created`/`_share` carry provenance and the user's sharing
        setting over when a record is recreated elsewhere (a move) — without them the
        mover would become the author, the creation date would reset to now, and a
        restricted record would silently become visible to everyone."""
        if not _trusted:
            self._check_tags(tags, new_tags)
        with self._lock:
            namespace = _safe(namespace or agent or "shared")
            name = _safe(name or slugify(description)[:60])
            now = _now()
            existing = self._get_payload(namespace, name)
            if existing and not self.can_record(None if _trusted else agent, existing):
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            created = existing["created"] if existing else (_created or now)
            author = existing["author"] if existing else (_author or agent)
            # the payload is rebuilt from scratch here, so anything the caller does not
            # pass has to be carried over explicitly — `share` is the user's setting and
            # an agent overwriting the record must not silently clear it
            share = existing.get("share") if existing else _share
            # `verified` is deliberately NOT carried over from `existing`: this call
            # replaces the content wholesale, and a confirmation that applied to the
            # old text says nothing about the new. Re-verifying is cheap; trusting a
            # stamp that outlived what it vouched for is not.
            payload = {"name": name, "description": description, "content": content,
                       "author": author, "created": created, "updated": now,
                       "updated_by": agent, "type": type, "tags": tags or [],
                       "share": share, "verified": None, "verified_by": None}
            nonce, ct = self.cipher.encrypt(payload)
            self.db.execute(
                "INSERT INTO memories (namespace, name_idx, nonce, ct) VALUES (?,?,?,?)"
                " ON CONFLICT(namespace, name_idx) DO UPDATE SET nonce=excluded.nonce, ct=excluded.ct",
                (namespace, self._idx(namespace, name), nonce, ct))
            self._log(_action, agent, namespace, name, payload)
            self.db.commit()
            return self._summary(namespace, payload)

    def update(self, *, namespace: str, name: str, agent: str,
               content: str | None = None, description: str | None = None,
               type: str | None = None, tags: list[str] | None = None,
               new_tags: list[str] | None = None, _trusted: bool = False,
               share: list[str] | None = None, _set_share: bool = False) -> dict:
        """`share` is only honoured together with `_trusted` and `_set_share` — it is
        the user's control, set from the admin web. An agent must never be able to
        widen (or narrow) who can see a record; otherwise a model could simply grant
        itself access to something the user restricted."""
        if not _trusted:
            self._check_tags(tags, new_tags)
        if _set_share and not _trusted:
            raise PermissionError("share can only be set by the user, not by an agent")
        with self._lock:
            p = self._get_payload(namespace, name)
            if p is None:
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            if not self.can_record(None if _trusted else agent, p):
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            if content is not None:
                p["content"] = content
                # the body changed, so an earlier "I checked this" no longer covers what
                # the record now says. Re-tagging or renaming a type does not invalidate
                # anything, which is why only content clears it.
                p["verified"] = p["verified_by"] = None
            if description is not None: p["description"] = description
            if type is not None: p["type"] = type
            if tags is not None: p["tags"] = tags
            if _set_share: p["share"] = share
            p["updated"] = _now()
            p["updated_by"] = agent
            nonce, ct = self.cipher.encrypt(p)
            self.db.execute("UPDATE memories SET nonce=?, ct=? WHERE namespace=? AND name_idx=?",
                            (nonce, ct, _safe(namespace), self._idx(namespace, name)))
            self._log("update", agent, namespace, name, p)
            self.db.commit()
            return self._summary(_safe(namespace), p)

    def mark_verified(self, namespace: str, name: str, *, agent: str,
                      _trusted: bool = False) -> dict:
        """Record that someone checked this record against reality, just now.

        This is a different fact from `updated`, and conflating the two is what made
        the memory quietly untrustworthy: `updated` says when the text last changed,
        which for a record nobody has touched in a year is the day it was written —
        it cannot distinguish "still true, confirmed yesterday" from "written once
        and never looked at again". Only the second kind needs re-checking.

        It touches no other field and writes no history entry: nothing about the
        record's content changed, and burying real edits under a stream of "still
        fine" entries would make the history harder to read for no gain.
        """
        with self._lock:
            p = self._get_payload(namespace, name)
            if p is None or not self.can_record(None if _trusted else agent, p):
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            p["verified"] = _now()
            p["verified_by"] = agent
            nonce, ct = self.cipher.encrypt(p)
            self.db.execute("UPDATE memories SET nonce=?, ct=? WHERE namespace=? AND name_idx=?",
                            (nonce, ct, _safe(namespace), self._idx(namespace, name)))
            self.db.commit()
            return self._summary(_safe(namespace), p)

    def read(self, namespace: str, name: str, *, agent: str | None = None) -> dict:
        with self._lock:
            p = self._get_payload(namespace, name)
            if p is None:
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            if not self.can_record(agent, p):
                # deliberately the same error as a missing record: a restricted record
                # must not be discoverable by probing for names
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            self._note_read(namespace, name, agent)
            return {**self._summary(_safe(namespace), p),
                    "created": p.get("created", ""), "content": p["content"]}

    def _note_read(self, namespace: str, name: str, agent: str | None) -> None:
        """Count a full read of the record. Caller holds the lock.

        Only agents are counted. The question this data answers is "does any model
        still use this record", and a user paging through the admin web would make
        every record look alive — which is exactly the signal being looked for.
        Listing and searching do not count either: seeing a summary in a result is
        not the same as needing the record.
        """
        if agent is None:
            return
        self.db.execute(
            "INSERT INTO reads (namespace, name_idx, n, last_ts, last_agent)"
            " VALUES (?,?,1,?,?)"
            " ON CONFLICT(namespace, name_idx) DO UPDATE SET"
            " n = n + 1, last_ts = excluded.last_ts, last_agent = excluded.last_agent",
            (_safe(namespace), self._idx(namespace, name), _now(), agent))
        self.db.commit()

    # ---------- attachments ----------
    def _owner_or_raise(self, namespace: str, name: str, agent: str | None,
                        _trusted: bool = False) -> dict:
        """The record an attachment hangs off, or the same 'does not exist' error the
        rest of the store gives. Attachments inherit the record's visibility — there is
        no separate ACL, because a file that outlived its record's restriction would be
        a hole in the per-record permissions."""
        p = self._get_payload(namespace, name)
        if p is None or not self.can_record(None if _trusted else agent, p):
            raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
        return p

    def attach(self, namespace: str, name: str, *, data: bytes, filename: str,
               agent: str, mime: str = "application/octet-stream",
               description: str = "", _trusted: bool = False) -> dict:
        """Attach a file to a record. Returns its metadata (never the bytes)."""
        import hashlib
        import secrets as _secrets
        with self._lock:
            self._owner_or_raise(namespace, name, agent, _trusted)
            meta = {"id": _secrets.token_hex(8), "filename": filename, "mime": mime,
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "description": description, "author": agent, "created": _now()}
            mn, mc = self.cipher.encrypt(meta)
            dn, dc = self.cipher.encrypt_bytes(data)
            self.db.execute(
                "INSERT INTO attachments (id, namespace, name_idx, meta_nonce, meta_ct,"
                " data_nonce, data_ct) VALUES (?,?,?,?,?,?,?)",
                (meta["id"], _safe(namespace), self._idx(namespace, name), mn, mc, dn, dc))
            # logged without the bytes: history is for "who changed what", and copying
            # every version of a 5 MB photo into it would bloat the store for nothing
            self._log("attach", agent, namespace, name,
                      {"name": _safe(name), "attachment": meta})
            self.db.commit()
            return meta

    def attachments(self, namespace: str, name: str, *,
                    agent: str | None = None) -> list[dict]:
        """Metadata of everything attached to a record, newest last. No bytes."""
        with self._lock:
            self._owner_or_raise(namespace, name, agent)
            rows = self.db.execute(
                "SELECT meta_nonce, meta_ct FROM attachments"
                " WHERE namespace=? AND name_idx=?",
                (_safe(namespace), self._idx(namespace, name))).fetchall()
        metas = [self.cipher.decrypt(n, c) for n, c in rows]
        return sorted(metas, key=lambda m: m.get("created", ""))

    def attachment(self, namespace: str, name: str, att_id: str, *,
                   agent: str | None = None) -> tuple[dict, bytes]:
        """One attachment's metadata and its bytes."""
        with self._lock:
            self._owner_or_raise(namespace, name, agent)
            row = self.db.execute(
                "SELECT meta_nonce, meta_ct, data_nonce, data_ct FROM attachments"
                " WHERE id=? AND namespace=? AND name_idx=?",
                (att_id, _safe(namespace), self._idx(namespace, name))).fetchone()
        if not row:
            raise FileNotFoundError(f"no attachment {att_id} on {namespace}/{name}")
        return self.cipher.decrypt(row[0], row[1]), self.cipher.decrypt_bytes(row[2], row[3])

    def detach(self, namespace: str, name: str, att_id: str, *, agent: str,
               _trusted: bool = False) -> dict:
        with self._lock:
            self._owner_or_raise(namespace, name, agent, _trusted)
            row = self.db.execute(
                "SELECT meta_nonce, meta_ct FROM attachments"
                " WHERE id=? AND namespace=? AND name_idx=?",
                (att_id, _safe(namespace), self._idx(namespace, name))).fetchone()
            if not row:
                raise FileNotFoundError(f"no attachment {att_id} on {namespace}/{name}")
            meta = self.cipher.decrypt(row[0], row[1])
            self.db.execute("DELETE FROM attachments WHERE id=?", (att_id,))
            self._log("detach", agent, namespace, name,
                      {"name": _safe(name), "attachment": meta})
            self.db.commit()
            return meta

    def move_attachments(self, namespace: str, name: str,
                         to_namespace: str, to_name: str) -> int:
        """Re-point a record's attachments at its new location.

        Called by a move BEFORE the source record is deleted, because deleting a record
        drops its attachments. Without this, promoting a record to `shared` would throw
        its files away silently — the record would arrive intact and the diagram that
        explained it would not.
        """
        with self._lock:
            cur = self.db.execute(
                "UPDATE attachments SET namespace=?, name_idx=?"
                " WHERE namespace=? AND name_idx=?",
                (_safe(to_namespace), self._idx(to_namespace, to_name),
                 _safe(namespace), self._idx(namespace, name)))
            self.db.commit()
            return cur.rowcount

    def attachment_totals(self) -> dict:
        """How much space attachments take. The store is copied whole by every backup,
        so this is the number that decides whether that is still cheap."""
        row = self.db.execute(
            "SELECT count(*), coalesce(sum(length(data_ct)), 0) FROM attachments").fetchone()
        return {"count": row[0], "bytes": row[1]}

    def read_stats(self) -> dict[tuple[str, str], dict]:
        """Read counters keyed by (namespace, name_idx) — join on the blind index."""
        rows = self.db.execute(
            "SELECT namespace, name_idx, n, last_ts, last_agent FROM reads").fetchall()
        return {(r[0], r[1]): {"reads": r[2], "last_read": r[3], "last_read_by": r[4]}
                for r in rows}

    def reads_since(self) -> str:
        """When read counting started. Anything before this has no history, not zero use."""
        v = self._meta("reads_since")
        return bytes(v).decode() if v else ""

    def delete(self, namespace: str, name: str, *, agent: str,
               _trusted: bool = False) -> dict:
        with self._lock:
            p = self._get_payload(namespace, name)
            if p is not None and not self.can_record(None if _trusted else agent, p):
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            idx = self._idx(namespace, name)
            cur = self.db.execute("DELETE FROM memories WHERE namespace=? AND name_idx=?",
                                  (_safe(namespace), idx))
            if cur.rowcount == 0:
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            # inline rather than via drop_vector(): we already hold the lock, and it is
            # not reentrant. An orphan vector would keep describing a deleted record.
            self.db.execute("DELETE FROM vectors WHERE namespace=? AND name_idx=?",
                            (_safe(namespace), idx))
            # same reasoning for attachments: a file whose record is gone can never be
            # reached again (they are addressed through the record), so leaving it would
            # be dead weight nobody can see — and it would still be in every backup.
            # A MOVE must therefore call move_attachments() BEFORE deleting the source.
            self.db.execute("DELETE FROM attachments WHERE namespace=? AND name_idx=?",
                            (_safe(namespace), idx))
            self._log("delete", agent, namespace, name, None)
            self.db.commit()
            return {"deleted": f"{_safe(namespace)}/{_safe(name)}"}

    def list(self, namespace: str | None = None, *, type: str | None = None,
             tags: list[str] | None = None, agent: str | None = None) -> list[dict]:
        """Record summaries. `tags` = must contain ALL of the given tags (AND)."""
        with self._lock:
            if namespace:
                rows = self.db.execute("SELECT namespace, nonce, ct FROM memories WHERE namespace=?",
                                       (_safe(namespace),)).fetchall()
            else:
                rows = self.db.execute("SELECT namespace, nonce, ct FROM memories").fetchall()
            payloads = [(r[0], self.cipher.decrypt(r[1], r[2])) for r in rows]
            out = [self._summary(ns, p) for ns, p in payloads if self.can_record(agent, p)]
        if type:
            out = [m for m in out if m["type"] == type]
        if tags:
            want = set(tags)
            out = [m for m in out if want <= set(m.get("tags") or [])]
        out.sort(key=lambda m: m["updated"], reverse=True)
        return out

    # ---------- vectors ----------
    def set_vector(self, namespace: str, name: str, vector, fingerprint: str,
                   model: str | None = None) -> None:
        """Store one record's embedding, encrypted.

        `fingerprint` is a hash of the exact text that was embedded, and it is what
        marks a vector stale later. It replaced the record's `updated` timestamp, which
        looked equivalent and was not: timestamps here have SECOND resolution, so an
        edit landing in the same second as the indexing pass left the vector looking
        current while describing text that no longer existed. A hash has no such blind
        spot, and it also avoids re-embedding a record whose edit did not touch the
        embedded text.

        NOTE: this takes the lock, so it must NOT be called from inside another method
        that already holds it (`self._lock` is a plain Lock, not reentrant). Indexing
        is therefore a separate pass over the store, not a side effect of write().
        """
        # `model` is stored alongside so a search can REFUSE vectors from a different
        # model rather than ranking them. Two models produce 384 numbers each and no
        # shared meaning between them; comparing across would return confident nonsense,
        # which is worse than returning nothing. A vector written before 1.1.0 has no
        # model recorded and is treated as foreign, so it is re-indexed rather than used.
        payload = {"v": [float(x) for x in vector], "fp": fingerprint, "m": model}
        nonce, ct = self.cipher.encrypt(payload)
        with self._lock:
            self.db.execute(
                "INSERT INTO vectors (namespace, name_idx, nonce, ct) VALUES (?,?,?,?)"
                " ON CONFLICT(namespace, name_idx) DO UPDATE SET"
                " nonce=excluded.nonce, ct=excluded.ct",
                (_safe(namespace), self._idx(namespace, name), nonce, ct))
            self.db.commit()

    def drop_vector(self, namespace: str, name: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM vectors WHERE namespace=? AND name_idx=?",
                            (_safe(namespace), self._idx(namespace, name)))
            self.db.commit()

    def vectors(self, *, agent: str | None = None) -> dict[tuple[str, str], dict]:
        """Every stored vector the agent may see, keyed by (namespace, name).

        Vectors are matched back to records through the blind index, so a vector whose
        record is gone — or is hidden from this agent by `share` — is skipped rather
        than returned. That matters: a vector on its own still leaks roughly what its
        record was about, so per-record permissions have to reach here too.

        Each value is {"v": [...], "fp": ..., "m": ...}; comparing `fp` against the
        current text is how the caller decides whether a vector is stale, and `m` names
        the model that produced it so vectors from different models are never compared
        (see semantic.py — this layer deliberately knows nothing about either).
        """
        with self._lock:
            mem = [(ns, self.cipher.decrypt(n, c)) for ns, n, c in self.db.execute(
                "SELECT namespace, nonce, ct FROM memories").fetchall()]
            visible = {(ns, self._idx(ns, p["name"])): p
                       for ns, p in mem if self.can_record(agent, p)}
            rows = self.db.execute("SELECT namespace, name_idx, nonce, ct FROM vectors").fetchall()
        out = {}
        for ns, idx, nonce, ct in rows:
            record = visible.get((ns, idx))
            if record is None:
                continue
            payload = self.cipher.decrypt(nonce, ct)
            out[(ns, record["name"])] = {"v": payload["v"], "fp": payload.get("fp", ""),
                                         "m": payload.get("m")}
        return out

    def vector_coverage(self) -> tuple[int, int]:
        """(records with a vector, records total) — how much of the store is indexed."""
        with self._lock:
            n_vec = self.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            n_mem = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return n_vec, n_mem

    def dump(self, *, agent: str | None = None) -> list[dict]:
        """Every readable record WITH its content. `list()` deliberately omits content
        to keep listings cheap; the hygiene checks need the body to find links and
        markers, so this is the one place that pays for decrypting everything."""
        with self._lock:
            rows = self.db.execute("SELECT namespace, nonce, ct FROM memories").fetchall()
            payloads = [(ns, self.cipher.decrypt(nonce, ct)) for ns, nonce, ct in rows]
            stats = self.read_stats()
        return [{**self._summary(ns, p), "created": p.get("created", ""),
                 "content": p.get("content", ""),
                 **stats.get((ns, self._idx(ns, p["name"])),
                             {"reads": 0, "last_read": None, "last_read_by": None})}
                for ns, p in payloads if self.can_record(agent, p)]

    def search(self, query: str, *, namespaces: list[str] | None = None,
               limit: int = 10, type: str | None = None,
               tags: list[str] | None = None, agent: str | None = None) -> list[dict]:
        terms = [t for t in SLUG_RE.split(query.lower()) if t]
        if not terms:
            return []
        want_tags = set(tags or [])
        with self._lock:
            rows = self.db.execute("SELECT namespace, nonce, ct FROM memories").fetchall()
        results = []
        for ns, nonce, ct in rows:
            if namespaces and ns not in namespaces:
                continue
            p = self.cipher.decrypt(nonce, ct)
            if not self.can_record(agent, p):
                continue
            if type and p.get("type", "note") != type:
                continue
            if want_tags and not want_tags <= set(p.get("tags") or []):
                continue
            title = f"{p['name']} {p.get('description','')} {' '.join(p.get('tags',[]))}".lower()
            body = p.get("content", "").lower()
            score = sum(3 * title.count(t) + body.count(t) for t in terms)
            if score > 0:
                r = self._summary(ns, p)
                r["score"] = score
                results.append(r)
        results.sort(key=lambda m: m["score"], reverse=True)
        return results[:limit]

    # ---------- history ----------
    def history(self, namespace: str, name: str, *, agent: str | None = None) -> list[dict]:
        idx = self._idx(namespace, name)
        with self._lock:
            # the current record governs: history holds older payloads, and an agent
            # that may not see the record now must not reach its past versions either
            current = self._get_payload(namespace, name)
            if current is not None and not self.can_record(agent, current):
                raise FileNotFoundError(f"memory does not exist: {namespace}/{name}")
            rows = self.db.execute(
                "SELECT ts, action, agent, nonce, ct FROM history"
                " WHERE namespace=? AND name_idx=? ORDER BY id",
                (_safe(namespace), idx)).fetchall()
        out = []
        for ts, action, by, nonce, ct in rows:
            p = self.cipher.decrypt(nonce, ct)
            out.append({"ts": ts, "action": action, "agent": by,
                        "description": p.get("description", ""), "type": p.get("type", "note"),
                        "tags": p.get("tags", []), "content": p.get("content")})
        return out

    def audit(self, *, limit: int = 50, since: str | None = None,
              agent: str | None = None, namespace: str | None = None,
              viewer: str | None = None) -> list[dict]:
        """Across all records: who did what, when and where. Newest first.

        `history()` only covers one specific record — this is a view over the whole
        store (because of the blind index the name is taken from the decrypted
        payload). `since` is an ISO date/time compared as a string (ISO sorts
        lexicographically). Content is not returned; use history()/read() for that.

        `agent` FILTERS by who acted; `viewer` is who is asking, and hides rows for
        records that viewer may not see (otherwise the audit would leak the names and
        descriptions of restricted records).
        """
        sql = "SELECT ts, namespace, action, agent, nonce, ct FROM history WHERE 1=1"
        params: list = []
        if since:
            sql += " AND ts >= ?"; params.append(since)
        if agent:
            sql += " AND agent = ?"; params.append(agent)
        if namespace:
            sql += " AND namespace = ?"; params.append(_safe(namespace))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._lock:
            rows = self.db.execute(sql, params).fetchall()
            hidden = set()
            if viewer is not None:
                # a record that still exists and is restricted must not show up here;
                # deleted records have no `share` left to consult, so they stay visible
                # (their namespace ACL is enforced by the caller)
                for ns_, nonce_, ct_ in self.db.execute(
                        "SELECT namespace, nonce, ct FROM memories").fetchall():
                    p_ = self.cipher.decrypt(nonce_, ct_)
                    if not self.can_record(viewer, p_):
                        hidden.add((ns_, p_.get("name")))
        out = []
        for ts, ns, action, ag, nonce, ct in rows:
            try:
                p = self.cipher.decrypt(nonce, ct)
            except Exception:
                continue
            if (ns, p.get("name")) in hidden:
                continue
            out.append({"ts": ts, "action": action, "agent": ag, "namespace": ns,
                        "name": p.get("name", "?"), "description": p.get("description", "")})
        return out

    def revert(self, namespace: str, name: str, version: int, *, agent: str) -> dict:
        versions = [h for h in self.history(namespace, name, agent=agent)
                    if h.get("content") is not None]
        if not versions:
            raise FileNotFoundError(f"no version with content: {namespace}/{name}")
        try:
            t = versions[version]
        except IndexError:
            raise IndexError(f"version {version} does not exist (there are {len(versions)})")
        return self.write(content=t["content"], description=t.get("description", ""),
                          agent=agent, namespace=namespace, name=name,
                          type=t.get("type", "note"), tags=t.get("tags"), _action="revert")

    # ---------- auth (agents / keys / permissions) ----------
    def _agent_rec(self, name: str):
        row = self.db.execute("SELECT nonce, ct FROM agents WHERE name=?", (name,)).fetchone()
        return self.cipher.decrypt(row[0], row[1]) if row else None

    def change_token(self) -> str:
        """A cheap fingerprint of "has anything been written since last time".

        Not a checksum of the data -- just enough to tell one state from another, so the
        scheduled export can back up BECAUSE something changed instead of on a clock
        alone. The history table gets a row for every write, update, delete, attach and
        detach, so its highest id moves whenever content does; the record count catches
        the one case it would not, a delete that history has since been trimmed around.

        Deliberately NOT including `verified` stamps or vectors: re-confirming a record
        or re-embedding it changes no claim, and a backup per embedding run would be
        noise that pushes real versions out of the retention window.
        """
        row = self.db.execute(
            "SELECT (SELECT coalesce(max(id), 0) FROM history),"
            "       (SELECT count(*) FROM memories)").fetchone()
        return f"h{row[0]}:m{row[1]}"

    def get_settings(self, key: str, default: dict | None = None) -> dict:
        with self._lock:
            row = self.db.execute("SELECT nonce, ct FROM settings WHERE k=?",
                                  (key,)).fetchone()
        if row is None:
            return dict(default or {})
        return self.cipher.decrypt(row[0], row[1])

    def set_settings(self, key: str, value: dict) -> None:
        nonce, ct = self.cipher.encrypt(value)
        with self._lock:
            self.db.execute(
                "INSERT INTO settings (k, nonce, ct) VALUES (?,?,?)"
                " ON CONFLICT(k) DO UPDATE SET nonce=excluded.nonce, ct=excluded.ct",
                (key, nonce, ct))
            self.db.commit()

    def add_agent(self, name: str, permissions: dict | None = None) -> str:
        import secrets
        with self._lock:
            if self.db.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
                raise ValueError(f"agent '{name}' already exists")
            key = KEY_PREFIX + secrets.token_hex(24)
            perms = permissions or {name: "rw", "shared": "rw", "*": "r"}
            nonce, ct = self.cipher.encrypt({"permissions": perms, "created": _now()})
            self.db.execute("INSERT INTO agents (name, key_idx, nonce, ct) VALUES (?,?,?,?)",
                            (name, self.cipher.blind_index("key:" + key), nonce, ct))
            self.db.commit()
            return key

    def rotate_key(self, name: str) -> str:
        import secrets
        with self._lock:
            if not self.db.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
                raise KeyError(name)
            key = KEY_PREFIX + secrets.token_hex(24)
            self.db.execute("UPDATE agents SET key_idx=? WHERE name=?",
                            (self.cipher.blind_index("key:" + key), name))
            self.db.commit()
            return key

    def remove_agent(self, name: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM agents WHERE name=?", (name,))
            self.db.commit()

    def verify_key(self, key: str) -> str | None:
        if not key:
            return None
        idx = self.cipher.blind_index("key:" + key)
        with self._lock:
            row = self.db.execute("SELECT name FROM agents WHERE key_idx=?", (idx,)).fetchone()
        return row[0] if row else None

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT name, nonce, ct FROM agents").fetchall()
        out = []
        for name, nonce, ct in rows:
            rec = self.cipher.decrypt(nonce, ct)
            out.append({"name": name, "permissions": rec.get("permissions", {}),
                        "created": rec.get("created", "")})
        return out

    def set_permission(self, name: str, namespace: str, perm: str) -> None:
        if perm not in PERMS:
            raise ValueError(f"perm must be one of {PERMS}")
        with self._lock:
            rec = self._agent_rec(name)
            if rec is None:
                raise KeyError(name)
            rec.setdefault("permissions", {})[namespace] = perm
            nonce, ct = self.cipher.encrypt(rec)
            self.db.execute("UPDATE agents SET nonce=?, ct=? WHERE name=?", (nonce, ct, name))
            self.db.commit()

    def can(self, agent: str | None, namespace: str, need: str) -> bool:
        if agent is None:
            return False
        with self._lock:
            rec = self._agent_rec(agent)
        if not rec:
            return False
        perms = rec.get("permissions", {})
        p = perms.get(namespace, perms.get("*", "none"))
        return (need == "r" and p in ("r", "rw")) or (need == "w" and p == "rw")
