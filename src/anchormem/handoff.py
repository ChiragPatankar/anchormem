"""Handoffs: where one agent session left off, for the next one to pick up.

A handoff is a Markdown file in .anchormem/handoffs/ with a summary, next steps, open
questions and the files that were in play (fingerprinted, so the next agent can see
which changed since). It is pending until one session takes it; it can be taken once,
and expires after a week by default.

    ---
    id: 20261110-2015-finish-invoice-rounding
    status: pending
    from: claude-code
    to: any
    created: 2026-11-10 20:15
    expires: 2026-11-17
    commit: 6ca7e9a
    files:
      - src/billing/invoice.ts  # fp:956852b772d1
    ---
    ## Summary
    Switched lineTotal to banker's rounding; tests for totals still failing.

    ## Next steps
    - Update the totals fixture in tests/billing

    ## Open questions
    - Does finance want half-even for refunds too?
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import gitutil
from .anchors import Anchor, FileMissing, TargetMissing, extract, fingerprint
from .store import DIR, HANDOFF_DIR, StoreError, slugify

PENDING, ACCEPTED, EXPIRED = "pending", "accepted", "expired"
_FP = re.compile(r"\s+#\s*fp:([0-9a-f]{6,64})\s*$")
SECTIONS = (("summary", "Summary"), ("next_steps", "Next steps"), ("questions", "Open questions"))


@dataclass
class Handoff:
    id: str
    summary: str
    status: str = PENDING
    sender: str = ""
    to: str = "any"
    created: str = ""
    expires: str = ""
    commit: str = ""
    accepted: str = ""
    accepted_by: str = ""
    files: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    path: Optional[Path] = None

    @property
    def expired(self) -> bool:
        try:
            return self.status == PENDING and dt.date.today() > dt.date.fromisoformat(self.expires)
        except ValueError:
            return False

    def for_agent(self, agent: str) -> bool:
        return self.to in ("any", "", agent)

    def to_text(self) -> str:
        meta = [("id", self.id), ("status", self.status), ("from", self.sender), ("to", self.to),
                ("created", self.created), ("expires", self.expires), ("commit", self.commit),
                ("accepted", self.accepted), ("accepted_by", self.accepted_by)]
        lines = ["---"] + [f"{k}: {v}" for k, v in meta if v] + ["files:"]
        lines += [f"  - {p}" + (f"  # fp:{fp}" if fp else "") for p, fp in self.files]
        lines += ["---", "## Summary", self.summary.strip(), ""]
        if self.next_steps:
            lines += ["## Next steps"] + [f"- {s}" for s in self.next_steps] + [""]
        if self.questions:
            lines += ["## Open questions"] + [f"- {q}" for q in self.questions] + [""]
        return "\n".join(lines).rstrip() + "\n"


def parse(text: str, path: Optional[Path] = None) -> Handoff:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise StoreError(f"{path or 'handoff'}: missing frontmatter")
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise StoreError(f"{path or 'handoff'}: frontmatter not closed") from None
    meta, files, current = {}, [], None
    for raw in lines[1:close]:
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item and current == "files":
            entry = item.group(1)
            m = _FP.search(entry)
            files.append(((entry[:m.start()] if m else entry).strip(), m.group(1) if m else None))
            continue
        kv = re.match(r"^([A-Za-z_]+):\s*(.*)$", raw)
        if kv:
            current = kv.group(1)
            if current != "files":
                meta[current] = kv.group(2).strip()
    sections = {"summary": [], "next_steps": [], "questions": []}
    names = {title.lower(): key for key, title in SECTIONS}
    cur = "summary"
    for raw in lines[close + 1:]:
        if raw.startswith("## "):
            cur = names.get(raw[3:].strip().lower(), cur)
            continue
        sections[cur].append(raw)
    bullets = lambda ls: [re.sub(r"^\s*[-*]\s+", "", x).strip() for x in ls if x.strip()]  # noqa: E731
    if not meta.get("id"):
        raise StoreError(f"{path or 'handoff'}: no id")
    return Handoff(id=meta["id"], summary="\n".join(sections["summary"]).strip(), status=meta.get("status", PENDING),
                   sender=meta.get("from", ""), to=meta.get("to", "any"), created=meta.get("created", ""),
                   expires=meta.get("expires", ""), commit=meta.get("commit", ""), accepted=meta.get("accepted", ""),
                   accepted_by=meta.get("accepted_by", ""), files=files, next_steps=bullets(sections["next_steps"]),
                   questions=bullets(sections["questions"]), path=path)


def save(root: Path, h: Handoff) -> Path:
    f = root / HANDOFF_DIR / f"{h.id}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(h.to_text(), encoding="utf-8")
    h.path = f
    return f


def load_all(root: Path) -> List[Handoff]:
    d = root / HANDOFF_DIR
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            h = parse(f.read_text(encoding="utf-8"), f)
            if h.expired:
                h.status = EXPIRED
                save(root, h)
            out.append(h)
    return out


def create(root: Path, summary: str, sender: str, to: str = "any", next_steps: Optional[List[str]] = None,
           questions: Optional[List[str]] = None, files: Optional[List[str]] = None, days: int = 7) -> Handoff:
    now = dt.datetime.now()
    base = f"{now:%Y%m%d-%H%M}-{slugify(summary)}"
    hid, n = base, 2
    while (root / HANDOFF_DIR / f"{hid}.md").exists():
        hid, n = f"{base}-{n}", n + 1
    paths = files if files is not None else gitutil.changed_files(root)
    fps = []
    for p in paths[:30]:
        try:
            fps.append((p, fingerprint(extract(root, Anchor(p)))))
        except (FileMissing, TargetMissing):
            fps.append((p, None))  # deleted in this session; still worth listing
    h = Handoff(id=hid, summary=summary.strip(), sender=sender, to=to or "any", created=f"{now:%Y-%m-%d %H:%M}",
                expires=(now.date() + dt.timedelta(days=days)).isoformat(), commit=gitutil.head(root) or "",
                files=fps, next_steps=next_steps or [], questions=questions or [])
    save(root, h)
    return h


def pending_for(root: Path, agent: str) -> List[Handoff]:
    hs = [h for h in load_all(root) if h.status == PENDING and h.for_agent(agent)]
    return sorted(hs, key=lambda h: h.created, reverse=True)


def accept(root: Path, h: Handoff, by: str) -> None:
    h.status, h.accepted, h.accepted_by = ACCEPTED, f"{dt.datetime.now():%Y-%m-%d %H:%M}", by
    save(root, h)


def file_status(root: Path, path: str, fp: Optional[str]) -> str:
    try:
        now = fingerprint(extract(root, Anchor(path)))
    except (FileMissing, TargetMissing):
        return "deleted" if fp else "still missing"
    if fp is None:
        return "re-created"
    return "unchanged" if now == fp else "changed since the handoff"


def render(root: Path, h: Handoff) -> str:
    lines = [f"## Handoff from {h.sender or 'a previous session'} ({h.created})", "", h.summary]
    if h.next_steps:
        lines += ["", "Next steps:"] + [f"- {s}" for s in h.next_steps]
    if h.questions:
        lines += ["", "Open questions:"] + [f"- {q}" for q in h.questions]
    if h.files:
        lines += ["", "Files in play:"] + [f"- {p} ({file_status(root, p, fp)})" for p, fp in h.files]
    if h.commit:
        lines += ["", f"Written at commit {h.commit}."]
    return "\n".join(lines) + "\n"


def default_agent() -> str:
    import os
    if os.environ.get("CLAUDECODE"):
        return "claude-code"
    if any(k.startswith("CODEX_") for k in os.environ):
        return "codex"
    return "user"


__all__ = ["Handoff", "create", "load_all", "pending_for", "accept", "render", "default_agent", "DIR"]
