"""What should the person do next? Shared by bare `anchormem` and the dashboard so they agree."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import core, handoff, hooks, inbox, store
from .lint import lint

MAX_STALE = 6


@dataclass
class Step:
    level: str      # "fix" (something is wrong), "todo" (waiting on you), "tip" (optional setup)
    text: str       # plain-language sentence
    command: str    # what to run
    memory: str = ""


# What each status means, in plain words. Used in the terminal and on the dashboard.
EXPLAIN = {
    core.OK: "The code this memory is about hasn't changed since someone last checked it.",
    core.CHANGED: "The code changed since this memory was last checked. It may still be true: read both and decide.",
    core.MOVED: "The file was renamed or the code now lives somewhere else. anchormem can follow it.",
    core.MISSING: "The code this memory was about is gone.",
    core.UNVERIFIED: "Added by hand with no fingerprint yet, so anchormem can't tell if the code changed.",
    core.FLAGGED: "An agent reported this memory as out of date, even though the code didn't change.",
}


def stale_step(r: core.MemoryResult) -> Step:
    mid = r.memory.id
    s = r.status
    if s == core.MOVED:
        return Step("fix", f"“{r.memory.title}” points at code that moved. Update it to the new place.",
                    f"anchormem verify {mid} --follow", mid)
    if s == core.MISSING:
        return Step("fix", f"“{r.memory.title}” is about code that no longer exists. Retire it, or edit its anchor.",
                    f'anchormem retire {mid} --reason "code removed"', mid)
    if s == core.UNVERIFIED:
        return Step("fix", f"“{r.memory.title}” has no fingerprint yet. Record one so changes can be noticed.",
                    f"anchormem verify {mid}", mid)
    why = "an agent reported it out of date" if s == core.FLAGGED else "its code changed"
    return Step("fix", f"“{r.memory.title}” may be stale ({why}). Read it. If it's still true, mark it checked:",
                f"anchormem verify {mid}", mid)


@dataclass
class Snapshot:
    root: Path
    mems: List[store.Memory]
    results: List[core.MemoryResult]
    pending: List[handoff.Handoff]
    notes: List[dict]
    groups: list
    findings: list
    steps: List[Step] = field(default_factory=list)

    @property
    def stale(self) -> List[core.MemoryResult]:
        return [r for r in self.results if not r.ok]

    @property
    def unconfirmed(self) -> List[store.Memory]:
        return [m for m in self.mems if m.unconfirmed_agent]


def snapshot(root: Path) -> Snapshot:
    from .render import order
    mems = store.load_all(root)
    results = order([core.check_memory(root, m) for m in mems])
    notes = inbox.load(root)
    pending = [h for h in handoff.load_all(root) if h.status == "pending"]
    snap = Snapshot(root, mems, results, pending, notes, inbox.candidates(notes), lint(root, mems))
    snap.steps = next_steps(snap)
    return snap


def next_steps(s: Snapshot, hooks_installed: Optional[bool] = None) -> List[Step]:
    steps: List[Step] = []
    if not s.mems:
        steps.append(Step("todo", "Save your first memory: something true about the code that isn't obvious from reading it.",
                          'anchormem add "<what is true>" --anchor <file>#<function> --body "<why it matters>"'))
    for r in s.stale[:MAX_STALE]:
        steps.append(stale_step(r))
    if len(s.stale) > MAX_STALE:
        steps.append(Step("fix", f"{len(s.stale) - MAX_STALE} more memories need checking.", "anchormem check"))
    if s.pending:
        h = s.pending[0]
        steps.append(Step("todo", f"A session left unfinished work: “{h.summary.splitlines()[0] if h.summary else h.id}”.",
                          "anchormem handoff take"))
    if s.unconfirmed:
        n = len(s.unconfirmed)
        steps.append(Step("todo", f"{n} {'memory was' if n == 1 else 'memories were'} written by an agent. "
                                  "Read and approve the ones that are right (it asks one at a time).",
                          "anchormem confirm"))
    if s.groups:
        steps.append(Step("todo", f"{len(s.groups)} {'note keeps' if len(s.groups) == 1 else 'notes keep'} coming up. "
                                  "Turn them into memories:", "anchormem promote --apply"))
    problems = [f for f in s.findings if f.level in ("error", "warn")]
    if problems:
        steps.append(Step("fix", f"{len(problems)} {'memory has' if len(problems) == 1 else 'memories have'} a problem "
                                 f"(first: {problems[0].code} in {problems[0].memory}).", "anchormem lint"))
    if hooks_installed is None:
        hooks_installed = bool(hooks.installed(s.root))
    if not hooks_installed:
        steps.append(Step("tip", "Using Claude Code? Install the hooks so it sees memories automatically.",
                          "anchormem hooks install"))
    return steps
