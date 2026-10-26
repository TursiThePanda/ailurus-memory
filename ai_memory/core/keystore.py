"""Optionally remembering the master passphrase, in the OS credential store.

READ THIS BEFORE CHANGING ANYTHING HERE.

The central security property of this project is that the passphrase is never written
down and the key exists only in RAM: the database, and every backup of it, is useless
to anyone who does not know it. Remembering the passphrase weakens that, and it cannot
be made not to. If the passphrase can be retrieved automatically at login, then anyone
who can run code as that user can retrieve it too, and the encryption stops protecting
that machine. It still protects the file everywhere else -- on the NAS, offsite, on a
stolen disk that is not booted as this user -- which is why the option is worth having
at all.

What it must NEVER be is a file this program writes. That would put the passphrase
beside the ciphertext and reduce the whole design to decoration. So this goes through
the operating system's own credential store, and nothing else:

    Windows   Credential Manager (protected by DPAPI, tied to the user account)
    Linux     Secret Service (KWallet / gnome-keyring) over D-Bus
    macOS     Keychain

Same feature, same behaviour, same UI on every platform -- the backend differs because
the OS differs, which is not a difference the user ever sees.

ONE HONEST CAVEAT, on Linux: the Secret Service may be locked at login and unable to
prompt, in which case retrieval fails and the passphrase is asked for as usual. That is
a degradation, not a fault, and the caller must cope with `load()` returning None.
"""
from __future__ import annotations

SERVICE = "ai-memory"
ACCOUNT = "master-passphrase"


def _keyring():
    import keyring
    return keyring


def available() -> tuple[bool, str]:
    """Can anything actually be stored here? Returns (usable, human explanation).

    This PROBES rather than trusting the backend name, because a backend can be
    present and still unusable. The case that proves it: Windows Credential Manager
    reports itself perfectly happily over an SSH session and then fails every call
    with error 1312, "the specified logon session does not exist" -- the vault is tied
    to an INTERACTIVE logon, and a network logon has none. Reporting it as available
    would make the admin web offer an option that cannot work.

    That limitation is not a problem for what this feature is for: remembering the
    passphrase exists so the tray can start unattended after the user logs in, which
    is an interactive session by definition. It does mean a headless or service-run
    instance cannot remember the passphrase, on Windows at least, and must be told so
    rather than left to fail.
    """
    try:
        kr = _keyring()
    except ImportError:
        return False, "the 'keyring' package is not installed"
    try:
        backend = kr.get_keyring()
    except Exception as exc:
        return False, f"no credential store available: {exc}"
    name = backend.__class__.__name__
    if "fail" in name.lower():
        return False, ("no credential store on this system "
                       "(on Linux: KWallet or gnome-keyring)")
    try:
        kr.get_password(SERVICE, "__probe__")   # absent key returns None, never raises
    except Exception as exc:
        if getattr(exc, "winerror", None) == 1312 or "1312" in str(exc):
            return False, (f"{name} needs an interactive logon session; this one has "
                           "none (a service, or a remote shell)")
        return False, f"{name} is present but unusable: {exc}"
    return True, name


def save(passphrase: str) -> tuple[bool, str]:
    ok, detail = available()
    if not ok:
        return False, detail
    try:
        _keyring().set_password(SERVICE, ACCOUNT, passphrase)
    except Exception as exc:
        return False, str(exc)
    return True, detail


def load() -> str | None:
    """The remembered passphrase, or None. Never raises: a locked or missing
    credential store must fall through to asking, not crash the startup."""
    try:
        return _keyring().get_password(SERVICE, ACCOUNT) or None
    except Exception:
        return None


def forget() -> bool:
    try:
        _keyring().delete_password(SERVICE, ACCOUNT)
        return True
    except Exception:
        return False


def is_remembered() -> bool:
    return load() is not None
