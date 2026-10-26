"""Passphrase input -- both branches, which is the point.

Every other test in this suite feeds the passphrase through a PIPE. That left the
interactive branch of read_passphrase() completely uncovered, and a self-recursive
call sat in it through 18 green tests on two platforms until it blew the stack on a
real Windows console. So this file exercises the terminal path explicitly, with a
fake stdin, on every platform.
"""
import getpass
import io
import sys

import os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the checkout
sys.path.insert(0, HERE)

from ai_memory.core import unlock
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label)


class FakeStdin(io.StringIO):
    def __init__(self, data="", tty=False):
        super().__init__(data)
        self._tty = tty

    def isatty(self):
        return self._tty


def main():
    real_stdin, real_getpass = sys.stdin, getpass.getpass

    # ---- a terminal: must delegate to getpass, exactly once ------------------
    calls = []

    def fake_getpass(prompt=""):
        calls.append(prompt)
        return "typed-at-the-terminal"

    sys.stdin = FakeStdin(tty=True)
    getpass.getpass = fake_getpass
    unlock.getpass = getpass
    try:
        got = unlock.read_passphrase("p: ")
        check("a terminal delegates to getpass", got == "typed-at-the-terminal")
        check("...and calls it exactly once (no recursion)", len(calls) == 1)
    except RecursionError:
        check("a terminal delegates to getpass", False)
        check("...and calls it exactly once (no recursion)", False)
    finally:
        getpass.getpass = real_getpass
        sys.stdin = real_stdin

    # ---- a pipe: must read stdin, never touch getpass -------------------------
    def exploding_getpass(prompt=""):
        raise AssertionError("getpass must not be used when stdin is redirected -- "
                             "on Windows it reads the console and hangs for ever")

    sys.stdin = FakeStdin("from-a-pipe\n", tty=False)
    getpass.getpass = exploding_getpass
    try:
        check("a pipe is read from stdin", unlock.read_passphrase("p: ") == "from-a-pipe")
    finally:
        getpass.getpass = real_getpass
        sys.stdin = real_stdin

    # ---- CRLF, because the pipe may come from a Windows tool ------------------
    sys.stdin = FakeStdin("windows-line\r\n", tty=False)
    try:
        check("a CRLF line ending is stripped",
              unlock.read_passphrase("p: ") == "windows-line")
    finally:
        sys.stdin = real_stdin

    # ---- empty stdin is an error, not an empty passphrase --------------------
    sys.stdin = FakeStdin("", tty=False)
    try:
        unlock.read_passphrase("p: ")
        check("closed stdin raises rather than returning an empty passphrase", False)
    except EOFError:
        check("closed stdin raises rather than returning an empty passphrase", True)
    finally:
        sys.stdin = real_stdin

    # ---- AI_MEMORY_PASSPHRASE_FILE (Docker secret) ------------------------------
    import os
    import tempfile
    saved = {k: os.environ.pop(k, None) for k in ("AI_MEMORY_PASSPHRASE", "AI_MEMORY_PASSPHRASE_FILE")}
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".secret") as fh:
            fh.write("from-the-secret\nsecond line ignored\n")
        os.environ["AI_MEMORY_PASSPHRASE_FILE"] = fh.name
        check("the passphrase is read from AI_MEMORY_PASSPHRASE_FILE, first line only",
              unlock.get_passphrase() == "from-the-secret")
        os.environ["AI_MEMORY_PASSPHRASE_FILE"] = fh.name + ".missing"
        try:
            unlock.get_passphrase()
            check("an unreadable secret file stops the start loudly", False)
        except SystemExit:
            check("an unreadable secret file stops the start loudly", True)
        os.unlink(fh.name)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
