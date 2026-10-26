"""Test of the admin web through Starlette TestClient (in-process)."""

import os as _os
import sys as _sys

# The project modules live one directory up; tests moved into tests/ in 2026-08. Without
# this, running a test file directly puts tests/ on sys.path instead of the project.
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import re
import tempfile
import time

from ai_memory.web import webapp
from starlette.testclient import TestClient
from ai_memory.core.store import SecureStore

PW = "web-test-passphrase"


def csrf_of(html_text):
    m = re.search(r'name=csrf value="([0-9a-f]+)"', html_text)
    assert m, "CSRF token not found"
    return m.group(1)


# ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
# test left open makes this cleanup raise WinError 32 and fails an otherwise green
# test. POSIX deletes it regardless, which is why it never showed up until the
# project first ran on Windows. SecureStore.close() is the real fix where a test
# owns the store; this keeps the temp dir from failing the run when it does not.
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    db = os.path.join(tmp, "memory.db")
    s = SecureStore(db, PW)
    s.write(content="secret content", description="seed record", agent="claude",
            namespace="shared", name="seed", tags=["network"], new_tags=["network"])
    # a second record with a different type and tag, so the filters have something to
    # separate — with one record every filter looks like it works
    s.write(content="other content", description="note record", agent="claude",
            namespace="shared", name="other", type="reference",
            tags=["hardware"], new_tags=["hardware"])
    webapp.STORE = s  # wire the store into the app

    c = TestClient(webapp.app)

    # not signed in -> redirect to /login
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", r.status_code
    print("auth guard OK (/ -> /login)")

    # login page + csrf
    r = c.get("/login")
    assert r.status_code == 200 and "Sign in" in r.text
    token = csrf_of(r.text)

    # wrong password
    r = c.post("/login", data={"password": "wrong", "csrf": token}, follow_redirects=False)
    assert "Wrong passphrase" in r.text, "the wrong password should have failed"
    print("wrong password rejected OK")

    # correct password -> 303 to /
    token = csrf_of(c.get("/login").text)
    r = c.post("/login", data={"password": PW, "csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/", r.status_code
    print("login with the correct password OK")

    # dashboard
    r = c.get("/")
    assert r.status_code == 200 and "Agents and keys" in r.text
    print("dashboard OK")

    # language: English by default, switch to Czech and back
    assert "Agents and keys" in c.get("/").text, "the default language should be English"
    r = c.post("/lang/cs", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    html_cs = c.get("/").text
    assert "Agenti a klíče" in html_cs and "Agents and keys" not in html_cs, "switching to cs failed"
    assert 'lang=cs' in html_cs, "the lang attribute on <html> did not change"
    c.post("/lang/xx")  # unknown code -> fall back to the default
    assert "Agents and keys" in c.get("/").text, "an unknown code should fall back to English"
    c.post("/lang/en")
    print("language switcher OK")

    # creating an agent -> shows the key once
    token = csrf_of(c.get("/").text)
    r = c.post("/agents/create", data={"name": "lmstudio-local", "csrf": token})
    assert r.status_code == 200 and "aim_" in r.text
    m = re.search(r"aim_[0-9a-f]{48}", r.text)
    assert m and s.verify_key(m.group(0)) == "lmstudio-local"
    print("agent creation + key OK:", m.group(0)[:14] + "...")

    # setting a permission
    token = csrf_of(c.get("/").text)
    r = c.post("/agents/perm", data={"name": "lmstudio-local", "namespace": "claude",
                                     "perm": "none", "csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and not s.can("lmstudio-local", "claude", "r")
    print("setting a permission OK")

    # permission matrix with checkboxes
    # (careful: httpx only form-encodes a dict; a list of pairs sends an empty body —
    #  repeated keys go in as a dict with a list of values)
    html = c.get("/").text
    assert "Save permissions" in html, "the permission matrix did not render"
    pairs = re.findall(r"name=pair value='([^']+)'", html)
    assert "lmstudio-local|shared" in pairs and "lmstudio-local|*" in pairs, pairs

    # only lmstudio-local -> shared = R+W is checked; the other cells stay empty = none
    r = c.post("/agents/perms", follow_redirects=False, data={
        "csrf": csrf_of(html), "pair": pairs,
        "perm": ["lmstudio-local|shared|r", "lmstudio-local|shared|w"]})
    assert r.status_code == 303, r.status_code
    assert s.can("lmstudio-local", "shared", "w"), "the write permission on shared was not saved"
    assert not s.can("lmstudio-local", "*", "r"), "unchecking the wildcard should disable the default"
    assert not s.can("lmstudio-local", "anything-else", "r"), "inheritance from * should be none"

    # write without read makes no sense -> saved as rw
    r = c.post("/agents/perms", follow_redirects=False, data={
        "csrf": csrf_of(c.get("/").text), "pair": pairs,
        "perm": ["lmstudio-local|shared|w"]})
    assert r.status_code == 303, r.status_code
    assert s.can("lmstudio-local", "shared", "r"), "write should imply read"
    print("permission matrix (checkboxes) OK")

    # memory browser + detail
    assert "seed" in c.get("/memories").text
    r = c.get("/memory", params={"ns": "shared", "name": "seed"})
    assert "secret content" in r.text and "History" in r.text
    print("memory browser + detail + history OK")

    # filtering by type and by tag
    all_html = c.get("/memories").text
    assert "seed" in all_html and "other" in all_html
    by_type = c.get("/memories", params={"type": "reference"}).text
    assert "other" in by_type and ">seed<" not in by_type, "the type filter did not narrow"
    by_tag = c.get("/memories", params={"tag": "network"}).text
    assert "seed" in by_tag and ">other<" not in by_tag, "the tag filter did not narrow"
    combined = c.get("/memories", params={"type": "reference", "tag": "network"}).text
    assert ">seed<" not in combined and ">other<" not in combined, \
        "type AND tag should intersect, not union"
    assert "?ns=&type=&tag=network" in all_html.replace("&amp;", "&") \
        or "tag=network" in all_html, "tags should be click-through links"
    print("filtering by type and tag OK")

    # full-text search over the table
    found = c.get("/memories", params={"q": "secret"}).text
    assert ">seed<" in found and ">other<" not in found, "search did not narrow"
    assert ">seed<" not in c.get("/memories", params={"q": "nothingmatches"}).text

    # expandable row: the detail row is rendered but starts hidden
    listing = c.get("/memories").text
    assert "class=expander" in listing and "class=detail" in listing
    assert "hidden" in listing, "the detail row should start collapsed"

    # editing a record through the UI: description, type, tags and content
    page = c.get("/memory", params={"ns": "shared", "name": "seed"}).text
    assert "<textarea" in page, "the content should be editable, not just displayed"
    r = c.post("/memory/save", follow_redirects=False, data={
        "csrf": csrf_of(page), "ns": "shared", "name": "seed",
        "description": "edited description", "type": "note",
        "tags": "network, hardware", "content": "edited content", "mode": "inherit"})
    assert r.status_code == 303, r.status_code
    m = s.read("shared", "seed")
    assert m["description"] == "edited description" and m["content"] == "edited content"
    assert m["tags"] == ["network", "hardware"], m["tags"]
    assert m["share"] is None, "inherit mode must leave share unset"
    print("editing a record through the UI OK")

    # per-record permissions: restrict, then lift
    page = c.get("/memory", params={"ns": "shared", "name": "seed"}).text
    assert "name=share" in page, "the agent checkboxes are missing"
    r = c.post("/memory/save", follow_redirects=False, data={
        "csrf": csrf_of(page), "ns": "shared", "name": "seed",
        "description": "edited description", "type": "note", "tags": "network",
        "content": "edited content", "mode": "restrict", "share": ["lmstudio-local"]})
    assert r.status_code == 303, r.status_code
    assert s.read("shared", "seed")["share"] == ["lmstudio-local"]
    assert s.list(agent="claude") == [] or all(
        x["name"] != "seed" for x in s.list(agent="claude")), "restriction not enforced"
    assert any(x["name"] == "seed" for x in s.list(agent="lmstudio-local"))
    assert "🔒" in c.get("/memories").text, "the table should mark a restricted record"

    r = c.post("/memory/save", follow_redirects=False, data={
        "csrf": csrf_of(c.get("/memory", params={"ns": "shared", "name": "seed"}).text),
        "ns": "shared", "name": "seed", "description": "edited description",
        "type": "note", "tags": "network", "content": "edited content",
        "mode": "inherit"})
    assert r.status_code == 303, r.status_code
    assert s.read("shared", "seed")["share"] is None, "the restriction was not lifted"

    # saving without a CSRF token must not go through
    r = c.post("/memory/save", data={
        "ns": "shared", "name": "seed", "description": "hacked",
        "type": "note", "tags": "", "content": "hacked", "mode": "inherit"})
    assert "CSRF" in r.text and s.read("shared", "seed")["description"] == "edited description"
    print("per-record permissions through the UI OK")

    # a duplicate key in the CS dict silently overwrites the earlier translation, and
    # the damage shows up on a completely unrelated page — catch it here
    import ast, collections
    for node in ast.walk(ast.parse(open(os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "ai_memory", "web", "i18n.py"), encoding="utf-8").read())):
        if isinstance(node, ast.Dict):
            keys = [ast.literal_eval(k) for k in node.keys]
            dupes = [k for k, n in collections.Counter(keys).items() if n > 1]
            assert not dupes, f"duplicate keys in the translation dict: {dupes}"
    print("translation dict has no duplicate keys OK")

    # tag administration through the UI
    html = c.get("/tags").text
    assert "network" in html and "hardware" in html
    r = c.post("/tags/rename", follow_redirects=False, data={
        "csrf": csrf_of(html), "old": "network", "new": "networking"})
    assert r.status_code == 303, r.status_code
    assert "networking" in s.known_tags() and "network" not in s.known_tags()
    r = c.post("/tags/delete", follow_redirects=False, data={
        "csrf": csrf_of(c.get("/tags").text), "tag": "hardware"})
    assert r.status_code == 303, r.status_code
    assert "hardware" not in s.known_tags(), "deleting the tag did not sweep the records"
    assert s.read("shared", "other") is not None, "deleting a tag must not delete records"
    r = c.post("/tags/rename", data={"old": "networking", "new": "x"})  # no CSRF
    assert "CSRF" in r.text and "networking" in s.known_tags()
    print("tag administration (rename/delete/CSRF) OK")

    # ---------- attachments ----------
    # The web upload path runs as agent="user" with _trusted=True — correct, since the
    # user is the authority on their own store, but worth pinning down.
    png = b"\x89PNG\r\n\x1a\n" + b"pixels" * 40
    token = csrf_of(c.get("/memory?ns=shared&name=seed").text)
    r = c.post("/memory/attachment/upload",
               data={"csrf": token, "ns": "shared", "name": "seed",
                     "description": "a screenshot"},
               files={"file": ("shot.png", png, "image/png")},
               follow_redirects=False)
    assert r.status_code == 303, r.status_code

    items = s.attachments("shared", "seed")
    assert len(items) == 1 and items[0]["filename"] == "shot.png", items
    assert items[0]["description"] == "a screenshot"
    assert items[0]["author"] == "user"

    page = c.get("/memory?ns=shared&name=seed").text
    assert "shot.png" in page, "the record page should list the attachment"
    assert "data:image/png;base64," in page, "an image should be previewed inline"

    att_id = items[0]["id"]
    r = c.get(f"/memory/attachment?ns=shared&name=seed&id={att_id}")
    assert r.content == png, "download must return the exact bytes"
    # arbitrary uploaded content must never render inline in the admin origin
    assert "attachment;" in r.headers.get("content-disposition", "")
    assert r.headers.get("x-content-type-options") == "nosniff"

    r = c.post("/memory/attachment/upload",
               data={"ns": "shared", "name": "seed"},
               files={"file": ("x.png", b"x", "image/png")}, follow_redirects=False)
    assert "CSRF" in r.text, "an upload without a CSRF token must be refused"

    token = csrf_of(c.get("/memory?ns=shared&name=seed").text)
    r = c.post("/memory/attachment/delete",
               data={"csrf": token, "ns": "shared", "name": "seed", "id": att_id},
               follow_redirects=False)
    assert r.status_code == 303 and s.attachments("shared", "seed") == []
    print("attachments through the web OK")

    # CSRF protection: POST without a token
    r = c.post("/agents/create", data={"name": "hacker"})
    assert "CSRF" in r.text and s.verify_key  # not created
    assert not any(a["name"] == "hacker" for a in s.list_agents())
    print("CSRF protection OK")

    # password change through the UI
    assert c.get("/passphrase").status_code == 200
    token = csrf_of(c.get("/passphrase").text)
    r = c.post("/passphrase", data={"current": "wrong", "new": "novehesloXY",
                                    "confirm": "novehesloXY", "csrf": token})
    assert "does not match" in r.text  # wrong current password
    token = csrf_of(c.get("/passphrase").text)
    r = c.post("/passphrase", data={"current": PW, "new": "novehesloXY",
                                    "confirm": "novehesloXY", "csrf": token})
    assert "changed" in r.text and s.verify_master("novehesloXY") and not s.verify_master(PW)
    print("password change through the UI OK")

    # Autostart switch. The handler once called a csrf_ok() that did not exist, so every
    # save was a 500 and no test noticed because none posted the form. The real
    # set_autostart is mocked: a test must not touch systemd or the Startup folder.
    from unittest import mock
    with mock.patch.object(webapp.cli, "autostart_status",
                           return_value={"service": False, "indicator": None}), \
         mock.patch.object(webapp.cli, "set_autostart") as set_as:
        page = c.get("/settings")
        assert page.status_code == 200 and "/settings/autostart" in page.text
        r = c.post("/settings/autostart", data={"csrf": csrf_of(page.text), "service": "1"},
                   follow_redirects=False)
        assert r.status_code == 303, r.status_code
        set_as.assert_called_once_with("service", True)
        r = c.post("/settings/autostart", data={"service": "1"}, follow_redirects=False)
        assert "CSRF" in r.text and set_as.call_count == 1, "no token, no change"
    print("autostart switch through the UI OK")

    # Installation block: version, mode and every path, with the overriding variable
    # named only when it is actually set.
    from ai_memory.core.version import __version__
    with mock.patch.object(webapp.cli, "autostart_status",
                           return_value={"service": False, "indicator": None}), \
         mock.patch.dict(os.environ, {"AI_MEMORY_DB": db}):
        page = c.get("/settings").text
        assert "Installation" in page and __version__ in page
        assert "running from a source checkout" in page, "tests run unfrozen, no marker"
        assert "set by AI_MEMORY_DB" in page, "an override must be named"
        assert webapp.html.escape(db) in page
        i18n_mod = __import__("ai_memory.web.i18n", fromlist=["i18n"])
        i18n_mod.set_lang("cs")
        try:
            assert "Instalace" in webapp._installation_section()
        finally:
            i18n_mod.set_lang("en")
    print("installation block OK")

    # Network + TLS settings through the UI. The config dir is redirected first: the
    # certificate must never land in the developer's real ~/.config.
    os.environ["AI_MEMORY_CONFIG_DIR"] = os.path.join(tmp, "config")
    with mock.patch.object(webapp.cli, "autostart_status",
                           return_value={"service": False, "indicator": None}):
        page = c.get("/settings").text
        assert "Your request came from" in page and "/settings/tls" in page
        tok = csrf_of(page)
        base = {"csrf": tok, "bind": "0.0.0.0", "port": "8443", "allowed_hosts": "memory.example.org",
                "mcp_allow": "", "trusted_proxies": "10.0.0.1"}
        r = c.post("/settings/network", data={**base, "admin_allow": ""})
        assert "at least one" in r.text, "an empty admin allow-list must be refused"
        r = c.post("/settings/network", data={**base, "admin_allow": "laptop.local"})
        assert "not an IP" in r.text, "host names are not accepted as client addresses"
        r = c.post("/settings/network", data={**base, "admin_allow": "192.168.7.0/24"},
                   follow_redirects=False)
        assert r.status_code == 303
        cfg = webapp.netconf.config(s)
        assert cfg["port"] == 8443 and cfg["admin_allow"] == ["192.168.7.0/24"] \
            and cfg["trusted_proxies"] == ["10.0.0.1/32"] and cfg["key_in_query"] is False
        r = c.post("/settings/tls/generate", data={"csrf": tok}, follow_redirects=False)
        assert r.status_code == 303
        from ai_memory.server import tls
        info = tls.info()
        assert info and info["self_signed"] and "memory.example.org" in info["names"], info
        if os.name == "posix":  # Windows has no mode bits; the per-user profile protects it
            assert oct(os.stat(tls.key_path()).st_mode & 0o777) == "0o600", "the key must be private"
        assert c.get("/settings/tls/cert").text.startswith("-----BEGIN CERTIFICATE")
        r = c.post("/settings/tls", data={"csrf": tok, "tls": "self-signed"}, follow_redirects=False)
        assert r.status_code == 303 and webapp.netconf.config(s)["tls"] == "self-signed"
    os.environ.pop("AI_MEMORY_CONFIG_DIR")
    print("network and TLS settings OK")

    # logout
    assert c.get("/logout").status_code == 405, "logout is POST-only"
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    print("logout OK")

    # Login throttling: five wrong passphrases are free, then even the RIGHT one is
    # refused until the wait is over, so a lucky guess during the wait tells nothing.
    def try_login(pw):
        tok = csrf_of(c.get("/login").text)
        return c.post("/login", data={"password": pw, "csrf": tok}, follow_redirects=False)
    webapp._login_fails.clear()
    for _ in range(webapp.LOGIN_FREE_ATTEMPTS):
        assert "Wrong passphrase" in try_login("wrong").text
    r = try_login("novehesloXY")
    assert r.status_code == 200 and "Too many wrong passphrases" in r.text, r.status_code
    webapp._login_fails["testclient"]["until"] = 0.0   # the wait has passed
    assert try_login("novehesloXY").status_code == 303
    assert "testclient" not in webapp._login_fails, "a good login resets the counter"
    c.post("/logout")
    # CSRF: a login POST without a token is refused, and an empty session token never matches
    r = c.post("/login", data={"password": "novehesloXY"}, follow_redirects=False)
    assert r.status_code == 200 and "CSRF" in r.text, "login without a CSRF token must fail"
    fresh = TestClient(webapp.app)
    r = fresh.post("/login", data={"password": "novehesloXY", "csrf": ""}, follow_redirects=False)
    assert r.status_code == 200, "empty token vs empty session must not match"
    print("login CSRF OK")
    print("login throttling OK")

# --- the backup warning -----------------------------------------------------------
#
# It is put on every page a signed-in user sees, so the two failure modes are equal and
# opposite: not warning when the backup has stopped, and warning when there is nothing
# wrong. The second one is what makes the first one dangerous later.
from datetime import datetime, timedelta, timezone   # noqa: E402

from ai_memory.features import export # noqa: E402
from ai_memory.web import i18n # noqa: E402

_now = datetime.now(timezone.utc)
_real_config = export.config


def _with_cfg(**kw):
    cfg = dict(export.DEFAULTS, enabled=True, folder="/somewhere")
    cfg.update(kw)
    webapp.export.config = lambda store: cfg


try:
    _with_cfg(last_run=(_now - timedelta(hours=2)).isoformat())
    assert webapp.backup_banner() == "", "a healthy backup must not put a banner on the page"

    _with_cfg(enabled=False, last_run=(_now - timedelta(days=90)).isoformat())
    assert webapp.backup_banner() == "", "an export switched off is not a warning"

    _with_cfg(last_run=(_now - timedelta(days=9)).isoformat())
    banner = webapp.backup_banner()
    assert "banner" in banner and "9 days" in banner, banner
    assert "/settings" in banner, "the warning must link to where it is fixed"

    i18n.set_lang("cs")
    assert "9 dní" in webapp.backup_banner(), webapp.backup_banner()
    i18n.set_lang("en")

    _with_cfg()
    assert "No backup has ever been taken" in webapp.backup_banner(), webapp.backup_banner()

    # A warning that can break the page it warns on is worse than no warning.
    webapp.export.config = lambda store: (_ for _ in ()).throw(RuntimeError("boom"))
    assert webapp.backup_banner() == "", "a failing check must degrade to silence"
    print("backup warning OK")
finally:
    webapp.export.config = _real_config

print("\nADMIN WEB OK")

# Stored timestamps are UTC; the admin web shows them in local time. Switching the
# zone needs time.tzset(), which exists only on POSIX; Windows CI skips the check.
if hasattr(time, "tzset"):
    _tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Prague"
    time.tzset()
    assert webapp.local_time("2026-10-26T20:29:50+00:00", seconds=True) == "2026-10-26 21:29:50"
    assert webapp.local_time("2026-07-01T10:00:00+00:00") == "2026-07-01 12:00", "summer time"
    if _tz is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = _tz
    time.tzset()
assert webapp.local_time("") == "" and webapp.local_time("garbage") == "garbage"
print("local time OK")
