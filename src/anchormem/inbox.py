"""Notes inbox: small observations that become memories once they keep coming up.

`anchormem note <anchor> "<text>"` appends one JSON line to .anchormem/inbox.jsonl.
`anchormem promote` groups notes by anchor and similar wording; a group with at least
MIN_COUNT notes inside WINDOW_DAYS is a candidate. Without --apply it only reports.
With --apply each candidate becomes an agent-written (unconfirmed) memory, and its
notes leave the inbox. Anchoring the notes is what makes grouping reliable: notes
about the same symbol are about the same thing far more often than notes that merely
share words.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .store import INBOX

MIN_COUNT = 3
WINDOW_DAYS = 30
_WORD = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "it", "this", "that", "be", "with"}


@dataclass
class Group:
    anchor: str
    notes: List[dict] = field(default_factory=list)

    @property
    def title(self) -> str:
        # the shortest wording is usually the clearest statement of the point
        text = min((n["text"] for n in self.notes), key=len)
        return text if len(text) <= 90 else text[:89] + "…"

    @property
    def body(self) -> str:
        lines = [f"Noted {len(self.notes)} times between {self.notes[0]['ts'][:10]} and {self.notes[-1]['ts'][:10]}:"]
        lines += [f"- {n['text']} ({n['ts'][:10]}, {n.get('by', 'unknown')})" for n in self.notes]
        return "\n".join(lines)


def add(root: Path, anchor: str, text: str, by: str) -> dict:
    note = {"ts": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "anchor": anchor, "text": text.strip(), "by": by}
    f = root / INBOX
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(note, ensure_ascii=False) + "\n")
    return note


def load(root: Path) -> List[dict]:
    f = root / INBOX
    if not f.exists():
        return []
    notes = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue  # a bad line (e.g. from a messy merge) shouldn't lose the rest
        if isinstance(n, dict) and n.get("anchor") and n.get("text") and n.get("ts"):
            notes.append(n)
    return notes


def _words(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.3


def candidates(notes: List[dict], min_count: int = MIN_COUNT, days: int = WINDOW_DAYS,
               today: dt.date = None) -> List[Group]:
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    recent = sorted((n for n in notes if n["ts"][:10] >= cutoff), key=lambda n: n["ts"])
    groups: List[Group] = []
    for n in recent:
        for g in groups:
            if g.anchor == n["anchor"] and any(_similar(n["text"], m["text"]) for m in g.notes):
                g.notes.append(n)
                break
        else:
            groups.append(Group(n["anchor"], [n]))
    return [g for g in groups if len(g.notes) >= min_count]


def remove(root: Path, used: List[dict]) -> None:
    keys = {json.dumps(n, sort_keys=True) for n in used}
    keep = [n for n in load(root) if json.dumps(n, sort_keys=True) not in keys]
    f = root / INBOX
    f.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in keep), encoding="utf-8")
