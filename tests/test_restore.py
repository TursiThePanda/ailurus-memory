"""Import a backup (restore) -- the core steps and the admin-web flow.

What matters: a refused import leaves the live memory exactly as it was; a done one
replaces the content but NOT the passphrase and NOT this machine's network/export
settings; the memory as it was is saved first and can itself be imported back; nothing
secret is kept in the session between the two steps.
"""
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

PW = "restore-test-pass"
PW_OTHER = "the-other-machine-pass"
checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def csrf_of(html_text):
    m = re.search(r'name=csrf value="([0-9a-f]+)"', html_text)
    assert m, "CSRF token not found"
    return m.group(1)


def readable(store, ns, name):
    try:
        store.read(ns, name)
        return True
    except Exception:
        return False


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        os.environ["AI_MEMORY_DATA_DIR"] = str(tmp / "data")
        from ai_memory.core.store import SecureStore
        from ai_memory.features import backup, restore

        # --- this machine's memory
        (tmp / "data").mkdir()
        live = SecureStore(str(tmp / "data" / "memory.db"), PW)
        live.write(content="Only here.", description="live record", agent="claude",
                   namespace="shared", name="live-one", tags=["network"], new_tags=["network"])
        live.write(content="Also here.", description="live record 2", agent="claude",
                   namespace="shared", name="live-two", tags=["network"])
        alpha_key = live.add_agent("alpha", {"shared": "rw"})
        live.set_settings("network", {"bind": "0.0.0.0", "marker": "this machine"})
        live.set_settings("export", {"folder": "/here", "enabled": True})

        # --- a memory from another machine, with another passphrase
        other_db = tmp / "other.db"
        other = SecureStore(str(other_db), PW_OTHER)
        other.write(content="From elsewhere.", description="other record", agent="claude",
                    namespace="shared", name="other-one", tags=["hardware"],
                    new_tags=["hardware"])
        beta_key = other.add_agent("beta", {"shared": "r"})
        other.set_settings("network", {"bind": "127.0.0.1", "marker": "other machine"})
        other.set_settings("ai_instructions", {"general": "from the file"})
        other.close()
        export_file = tmp / "memory-export.db"
        backup.snapshot(str(other_db), export_file)

        def stage_copy(src):
            return restore.stage_from_file(src)[1]

        token_before = live.change_token()

        # --- refusals leave the live memory untouched
        staged = stage_copy(export_file)
        try:
            restore.prepare(staged, "wrong-pass", PW, live)
            check("a wrong file passphrase is refused", False)
        except restore.RestoreError as e:
            check("a wrong file passphrase is refused", "does not open" in str(e), str(e))
        restore.discard(staged)

        staged = stage_copy(export_file)
        try:
            restore.prepare(staged, PW_OTHER, "not-this-memory", live)
            check("a wrong current passphrase is refused", False)
        except restore.RestoreError as e:
            check("a wrong current passphrase is refused", "this memory" in str(e), str(e))
        restore.discard(staged)

        junk = tmp / "junk.db"
        junk.write_bytes(b"this is not a database" * 100)
        staged = stage_copy(junk)
        try:
            restore.prepare(staged, PW_OTHER, PW, live)
            check("a file that is not a database is refused", False)
        except restore.RestoreError as e:
            check("a file that is not a database is refused", "not a usable" in str(e), str(e))
        restore.discard(staged)

        with mock.patch.object(restore, "MAX_BYTES", 10):
            try:
                restore.stage_from_file(export_file)
                check("an oversized file is refused", False)
            except restore.RestoreError:
                check("an oversized file is refused", True)

        check("after the refusals the live memory is unchanged",
              live.change_token() == token_before and readable(live, "shared", "live-one"))

        # --- a real restore from a file with ANOTHER passphrase
        staged = stage_copy(export_file)
        info, dek = restore.prepare(staged, PW_OTHER, PW, live)
        check("the summary says what is inside",
              info["records"] == 1 and info["agents"] == ["beta"]
              and info["namespaces"] == ["shared"], str(info))
        check("prepare does not touch the live memory",
              live.change_token() == token_before)
        result = restore.apply(live, staged, dek)
        check("the file's records are there, the old ones gone",
              readable(live, "shared", "other-one") and not readable(live, "shared", "live-one"))
        check("the result counts the records", result["records"] == 1, str(result))
        check("the staged copy is removed", not staged.exists())
        check("the passphrase did NOT change", live.verify_master(PW)
              and not live.verify_master(PW_OTHER))
        live.close()
        live = SecureStore(str(tmp / "data" / "memory.db"), PW)
        check("after a reopen it opens with the CURRENT passphrase and reads the file's data",
              readable(live, "shared", "other-one"))
        check("this machine's network settings stay",
              live.get_settings("network").get("marker") == "this machine")
        check("this machine's export settings stay",
              live.get_settings("export").get("folder") == "/here")
        check("the memory's own settings come from the file",
              live.get_settings("ai_instructions").get("general") == "from the file")
        check("agents and keys come from the file",
              live.verify_key(beta_key) == "beta" and live.verify_key(alpha_key) is None)

        snap = Path(result["snapshot"])
        ok, detail, n = backup.verify(snap)
        check("the previous memory was saved and verifies", ok and n == 2, detail)

        # --- the snapshot imports back (same passphrase: file passphrase left empty)
        staged = stage_copy(snap)
        info, dek = restore.prepare(staged, "", PW, live)
        restore.apply(live, staged, dek)
        check("a second restore keeps the first safety copy",
              backup.verify(snap)[2] == 2
              and len(list((tmp / "data" / "backups").glob("before-restore-*.db"))) == 2)
        check("the snapshot restores the memory as it was",
              readable(live, "shared", "live-one") and not readable(live, "shared", "other-one")
              and live.verify_key(alpha_key) == "alpha")

        # --- the in-memory hand-over between the two web steps
        restore.hold("a" * 32, b"k" * 32)
        check("a held key is taken once", restore.take("a" * 32) == b"k" * 32
              and restore.take("a" * 32) is None)
        check("stage_path refuses anything but a token",
              restore.stage_path("../../etc/passwd") is None and restore.stage_path("") is None)

        # --- the admin web: modal, check, confirm
        from starlette.testclient import TestClient
        from ai_memory.web import webapp
        webapp.STORE = live
        c = TestClient(webapp.app)
        token = csrf_of(c.get("/login").text)
        c.post("/login", data={"password": PW, "csrf": token})
        page = c.get("/settings")
        check("Settings offers the import", page.status_code == 200
              and "Import a backup" in page.text and "restore-upload" in page.text)

        def dialog_ok(html, dialog_id, must_contain):
            # cards() splits on <h3>; a dialog caught in it ends up empty, its form in a
            # card of its own. The dialog must exist once and hold its whole form.
            if html.count(f"id={dialog_id}") != 1:
                return False
            start = html.index(f"<dialog id={dialog_id}>")
            inner = html[start:html.index("</dialog>", start)]
            return "<section" not in inner and all(m in inner for m in must_contain)

        check("the upload dialog is whole and appears once",
              dialog_ok(page.text, "restore-upload",
                        ["type=file", "name=file_passphrase", "name=passphrase", "</form>"]))

        token_before = live.change_token()
        r = c.post("/settings/restore",
                   data={"csrf": csrf_of(page.text), "file_passphrase": "nope",
                         "passphrase": PW},
                   files={"file": ("memory-export.db", export_file.read_bytes())})
        check("a wrong passphrase reopens the dialog with the error",
              r.status_code == 200 and "does not open" in r.text
              and "showModal()" in r.text, r.text[-400:])
        check("...the error is INSIDE the one upload dialog",
              dialog_ok(r.text, "restore-upload", ["does not open", "type=file"]))
        check("...and changes nothing", live.change_token() == token_before)
        check("...and leaves no staged file", not list(restore.staging_dir().glob("*.db")))

        r = c.post("/settings/restore",
                   data={"csrf": csrf_of(page.text), "file_passphrase": PW_OTHER,
                         "passphrase": PW},
                   files={"file": ("memory-export.db", export_file.read_bytes())})
        check("a good file shows the confirmation",
              r.status_code == 200 and "Replace this memory" in r.text
              and "restore-confirm" in r.text and "beta" in r.text)
        check("the passphrases are not in the session cookie",
              all(PW not in v and PW_OTHER not in v for v in c.cookies.values()))
        check("the confirm dialog is whole and the upload one still appears once",
              dialog_ok(r.text, "restore-confirm", ["Replace this memory", "name=token"])
              and dialog_ok(r.text, "restore-upload", ["type=file"]))
        tok = re.search(r"name=token value='([0-9a-f]{32})'", r.text).group(1)
        check("still nothing changed before the second click",
              live.change_token() == token_before)

        r = c.post("/settings/restore/apply", data={"csrf": csrf_of(r.text), "token": "f" * 32},
                   follow_redirects=False)
        check("a wrong token is refused", "no longer pending" in r.text)
        check("...and its staged file is removed", not list(restore.staging_dir().glob("*.db")))
        # the wrong attempt consumed the pending import -- by design, start again
        r = c.post("/settings/restore",
                   data={"csrf": csrf_of(page.text), "file_passphrase": PW_OTHER,
                         "passphrase": PW},
                   files={"file": ("memory-export.db", export_file.read_bytes())})
        tok = re.search(r"name=token value='([0-9a-f]{32})'", r.text).group(1)
        r = c.post("/settings/restore/apply", data={"csrf": csrf_of(r.text), "token": tok},
                   follow_redirects=False)
        check("confirming imports and redirects", r.status_code == 303, str(r.status_code))
        check("the web import replaced the content",
              readable(live, "shared", "other-one") and not readable(live, "shared", "live-one"))
        check("the settings page reports it once",
              "before-restore-" in c.get("/settings").text
              and "before-restore-" not in c.get("/settings").text)
        check("the admin session survives the import", c.get("/settings").status_code == 200
              and "Import a backup" in c.get("/settings").text)

        r = c.post("/settings/restore/apply", data={"csrf": csrf_of(page.text), "token": tok},
                   follow_redirects=False)
        check("the same import cannot be applied twice", "no longer pending" in r.text)

        # cancel removes the staged file
        r = c.post("/settings/restore",
                   data={"csrf": csrf_of(page.text), "file_passphrase": PW_OTHER,
                         "passphrase": PW},
                   files={"file": ("memory-export.db", export_file.read_bytes())})
        c.post("/settings/restore/cancel", data={"csrf": csrf_of(r.text)})
        check("cancel removes the staged file", not list(restore.staging_dir().glob("*.db")))

        r = c.post("/settings/restore", data={"file_passphrase": "", "passphrase": PW},
                   files={"file": ("x.db", b"x")})
        check("no CSRF token, no import", "CSRF" in r.text)

        # --- the command line: refuses while the memory runs, imports when stopped
        import argparse
        from ai_memory import cli, manage
        with mock.patch.object(cli, "port_up", lambda port: True), \
             mock.patch.object(sys, "argv", ["ai-memory-manage", "restore", str(snap)]):
            try:
                manage.main()
                check("the CLI refuses while the memory runs", False)
            except SystemExit as e:
                check("the CLI refuses while the memory runs", "stop it first" in str(e), str(e))
        with mock.patch("getpass.getpass", lambda prompt="": ""):
            manage._restore(live, argparse.Namespace(file=str(snap), yes=True), PW)
        check("the CLI imports a file", readable(live, "shared", "live-one")
              and not readable(live, "shared", "other-one"))
        live.close()

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
