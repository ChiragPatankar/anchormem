"""Memory files: Markdown with a small frontmatter block, one memory per file.

    ---
    id: pool-size-why
    title: Pool capped at 10 because of the Postgres plan limit
    created: 2026-11-10
    verified: 2026-11-10
    commit: 2c9e4f8
    anchors:
      - src/db/pool.py#POOL_SIZE  # fp:5a0d2e7b13c4
    ---
    Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading.

The frontmatter is a strict subset of YAML (the fingerprint is a YAML comment), so other
tools can read it, and people can edit it by hand. An anchor written without a fingerprint
is "unverified" until `anchormem verify` records one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import gitutil
from .anchors import Anchor

DIR = ".anchormem"
MEM_DIR = f"{DIR}/memories"
RETIRED_DIR = f"{DIR}/retired"
STATE_DIR = f"{DIR}/.state"
_FP = re.compile(r"\s+#\s*fp:([0-9a-f]{6,64})\s*$")
_KEY = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
ORDER = ["id", "title", "origin", "confirmed", "created", "verified", "commit"]
ORIGINS = ("user", "agent")


class StoreError(Exception):
    pass


@dataclass
class Memory:
    id: str
    title: str
    body: str = ""
    anchors: List[Tuple[Anchor, Optional[str]]] = field(default_factory=list)
    created: str = ""
    verified: str = ""
    commit: str = ""
    origin: str = ""      # "user" or "agent": who wrote it
    confirmed: str = ""   # date a person confirmed an agent-written memory
    extra: Dict[str, str] = field(default_factory=dict)
    path: Optional[Path] = None

    @property
    def unconfirmed_agent(self) -> bool:
        return self.origin == "agent" and not self.confirmed

    def to_text(self) -> str:
        vals = {"id": self.id, "title": self.title, "origin": self.origin, "confirmed": self.confirmed,
                "created": self.created, "verified": self.verified, "commit": self.commit}
        lines = ["---"]
        lines += [f"{k}: {vals[k]}" for k in ORDER if vals[k]]
        lines += [f"{k}: {v}" for k, v in self.extra.items()]
        lines.append("anchors:")
        for anchor, fp in self.anchors:
            lines.append(f"  - {anchor}" + (f"  # fp:{fp}" if fp else ""))
        lines.append("---")
        return "\n".join(lines) + "\n" + (self.body.strip() + "\n" if self.body.strip() else "")


def parse(text: str, path: Optional[Path] = None) -> Memory:
    where = str(path) if path else "memory"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise StoreError(f"{where}: missing '---' frontmatter at the top")
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise StoreError(f"{where}: frontmatter is not closed with '---'") from None
    vals: Dict[str, str] = {}
    anchors: List[Tuple[Anchor, Optional[str]]] = []
    current = None
    for n, raw in enumerate(lines[1:close], start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item and current == "anchors":
            entry = item.group(1)
            m = _FP.search(entry)
            fp = m.group(1) if m else None
            entry = entry[:m.start()] if m else entry
            try:
                anchors.append((Anchor.parse(entry), fp))
            except ValueError as e:
                raise StoreError(f"{where}:{n}: {e}") from None
            continue
        m = _KEY.match(raw)
        if not m:
            raise StoreError(f"{where}:{n}: cannot read line {raw!r}")
        current = m.group(1)
        if current != "anchors":
            vals[current] = m.group(2).strip().strip('"')
    if not vals.get("id"):
        raise StoreError(f"{where}: no id")
    known = {k: vals.pop(k, "") for k in ORDER}
    return Memory(id=known["id"], title=known["title"] or known["id"], body="\n".join(lines[close + 1:]).strip(),
                  anchors=anchors, created=known["created"], verified=known["verified"],
                  commit=known["commit"], origin=known["origin"], confirmed=known["confirmed"],
                  extra=vals, path=path)


# ---------------------------------------------------------------- repo

def find_root(start: Path) -> Optional[Path]:
    start = start.resolve()
    for d in [start, *start.parents]:
        if (d / DIR).is_dir():
            return d
    return None


def require_root(start: Path) -> Path:
    root = find_root(start)
    if root is None:
        raise StoreError("no .anchormem folder here or in any parent. Run `anchormem init` in your repo first.")
    return root


def init_root(start: Path) -> Path:
    root = find_root(start) or gitutil.toplevel(start) or start.resolve()
    (root / MEM_DIR).mkdir(parents=True, exist_ok=True)
    return root


def load_all(root: Path, folder: str = MEM_DIR) -> List[Memory]:
    mems = []
    d = root / folder
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            mems.append(parse(f.read_text(encoding="utf-8"), f))
    return mems


def load_retired(root: Path) -> List[Memory]:
    return load_all(root, RETIRED_DIR)


def load(root: Path, mem_id: str, folder: str = MEM_DIR) -> Memory:
    f = root / folder / f"{mem_id}.md"
    if not f.is_file():
        if folder == MEM_DIR and (root / RETIRED_DIR / f"{mem_id}.md").is_file():
            raise StoreError(f"memory {mem_id!r} is retired. Bring it back with `anchormem restore {mem_id}`")
        raise StoreError(f"no memory with id {mem_id!r} (looked for {f.relative_to(root)})")
    return parse(f.read_text(encoding="utf-8"), f)


def save(root: Path, mem: Memory, folder: str = MEM_DIR) -> Path:
    f = root / folder / f"{mem.id}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(mem.to_text(), encoding="utf-8")
    mem.path = f
    return f


def retire(root: Path, mem: Memory, reason: str, when: str, superseded_by: str = "") -> Path:
    old = mem.path
    mem.extra["retired"] = when
    mem.extra["retired_reason"] = reason
    if superseded_by:
        mem.extra["superseded_by"] = superseded_by
    f = save(root, mem, RETIRED_DIR)
    if old and old.exists() and old.resolve() != f.resolve():
        old.unlink()
    return f


def restore(root: Path, mem_id: str) -> Memory:
    mem = load(root, mem_id, RETIRED_DIR)
    if (root / MEM_DIR / f"{mem_id}.md").exists():
        raise StoreError(f"an active memory {mem_id!r} already exists")
    old = mem.path
    for k in ("retired", "retired_reason", "superseded_by"):
        mem.extra.pop(k, None)
    save(root, mem)
    if old:
        old.unlink()
    return mem


def ensure_support_files(root: Path) -> List[str]:
    """Files that keep per-machine state out of git and make the log merge cleanly."""
    made = []
    for name, text in ((".gitignore", ".state/\n"), (".gitattributes", "log.md merge=union\n")):
        f = root / DIR / name
        if not f.exists():
            f.write_text(text, encoding="utf-8")
            made.append(f"{DIR}/{name}")
    return made


_STOP = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "but", "is", "are", "be",
         "because", "then", "it", "its", "this", "that", "with", "by", "from", "as", "not", "only"}


def slugify(title: str) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", title.lower()) if w]
    keep = [w for w in words if w not in _STOP] or words
    return "-".join(keep[:5]) or "memory"


def unique_id(root: Path, base: str) -> str:
    cand, n = base, 2
    while (root / MEM_DIR / f"{cand}.md").exists():
        cand, n = f"{base}-{n}", n + 1
    return cand
