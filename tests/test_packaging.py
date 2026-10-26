"""The installed package has to contain every module it imports.

This test exists because a from-scratch install failed and no other test could have
caught it. Everything else here runs from the project directory, where the cwd is on
sys.path and every module is importable whether or not it was ever packaged. The
`ai-memory` console script does not: it runs from anywhere, and sees only what
`py-modules` declared. On 2026-08-30 that list was missing nine modules, and
`ai-memory setup` died on `ModuleNotFoundError: No module named 'crypto'` -- on a
machine where all 19 test files passed.

So the check is not "does it import" but "is the declared list CLOSED under import":
every console-script target is declared, and every local module reachable from a
declared one is declared too. Standalone scripts (migrate.py, repair_author.py) are
deliberately outside the package -- the test's job is to confirm nothing inside
imports them, which is what would make them part of it.
"""
import ast
import os
import sys
import tomllib
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent.parent
sys.path.insert(0, str(HERE))

checks = []


def check(label, ok, detail=""):
    checks.append((label, bool(ok)))
    print(("  ok   " if ok else "  FAIL ") + label + (f"  -- {detail}" if detail and not ok else ""))


def bare_local_imports(path: Path, local: set[str]) -> set[str]:
    """Top-level names imported by `path` that are really modules of the package --
    `import store` instead of `from ai_memory.core import store`. At any depth, so a
    lazy import inside a function counts too: those fail at first use, not at start."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found & local


def main():
    # Since 1.2.0 everything is one package, found by setuptools. The failure this
    # guards against is the same as before the move (2026-08-30: a from-scratch install
    # died on ModuleNotFoundError while every test passed, because the tests run from
    # the checkout): code that is not inside the package, or that imports a sibling by
    # its bare name, works from the checkout and breaks once installed.
    cfg = tomllib.loads((HERE / "pyproject.toml").read_text(encoding="utf-8"))
    find = cfg["tool"]["setuptools"]["packages"]["find"]
    check("setuptools packages the ai_memory package", find.get("include") == ["ai_memory*"])
    pkg = HERE / "ai_memory"
    modules = [p for p in pkg.rglob("*.py")]
    local = {p.stem for p in modules if p.stem != "__init__"}
    print("modules in the package:", len(local))

    no_init = sorted(str(d.relative_to(HERE)) for d in {p.parent for p in modules}
                     if not (d / "__init__.py").exists())
    check("every package directory has an __init__.py", not no_init, str(no_init))

    loose = sorted(p.name for p in HERE.glob("*.py"))
    check("no Python modules loose in the project root", not loose, str(loose))

    bad_targets = []
    for script, target in cfg["project"]["scripts"].items():
        mod = target.split(":")[0]
        f = HERE / (mod.replace(".", "/") + ".py")
        if not f.exists():
            bad_targets.append(f"{script} -> {target}")
    check("every console script points at a module in the package", not bad_targets,
          str(bad_targets))

    bare = {}
    for f in modules + list((HERE / "tests").glob("*.py")) + list((HERE / "scripts").glob("*.py")):
        if f.name == "indicator.py":
            continue  # runs on the SYSTEM python by path, imports only stdlib + gi
        for dep in bare_local_imports(f, local):
            bare.setdefault(dep, []).append(str(f.relative_to(HERE)))
    check("nothing imports a package module by its bare name", not bare,
          "; ".join(f"{d} <- {', '.join(v)}" for d, v in sorted(bare.items())))

    # The build inputs have to agree with the code they package. Both of these were
    # wrong on 2026-08-30: the installer still said 0.1.0, and the spec shipped only the
    # PNG, so a frozen admin web would have lost the icon in its header while everything
    # else looked fine.
    from ai_memory.core import version
    iss = (HERE / "packaging" / "installer.iss").read_text(encoding="utf-8")
    check("the installer announces the version in version.py",
          f'#define AppVersion "{version.__version__}"' in iss, version.__version__)

    spec = (HERE / "packaging" / "ai-memory.spec").read_text(encoding="utf-8")
    for asset in ("ai-memory-64.png", "ai-memory.svg"):
        check(f"the bundle ships {asset}", asset in spec)
        check(f"...and {asset} exists to be shipped", (HERE / "ai_memory" / "assets" / asset).exists())
    # Everything the frozen binary reaches only by re-invoking itself or importing lazily.
    for mod in ("ai_memory.desktop.passdialog", "ai_memory.core.version",
                "ai_memory.desktop.tray", "ai_memory.server.app", "ai_memory.server.server",
                "ai_memory.web.webapp"):
        check(f"{mod} is a declared hidden import", f"'{mod}'" in spec)
    check("Qt is kept out of the bundle", "'PySide6'" in spec.split("EXCLUDES")[1][:200])

    # The layout the runner, CI and the packaging all assume. Moving a test file back
    # into the root would leave it silently unrun by ./run-tests.sh (found while
    # tidying the repo on 2026-08-30, when tests/ was created).
    root_tests = sorted(p.name for p in HERE.glob("test_*.py"))
    check("no test files left loose in the project root", not root_tests, str(root_tests))
    check("the test directory is where the runner looks",
          (HERE / "tests").is_dir() and any((HERE / "tests").glob("test_*.py")))
    runner = (HERE / "run-tests.sh").read_text(encoding="utf-8")
    check("the runner points at tests/", "tests/test_*.py" in runner)
    ci = (HERE / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    check("CI points at tests/ too", "tests\\test_*.py" in ci or "tests/test_*.py" in ci)

    # Every MCP tool has to appear in the README. The list drifted before: the code had
    # 21 tools, the README named 16 and described none — memory_edit and the whole
    # attachment family were simply missing. An agent gets the descriptions from the
    # server regardless, so nothing breaks; the person reading the README just never
    # learns the feature exists.
    import ast
    server_src = (HERE / "ai_memory" / "server" / "server.py").read_text(encoding="utf-8")
    tools = [n.name for n in ast.parse(server_src).body
             if isinstance(n, ast.FunctionDef)
             and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
                     for d in n.decorator_list)]
    readme = (HERE / "docs" / "user-guide.md").read_text(encoding="utf-8")  # the full guide since 1.2.0
    undocumented = [t for t in tools if f"`{t}`" not in readme]
    check(f"all {len(tools)} MCP tools appear in docs/user-guide.md", not undocumented,
          str(undocumented))

    bad = [l for l, o in checks if not o]
    print(f"\n{len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
