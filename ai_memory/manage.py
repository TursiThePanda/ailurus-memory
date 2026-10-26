"""CLI for managing the encrypted store (agents, keys, permissions).

Requires the master passphrase (env AI_MEMORY_PASSPHRASE or a prompt).
DB: env AI_MEMORY_DB, else the per-platform data dir (see paths.py).

Examples:
    ai-memory-manage add-agent lmstudio-local
    ai-memory-manage add-agent claude --perm claude=rw --perm shared=rw --perm '*'=r
    ai-memory-manage list
    ai-memory-manage rotate-key lmstudio-local
    ai-memory-manage set-perm lmstudio-local shared rw
    ai-memory-manage remove-agent old
    ai-memory-manage tag list
    ai-memory-manage tag rename konvence convention
    ai-memory-manage tag delete obsolete
"""
import argparse
import os

from ai_memory.core import paths
from ai_memory.core.store import SecureStore, WrongPassphrase
from ai_memory.core.unlock import get_passphrase

DB = str(paths.db_path())


def parse_perms(pairs):
    if not pairs:
        return None
    out = {}
    for p in pairs:
        k, _, v = p.partition("=")
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser(description="Manage ai-memory (encrypted store)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add-agent"); p.add_argument("name")
    p.add_argument("--perm", action="append", help="namespace=none|r|rw (repeatable)")
    p = sub.add_parser("rotate-key"); p.add_argument("name")
    p = sub.add_parser("remove-agent"); p.add_argument("name")
    p = sub.add_parser("set-perm"); p.add_argument("name"); p.add_argument("namespace")
    p.add_argument("perm", choices=["none", "r", "rw"])
    sub.add_parser("list")
    sub.add_parser("change-passphrase")
    p = sub.add_parser("lint", help="hygiene check over the memory (reports, never edits)")
    p.add_argument("--severity", choices=["broken", "stale", "hygiene"])
    p.add_argument("--all", action="store_true",
                   help="include the noisy checks (single-use tags)")
    p = sub.add_parser("embed", help="the local embedding model (download, index, status)")
    p.add_argument("--setup", action="store_true",
                   help="download the model — the only command here that uses the network")
    p.add_argument("--index", action="store_true", help="embed new and changed records")
    p.add_argument("--force", action="store_true", help="re-embed everything")
    p.add_argument("--overlaps", action="store_true", help="show records that cover the same ground")
    p.add_argument("--threshold", type=float, default=0.65)
    p = sub.add_parser("tag", help="administer the tag taxonomy (user authority)")
    tsub = p.add_subparsers(dest="tagcmd", required=True)
    tsub.add_parser("list")
    t = tsub.add_parser("rename"); t.add_argument("old"); t.add_argument("new")
    t = tsub.add_parser("delete"); t.add_argument("tag")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    try:
        s = SecureStore(DB, get_passphrase())
    except WrongPassphrase as e:
        raise SystemExit(f"ERROR: {e}")

    if args.cmd == "add-agent":
        key = s.add_agent(args.name, parse_perms(args.perm))
        print(f"agent '{args.name}' created.")
        print(f"API KEY (shown ONLY now, save it): {key}")
    elif args.cmd == "rotate-key":
        print(f"new key for '{args.name}': {s.rotate_key(args.name)}")
    elif args.cmd == "remove-agent":
        s.remove_agent(args.name); print(f"agent '{args.name}' removed.")
    elif args.cmd == "set-perm":
        s.set_permission(args.name, args.namespace, args.perm)
        print(f"{args.name}: {args.namespace} = {args.perm}")
    elif args.cmd == "change-passphrase":
        import getpass
        new = getpass.getpass("new password: ")
        if len(new) < 8:
            raise SystemExit("the new password must be at least 8 characters")
        if new != getpass.getpass("new password again: "):
            raise SystemExit("passwords do not match")
        s.change_passphrase(new)
        print("password changed. Use the new one from the next service start.")
    elif args.cmd == "embed":
        from ai_memory.features import embed as embed_mod
        from ai_memory.features import semantic
        if args.setup:
            embed_mod.download()
            print("model ready.")
        if args.index or args.force:
            def show(done, total):
                print(f"  embedded {done}/{total}", end="\r", flush=True)
            result = semantic.reindex(s, force=args.force, progress=show)
            print(f"indexed {result['indexed']}, up to date {result['skipped']},"
                  f" total {result['total']}          ")
        if args.overlaps:
            pairs = semantic.overlaps(s, threshold=args.threshold)
            if not pairs:
                print(f"no pairs above {args.threshold}.")
            for p_ in pairs:
                print(f"  {p_['score']:.3f}  {p_['a']}  ~  {p_['b']}")
        if not (args.setup or args.index or args.force or args.overlaps):
            st = embed_mod.status()
            have, total = s.vector_coverage()
            print(f"ready: {st['ready']}")
            print(f"model dir: {st['model_dir']}")
            for f, present in st["model_files"].items():
                print(f"  {f}: {'present' if present else 'MISSING'}")
            print(f"dependencies installed: {st['dependencies_installed']}")
            print(f"vectors: {have}/{total} records indexed")
            stale = semantic.stale_count(s.vectors())
            if stale:
                print(f"  {stale} of them were made by a DIFFERENT model and are"
                      f" ignored by search until rebuilt: ai-memory-manage embed --index")
    elif args.cmd == "lint":
        from ai_memory.features import lint as lint_mod
        findings = lint_mod.run(s.dump(), include_optional=args.all)
        if args.severity:
            findings = [f for f in findings if f["severity"] == args.severity]
        if not findings:
            print("nothing to report -- the memory is clean.")
            return
        mark = {"broken": "!!", "stale": " ~", "hygiene": "  "}
        for f in findings:
            print(f"{mark.get(f['severity'], '  ')} {f['namespace']}/{f['name']}"
                  f"  [{f['check']}]  {f['detail']}")
        summary = lint_mod.summarize(findings)
        print(f"\n{summary['total']} findings: " +
              ", ".join(f"{k} {v}" for k, v in sorted(summary['by_severity'].items())))
    elif args.cmd == "tag":
        if args.tagcmd == "list":
            tags = s.known_tags()
            if not tags:
                print("(no tags yet)")
            for t, n in tags.items():
                print(f"- {t}  ({n})")
        elif args.tagcmd == "rename":
            known = s.known_tags()
            if args.old not in known:
                raise SystemExit(f"ERROR: tag '{args.old}' does not exist. "
                                 f"Known: {', '.join(known) or '(none)'}")
            if args.new in known:
                print(f"note: '{args.new}' already exists -- the two are being merged.")
            n = s.rename_tag(args.old, args.new)
            print(f"'{args.old}' -> '{args.new}' in {n} record(s).")
        elif args.tagcmd == "delete":
            n = s.delete_tag(args.tag)
            print(f"'{args.tag}' removed from {n} record(s).")
    elif args.cmd == "list":
        agents = s.list_agents()
        if not agents:
            print("(no agents)")
        for ag in agents:
            print(f"- {ag['name']}  (since {ag['created']})")
            for ns, pm in ag["permissions"].items():
                print(f"    {ns}: {pm}")


if __name__ == "__main__":
    main()
