"""Turning check results into text an agent reads (used by `context` and the hooks)."""
from __future__ import annotations

from typing import List, Optional

from .confidence import describe, score
from .core import MemoryResult

AGENT_NOTE = "written by an agent and not yet confirmed by a person"
STALE_NOTE = "This memory may be out of date. Check it against the code before relying on it."


def order(results: List[MemoryResult]) -> List[MemoryResult]:
    """Stale memories first (they need attention), then the best-supported ones."""
    return sorted(results, key=lambda r: (r.ok, -(score(r.memory) or 0.0), r.memory.id))


def one(r: MemoryResult, full: bool = True) -> str:
    m = r.memory
    tag = "[OK]" if r.ok else f"[STALE: {r.status.upper()}]"
    lines = [f"## {m.title}  {tag}"]
    meta = f"id: {m.id} · verified {m.verified or 'never'}" + (f" @ {m.commit}" if m.commit else "")
    if m.unconfirmed_agent:
        meta += f" · {AGENT_NOTE}"
    conf = describe(m)
    if conf:
        meta += f" · {conf}"
    lines.append(meta)
    if full:
        lines.append("anchors: " + ", ".join(f"{a.anchor} ({a.status})" for a in r.anchors))
    if not r.ok:
        lines.append(f"> {STALE_NOTE}")
        if r.flagged:
            lines.append(f">   reported outdated {r.flagged}")
        if full:
            lines += [f">   {a.anchor}: {a.detail}" for a in r.anchors if not a.ok and a.detail]
    if full and m.body:
        lines += ["", m.body]
    return "\n".join(lines) + "\n"


def memories(results: List[MemoryResult], header: str, budget: Optional[int] = None, more_cmd: str = "") -> str:
    """Render memories under a character budget.

    Over budget, details are dropped first (headlines and status stay), then whole
    memories from the end of the list, and a notice says what was left out.
    """
    results = order(results)
    full = header + "\n\n" + "\n".join(one(r) for r in results)
    if budget is None or len(full) <= budget:
        return full
    notice_room = 200
    out, used, dropped = [header, ""], len(header) + 2, 0
    for r in results:
        block = one(r, full=False)
        if used + len(block) + 1 > budget - notice_room:
            dropped += 1
            continue
        out.append(block)
        used += len(block) + 1
    what = "details hidden" + (f", {dropped} memor{'y' if dropped == 1 else 'ies'} left out" if dropped else "")
    hint = f" Run `{more_cmd}` to see everything." if more_cmd else ""
    return "\n".join(out) + f"\n(Shortened to fit: {what}.{hint})\n"
