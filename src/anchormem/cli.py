"""anchormem command line."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, gitutil, store
from .anchors import Anchor, FileMissing, TargetMissing, extract, fingerprint
from .core import CHANGED, MISSING, MOVED, OK, UNVERIFIED, check_memory
from .store import Memory, StoreError

MARKER = "<!-- anchormem -->"
AGENT_RULES = f"""{MARKER}
## Project memory (anchormem)

This repo keeps agent memory in `.anchormem/memories/`. Each memory is anchored to the code it describes, and gets flagged when that code changes.

- Before editing a file, run `anchormem context <file>` and read what comes back.
- A memory marked STALE may be out of date. Check it against the code before relying on it.
- If a stale memory is still true, run `anchormem verify <id>`. If it moved, run `anchormem verify <id> --follow`. If it is wrong, edit or delete the file in `.anchormem/memories/`.
- When you learn something non-obvious about the code (a decision, a gotcha, a constraint), save it:
  `anchormem add "<one-line title>" --anchor <path>#<symbol> --body "<why it matters>"`
"""


# ---------------------------------------------------------------- output helpers

def _color_on() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _color_on() else text


RED, GREEN, YELLOW, DIM, BOLD = "31", "32", "33", "2", "1"
STATUS_STYLE = {OK: ("OK", GREEN), CHANGED: ("CHANGED", RED), MOVED: ("MOVED", YELLOW),
                MISSING: ("MISSING", RED), UNVERIFIED: ("UNVERIFIED", YELLOW)}


def badge(status: str) -> str:
    label, code = STATUS_STYLE[status]
    return paint(label, code) + " " * (10 - len(label))


def today() -> str:
    return dt.date.today().isoformat()


def rel_to_root(root: Path, p: str) -> str:
    """Interpret a user-given path relative to the cwd and express it relative to the repo root."""
    path = Path(p)
    full = path if path.is_absolute() else Path.cwd() / path
    try:
        return full.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.replace("\\", "/")


def parse_anchor(root: Path, text: str) -> Anchor:
    a = Anchor.parse(text)
    return a.with_path(rel_to_root(root, a.path))


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = store.init_root(Path.cwd())
    print(f"created {store.MEM_DIR}/ in {root}")
    targets = ["AGENTS.md"]
    if args.claude or (root / "CLAUDE.md").exists():
        targets.append("CLAUDE.md")
    for name in targets:
        f = root / name
        existing = f.read_text(encoding="utf-8") if f.exists() else ""
        if MARKER in existing:
            print(f"{name} already has anchormem rules")
            continue
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
        f.write_text(existing + sep + AGENT_RULES, encoding="utf-8")
        print(f"{'added agent rules to' if existing else 'created'} {name}")
    print(paint('next: anchormem add "<title>" --anchor path/to/file.py#symbol --body "<why>"', DIM))
    return 0


def cmd_add(args) -> int:
    root = store.require_root(Path.cwd())
    body = args.body
    if body is None and not sys.stdin.isatty():
        body = sys.stdin.read()
    anchors = []
    for text in args.anchor:
        a = parse_anchor(root, text)
        try:
            anchors.append((a, fingerprint(extract(root, a))))
        except (FileMissing, TargetMissing) as e:
            print(paint(f"error: anchor {a} does not resolve: {e}", RED), file=sys.stderr)
            return 2
    mem_id = args.id or store.unique_id(root, store.slugify(args.title))
    if args.id and (root / store.MEM_DIR / f"{mem_id}.md").exists():
        print(paint(f"error: a memory with id {mem_id!r} already exists", RED), file=sys.stderr)
        return 2
    mem = Memory(id=mem_id, title=args.title.strip(), body=(body or "").strip(), anchors=anchors,
                 created=today(), verified=today(), commit=gitutil.head(root) or "")
    f = store.save(root, mem)
    fps = ", ".join(fp for _, fp in anchors)
    print(f"saved {mem_id} · {len(anchors)} anchor{'s' * (len(anchors) != 1)} · fp {fps}")
    print(paint(f"  {f.relative_to(root)}", DIM))
    return 0


def cmd_list(args) -> int:
    root = store.require_root(Path.cwd())
    mems = store.load_all(root)
    if args.json:
        print(json.dumps([{"id": m.id, "title": m.title, "verified": m.verified, "commit": m.commit,
                           "anchors": [str(a) for a, _ in m.anchors]} for m in mems], indent=2))
        return 0
    if not mems:
        print('no memories yet. Add one with: anchormem add "<title>" --anchor <path>#<symbol>')
        return 0
    width = max(len(m.id) for m in mems)
    for m in mems:
        print(f"{m.id.ljust(width)}  {paint(m.verified or '-', DIM)}  {m.title}")
    return 0


def _results(root: Path, ids: List[str]):
    mems = [store.load(root, i) for i in ids] if ids else store.load_all(root)
    return [check_memory(root, m) for m in mems]


def cmd_check(args) -> int:
    root = store.require_root(Path.cwd())
    results = _results(root, args.ids)
    stale = [r for r in results if not r.ok]
    if args.json:
        print(json.dumps({"ok": not stale, "memories": [r.to_dict() for r in results]}, indent=2))
        return 1 if stale else 0
    if not results:
        print("no memories to check")
        return 0
    width = max(len(r.memory.id) for r in results)
    for r in results:
        if r.ok and args.quiet:
            continue
        mark = paint("✓", GREEN) if r.ok else paint("✗", RED)
        first = True
        for a in r.anchors:
            if a.ok and not first and not args.verbose:
                continue
            name = r.memory.id.ljust(width) if first else " " * width
            line = f"{mark if first else ' '} {name}  {badge(a.status)}  {paint(str(a.anchor), DIM)}"
            if a.status == CHANGED and r.memory.commit:
                line += paint(f"  since {r.memory.commit}", DIM)
            if a.moved_to:
                line += paint(f"  → {a.moved_to[0]}", YELLOW)
            print(line)
            first = False
    n_anchors = sum(len(r.anchors) for r in results)
    print()
    if stale:
        print(paint(f"{len(stale)} of {len(results)} memories need checking", RED) + paint(f" ({n_anchors} anchors)", DIM))
        hints = []
        if any(a.status == MOVED for r in stale for a in r.anchors):
            hints.append("moved: anchormem verify <id> --follow")
        if any(a.status in (CHANGED, UNVERIFIED) for r in stale for a in r.anchors):
            hints.append("still true: anchormem verify <id>")
        hints.append("wrong: edit or delete .anchormem/memories/<id>.md")
        for h in hints:
            print(paint(f"  {h}", DIM))
        return 1
    print(paint(f"all {len(results)} memories fresh ({n_anchors} anchors)", GREEN))
    return 0


def cmd_context(args) -> int:
    root = store.require_root(Path.cwd())
    wanted = [rel_to_root(root, p).rstrip("/") for p in args.paths]

    def matches(a: Anchor) -> bool:
        return args.all or any(a.path == w or a.path.startswith(w + "/") or w in ("", ".") for w in wanted)

    results = [check_memory(root, m) for m in store.load_all(root) if any(matches(a) for a, _ in m.anchors)]
    if args.json:
        print(json.dumps([dict(r.to_dict(), body=r.memory.body) for r in results], indent=2))
        return 0
    what = "all files" if args.all else ", ".join(wanted)
    if not results:
        print(f"# anchormem: no memories for {what}")
        return 0
    print(f"# anchormem: {len(results)} memor{'y' if len(results) == 1 else 'ies'} for {what}\n")
    for r in results:
        m = r.memory
        tag = "[OK]" if r.ok else f"[STALE: {r.status.upper()}]"
        print(f"## {m.title}  {tag}")
        print(f"id: {m.id} · verified {m.verified or 'never'}" + (f" @ {m.commit}" if m.commit else ""))
        print("anchors: " + ", ".join(f"{a.anchor} ({a.status})" for a in r.anchors))
        if not r.ok:
            print("> This memory may be out of date. Check it against the code before relying on it.")
            for a in r.anchors:
                if not a.ok and a.detail:
                    print(f">   {a.anchor}: {a.detail}")
        if m.body:
            print("\n" + m.body)
        print()
    return 0


def cmd_verify(args) -> int:
    root = store.require_root(Path.cwd())
    failed = 0
    for mem_id in args.ids:
        mem = store.load(root, mem_id)
        result = check_memory(root, mem)
        new_anchors, problems = [], []
        for res in result.anchors:
            anchor = res.anchor
            if res.status == MOVED:
                if args.follow and len(res.moved_to) == 1:
                    anchor = parse_anchor(root, res.moved_to[0])
                else:
                    opts = " or ".join(res.moved_to)
                    problems.append(f"{res.anchor} moved ({opts}). Use --follow" +
                                    (" or edit the anchor by hand" if len(res.moved_to) > 1 else ""))
                    continue
            elif res.status == MISSING:
                problems.append(f"{res.anchor}: {res.detail}. Edit or remove this anchor first")
                continue
            new_anchors.append((anchor, fingerprint(extract(root, anchor))))
        if problems:
            failed += 1
            print(paint(f"✗ {mem_id} not verified", RED))
            for p in problems:
                print(f"    {p}")
            continue
        changed = [str(a) for (a, fp), res in zip(new_anchors, result.anchors) if str(a) != str(res.anchor)]
        mem.anchors, mem.verified, mem.commit = new_anchors, today(), gitutil.head(root) or ""
        store.save(root, mem)
        note = f" · anchor now {', '.join(changed)}" if changed else ""
        print(paint("✓", GREEN) + f" {mem_id} verified" + paint(f" @ {mem.commit or today()}{note}", DIM))
    return 1 if failed else 0


def cmd_rm(args) -> int:
    root = store.require_root(Path.cwd())
    for mem_id in args.ids:
        f = root / store.MEM_DIR / f"{mem_id}.md"
        if not f.exists():
            print(paint(f"no memory {mem_id!r}", RED), file=sys.stderr)
            return 1
        f.unlink()
        print(f"removed {mem_id}")
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anchormem", description="Agent memory anchored to code. Flags memories when the code they describe changes.")
    p.add_argument("--version", action="version", version=f"anchormem {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    sub.required = True

    s = sub.add_parser("init", help="create .anchormem/ and add agent rules to AGENTS.md")
    s.add_argument("--claude", action="store_true", help="also add the rules to CLAUDE.md")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="save a memory anchored to code")
    s.add_argument("title", help="one-line summary of what is true")
    s.add_argument("--anchor", "-a", action="append", required=True, metavar="ANCHOR",
                   help="path, path#symbol, path#Class.method or path:10-20 (repeatable)")
    s.add_argument("--body", "-b", help="details (or pipe them on stdin)")
    s.add_argument("--id", help="memory id (default: from the title)")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="list memories")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("check", help="find memories whose code changed, moved or disappeared (exit 1 if any)")
    s.add_argument("ids", nargs="*", help="only these memories")
    s.add_argument("--json", action="store_true")
    s.add_argument("--quiet", "-q", action="store_true", help="only show stale memories")
    s.add_argument("--verbose", "-v", action="store_true", help="show every anchor")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("context", help="print the memories relevant to files or folders, for an agent to read")
    s.add_argument("paths", nargs="*", default=["."])
    s.add_argument("--all", action="store_true", help="every memory")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("verify", help="mark memories as still true and record new fingerprints")
    s.add_argument("ids", nargs="+")
    s.add_argument("--follow", action="store_true", help="update anchors that moved to their new location")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("rm", help="delete memories")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn=cmd_rm)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except StoreError as e:
        print(paint(f"error: {e}", RED), file=sys.stderr)
        return 2
    except ValueError as e:
        print(paint(f"error: {e}", RED), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
