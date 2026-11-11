"""Checking anchors against the current code."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import gitutil
from .anchors import (Anchor, FileMissing, TargetMissing, extract, find_file_by_fingerprint,
                      find_symbol_elsewhere, fingerprint)
from .store import Memory

OK, CHANGED, MOVED, MISSING, UNVERIFIED = "ok", "changed", "moved", "missing", "unverified"
FLAGGED = "flagged"  # an agent reported the memory outdated; cleared by verify


@dataclass
class AnchorResult:
    anchor: Anchor
    status: str
    stored_fp: Optional[str] = None
    current_fp: Optional[str] = None
    moved_to: List[str] = field(default_factory=list)  # candidate new anchors (as strings)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict:
        return {"anchor": str(self.anchor), "status": self.status, "stored_fp": self.stored_fp,
                "current_fp": self.current_fp, "moved_to": self.moved_to, "detail": self.detail}


@dataclass
class MemoryResult:
    memory: Memory
    anchors: List[AnchorResult]

    @property
    def flagged(self) -> str:
        """Why an agent reported this memory outdated (empty if it wasn't)."""
        return self.memory.extra.get("flagged", "")

    @property
    def ok(self) -> bool:
        return all(a.ok for a in self.anchors) and not self.flagged

    @property
    def status(self) -> str:
        bad = [a.status for a in self.anchors if not a.ok]
        for s in (MISSING, MOVED, CHANGED):
            if s in bad:
                return s
        if self.flagged:
            return FLAGGED
        return UNVERIFIED if UNVERIFIED in bad else OK

    def to_dict(self) -> dict:
        from .confidence import band, counts, score
        m = self.memory
        applied, violated = counts(m)
        return {"id": m.id, "title": m.title, "status": self.status, "verified": m.verified,
                "commit": m.commit, "flagged": self.flagged or None,
                "confidence": {"band": band(score(m)), "score": score(m), "applied": applied, "violated": violated},
                "anchors": [a.to_dict() for a in self.anchors]}


def check_anchor(root: Path, anchor: Anchor, stored_fp: Optional[str], since: Optional[str] = None) -> AnchorResult:
    try:
        text = extract(root, anchor)
    except FileMissing:
        new_paths = gitutil.renamed_to(root, since, anchor.path)
        if not new_paths and stored_fp and not anchor.symbol and anchor.start is None:
            new_paths = find_file_by_fingerprint(root, anchor, stored_fp)
        if not new_paths and anchor.symbol:
            new_paths = find_symbol_elsewhere(root, anchor)
        candidates = [str(anchor.with_path(p)) for p in new_paths]
        if candidates:
            return AnchorResult(anchor, MOVED, stored_fp, moved_to=candidates,
                                detail=f"{anchor.path} is gone; found at {', '.join(new_paths)}")
        return AnchorResult(anchor, MISSING, stored_fp, detail=f"{anchor.path} does not exist")
    except TargetMissing as e:
        hits = find_symbol_elsewhere(root, anchor)
        candidates = [str(anchor.with_path(p)) for p in hits]
        if candidates:
            return AnchorResult(anchor, MOVED, stored_fp, moved_to=candidates,
                                detail=f"{anchor.symbol} is no longer in {anchor.path}; found in {', '.join(hits)}")
        return AnchorResult(anchor, MISSING, stored_fp, detail=str(e))
    current = fingerprint(text)
    if not stored_fp:
        return AnchorResult(anchor, UNVERIFIED, None, current, detail="no fingerprint recorded yet")
    if current != stored_fp:
        return AnchorResult(anchor, CHANGED, stored_fp, current, detail="code changed since last verified")
    return AnchorResult(anchor, OK, stored_fp, current)


def check_memory(root: Path, mem: Memory) -> MemoryResult:
    return MemoryResult(mem, [check_anchor(root, a, fp, mem.commit or None) for a, fp in mem.anchors])
