"""Admin web for ai-memory (Starlette, localhost only).

Login = master passphrase (verify_master). Session via a signed cookie.
Provides: agent/key management, a permission matrix, a memory browser (content is
decrypted ONLY here in the browser, never on disk) and history.

Language: source strings are English, Czech comes from i18n.py (switcher in the header).

Served by ai_memory/server/app.py (`ai-memory run`) at /, next to MCP at /mcp.
Passphrase: env AI_MEMORY_PASSPHRASE / systemd-ask-password / prompt.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import html
import os
import secrets
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import (FileResponse, HTMLResponse, RedirectResponse,
                                 Response)
from starlette.routing import Route

from ai_memory.features import backup
from ai_memory.core.version import __version__
# cli, for the autostart switches. Safe despite cli importing this module back: that
# import happens inside _serve(), not at module level.
from ai_memory import cli
from ai_memory.features import export
from ai_memory.core import keystore
from ai_memory.features import lint
from ai_memory.server import netconf
from ai_memory.web.i18n import LANGS, get_lang, set_lang, t
from ai_memory.core import paths
from ai_memory.core.store import PERMS, SecureStore
from ai_memory.core.unlock import get_passphrase

DB = str(paths.db_path())
STORE: SecureStore | None = None

CSS = """
/* Ailurus Memory -- warm palette taken from the red-panda logo. One inline sheet, no
   web fonts, no CDN: the admin web must work offline and under a strict CSP. */
:root{
  color-scheme:light dark;
  --bg:#faf7f2; --fg:#1f1a17; --muted:#7a6f66;
  --border:#e8e0d4; --surface:#ffffff; --surface2:#f4efe7;
  --accent:#ff8101; --accent-fg:#fff; --accent-soft:#fff1e0; --deep:#800101;
  --link:#c25e00; --danger:#c0261d; --ok:#2f8f4e; --ok-soft:#e6f4ea;
  --warn-soft:#fff4dc;
  --key-bg:#fff8e6; --key-border:#f0c070; --key-fg:#1f1a17;
  --shadow:0 1px 2px rgba(60,30,10,.06),0 4px 16px rgba(60,30,10,.06);
  --radius:12px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#171412; --fg:#efe8e1; --muted:#a3978c;
    --border:#3a332d; --surface:#211d1a; --surface2:#2a2521;
    --accent:#ff8f1f; --accent-fg:#1a0d00; --accent-soft:#3a2510; --deep:#ff9c8a;
    --link:#ffb061; --danger:#ff7a6e; --ok:#6fd08f; --ok-soft:#1d3324;
    --warn-soft:#3a2c12;
    --key-bg:#2b2310; --key-border:#8a6a20; --key-fg:#f6dfa8;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 4px 16px rgba(0,0,0,.25);
  }
}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     color:var(--fg);background:var(--bg)}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:5;background:var(--surface);
       border-bottom:1px solid var(--border)}
header .in{max-width:1100px;margin:0 auto;padding:.55rem 1.2rem;display:flex;
           align-items:center;gap:1.2rem;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:700;letter-spacing:-.01em;
       white-space:nowrap}
.brand svg{width:34px;height:auto;flex:none;display:block}
.brand .ver{font-weight:600;font-size:.72rem;color:var(--muted);background:var(--surface2);
            border-radius:999px;padding:.08rem .5rem}
nav{display:flex;gap:.2rem;flex:1;flex-wrap:wrap}
nav a{color:var(--muted);padding:.38rem .75rem;border-radius:8px;font-weight:500}
nav a:hover{background:var(--surface2);color:var(--fg);text-decoration:none}
nav a.here{background:var(--accent-soft);color:var(--accent)}
.tools{display:flex;gap:.5rem;align-items:center;color:var(--muted);font-size:.88rem}
main{max-width:1100px;margin:0 auto;padding:1.4rem 1.2rem 4rem}
h1{font-size:1.5rem;letter-spacing:-.02em}
h2{font-size:1.35rem;margin:.3rem 0 1rem;letter-spacing:-.02em}
h3{font-size:1.02rem;margin:0 0 .7rem}
.card,section.card,fieldset.card{background:var(--surface);border:1px solid var(--border);
      border-radius:var(--radius);padding:1.1rem 1.2rem;margin:0 0 1rem;box-shadow:var(--shadow)}
.signin{max-width:380px;margin:9vh auto;text-align:center;background:var(--surface);
        border:1px solid var(--border);border-radius:var(--radius);padding:2rem 1.8rem;
        box-shadow:var(--shadow)}
.signin svg{width:110px;height:auto;display:block;margin:0 auto .6rem}
.signin h1{font-size:1.35rem;margin:.2rem 0 .2rem}
.signin form{text-align:left;margin-top:1.2rem}
.signin input{width:100%}
.signin button{width:100%;margin:1rem 0 0}
table{border-collapse:separate;border-spacing:0;width:100%;margin:.6rem 0 1rem;
      background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
      overflow:hidden}
.card table{border:none;border-radius:0;background:transparent}
th{font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
   font-weight:600;text-align:left;padding:.55rem .7rem;border-bottom:1px solid var(--border);
   background:var(--surface2)}
td{padding:.55rem .7rem;border-bottom:1px solid var(--border);text-align:left;
   vertical-align:top;font-size:.94rem}
tr:last-child>td{border-bottom:none}
tbody tr:hover>td{background:var(--surface2)}
code{font:.85rem ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--surface2);
     padding:.08rem .35rem;border-radius:5px;word-break:break-all}
input,select{font:inherit;color:var(--fg);background:var(--surface);
             border:1px solid var(--border);border-radius:9px;padding:.45rem .65rem;margin:.15rem 0;
             max-width:100%}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);
             box-shadow:0 0 0 3px var(--accent-soft)}
input[type=checkbox],input[type=radio]{accent-color:var(--accent)}
section.card input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=number]){width:min(30rem,100%)}
input[type=number]{width:7rem}
label{font-size:.92rem}
.row{display:flex;gap:.9rem;flex-wrap:wrap;align-items:end}
.row>div{flex:1;min-width:200px}
section.card .row>div input{width:100%}
.row label,section.card form>label:not(.switch){display:block;font-weight:600;margin:.7rem 0 .2rem}
label.switch{display:flex;gap:.5rem;align-items:center;margin:.8rem 0 .2rem;font-weight:500}
button{font:inherit;font-size:.9rem;font-weight:600;padding:.48rem .95rem;cursor:pointer;
       background:var(--surface);color:var(--fg);border:1px solid var(--border);
       border-radius:9px;margin:.15rem .2rem .15rem 0}
button:hover{border-color:var(--muted)}
form button:not([type=button]):not(.danger):not(.ghost):not(.linkbtn):not(.expander),button.primary{
       background:var(--accent);border-color:var(--accent);color:var(--accent-fg)}
form button:not([type=button]):not(.danger):not(.ghost):not(.linkbtn):not(.expander):hover{filter:brightness(1.06)}
button.ghost{background:transparent;font-weight:500;color:var(--muted)}
button.linkbtn{background:none;border:none;padding:0;margin:0;color:var(--link);
               font-weight:500;font-size:inherit}
small{color:var(--muted)}
.key{background:var(--key-bg);border:1px solid var(--key-border);color:var(--key-fg);
     padding:.7rem;font-family:ui-monospace,monospace;word-break:break-all;border-radius:9px}
.danger{color:var(--danger)}
button.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 45%,var(--border))}
pre{white-space:pre-wrap;background:var(--surface2);border:1px solid var(--border);
    padding:.7rem;border-radius:9px;font-size:.85rem}
.muted{color:var(--muted);font-size:.9rem}
.banner{border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);background:var(--warn-soft);
        padding:.7rem 1rem;border-radius:var(--radius);margin:0 0 1rem}
.stale{color:var(--danger)}
.filters{display:flex;gap:.8rem;align-items:center;flex-wrap:wrap;margin:0 0 1rem;
         background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
         padding:.8rem 1rem;box-shadow:var(--shadow)}
.filters label{color:var(--muted);font-size:.88rem}
.inline{display:inline;margin-right:.2rem}
.inline input{width:9rem}
a.tag{display:inline-block;background:var(--accent-soft);color:var(--accent);
      border-radius:999px;padding:.06rem .6rem;margin:.1rem .2rem .1rem 0;
      font-size:.82rem;font-weight:500;text-decoration:none}
a.tag:hover{filter:brightness(.97)}
.ok{color:var(--ok)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
img.thumb{max-width:140px;max-height:100px;display:block;border:1px solid var(--border);
          border-radius:6px;background:var(--surface)}
button.expander{border:none;background:none;padding:0 .3rem;color:var(--muted);
                font-size:.9rem;line-height:1;margin:0}
tr.detail>td{background:var(--surface2)}
.detailbox p{margin:.3rem 0}
.agentlist{display:flex;gap:1rem;flex-wrap:wrap}
.agentbox{white-space:nowrap}
legend{padding:0 .4rem;color:var(--muted);font-size:.9rem}
textarea.editor{width:100%;font-family:ui-monospace,monospace;font-size:.9rem;
                line-height:1.45;background:var(--surface);color:var(--fg);
                border:1px solid var(--border);border-radius:9px;padding:.7rem;
                resize:vertical;tab-size:2}
@media (max-width:640px){header .in{gap:.5rem}nav{order:3;width:100%}main{padding:1rem}}
"""


def local_time(ts: str, seconds: bool = False) -> str:
    """A stored timestamp (UTC, ISO 8601) as this machine's local time. They used to be
    printed raw, so a Prague user read an export made a minute ago as an hour old --
    and took the server's clock for wrong."""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ts or ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def esc(x) -> str:
    return html.escape(str(x))


class LangMiddleware:
    """Set the language for the whole request from the session (runs after SessionMiddleware)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            set_lang(scope.get("session", {}).get("lang"))
        await self.app(scope, receive, send)


def _lang_switch() -> str:
    cur = get_lang()
    parts = [(f"<strong>{esc(label)}</strong>" if code == cur
              else f"<form method=post action='/lang/{code}' class=inline>"
                   f"<button class=linkbtn>{esc(label)}</button></form>")
             for code, label in LANGS.items()]
    return "<span class=lang>" + " · ".join(parts) + "</span>"


async def set_language(request: Request):
    request.session["lang"] = request.path_params["code"]
    back = request.headers.get("referer") or "/"
    return RedirectResponse(back, status_code=303)


# The red-panda logo (Ailurus is the red panda's genus). The tray keeps its own
# small icon in assets/ai-memory*.
ICON_FILE = Path(__file__).resolve().parents[1] / "assets" / "ailurus.svg"


@lru_cache(maxsize=1)
def app_icon() -> str:
    """The application icon, inline, from the SAME file the tray and the .desktop use.

    Inline rather than served as a route: it is under a kilobyte, it needs no request of
    its own, and it cannot 404 on a page that is otherwise fine. If the file is missing
    the header simply loses its picture -- an admin web that refuses to render because an
    icon is absent would be an absurd way to lose access to the memory."""
    try:
        svg = ICON_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
    return svg[svg.index("<svg"):] if "<svg" in svg else ""


FAVICON_FILE = ICON_FILE.parent / "ram-favicon.svg"


@lru_cache(maxsize=1)
def favicon_href() -> str:
    """The browser tab gets the RAM stick alone -- the whole panda is unreadable at
    16 px. A data: URI keeps it in the document, so it survives being opened from
    anywhere without another route to authorise."""
    try:
        icon = FAVICON_FILE.read_text(encoding="utf-8")
    except OSError:
        icon = app_icon()
    if not icon:
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(icon.encode()).decode()


APP_NAME = "Ailurus Memory"


def layout(title: str, body: str, banner: str = "") -> HTMLResponse:
    fav = favicon_href()
    icon = app_icon()
    head = "" if title == t("Sign in") else (
        f"<header><div class=in><span class=brand>{icon}<span>{APP_NAME}</span>"
        f"<span class=ver>{esc(__version__)}</span></span>"
        f"<nav>{_nav_links(title)}</nav>"
        f"<span class=tools>{_lang_switch()}{_logout_button()}</span></div></header>")
    return HTMLResponse(f"""<!doctype html><html lang={get_lang()}><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{esc(title)} – {APP_NAME}</title>
{f'<link rel=icon href="{fav}">' if fav else ''}
<style>{CSS}</style></head><body>
{head}<main>{banner}{body}</main></body></html>""")


def cards(body: str) -> str:
    """Wrap every <h3> section of a page in a card. The pages are built as one string
    per page; this keeps them that way instead of threading markup through each one."""
    head, *parts = body.split("<h3")
    return head + "".join(f"<section class=card><h3{p}</section>" for p in parts)


def _nav_links(title: str) -> str:
    """The navigation, with the current page marked. `title` is what identifies it --
    layout() is called from thirty places and does not see the request, and threading one
    through all of them to underline a word would be a poor trade."""
    items = ((("/", "Overview"), ("/memories", "Memory"), ("/tags", "Tags"),
              ("/passphrase", "Password"), ("/settings", "Settings")))
    out = []
    for href, label in items:
        here = " class=here" if t(label) == title else ""
        out.append(f'<a href="{href}"{here}>{esc(t(label))}</a>')
    return "".join(out)


def _logout_button() -> str:
    # POST, so a prefetch or an <img src=/logout> on another site cannot sign you out
    return (f"<form method=post action='/logout' class=inline>"
            f"<button class=ghost>{esc(t('Log out'))}</button></form>")


def backup_banner() -> str:
    """The one warning worth putting on every page a logged-in user sees.

    Everything else in here can wait until somebody goes looking for it. A backup that
    stopped happening cannot: it is discovered when it is needed, which is the one moment
    it cannot be fixed. Silent by design when no export is configured — see
    export.staleness for why warning about a backup nobody asked for is worse than not.
    """
    try:
        st = export.staleness(export.config(STORE), token=STORE.change_token())
    except Exception:
        return ""      # never let a warning break the page it is warning on
    if not st["stale"]:
        return ""
    msg = export.staleness_message(st)
    if st["state"] == "never":
        detail = t("Set a destination folder below and export once, so a restore is "
                   "something you have done rather than something you hope works.")
    else:
        detail = t("The schedule only runs while this admin web is up, which needs the "
                   "passphrase — so days the memory is never unlocked are days with no "
                   "backup.")
    return (f"<p class=banner><strong>{esc(t(msg) if st['state'] == 'never' else esc_msg(msg))}"
            f"</strong> {esc(detail)} <a href='/settings'>{esc(t('Settings'))}</a></p>")


def esc_msg(msg: str) -> str:
    """The stale message carries a number, so it cannot be a translation key on its own;
    translate the shape and put the number back."""
    import re
    m = re.match(r"The last backup is (\d+) (days|hours) old", msg)
    if not m:
        return esc(msg)
    n, unit = m.groups()
    shape = ("The last backup is {n} days old" if unit == "days"
             else "The last backup is {n} hours old")
    return esc(t(shape).format(n=n))


# A signed-in admin session ends after SESSION_MAX_AGE in any case and after
# SESSION_IDLE without a request. This is the admin web only: agents authenticate with
# their API keys on every request and are never "logged out" by any of this.
SESSION_MAX_AGE = 8 * 3600
SESSION_IDLE = 30 * 60


def authed(request: Request) -> bool:
    if request.session.get("auth") is not True:
        return False
    now = time.time()
    if now - float(request.session.get("seen", 0)) > SESSION_IDLE:
        request.session.pop("auth", None)
        return False
    request.session["seen"] = now
    return True


def csrf(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_hex(16)
        request.session["csrf"] = tok
    return tok


def csrf_field(request: Request) -> str:
    return f'<input type=hidden name=csrf value="{csrf(request)}">'


async def check_csrf(request: Request, form) -> bool:
    # compare_digest("", "") is True -- a session that never got a token must not
    # match a form that sends none
    tok = request.session.get("csrf", "")
    return bool(tok) and secrets.compare_digest(form.get("csrf", ""), tok)


def _err(msg: str, back_to: str | None = None) -> HTMLResponse:
    tail = f"<p><a href='{back_to}'>{esc(t('back'))}</a></p>" if back_to else ""
    return layout(t("Error"), f"<p class=danger>{esc(msg)}</p>{tail}")


# ---------- login ----------
# Throttling of wrong passphrases, per client address. The address is trustworthy
# since 1.2.0: it is the socket peer, or what a CONFIGURED trusted proxy reports --
# never a header the client wrote itself (netconf.resolve_client). Behind a proxy that
# is not configured as trusted every client shares the proxy's address, which turns
# this into the old global lock -- the safe direction to fail in.
LOGIN_FREE_ATTEMPTS = 5
LOGIN_MAX_WAIT = 900
_login_fails: dict[str, dict] = {}


def _client(request: Request) -> str:
    return (request.client.host if request.client else None) or "?"


def _login_wait(who: str) -> int:
    """Seconds until the next attempt from `who` is accepted, 0 when it is now."""
    f = _login_fails.get(who)
    return max(0, int(f["until"] - time.monotonic() + 0.999)) if f else 0


def _login_failed(who: str) -> None:
    f = _login_fails.setdefault(who, {"count": 0, "until": 0.0})
    f["count"] += 1
    over = f["count"] - LOGIN_FREE_ATTEMPTS
    if over >= 0:
        # 30 s, 60 s, 2 min, 4 min ... capped at 15 min
        f["until"] = time.monotonic() + min(LOGIN_MAX_WAIT, 30 * 2 ** over)
    if len(_login_fails) > 10000:  # bound the memory a scan of addresses can cost
        _login_fails.pop(next(iter(_login_fails)))


def _audit(event: str, who: str) -> None:
    print(f"[ai-memory admin] {event} from {who}", file=sys.stderr, flush=True)


async def login(request: Request):
    if request.method == "POST":
        form = await request.form()
        who = _client(request)
        if not await check_csrf(request, form):
            return layout(t("Sign in"), f"<p class=danger>{esc(t('CSRF token mismatch.'))}</p>"
                          + _login_form(request))
        wait = _login_wait(who)
        if wait:
            # Refused without even checking the passphrase: a correct guess during the
            # wait must not be distinguishable from a wrong one.
            msg = t("Too many wrong passphrases. Try again in {n} s.").format(n=wait)
            return layout(t("Sign in"), f"<p class=danger>{esc(msg)}</p>" + _login_form(request))
        # scrypt takes a noticeable fraction of a second; off the event loop, or every
        # login attempt would stall the MCP requests served by the same process
        if await run_in_threadpool(STORE.verify_master, form.get("password", "")):
            _login_fails.pop(who, None)
            _audit("admin login OK", who)
            lang = request.session.get("lang")
            request.session.clear()  # a fresh session on login: no fixation
            if lang:
                request.session["lang"] = lang
            request.session.update(auth=True, seen=time.time())
            return RedirectResponse("/", status_code=303)
        _login_failed(who)
        _audit("admin login FAILED", who)
        return layout(t("Sign in"),
                      f"<p class=danger>{esc(t('Wrong passphrase.'))}</p>" + _login_form(request))
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return layout(t("Sign in"), _login_form(request))


def _login_form(request: Request) -> str:
    """The sign-in page is the one screen shown to somebody who is not in yet, so it is
    where the application should say what it is. Logo large, chrome minimal."""
    return (f'<div class=signin>{app_icon()}'
            f'<h1>{APP_NAME}</h1><div class=muted>{esc(t("Administration"))} · {esc(__version__)}</div>'
            f'<form method=post action="/login">'
            f'{csrf_field(request)}<label>{esc(t("Master passphrase:"))}</label>'
            f'<input type=password name=password autofocus>'
            f'<button type=submit>{esc(t("Sign in "))}</button></form>'
            f'<p class=muted>{esc(t("Your address"))}: <code>{esc(_client(request))}</code></p></div>')


async def logout(request: Request):
    lang = request.session.get("lang")  # the language choice survives logout
    request.session.clear()
    if lang:
        request.session["lang"] = lang
    return RedirectResponse("/login", status_code=303)


# ---------- dashboard ----------
async def dashboard(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    agents = STORE.list_agents()
    rows = ""
    for a in agents:
        perms = ", ".join(f"{esc(k)}={esc(v)}" for k, v in a["permissions"].items())
        rows += (f"<tr><td>{esc(a['name'])}</td><td>{perms}</td><td>{esc(a['created'][:10])}</td>"
                 f"<td><form method=post action='/agents/rotate' style='display:inline'>"
                 f"{csrf_field(request)}<input type=hidden name=name value='{esc(a['name'])}'>"
                 f"<button>{esc(t('Rotate key'))}</button></form> "
                 f"<form method=post action='/agents/delete' style='display:inline' "
                 f"onsubmit=\"return confirm('{esc(t('Delete agent'))} {esc(a['name'])}?')\">"
                 f"{csrf_field(request)}<input type=hidden name=name value='{esc(a['name'])}'>"
                 f"<button class=danger>{esc(t('Delete'))}</button></form></td></tr>")
    counts = {}
    for m in STORE.list():
        counts[m["namespace"]] = counts.get(m["namespace"], 0) + 1
    ns_rows = "".join(f"<tr><td><a href='/memories?ns={esc(k)}'>{esc(k)}</a></td><td>{v}</td></tr>"
                      for k, v in sorted(counts.items())) \
        or f"<tr><td colspan=2>{esc(t('(empty so far)'))}</td></tr>"
    perm_opts = "".join(f"<option value={p}>{p}</option>" for p in PERMS)
    matrix = _perm_matrix(request, agents, sorted(counts))

    body = f"""
<h2>{esc(t("Agents and keys"))}</h2>
<table><tr><th>{esc(t("Agent"))}</th><th>{esc(t("Permissions"))}</th><th>{esc(t("Created"))}</th><th></th></tr>
{rows or f'<tr><td colspan=4>{esc(t("(none)"))}</td></tr>'}</table>

<div class=card><h3>{esc(t("New agent"))}</h3>
<form method=post action="/agents/create">{csrf_field(request)}
{esc(t("Name:"))} <input name=name required> &nbsp;
<small>{esc(t("(default permissions: own=rw, shared=rw, others=r)"))}</small>
<button>{esc(t("Create + issue key"))}</button></form></div>

{matrix}

<details><summary>{esc(t("Set a single permission manually (also for a namespace that does not exist yet)"))}</summary>
<form method=post action="/agents/perm">{csrf_field(request)}
{esc(t("agent"))} <input name=name required> {esc(t("on namespace"))} <input name=namespace required>
= <select name=perm>{perm_opts}</select> <button>{esc(t("Save"))}</button></form></details>

<h2>{esc(t("Namespaces"))}</h2>
<table><tr><th>{esc(t("Namespace"))}</th><th>{esc(t("Records"))}</th></tr>{ns_rows}</table>
"""
    return layout(t("Overview"), body, banner=backup_banner())


SEP = "|"  # separator inside checkbox values (does not occur in agent or namespace names)


def _effective(perms: dict, ns: str) -> tuple[str, bool]:
    """Return (permission, inherited_from_wildcard) for the given namespace."""
    if ns in perms:
        return perms[ns], False
    return perms.get("*", "none"), ns != "*"


def _perm_matrix(request: Request, agents: list[dict], namespaces: list[str]) -> str:
    """Agent x namespace matrix with R(ead) / W(rite) checkboxes.

    Shows the effective permission (including inheritance from '*'). Only cells that
    differ from the effective state are saved — inherited ones are not expanded into
    explicit entries until the user changes them.
    """
    cols = ["*"] + [n for n in dict.fromkeys([*namespaces, "shared", *(a["name"] for a in agents)])
                    if n != "*"]
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    rows, pairs = "", []
    for a in agents:
        cells = ""
        for ns in cols:
            perm, inherited = _effective(a.get("permissions", {}), ns)
            pair = f"{a['name']}{SEP}{ns}"
            pairs.append(pair)
            r = " checked" if perm in ("r", "rw") else ""
            w = " checked" if perm == "rw" else ""
            hint = f" title='{esc(t('inherited from *'))}'" if inherited else ""
            style = " style='opacity:.6'" if inherited else ""
            cells += (f"<td{hint}{style}>"
                      f"<label><input type=checkbox name=perm value='{esc(pair)}{SEP}r'{r}> "
                      f"{esc(t('R'))}</label> "
                      f"<label><input type=checkbox name=perm value='{esc(pair)}{SEP}w'{w}> "
                      f"{esc(t('W'))}</label></td>")
        rows += f"<tr><td><strong>{esc(a['name'])}</strong></td>{cells}</tr>"
    hidden = "".join(f"<input type=hidden name=pair value='{esc(p)}'>" for p in pairs)
    return (f"<div class=card><h3>{esc(t('Permissions'))}</h3>"
            f"<form method=post action='/agents/perms'>{csrf_field(request)}{hidden}"
            f"<table><tr><th>{esc(t('Agent'))}</th>{head}</tr>{rows}</table>"
            f"<p><small><strong>{esc(t('R'))}</strong> {esc(t('= read'))}, "
            f"<strong>{esc(t('W'))}</strong> {esc(t('= write (turning on write turns on read too)'))}. "
            f"<strong>*</strong> {esc(t('column is the default permission for namespaces without '
                                        'their own setting; dimmed cells inherit from it.'))}"
            f"</small></p>"
            f"<button>{esc(t('Save permissions'))}</button></form></div>")


async def save_perms(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))

    checked = set(form.getlist("perm"))
    current = {a["name"]: a.get("permissions", {}) for a in STORE.list_agents()}
    changed, errors = 0, []
    for pair in dict.fromkeys(form.getlist("pair")):
        agent, _, ns = pair.partition(SEP)
        if agent not in current:
            continue
        want_w = f"{pair}{SEP}w" in checked
        want_r = f"{pair}{SEP}r" in checked or want_w  # write without read makes no sense
        new = "rw" if want_w else ("r" if want_r else "none")
        old, _inherited = _effective(current[agent], ns)
        if new != old:
            try:
                STORE.set_permission(agent, ns, new)
                changed += 1
            except Exception as e:
                errors.append(f"{agent}/{ns}: {e}")
    if errors:
        return layout(t("Error"),
                      "<p class=danger>" + "<br>".join(esc(x) for x in errors) + "</p>"
                      f"<p>{esc(t('Changes saved:'))} {changed}</p>"
                      f"<p><a href='/'>{esc(t('back'))}</a></p>")
    return RedirectResponse("/", 303)


async def create_agent(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    name = (form.get("name") or "").strip()
    try:
        key = STORE.add_agent(name)
    except Exception as e:
        return _err(str(e), "/")
    body = (f"<div class=card><h2>{esc(t('Agent'))} '{esc(name)}' {esc(t('created'))}</h2>"
            f"<p>{esc(t('API key (shown only now — save it and put it into the client):'))}</p>"
            f"<p class=key>{esc(key)}</p>"
            f"<p><a href='/'>{esc(t('Back to overview'))}</a></p></div>")
    return layout(t("New agent"), body)


async def rotate_agent(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    name = form.get("name", "")
    try:
        key = STORE.rotate_key(name)
    except Exception as e:
        return _err(str(e), "/")
    body = (f"<div class=card><h2>{esc(t('New key for'))} '{esc(name)}'</h2>"
            f"<p class=key>{esc(key)}</p>"
            f"<p>{esc(t('The old key stopped working. Update the client.'))}</p>"
            f"<p><a href='/'>{esc(t('Back'))}</a></p></div>")
    return layout(t("Key rotation"), body)


async def delete_agent(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    STORE.remove_agent(form.get("name", ""))
    return RedirectResponse("/", 303)


async def set_perm(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    try:
        STORE.set_permission(form.get("name", ""), form.get("namespace", ""), form.get("perm", ""))
    except Exception as e:
        return _err(str(e), "/")
    return RedirectResponse("/", 303)


# ---------- master passphrase change ----------
def _pw_form(request: Request, msg: str = "") -> str:
    m = f"<p class=danger>{esc(msg)}</p>" if msg else ""
    return (f"<div class=card><h2>{esc(t('Change master passphrase'))}</h2>{m}"
            f"<form method=post action='/passphrase'>{csrf_field(request)}"
            f"<p>{esc(t('Current password:'))}<br>"
            f"<input type=password name=current style='width:20rem'></p>"
            f"<p>{esc(t('New password:'))}<br>"
            f"<input type=password name=new style='width:20rem'></p>"
            f"<p>{esc(t('New password again:'))}<br>"
            f"<input type=password name=confirm style='width:20rem'></p>"
            f"<button>{esc(t('Change password'))}</button></form>"
            f"<p><small>{esc(t('Data is not re-encrypted (fast). Running services keep working; '
                               'the new password applies from the next start. Losing the password '
                               '= irreversible loss of the memory.'))}</small></p></div>")


async def passphrase(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    if request.method == "GET":
        return layout(t("Password change"), _pw_form(request))
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    cur, new, conf = form.get("current", ""), form.get("new", ""), form.get("confirm", "")
    if not STORE.verify_master(cur):
        return layout(t("Password change"), _pw_form(request, t("Current password does not match.")))
    if len(new) < 8:
        return layout(t("Password change"),
                      _pw_form(request, t("New password must be at least 8 characters.")))
    if new != conf:
        return layout(t("Password change"), _pw_form(request, t("New passwords do not match.")))
    STORE.change_passphrase(new)
    return layout(t("Password change"),
                  f"<div class=card><h2>{esc(t('Password changed'))} ✅</h2>"
                  f"<p>{esc(t('Use the new password when the services start next time. '
                              'Running services keep going unchanged.'))}</p>"
                  f"<p><a href='/'>{esc(t('Back'))}</a></p></div>")


# ---------- memory browser ----------
def _tag_links(tags: list[str], ns: str = "", type_: str = "") -> str:
    """Tags as click-through filters. Keeps the namespace/type already in effect, so
    clicking a tag narrows the current view instead of resetting it."""
    return " ".join(
        f"<a class=tag href='/memories?{_qs(ns=ns, type=type_, tag=tg)}'>{esc(tg)}</a>"
        for tg in tags)


def _qs(**params) -> str:
    return "&".join(f"{k}={quote_plus(v)}" for k, v in params.items() if v)


def _share_label(share) -> str:
    """How a record's sharing shows up in the table. `None` is the ordinary case —
    the namespace ACL decides — so it must not look like an alarm."""
    if share is None:
        return f"<span class=muted>{esc(t('namespace'))}</span>"
    if not share:
        return f"<span class=danger>🔒 {esc(t('nobody'))}</span>"
    return f"🔒 {esc(t('{n} agents').format(n=len(share)))}"


def _filter_bar(namespaces: list[str], types: list[str], tags: dict[str, int],
                ns: str, type_: str, tag: str, q: str) -> str:
    def opts(values, current, any_label):
        out = [f"<option value=''>{esc(any_label)}</option>"]
        for v in values:
            label = f"{v} ({tags[v]})" if values is tags else v
            sel = " selected" if v == current else ""
            out.append(f"<option value='{esc(v)}'{sel}>{esc(label)}</option>")
        return "".join(out)

    active = _qs(ns=ns, type=type_, tag=tag, q=q)
    clear = (f" · <a href='/memories'>{esc(t('clear filters'))}</a>") if active else ""
    return (f"<form method=get action='/memories' class=filters>"
            f"<input name=q value='{esc(q)}' placeholder='{esc(t('search…'))}' size=24>"
            f"<label>{esc(t('namespace'))} <select name=ns>"
            f"{opts(namespaces, ns, t('(any)'))}</select></label> "
            f"<label>{esc(t('type'))} <select name=type>"
            f"{opts(types, type_, t('(any)'))}</select></label> "
            f"<label>{esc(t('tag'))} <select name=tag>"
            f"{opts(tags, tag, t('(any)'))}</select></label> "
            f"<button type=submit>{esc(t('Filter'))}</button>{clear}</form>")


# Expanding a row is the only thing on this page that needs a script. Kept inline and
# tiny on purpose: the admin runs on localhost with a strict no-external-assets rule,
# and without JS the table still lists everything — only the expander stops working.
ROW_TOGGLE_JS = """<script>
document.addEventListener('click', function (e) {
  var b = e.target.closest('.expander'); if (!b) return;
  var row = document.getElementById(b.dataset.target);
  var open = row.hasAttribute('hidden');
  if (open) { row.removeAttribute('hidden'); } else { row.setAttribute('hidden', ''); }
  b.textContent = open ? '\u25be' : '\u25b8';
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
});
</script>"""


def _verified_label(m: dict) -> str:
    """Short form for the table: when the record was last confirmed against reality.

    `updated` cannot answer this — on a record nobody has touched it is just the day
    it was written, which reads as fresh while saying nothing about whether the claims
    still hold. The dash is the useful state to be able to see at a glance: never
    checked. Anything older than STALE marks itself, matching what memory_lint reports
    so the web and the check cannot disagree.
    """
    v = m.get("verified")
    if not v:
        return f"<span class=muted title='{esc(t('never confirmed'))}'>—</span>"
    age = _age_days(v)
    cls = " class=stale" if age is not None and age >= lint.STALE_AFTER_DAYS else ""
    return f"<span{cls} title='{esc(m.get('verified_by') or '')}'>{esc(v[:10])}</span>"


def _verified_detail(m: dict) -> str:
    v = m.get("verified")
    if not v:
        return (f"<span class=muted>{esc(t('never — nobody has confirmed this record '
                                          'against reality'))}</span>")
    age = _age_days(v)
    how_old = f" ({int(age)} {esc(t('days ago'))})" if age is not None else ""
    return f"{esc(v[:16])}{how_old} · {esc(m.get('verified_by') or '?')}"


def _age_days(ts: str) -> float | None:
    try:
        when = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def _detail_row(m: dict, rid: str, cols: int) -> str:
    """The expanded view of one record: the parameters that do not fit the table,
    read-only. Editing happens on the record's own page — this is for looking."""
    tags = _tag_links(m.get("tags") or []) or f"<span class=muted>{esc(t('(no tags)'))}</span>"
    share = m.get("share")
    if share is None:
        who = esc(t("everyone allowed by the namespace ACL"))
    elif not share:
        who = esc(t("nobody — the record is hidden from every agent"))
    else:
        who = esc(", ".join(share))
    link = f"/memory?ns={quote_plus(m['namespace'])}&name={quote_plus(m['name'])}"
    return (f"<tr class=detail id='{rid}' hidden><td colspan={cols}>"
            f"<div class=detailbox>"
            f"<p><em>{esc(m['description'])}</em></p>"
            f"<p><strong>{esc(t('tags'))}</strong> {tags}</p>"
            f"<p><strong>{esc(t('visible to'))}</strong> {who}</p>"
            f"<p><strong>{esc(t('checked'))}</strong> {_verified_detail(m)}</p>"
            f"<p><strong>{esc(t('author'))}</strong> {esc(m['author'])} · "
            f"<strong>{esc(t('last'))}</strong> {esc(m['updated_by'])} "
            f"{esc(t('at'))} {esc(local_time(m['updated']))}</p>"
            f"<p><a href='{link}'>{esc(t('Open and edit →'))}</a></p>"
            f"</div></td></tr>")


async def memories(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    ns = request.query_params.get("ns", "")
    type_ = request.query_params.get("type", "")
    tag = request.query_params.get("tag", "")
    q = request.query_params.get("q", "").strip()

    if q:
        # full-text goes through search(), which ranks by score; the other filters
        # still apply so a query can be narrowed by namespace/type/tag
        items = STORE.search(q, namespaces=[ns] if ns else None, limit=500,
                             type=type_ or None, tags=[tag] if tag else None)
    else:
        items = STORE.list(ns or None, type=type_ or None, tags=[tag] if tag else None)
    everything = STORE.list()
    namespaces = sorted({m["namespace"] for m in everything})
    types = sorted({m["type"] for m in everything})

    cols = 9
    body_rows = []
    for i, m in enumerate(items):
        rid = f"d{i}"
        link = f"/memory?ns={quote_plus(m['namespace'])}&name={quote_plus(m['name'])}"
        ntags = len(m.get("tags") or [])
        body_rows.append(
            f"<tr><td><button type=button class=expander data-target='{rid}'"
            f" aria-expanded=false title='{esc(t('show detail'))}'>\u25b8</button></td>"
            f"<td>{esc(m['namespace'])}</td>"
            f"<td><a href='{link}'>{esc(m['name'])}</a></td>"
            f"<td>{esc(m['description'])}</td>"
            f"<td>{esc(m['type'])}</td>"
            f"<td class=num>{ntags}</td>"
            f"<td>{_share_label(m.get('share'))}</td>"
            f"<td>{_verified_label(m)}</td>"
            f"<td>{esc(local_time(m['updated']))}</td></tr>")
        body_rows.append(_detail_row(m, rid, cols))
    rows = "".join(body_rows) or f"<tr><td colspan={cols}>{esc(t('(nothing)'))}</td></tr>"

    title = t("Memory: all") if not (ns or type_ or tag or q) else t("Memory: filtered")
    count = t("{n} of {total} records").format(n=len(items), total=len(everything))
    body = (f"<h2>{esc(title)}</h2>"
            f"{_filter_bar(namespaces, types, STORE.known_tags(), ns, type_, tag, q)}"
            f"<p class=muted>{esc(count)} · <a href='/tags'>{esc(t('Manage tags'))}</a></p>"
            f"<table><tr><th></th><th>ns</th><th>{esc(t('name'))}</th>"
            f"<th>{esc(t('description'))}</th><th>{esc(t('type'))}</th>"
            f"<th>{esc(t('tags'))}</th><th>{esc(t('visible to'))}</th>"
            f"<th>{esc(t('checked'))}</th>"
            f"<th>{esc(t('updated'))}</th></tr>{rows}</table>{ROW_TOGGLE_JS}")
    return layout(title, body)


# ---------- attachments ----------
async def attachment_download(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    ns = request.query_params.get("ns", "")
    name = request.query_params.get("name", "")
    att = request.query_params.get("id", "")
    try:
        meta, data = STORE.attachment(ns, name, att)
    except Exception as e:
        return _err(str(e), "/memories")
    filename = meta.get("filename", "attachment")
    return Response(
        data, media_type=meta.get("mime") or "application/octet-stream",
        headers={
            # `attachment` and a quoted filename: an attachment is arbitrary
            # user-supplied content, and rendering it inline would let an uploaded
            # .html or .svg run script in the admin session's origin.
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        })


async def attachment_upload(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("Invalid request (CSRF)."), "/memories")
    ns, name = form.get("ns", ""), form.get("name", "")
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return _err(t("No file chosen."), f"/memory?ns={quote_plus(ns)}&name={quote_plus(name)}")
    data = await upload.read()
    try:
        STORE.attach(ns, name, data=data, filename=os.path.basename(upload.filename),
                     agent="user", mime=upload.content_type or "application/octet-stream",
                     description=form.get("description", "").strip(), _trusted=True)
    except Exception as e:
        return _err(str(e), f"/memory?ns={quote_plus(ns)}&name={quote_plus(name)}")
    return RedirectResponse(
        f"/memory?ns={quote_plus(ns)}&name={quote_plus(name)}&saved=1", 303)


async def attachment_delete(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("Invalid request (CSRF)."), "/memories")
    ns, name = form.get("ns", ""), form.get("name", "")
    try:
        STORE.detach(ns, name, form.get("id", ""), agent="user", _trusted=True)
    except Exception as e:
        return _err(str(e), f"/memory?ns={quote_plus(ns)}&name={quote_plus(name)}")
    return RedirectResponse(
        f"/memory?ns={quote_plus(ns)}&name={quote_plus(name)}&saved=1", 303)


# ---------- tag administration ----------
async def tags_page(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    known = STORE.known_tags()
    rows = "".join(
        f"<tr><td><a href='/memories?tag={quote_plus(tg)}'>{esc(tg)}</a></td><td>{n}</td>"
        f"<td><form method=post action='/tags/rename' class=inline>{csrf_field(request)}"
        f"<input type=hidden name=old value='{esc(tg)}'>"
        f"<input name=new placeholder='{esc(t('new name'))}' required>"
        f"<button type=submit>{esc(t('Rename'))}</button></form>"
        f"<form method=post action='/tags/delete' class=inline"
        f" onsubmit=\"return confirm('{esc(t('Remove this tag from all records?'))}')\">"
        f"{csrf_field(request)}<input type=hidden name=tag value='{esc(tg)}'>"
        f"<button type=submit>{esc(t('Delete'))}</button></form></td></tr>"
        for tg, n in known.items()) or f"<tr><td colspan=3>{esc(t('(nothing)'))}</td></tr>"
    hint = t("Renaming onto an existing tag merges the two. Deleting removes the tag "
             "from every record; the records themselves stay.")
    body = (f"<h2>{esc(t('Tags'))}</h2>"
            f"<p class=muted>{esc(hint)}</p>"
            f"<table><tr><th>{esc(t('tag'))}</th><th>{esc(t('records'))}</th>"
            f"<th>{esc(t('actions'))}</th></tr>{rows}</table>"
            f"<p><a href='/memories'>← {esc(t('back'))}</a></p>")
    return layout(t("Tags"), body)


async def rename_tag(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    old, new = form.get("old", "").strip(), form.get("new", "").strip()
    if not old or not new:
        return _err(t("Both the old and the new tag name are required."), "/tags")
    try:
        STORE.rename_tag(old, new)
    except Exception as e:
        return _err(str(e), "/tags")
    return RedirectResponse("/tags", 303)


async def delete_tag(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    try:
        STORE.delete_tag(form.get("tag", ""))
    except Exception as e:
        return _err(str(e), "/tags")
    return RedirectResponse("/tags", 303)


def _human_size(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _attachments_panel(request: Request, ns: str, name: str) -> str:
    """Files attached to this record: preview, download, remove, upload.

    Images are shown inline as data: URIs rather than through a second request. The
    bytes are decrypted in memory either way, and this keeps the attachment out of any
    URL, cache or proxy log — the store goes to some trouble to keep content off disk
    in the clear, and a preview endpoint that serves it over a guessable URL would
    undo part of that.
    """
    try:
        items = STORE.attachments(ns, name)
    except Exception:
        return ""

    rows = []
    for a in items:
        dl = (f"/memory/attachment?ns={quote_plus(ns)}&name={quote_plus(name)}"
              f"&id={quote_plus(a['id'])}")
        preview = ""
        if (a.get("mime") or "").startswith("image/") and a["bytes"] <= 2_000_000:
            try:
                _, data = STORE.attachment(ns, name, a["id"])
                b64 = base64.b64encode(data).decode()
                preview = (f"<a href='{dl}'><img class=thumb "
                           f"src='data:{esc(a['mime'])};base64,{b64}' "
                           f"alt='{esc(a['filename'])}'></a>")
            except Exception:
                preview = ""
        rows.append(
            f"<tr><td>{preview}</td>"
            f"<td><a href='{dl}'>{esc(a['filename'])}</a><br>"
            f"<span class=muted>{esc(a.get('description') or '')}</span></td>"
            f"<td class=muted>{esc(a.get('mime') or '')}</td>"
            f"<td class=num>{esc(_human_size(a['bytes']))}</td>"
            f"<td class=muted>{esc((a.get('created') or '')[:10])}<br>{esc(a.get('author') or '')}</td>"
            f"<td><form method=post action='/memory/attachment/delete' class=inline"
            f" onsubmit=\"return confirm('{esc(t('Delete this attachment?'))}')\">"
            f"{csrf_field(request)}"
            f"<input type=hidden name=ns value='{esc(ns)}'>"
            f"<input type=hidden name=name value='{esc(name)}'>"
            f"<input type=hidden name=id value='{esc(a['id'])}'>"
            f"<button type=submit class=danger>{esc(t('Delete'))}</button></form></td></tr>")

    table = ("<table><tr><th></th><th>" + esc(t("file")) + "</th><th>" + esc(t("type"))
             + "</th><th>" + esc(t("size")) + "</th><th>" + esc(t("added"))
             + "</th><th></th></tr>" + "".join(rows) + "</table>") if rows else (
        f"<p class=muted>{esc(t('Nothing attached yet.'))}</p>")

    return f"""
<h3>{esc(t('Attachments'))} ({len(items)})</h3>
{table}
<form method=post action="/memory/attachment/upload" enctype="multipart/form-data">
{csrf_field(request)}
<input type=hidden name=ns value="{esc(ns)}"><input type=hidden name=name value="{esc(name)}">
<p><input type=file name=file required>
<input name=description placeholder="{esc(t('what it shows (optional)'))}" size=40>
<button type=submit>{esc(t('Attach'))}</button></p>
</form>
"""


async def memory_view(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    ns = request.query_params.get("ns", "")
    name = request.query_params.get("name", "")
    try:
        m = STORE.read(ns, name)
        hist = STORE.history(ns, name)
    except Exception as e:
        return _err(str(e), "/memories")

    saved = request.query_params.get("saved") == "1"
    note = f"<p class=ok>{esc(t('Saved.'))}</p>" if saved else ""

    hrows = "".join(
        f"<tr><td>{esc(local_time(h['ts']))}</td><td>{esc(h['action'])}</td><td>{esc(h['agent'])}</td></tr>"
        for h in reversed(hist))

    known = STORE.known_tags()
    current = m.get("tags") or []
    # the known tags are offered as a datalist, but the field stays free text: the
    # user is the authority on the taxonomy, so the approval rule that binds agents
    # must not block them here
    datalist = "".join(f"<option value='{esc(tg)}'>" for tg in known)

    share = m.get("share")
    agents = [a["name"] for a in STORE.list_agents()]
    boxes = "".join(
        f"<label class=agentbox><input type=checkbox name=share value='{esc(a)}'"
        f"{' checked' if share is not None and a in share else ''}> {esc(a)}</label>"
        for a in agents) or f"<span class=muted>{esc(t('(no agents yet)'))}</span>"

    inherit = share is None
    body = f"""
{note}
<h2>{esc(ns)}/{esc(name)}</h2>
<p class=muted>{esc(t('author'))} <strong>{esc(m['author'])}</strong>, {esc(t('last'))}
{esc(m['updated_by'])} {esc(t('at'))} {esc(local_time(m['updated']))} ·
{esc(t('created'))} {esc((m.get('created') or '')[:16])}</p>

<form method=post action="/memory/save">
{csrf_field(request)}
<input type=hidden name=ns value="{esc(ns)}"><input type=hidden name=name value="{esc(name)}">

<p><label>{esc(t('description'))}<br>
<input name=description value="{esc(m['description'])}" style="width:100%"></label></p>

<p><label>{esc(t('type'))}
<input name=type value="{esc(m['type'])}" size=16></label>
&nbsp;&nbsp;<label>{esc(t('tags'))} <small>{esc(t('(comma separated)'))}</small><br>
<input name=tags list=knowntags value="{esc(', '.join(current))}" style="width:100%"></label>
<datalist id=knowntags>{datalist}</datalist></p>

<fieldset class=card>
<legend>{esc(t('Who may see this record'))}</legend>
<p><label><input type=radio name=mode value=inherit{' checked' if inherit else ''}>
{esc(t('Anyone allowed by the namespace permissions (default)'))}</label></p>
<p><label><input type=radio name=mode value=restrict{'' if inherit else ' checked'}>
{esc(t('Only the agents ticked below'))}</label></p>
<p class=agentlist>{boxes}</p>
<p class=muted>{esc(t('This narrows the namespace permissions, it never widens them. '
                      'Only you can change it — no agent can grant itself access.'))}</p>
</fieldset>

<p><label>{esc(t('content'))} <small>{esc(t('(markdown)'))}</small><br>
<textarea name=content class=editor rows=24>{esc(m['content'])}</textarea></label></p>

<p><button type=submit>{esc(t('Save changes'))}</button>
&nbsp;<a href="/memories?ns={quote_plus(ns)}">{esc(t('back'))}</a></p>
</form>

{_attachments_panel(request, ns, name)}

<h3>{esc(t('History'))} ({len(hist)})</h3>
<table><tr><th>{esc(t('when'))}</th><th>{esc(t('action'))}</th><th>{esc(t('who'))}</th></tr>{hrows}</table>
"""
    return layout(f"{ns}/{name}", body)


async def memory_save(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    ns, name = form.get("ns", ""), form.get("name", "")
    tags = [x.strip() for x in form.get("tags", "").split(",") if x.strip()]
    mode = form.get("mode", "inherit")
    share = None if mode == "inherit" else form.getlist("share")
    try:
        # _trusted: this is the user, so the tag-approval rule and the share guard
        # that apply to agents do not apply here
        STORE.update(namespace=ns, name=name, agent="user", _trusted=True,
                     description=form.get("description"), type=form.get("type") or None,
                     tags=tags, content=form.get("content"),
                     share=share, _set_share=True)
    except Exception as e:
        return _err(str(e), "/memories")
    return RedirectResponse(f"/memory?{_qs(ns=ns, name=name)}&saved=1", 303)



async def settings_page(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    cfg = export.config(STORE)
    remembered = keystore.is_remembered()
    can_remember, backend = keystore.available()

    status = ""
    if cfg.get("last_error"):
        status = f"<p class=err>{esc(t('Last export failed'))}: {esc(cfg['last_error'])}</p>"
    elif cfg.get("last_run"):
        status = (f"<p class=ok>{esc(t('Last export'))}: {esc(local_time(cfg['last_run'], seconds=True))}"
                  f" &mdash; {esc(cfg.get('last_detail', ''))}</p>")
    else:
        status = f"<p class=muted>{esc(t('Nothing exported yet.'))}</p>"

    export_hint = t(
        "The export is the ENCRYPTED database, exactly as it sits on disk -- not a "
        "readable dump. It carries no passphrase, so it is safe to write to a network "
        "share or ship offsite; without the passphrase it cannot be read. Point the "
        "folder at a share and your existing backup handles the rest.")
    catchup_hint = t(
        "An overdue export runs as soon as the application is next started, so a "
        "machine that was switched off does not simply skip a day.")

    body = (
        f"<h2>{esc(t('Settings'))}</h2>"

        f"<h3>{esc(t('Scheduled export'))}</h3>"
        f"<p class=muted>{esc(export_hint)}</p>"
        f"{status}"
        f"<form method=post action='/settings/export'>{csrf_field(request)}"
        f"<p><label><input type=checkbox name=enabled value=1"
        f"{' checked' if cfg.get('enabled') else ''}> {esc(t('Export automatically'))}</label><br>"
        f"<span class=muted>{esc(t('With this on, a copy is made whenever the memory '
                                   'changes — once the wait below has passed since the '
                                   'last one. Nothing changes, nothing is copied.'))}"
        f"</span></p>"
        f"<p>{esc(t('Destination folder'))}<br>"
        f"<input name=folder size=60 value='{esc(cfg.get('folder', ''))}'"
        f" placeholder='{esc(t('e.g. a mounted share'))}'></p>"
        f"<p>{esc(t('After a change, wait (minutes)'))} "
        f"<input name=on_change_minutes type=number min=1 max=1440 size=5"
        f" value='{esc(cfg.get('on_change_minutes', 15))}'>"
        f" &nbsp; {esc(t('Keep copies'))} "
        f"<input name=keep type=number min=1 max=999 size=5"
        f" value='{esc(cfg.get('keep', 14))}'><br>"
        f"<span class=muted>{esc(t('The wait is what stops a busy session from filling '
                                   'the whole retention with one copy per write.'))}"
        f"</span></p>"
        f"<p><label><input type=checkbox name=periodic value=1"
        f"{' checked' if cfg.get('periodic', True) else ''}> "
        f"{esc(t('Also copy on a schedule, even with nothing changed'))}</label> "
        f"{esc(t('Every (hours)'))} "
        f"<input name=interval_hours type=number min=1 max=8760 size=5"
        f" value='{esc(cfg.get('interval_hours', 24))}'><br>"
        f"<span class=muted>{esc(t('Recommended: it is the only copy that would still '
                                   'be made if change detection ever went wrong. Turn it '
                                   'off to stop identical copies accumulating on quiet '
                                   'days.'))}</span></p>"
        f"<p class=muted>{esc(catchup_hint)}</p>"
        f"<button type=submit>{esc(t('Save'))}</button></form>"

        f"<form method=post action='/settings/export/run' class=inline>{csrf_field(request)}"
        f"<button type=submit>{esc(t('Export now'))}</button></form>"
        f"<a href='/settings/export/download'><button type=button>"
        f"{esc(t('Download a copy'))}</button></a>"

        f"<h3>{esc(t('Master passphrase'))}</h3>")

    if not can_remember:
        body += (f"<p class=muted>{esc(t('This machine has no credential store, so the '
                                        'passphrase cannot be remembered here.'))}"
                 f" ({esc(backend)})</p>")
    elif remembered:
        body += (f"<p class=ok>{esc(t('The passphrase is remembered in the OS credential '
                                     'store, and the application starts unattended.'))}</p>"
                 f"<form method=post action='/settings/forget'>{csrf_field(request)}"
                 f"<button type=submit>{esc(t('Forget it and ask me again'))}</button></form>")
    else:
        warn = t("Remembering it means anything running as your user account can read "
                 "it back, so the encryption stops protecting THIS machine. It keeps "
                 "protecting every copy elsewhere -- the share, the offsite backup, a "
                 "stolen disk. Enter the passphrase to confirm.")
        body += (f"<p class=muted>{esc(warn)}</p>"
                 f"<form method=post action='/settings/remember'>{csrf_field(request)}"
                 f"<input type=password name=passphrase required"
                 f" placeholder='{esc(t('master passphrase'))}'>"
                 f"<button type=submit>{esc(t('Remember on this machine'))}</button></form>")

    body += _network_section(request)
    body += _tls_section(request)
    body += _autostart_section(request)
    body += _installation_section()
    return layout(t("Settings"), cards(body), banner=backup_banner())


def _installation_section() -> str:
    """Read-only: which build this is and where everything it touches lives."""
    mode = {"portable": t("portable (everything in the data folder beside the program)"),
            "installed": t("installed build"),
            "source": t("running from a source checkout")}[paths.install_mode()]
    rows = "".join(
        f"<tr><td>{esc(t(label))}</td><td><code>{esc(path)}</code></td>"
        f"<td>{esc(t('set by') + ' ' + var) if var else ''}</td></tr>"
        for label, path, var in paths.describe())
    return (f"<h3>{esc(t('Installation'))}</h3>"
            f"<p>{esc(t('Version'))} <strong>{esc(__version__)}</strong>, {esc(mode)}</p>"
            f"<table>{rows}</table>"
            f"<p class=muted>{esc(t('Attachments themselves are stored encrypted inside '
                                    'the database and are in every backup with it; the '
                                    'read-out folder only holds copies handed to agents.'))}"
            f"</p>")


def _autostart_section(request: Request) -> str:
    """What starts at login. On Linux these are two independent things and saying so is
    the point: the systemd unit runs the memory, the tray icon only watches it. Someone
    who prefers to start the memory by hand still wants the icon -- greyed out, with
    Start in its menu. On Windows the tray IS the supervisor, so there is one switch."""
    status = cli.autostart_status()
    rows = [("service", t("Start the memory when I log in"),
             t("Without this the memory stays locked until you start it yourself."))]
    if status["indicator"] is not None:
        rows.append(("indicator", t("Show the tray icon when I log in"),
                     t("The icon only reports the state; it never starts the memory "
                       "on its own.")))
    fields = "".join(
        f"<p><label><input type=checkbox name={name} value=1"
        f"{' checked' if status[name] else ''}> {esc(label)}</label><br>"
        f"<span class=muted>{esc(note)}</span></p>"
        for name, label, note in rows)
    return (f"<h3>{esc(t('Autostart'))}</h3>"
            f"<form method=post action='/settings/autostart'>{csrf_field(request)}"
            f"{fields}<button type=submit>{esc(t('Save'))}</button></form>")


async def save_autostart(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."), "/settings")
    status = cli.autostart_status()
    for part in ("service", "indicator"):
        if status[part] is None:
            continue
        wanted = bool(form.get(part))
        if wanted != status[part]:
            # Only touch what changed: enabling an already-enabled unit is harmless, but
            # the log line it writes is not, and neither is rewriting a file the user
            # has not asked us to rewrite.
            await run_in_threadpool(cli.set_autostart, part, wanted)
    return RedirectResponse("/settings", 303)


async def save_export(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    try:
        hours = max(1, int(form.get("interval_hours") or 24))
        keep = max(1, int(form.get("keep") or 14))
        on_change = max(1, int(form.get("on_change_minutes") or 15))
    except ValueError:
        return _err(t("The interval and the number of copies must be numbers."), "/settings")
    export.save(STORE, enabled=bool(form.get("enabled")),
                folder=(form.get("folder") or "").strip(),
                periodic=bool(form.get("periodic")),
                interval_hours=hours, keep=keep, on_change_minutes=on_change)
    return RedirectResponse("/settings", 303)


async def run_export(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    res = await run_in_threadpool(export.run_now, STORE, DB)
    if not res.get("ok"):
        return _err(res.get("error") or t("The export failed."), "/settings")
    return RedirectResponse("/settings", 303)


async def download_export(request: Request):
    """A verified snapshot, streamed straight out. Made in a temporary file rather
    than served from the live database: a file being written to while it is read is
    exactly how a corrupt-looking backup happens."""
    if not authed(request):
        return RedirectResponse("/login", 303)
    tmp = Path(tempfile.mkdtemp()) / f"memory-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.db"

    def _make():
        backup.snapshot(DB, tmp)
        return backup.verify(tmp)

    ok, detail, _ = await run_in_threadpool(_make)
    if not ok:
        return _err(f"{t('The copy did not verify')}: {detail}", "/settings")
    from starlette.background import BackgroundTask
    return FileResponse(str(tmp), filename=tmp.name, media_type="application/octet-stream",
                        background=BackgroundTask(shutil.rmtree, tmp.parent, True))


async def remember_passphrase(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    pw = form.get("passphrase") or ""
    # Re-authenticate: this weakens the security of the machine, so it is not something
    # a logged-in session should be able to do without proving the passphrase again.
    if not pw or not STORE.verify_master(pw):
        return _err(t("That is not the master passphrase."), "/settings")
    ok, detail = keystore.save(pw)
    if not ok:
        return _err(f"{t('Could not store it')}: {detail}", "/settings")
    return RedirectResponse("/settings", 303)


def _network_section(request: Request) -> str:
    cfg = netconf.config(STORE)
    peer = getattr(request.state, "socket_peer", None) if hasattr(request, "state") else None
    proxied = getattr(request.state, "proxied", False)
    who = request.client.host if request.client else "?"
    via = (t("through the trusted proxy {p}").format(p=peer) if proxied
           else t("directly, no proxy"))
    j = lambda xs: ", ".join(xs)  # noqa: E731
    return (
        f"<h3 id=network>{esc(t('Network and access'))}</h3>"
        f"<p class=muted>{esc(t('Your request came from'))} <code>{esc(who)}</code> "
        f"({esc(via)}).</p>"
        f"<form method=post action='/settings/network'>{csrf_field(request)}"
        f"<div class=row><div><label>{esc(t('Listen on address'))}</label>"
        f"<input name=bind value='{esc(cfg['bind'])}'></div>"
        f"<div><label>{esc(t('Port'))}</label>"
        f"<input name=port type=number min=1 max=65535 value='{esc(cfg['port'])}'></div></div>"
        f"<label>{esc(t('Allowed host names'))}</label>"
        f"<input name=allowed_hosts value='{esc(j(cfg['allowed_hosts']))}'"
        f" placeholder='memory.example.org'>"
        f"<p class=muted>{esc(t('The names clients use to reach this memory. localhost and 127.0.0.1 always work; any other name must be listed, which stops DNS-rebinding attacks.'))}</p>"
        f"<div class=row><div><label>{esc(t('MCP: allowed IPs (optional)'))}</label>"
        f"<input name=mcp_allow value='{esc(j(cfg['mcp_allow']))}'"
        f" placeholder='{esc(t('anywhere -- the agent token protects it'))}'></div>"
        f"<div><label>{esc(t('Admin web: allowed IPs (required)'))}</label>"
        f"<input name=admin_allow required value='{esc(j(cfg['admin_allow']))}'></div></div>"
        f"<p class=muted>{esc(t('IP addresses or ranges, comma separated (192.168.1.0/24, 10.8.0.5). This machine itself can always reach the admin web.'))}</p>"
        f"<label>{esc(t('Trusted proxies'))}</label>"
        f"<input name=trusted_proxies value='{esc(j(cfg['trusted_proxies']))}'"
        f" placeholder='{esc(t('none'))}'>"
        f"<p class=muted>{esc(t('Only from these addresses is X-Forwarded-For believed -- set it to your reverse proxy, or every client looks like the proxy.'))}</p>"
        f"<label class=switch><input type=checkbox name=key_in_query value=1"
        f"{' checked' if cfg['key_in_query'] else ''}> "
        f"{esc(t('Accept the agent key in the URL (?key=)'))}</label>"
        f"<p class=muted>{esc(t('Only for clients that cannot send an Authorization header (LM Studio). The key then shows up in proxy logs.'))}</p>"
        f"<p class=muted>{esc(t('Address and port take effect after a restart; the lists apply immediately.'))}</p>"
        f"<button type=submit class=primary>{esc(t('Save'))}</button></form>")


def _tls_section(request: Request) -> str:
    from ai_memory.server import tls
    cfg = netconf.config(STORE)
    info = tls.info()
    if info:
        kind = t("self-signed") if info["self_signed"] else t("issued by a CA")
        cert = (f"<p class=muted>{esc(t('Certificate'))}: {esc(kind)}, "
                f"{esc(', '.join(info['names']))} -- {esc(t('valid until'))} {esc(info['not_after'])}</p>")
    else:
        cert = f"<p class=muted>{esc(t('No certificate yet.'))}</p>"
    opts = "".join(f"<option value={m}{' selected' if cfg['tls'] == m else ''}>"
                   f"{esc(t({'off': 'off (plain HTTP / TLS proxy in front)', 'self-signed': 'self-signed certificate', 'custom': 'my own certificate'}[m]))}</option>"
                   for m in netconf.TLS_MODES)
    return (
        f"<h3>HTTPS</h3>"
        f"<form method=post action='/settings/tls'>{csrf_field(request)}"
        f"<label>{esc(t('Encryption in the application'))}</label><select name=tls>{opts}</select> "
        f"<button type=submit>{esc(t('Save'))}</button></form>{cert}"
        f"<form method=post action='/settings/tls/generate' class=inline>{csrf_field(request)}"
        f"<button type=submit>{esc(t('Generate a new self-signed certificate'))}</button></form>"
        f"<a href='/settings/tls/cert'><button type=button>{esc(t('Download the certificate'))}</button></a>"
        f"<form method=post action='/settings/tls/upload' enctype=multipart/form-data>"
        f"{csrf_field(request)}<div class=row>"
        f"<div><label>{esc(t('Certificate (PEM, chain allowed)'))}</label><input type=file name=cert required></div>"
        f"<div><label>{esc(t('Private key (PEM, unencrypted)'))}</label><input type=file name=key required></div></div>"
        f"<p><button type=submit>{esc(t('Upload my own certificate'))}</button></p></form>"
        f"<p class=muted>{esc(t('A new certificate or mode takes effect after a restart. Clients must trust a self-signed certificate (Claude Code: NODE_EXTRA_CA_CERTS).'))}</p>")


async def save_network(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    try:
        port = int(form.get("port") or 0)
        if not 1 <= port <= 65535:
            raise ValueError(t("The port must be between 1 and 65535."))
        admin = netconf.parse_networks(form.get("admin_allow", ""))
        if not admin:
            raise ValueError(t("The admin web needs at least one allowed address or range."))
        changes = dict(
            bind=(form.get("bind") or netconf.LOCAL).strip(), port=port,
            allowed_hosts=netconf.parse_hosts(form.get("allowed_hosts", "")),
            mcp_allow=netconf.parse_networks(form.get("mcp_allow", "")),
            admin_allow=admin, admin_allow_default=False,
            trusted_proxies=netconf.parse_networks(form.get("trusted_proxies", "")),
            key_in_query=bool(form.get("key_in_query")))
    except ValueError as e:
        return _err(str(e), "/settings#network")
    netconf.save(STORE, **changes)
    return RedirectResponse("/settings#network", 303)


async def save_tls(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    mode = form.get("tls")
    if mode not in netconf.TLS_MODES:
        return _err(t("Unknown TLS mode."), "/settings")
    from ai_memory.server import tls
    if mode == "custom" and not (tls.cert_path().exists() and tls.key_path().exists()):
        return _err(t("Upload a certificate and key first."), "/settings")
    netconf.save(STORE, tls=mode)
    return RedirectResponse("/settings", 303)


def _cert_names() -> list[str]:
    cfg = netconf.config(STORE)
    names = list(cfg["allowed_hosts"])
    if cfg["bind"] not in (netconf.ALL, "::"):
        names.append(cfg["bind"])
    return names


async def generate_tls(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    from ai_memory.server import tls
    await run_in_threadpool(tls.generate_self_signed, _cert_names())
    return RedirectResponse("/settings", 303)


async def upload_tls(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    from ai_memory.server import tls
    try:
        tls.install_custom(await form["cert"].read(), await form["key"].read())
    except (ValueError, KeyError, AttributeError) as e:
        return _err(str(e) or t("Both files are needed."), "/settings")
    netconf.save(STORE, tls="custom")
    return RedirectResponse("/settings", 303)


async def download_cert(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    from ai_memory.server import tls
    if not tls.cert_path().exists():
        return _err(t("No certificate yet."), "/settings")
    return FileResponse(str(tls.cert_path()), filename="ai-memory-cert.pem",
                        media_type="application/x-pem-file")


async def forget_passphrase(request: Request):
    if not authed(request):
        return RedirectResponse("/login", 303)
    form = await request.form()
    if not await check_csrf(request, form):
        return _err(t("CSRF token mismatch."))
    keystore.forget()
    return RedirectResponse("/settings", 303)

routes = [
    Route("/login", login, methods=["GET", "POST"]),
    Route("/logout", logout, methods=["POST"]),
    Route("/lang/{code}", set_language, methods=["POST"]),
    Route("/", dashboard),
    Route("/agents/create", create_agent, methods=["POST"]),
    Route("/agents/rotate", rotate_agent, methods=["POST"]),
    Route("/agents/delete", delete_agent, methods=["POST"]),
    Route("/agents/perm", set_perm, methods=["POST"]),
    Route("/agents/perms", save_perms, methods=["POST"]),
    Route("/passphrase", passphrase, methods=["GET", "POST"]),
    Route("/memories", memories),
    Route("/memory", memory_view),
    Route("/memory/save", memory_save, methods=["POST"]),
    Route("/memory/attachment", attachment_download),
    Route("/memory/attachment/upload", attachment_upload, methods=["POST"]),
    Route("/memory/attachment/delete", attachment_delete, methods=["POST"]),
    Route("/tags", tags_page),
    Route("/tags/rename", rename_tag, methods=["POST"]),
    Route("/tags/delete", delete_tag, methods=["POST"]),
    Route("/settings", settings_page),
    Route("/settings/export", save_export, methods=["POST"]),
    Route("/settings/export/run", run_export, methods=["POST"]),
    Route("/settings/export/download", download_export),
    Route("/settings/remember", remember_passphrase, methods=["POST"]),
    Route("/settings/forget", forget_passphrase, methods=["POST"]),
    Route("/settings/network", save_network, methods=["POST"]),
    Route("/settings/tls", save_tls, methods=["POST"]),
    Route("/settings/tls/generate", generate_tls, methods=["POST"]),
    Route("/settings/tls/upload", upload_tls, methods=["POST"]),
    Route("/settings/tls/cert", download_cert),
    Route("/settings/autostart", save_autostart, methods=["POST"]),
]

# Behind TLS (our own, or a trusted proxy's) the session cookie must never travel over
# plain HTTP. Not unconditional: a browser talking to http://127.0.0.1 would then never
# send the cookie back and login would loop.
HTTPS_ONLY = os.environ.get("AI_MEMORY_WEB_HTTPS_ONLY", "") == "1"


def _session_secret(store) -> str:
    """Derived from the data key, like the record keys in crypto.py: stable across
    restarts (nobody is logged out by a restart) and never written anywhere."""
    if store is None:
        return secrets.token_hex(32)
    import hashlib
    import hmac
    return hmac.new(store.cipher.enc_key, b"admin-session-v1", hashlib.sha256).hexdigest()


def make_app(store=None) -> Starlette:
    https_only = HTTPS_ONLY
    if store is not None:
        cfg = netconf.config(store)
        https_only = https_only or cfg["tls"] != "off" or bool(cfg["trusted_proxies"])
    return Starlette(routes=routes, middleware=[
        Middleware(SessionMiddleware, secret_key=_session_secret(store),
                   https_only=https_only, same_site="lax", max_age=SESSION_MAX_AGE,
                   session_cookie="aimem_session"),
        Middleware(LangMiddleware),  # after session — it reads the chosen language from it
    ])


app = make_app()


def main() -> None:
    """The admin web is served by app.py together with MCP since 1.2.0."""
    from ai_memory.server import app as combined
    combined.main()


if __name__ == "__main__":
    main()
