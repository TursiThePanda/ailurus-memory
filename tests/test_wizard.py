"""The first-run wizard: both paths, and the config it prints.

The snippets matter as much as the store does -- they are what the user pastes, so
a malformed one is a broken install that looks like a working one. Every JSON block
printed here is parsed back to prove it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

# The project root: this file lives in tests/, cli.py is one level up. Used both to
# import from and as the cwd for the subprocesses this test drives.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
URL = "http://127.0.0.1:8080/mcp"
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label)


def json_blocks(text):
    """Pull every {...} block out of the printed output and parse it."""
    return [json.loads(m) for m in re.findall(r"^\{$.*?^\}$", text, re.M | re.S)]


def run_cli(args, data_dir, stdin=""):
    env = dict(os.environ, AI_MEMORY_DATA_DIR=data_dir)
    env.pop("AI_MEMORY_PASSPHRASE", None)
    return subprocess.run([sys.executable, "-m", "ai_memory.cli", *args],
                          input=stdin, capture_output=True, text=True, cwd=HERE, env=env)


def main():
    from ai_memory.desktop import wizard # ---- the printed config is valid JSON, and carries the real key ----
    out = wizard.snippets(URL, "claude-code", "aim_testkey")
    blocks = json_blocks(out)
    check("two mcpServers blocks printed", len(blocks) == 2)
    check("every block is parseable JSON", all("mcpServers" in b for b in blocks))
    lm, desktop = blocks
    check("LM Studio carries the key in the URL (their header bug)",
          lm["mcpServers"]["ai-memory"]["url"].endswith("?key=aim_testkey"))
    check("the other uses an Authorization header",
          desktop["mcpServers"]["ai-memory"]["headers"]["Authorization"] == "Bearer aim_testkey")
    check("Claude Code line is a claude mcp add command", "claude mcp add ai-memory" in out)

    # ---- an adopted store must NOT print a key that looks real ----
    adopted = wizard.snippets(URL, "claude-code", None)
    check("no key -> says so rather than inventing one",
          "cannot be read" in adopted and "aim_" not in adopted)

    # ---- NEW: setup creates the store, one agent, and hands the key over once ----
    # ignore_cleanup_errors: on Windows an OPEN file cannot be deleted, so a store a
    # test left open makes this cleanup raise WinError 32 and fails an otherwise green
    # test. POSIX deletes it regardless, which is why it never showed up until the
    # project first ran on Windows. SecureStore.close() is the real fix where a test
    # owns the store; this keeps the temp dir from failing the run when it does not.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = run_cli(["setup"], d, stdin="secret-pass\nsecret-pass\nmy-agent\n")
        db = os.path.join(d, "memory.db")
        check("setup created the database", os.path.exists(db))
        key = re.search(r"(aim_[0-9a-f]+)", r.stdout)
        check("the key is shown exactly once", key is not None and r.stdout.count(key.group(1)) >= 1)

        sys.path.insert(0, HERE)
        from ai_memory.core.store import SecureStore, WrongPassphrase
        s = SecureStore(db, "secret-pass")
        check("the agent the user named exists",
              [a["name"] for a in s.list_agents()] == ["my-agent"])
        check("the printed key actually authenticates",
              key is not None and s.verify_key(key.group(1)) == "my-agent")
        s.db.close()

        # ---- ADOPT: run setup again over the same store ----
        r2 = run_cli(["setup"], d, stdin="secret-pass\n\n")
        check("adopt path recognised the existing database",
              "already here" in r2.stdout)
        check("adopt lists what is inside", "my-agent" in r2.stdout)
        check("adopt explains that keys cannot be printed",
              "CANNOT be printed" in r2.stdout)

        s = SecureStore(db, "secret-pass")
        check("adopt did NOT create a second agent",
              [a["name"] for a in s.list_agents()] == ["my-agent"])
        check("adopt did NOT change the passphrase", s.verify_master("secret-pass"))
        s.db.close()

        # ---- adopt refuses a wrong passphrase ----
        r3 = run_cli(["setup"], d, stdin="wrong\nwrong\nwrong\n")
        check("wrong passphrase is refused", "Wrong passphrase" in r3.stdout)
        check("...and it exits non-zero", r3.returncode != 0)

        # ---- rotate through the wizard produces a working key ----
        r4 = run_cli(["setup"], d, stdin="secret-pass\nmy-agent\n")
        newkey = re.search(r"(aim_[0-9a-f]+)", r4.stdout)
        check("rotate printed a new key", newkey is not None)
        if newkey:
            s = SecureStore(db, "secret-pass")
            check("the rotated key authenticates", s.verify_key(newkey.group(1)) == "my-agent")
            check("the old key no longer does", s.verify_key(key.group(1)) is None)
            s.db.close()

    # ---- start without a database sends the user to setup ----
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = run_cli(["start"], os.path.join(d, "empty"))
        check("start with no database points at setup",
              "ai-memory setup" in r.stdout and r.returncode == 1)

    bad = [l for l, ok in checks if not ok]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
