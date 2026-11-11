"""anchormem command line."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, gitutil, hooks, journal, render, store
from .anchors import Anchor, FileMissing, TargetMissing, extract, fingerprint
from .core import CHANGED, MISSING, MOVED, OK, UNVERIFIED, check_memory
from .lint import lint
from .store import Memory, StoreError

BEGIN = "<!-- anchormem:begin (managed by `anchormem init`; edits between these markers are replaced) -->"
END = "<!-- anchormem:end -->"
RULES = """## Project memory (anchormem)

This repo keeps memory for coding agents in `.anchormem/memories/`. Each memory is anchored to the code it describes and gets flagged when that code changes.

- Before editing a file, run `anchormem context <file>` and read what comes back. (With the Claude Code hooks installed, this happens automatically.)
- A memory marked STALE may be out of date. Check it against the code before relying on it.
- If a stale memory is still true, run `anchormem verify <id>`. If its code moved, run `anchormem verify <id> --follow`. If it is wrong, edit the file or run `anchormem retire <id> --reason "<why>"`.
- Memories marked "written by an agent and not yet confirmed" are notes, not rules.
- When you learn something non-obvious about the code (a decision, a gotcha, a constraint), save it:
  `anchormem add "<one-line title>" --anchor <path>#<symbol> --body "<why it matters>" --by agent`"""
BLOCK = f"{BEGIN}\n{RULES}\n{END}\n"

# the single-marker block written by anchormem 0.1, replaced on the next `init`
LEGACY_BLOCK = """<!-- anchormem -->
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


def err(msg: str) -> None:
    print(paint(f"error: {msg}", RED), file=sys.stderr)


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


def default_origin() -> str:
    # Claude Code sets CLAUDECODE=1 in the shells it runs
    return "agent" if os.environ.get("CLAUDECODE") else "user"


# ---------------------------------------------------------------- managed block

def upsert_block(text: str) -> Tuple[str, str]:
    """Return (new_text, action) with the managed block added or refreshed."""
    if BEGIN.split(" (")[0] in text and END in text:
        start = text.index(BEGIN.split(" (")[0])
        end = text.index(END, start) + len(END)
        if end < len(text) and text[end] == "\n":
            end += 1
        new = text[:start] + BLOCK + text[end:]
        return new, ("unchanged" if new == text else "updated")
    if LEGACY_BLOCK in text:
        return text.replace(LEGACY_BLOCK, BLOCK), "updated"
    if not text:
        return BLOCK, "created"
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + BLOCK, "added"


def remove_block(text: str) -> Tuple[str, bool]:
    key = BEGIN.split(" (")[0]
    if key in text and END in text:
        start = text.index(key)
        end = text.index(END, start) + len(END)
        new = (text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")).strip("\n")
        return (new + "\n" if new else ""), True
    if LEGACY_BLOCK in text:
        return text.replace(LEGACY_BLOCK, "").rstrip("\n") + "\n", True
    return text, False


def block_status(f: Path) -> str:
    if not f.exists():
        return "absent"
    text = f.read_text(encoding="utf-8")
    if BLOCK in text:
        return "current"
    if BEGIN.split(" (")[0] in text or LEGACY_BLOCK in text:
        return "outdated"
    return "absent"


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    if args.remove:
        root = store.require_root(Path.cwd())
        for name in ("AGENTS.md", "CLAUDE.md"):
            f = root / name
            if not f.exists():
                continue
            new, found = remove_block(f.read_text(encoding="utf-8"))
            if found:
                if args.dry_run:
                    print(f"would remove the anchormem block from {name}")
                elif new.strip():
                    f.write_text(new, encoding="utf-8")
                    print(f"removed the anchormem block from {name}")
                else:
                    f.unlink()
                    print(f"removed {name} (it only held the anchormem block)")
        if hooks.installed(root):
            print(paint("hooks are still installed; remove them with: anchormem hooks remove", DIM))
        print(paint(f"memories in {store.DIR}/ were left alone", DIM))
        return 0

    if args.dry_run:
        root = store.find_root(Path.cwd()) or gitutil.toplevel(Path.cwd()) or Path.cwd().resolve()
    else:
        root = store.init_root(Path.cwd())
        print(f"{store.MEM_DIR}/ ready in {root}")
        for made in store.ensure_support_files(root):
            print(f"created {made}")
    targets = ["AGENTS.md"]
    if args.claude or (root / "CLAUDE.md").exists():
        targets.append("CLAUDE.md")
    for name in targets:
        f = root / name
        new, action = upsert_block(f.read_text(encoding="utf-8") if f.exists() else "")
        if action == "unchanged":
            print(f"{name} already has the current anchormem rules")
            continue
        if args.dry_run:
            print({"created": f"would create {name}", "added": f"would add agent rules to {name}",
                   "updated": f"would update the agent rules in {name}"}[action])
            continue
        f.write_text(new, encoding="utf-8")
        print({"created": f"created {name}", "added": f"added agent rules to {name}",
               "updated": f"updated the agent rules in {name}"}[action])
    if args.hooks:
        return _install_hooks(root, local=False, remove=False, dry_run=args.dry_run)
    if not args.dry_run:
        print(paint('next: anchormem add "<title>" --anchor path/to/file.py#symbol --body "<why>"', DIM))
        if not hooks.installed(root):
            print(paint("using Claude Code? anchormem hooks install  (shows memories to the agent automatically)", DIM))
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
            err(f"anchor {a} does not resolve: {e}")
            return 2
    mem_id = args.id or store.unique_id(root, store.slugify(args.title))
    if args.id and ((root / store.MEM_DIR / f"{mem_id}.md").exists() or (root / store.RETIRED_DIR / f"{mem_id}.md").exists()):
        err(f"a memory with id {mem_id!r} already exists")
        return 2
    origin = args.by or default_origin()
    mem = Memory(id=mem_id, title=args.title.strip(), body=(body or "").strip(), anchors=anchors, origin=origin,
                 created=today(), verified=today(), commit=gitutil.head(root) or "")
    f = store.save(root, mem)
    journal.append(root, "add", mem_id, f"by {origin} | " + ", ".join(str(a) for a, _ in anchors))
    fps = ", ".join(fp for _, fp in anchors)
    print(f"saved {mem_id} · {len(anchors)} anchor{'s' * (len(anchors) != 1)} · fp {fps}")
    print(paint(f"  {f.relative_to(root)}", DIM))
    for finding in lint(root, [mem]):
        if finding.level != "info":
            print(paint(f"  note: {finding.message}", YELLOW))
    return 0


def cmd_list(args) -> int:
    root = store.require_root(Path.cwd())
    mems = store.load_retired(root) if args.retired else store.load_all(root)
    if args.json:
        print(json.dumps([{"id": m.id, "title": m.title, "origin": m.origin, "confirmed": m.confirmed,
                           "verified": m.verified, "commit": m.commit, "anchors": [str(a) for a, _ in m.anchors],
                           **({k: m.extra[k] for k in ("retired", "retired_reason", "superseded_by") if k in m.extra})}
                          for m in mems], indent=2))
        return 0
    if not mems:
        print("no retired memories" if args.retired else
              'no memories yet. Add one with: anchormem add "<title>" --anchor <path>#<symbol>')
        return 0
    width = max(len(m.id) for m in mems)
    for m in mems:
        if args.retired:
            print(f"{m.id.ljust(width)}  {paint(m.extra.get('retired', '-'), DIM)}  {m.title}"
                  + paint(f"  ({m.extra.get('retired_reason', '')})", DIM))
        else:
            who = paint("  [agent, unconfirmed]", YELLOW) if m.unconfirmed_agent else ""
            print(f"{m.id.ljust(width)}  {paint(m.verified or '-', DIM)}  {m.title}{who}")
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
    if args.quiet and not stale:
        return 0
    print()
    if stale:
        print(paint(f"{len(stale)} of {len(results)} memories need checking", RED) + paint(f" ({n_anchors} anchors)", DIM))
        hints = []
        if any(a.status == MOVED for r in stale for a in r.anchors):
            hints.append("moved: anchormem verify <id> --follow")
        if any(a.status in (CHANGED, UNVERIFIED) for r in stale for a in r.anchors):
            hints.append("still true: anchormem verify <id>")
        hints.append('wrong or gone: edit .anchormem/memories/<id>.md or anchormem retire <id> --reason "..."')
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
        print(json.dumps([dict(r.to_dict(), body=r.memory.body, origin=r.memory.origin,
                               confirmed=r.memory.confirmed) for r in render.order(results)], indent=2))
        return 0
    what = "all files" if args.all else ", ".join(wanted)
    if not results:
        print(f"# anchormem: no memories for {what}")
        return 0
    header = f"# anchormem: {len(results)} memor{'y' if len(results) == 1 else 'ies'} for {what}"
    print(render.memories(results, header, budget=args.budget, more_cmd="anchormem context " + " ".join(args.paths)))
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
                problems.append(f"{res.anchor}: {res.detail}. Edit or remove this anchor, or retire the memory")
                continue
            new_anchors.append((anchor, fingerprint(extract(root, anchor))))
        if problems:
            failed += 1
            print(paint(f"✗ {mem_id} not verified", RED))
            for p in problems:
                print(f"    {p}")
            continue
        changes = []
        for (a, fp), res in zip(new_anchors, result.anchors):
            if str(a) != str(res.anchor):
                changes.append(f"{res.anchor} -> {a}")
            elif fp != res.stored_fp:
                changes.append(f"{a} fp {res.stored_fp or 'none'} -> {fp}")
        mem.anchors, mem.verified, mem.commit = new_anchors, today(), gitutil.head(root) or ""
        store.save(root, mem)
        journal.append(root, "verify", mem_id, ("; ".join(changes) or "unchanged") + (f" @ {mem.commit}" if mem.commit else ""))
        moved = [c for c in changes if " -> " in c and " fp " not in c]
        note = f" · anchor now {', '.join(c.split(' -> ')[1] for c in moved)}" if moved else ""
        print(paint("✓", GREEN) + f" {mem_id} verified" + paint(f" @ {mem.commit or today()}{note}", DIM))
    return 1 if failed else 0


def cmd_confirm(args) -> int:
    root = store.require_root(Path.cwd())
    if not args.yes and not sys.stdin.isatty():
        err("confirm needs a person at a terminal (agents can't confirm their own memories). Run it yourself, "
            "or pass --yes in a script you trust.")
        return 2
    for mem_id in args.ids:
        mem = store.load(root, mem_id)
        if mem.confirmed:
            print(f"{mem_id} was already confirmed on {mem.confirmed}")
            continue
        if not args.yes:
            print(paint(f"## {mem.title}", BOLD))
            if mem.body:
                print(mem.body)
            print(paint("anchors: " + ", ".join(str(a) for a, _ in mem.anchors), DIM))
            answer = input(f"Confirm {mem_id} as correct? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print(f"skipped {mem_id}")
                continue
        mem.confirmed = today()
        store.save(root, mem)
        journal.append(root, "confirm", mem_id)
        print(paint("✓", GREEN) + f" {mem_id} confirmed")
    return 0


def cmd_retire(args) -> int:
    root = store.require_root(Path.cwd())
    if args.superseded_by:
        store.load(root, args.superseded_by)  # must exist
    for mem_id in args.ids:
        mem = store.load(root, mem_id)
        f = store.retire(root, mem, args.reason, today(), args.superseded_by or "")
        journal.append(root, "retire", mem_id, args.reason + (f" | superseded by {args.superseded_by}" if args.superseded_by else ""))
        print(f"retired {mem_id}" + paint(f"  → {f.relative_to(root)}", DIM))
    return 0


def cmd_restore(args) -> int:
    root = store.require_root(Path.cwd())
    for mem_id in args.ids:
        store.restore(root, mem_id)
        journal.append(root, "restore", mem_id)
        print(f"restored {mem_id}" + paint("  (run anchormem check: its code may have changed since)", DIM))
    return 0


def cmd_rm(args) -> int:
    root = store.require_root(Path.cwd())
    for mem_id in args.ids:
        f = root / store.MEM_DIR / f"{mem_id}.md"
        if not f.exists():
            f = root / store.RETIRED_DIR / f"{mem_id}.md"
        if not f.exists():
            err(f"no memory {mem_id!r}")
            return 1
        f.unlink()
        journal.append(root, "rm", mem_id)
        print(f"removed {mem_id}" + paint("  (to keep history instead, use anchormem retire)", DIM))
    return 0


def cmd_lint(args) -> int:
    root = store.require_root(Path.cwd())
    findings = lint(root, store.load_all(root))
    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    elif not findings:
        print(paint("no problems found", GREEN))
    else:
        style = {"error": RED, "warn": YELLOW, "info": DIM}
        for f in findings:
            print(f"{paint(f.level.upper().ljust(5), style[f.level])}  {f.memory}  {paint(f.code, DIM)}  {f.message}")
    failing = [f for f in findings if f.level == "error" or (args.strict and f.level == "warn")]
    return 1 if failing else 0


def cmd_doctor(args) -> int:
    rows: List[Tuple[str, str, str]] = []  # level, item, message
    root = store.find_root(Path.cwd())
    if root is None:
        print(paint("✗", RED) + " no .anchormem folder here or above. Run: anchormem init")
        return 1
    rows.append(("ok", "repo", str(root)))
    try:
        mems = store.load_all(root)
        rows.append(("ok", "memories", f"{len(mems)} active, {len(store.load_retired(root))} retired, all files parse"))
    except StoreError as e:
        mems = []
        rows.append(("error", "memories", str(e)))
    if mems:
        stale = [r for r in (check_memory(root, m) for m in mems) if not r.ok]
        rows.append(("warn" if stale else "ok", "freshness",
                     f"{len(stale)} stale (run anchormem check)" if stale else "every memory matches the code"))
        findings = lint(root, mems)
        bad = [f for f in findings if f.level in ("error", "warn")]
        rows.append(("warn" if bad else "ok", "lint", f"{len(bad)} findings (run anchormem lint)" if bad else "no problems"))
    for name in ("AGENTS.md", "CLAUDE.md"):
        st = block_status(root / name)
        if name == "CLAUDE.md" and st == "absent" and not (root / name).exists():
            continue
        level = {"current": "ok", "outdated": "warn", "absent": "warn"}[st]
        msg = {"current": "agent rules are current", "outdated": "agent rules are out of date (run anchormem init)",
               "absent": "no agent rules (run anchormem init)"}[st]
        rows.append((level, name, msg))
    inst = hooks.installed(root)
    rows.append(("ok" if inst else "info", "claude hooks",
                 "installed in " + ", ".join(str(p.relative_to(root)) for p in inst) if inst
                 else "not installed (optional: anchormem hooks install)"))
    rows.append(("ok" if shutil.which("anchormem") else "warn", "on PATH",
                 "anchormem command found" if shutil.which("anchormem") else
                 "the anchormem command isn't on PATH, so agents and hooks can't run it"))
    gi = root / store.DIR / ".gitignore"
    rows.append(("ok" if gi.exists() else "warn", "gitignore",
                 "per-machine state is ignored" if gi.exists() else f"{store.DIR}/.gitignore missing (run anchormem init)"))
    rows.append(("ok" if gitutil.head(root) else "info", "git",
                 "repo found; renames can be followed" if gitutil.head(root) else "no git commits; rename detection is limited"))
    marks = {"ok": paint("✓", GREEN), "info": paint("·", DIM), "warn": paint("!", YELLOW), "error": paint("✗", RED)}
    width = max(len(r[1]) for r in rows)
    for level, item, msg in rows:
        print(f"{marks[level]} {item.ljust(width)}  {msg}")
    return 1 if any(r[0] == "error" for r in rows) else 0


def _install_hooks(root: Path, local: bool, remove: bool, dry_run: bool) -> int:
    f = hooks.settings_path(root, local)
    try:
        existing = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except ValueError as e:
        err(f"{f.relative_to(root)} is not valid JSON ({e}); fix it first")
        return 2
    if not isinstance(existing, dict):
        err(f"{f.relative_to(root)} does not contain a JSON object")
        return 2
    new = hooks.plan_settings(existing, remove=remove)
    rel = f.relative_to(root)
    if new == existing:
        print(f"{rel}: nothing to change")
        return 0
    if dry_run:
        print(f"would write {rel}:")
        print(json.dumps(new, indent=2))
        return 0
    if not new and remove and f.exists():
        f.unlink()
        print(f"removed {rel} (it only held anchormem hooks)")
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    if remove:
        print(f"removed anchormem hooks from {rel}")
    else:
        print(f"installed Claude Code hooks in {rel}")
        print(paint("  SessionStart: summary of memories, listing stale ones", DIM))
        print(paint(f"  PostToolUse ({hooks.FILE_TOOLS}): memories about the file just touched", DIM))
        if not shutil.which("anchormem"):
            print(paint(f"  note: anchormem isn't on PATH, so the hooks call {hooks.command_prefix()}", YELLOW))
        print(paint("  restart Claude Code (or run /hooks) to load them", DIM))
    return 0


def cmd_hooks(args) -> int:
    root = store.require_root(Path.cwd())
    return _install_hooks(root, args.local, args.action == "remove", args.dry_run)


def cmd_hook(args) -> int:
    return hooks.run(args.kind)


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="anchormem", description="Agent memory anchored to code. Flags memories when the code they describe changes.")
    p.add_argument("--version", action="version", version=f"anchormem {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")
    sub.required = True

    s = sub.add_parser("init", help="create .anchormem/ and add agent rules to AGENTS.md")
    s.add_argument("--claude", action="store_true", help="also add the rules to CLAUDE.md")
    s.add_argument("--hooks", action="store_true", help="also install the Claude Code hooks")
    s.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    s.add_argument("--remove", action="store_true", help="remove the agent rules (memories are kept)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="save a memory anchored to code")
    s.add_argument("title", help="one-line summary of what is true")
    s.add_argument("--anchor", "-a", action="append", required=True, metavar="ANCHOR",
                   help="path, path#symbol, path#Class.method or path:10-20 (repeatable)")
    s.add_argument("--body", "-b", help="details (or pipe them on stdin)")
    s.add_argument("--id", help="memory id (default: from the title)")
    s.add_argument("--by", choices=store.ORIGINS, help="who wrote it (default: agent inside Claude Code, else user)")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="list memories")
    s.add_argument("--retired", action="store_true", help="list retired memories instead")
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
    s.add_argument("--budget", type=int, metavar="CHARS", help="shorten the output to about this many characters")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("verify", help="mark memories as still true and record new fingerprints")
    s.add_argument("ids", nargs="+")
    s.add_argument("--follow", action="store_true", help="update anchors that moved to their new location")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("confirm", help="as a person, confirm memories an agent wrote")
    s.add_argument("ids", nargs="+")
    s.add_argument("--yes", "-y", action="store_true", help="don't ask (for scripts you trust)")
    s.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("retire", help="move memories to .anchormem/retired/ with a reason (keeps history)")
    s.add_argument("ids", nargs="+")
    s.add_argument("--reason", "-r", required=True)
    s.add_argument("--superseded-by", metavar="ID", help="the memory that replaces it")
    s.set_defaults(fn=cmd_retire)

    s = sub.add_parser("restore", help="bring retired memories back")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("rm", help="delete memories permanently")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn=cmd_rm)

    s = sub.add_parser("lint", help="find duplicate, contradictory, empty or risky memories")
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("doctor", help="check that anchormem is set up correctly in this repo")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("hooks", help="install or remove the Claude Code hooks")
    s.add_argument("action", choices=["install", "remove"])
    s.add_argument("--local", action="store_true", help="use .claude/settings.local.json (just you) instead of settings.json")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser("hook", help="run by the Claude Code hooks (reads hook JSON on stdin)")
    s.add_argument("kind", choices=["session-start", "post-tool"])
    s.set_defaults(fn=cmd_hook)
    return p


def _utf8_streams() -> None:
    # Windows consoles default to a legacy code page that can't print ✓ / ✗ / →
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except StoreError as e:
        err(str(e))
        return 2
    except ValueError as e:
        err(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
