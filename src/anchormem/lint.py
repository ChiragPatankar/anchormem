"""Rule-based checks on the memories themselves (no LLM, no network).

Findings:
  error    id-mismatch        file name and id differ
  warn     empty-body         no explanation of why the memory matters
  warn     duplicate          two memories say nearly the same thing
  warn     contradiction      memories on the same code disagree (one says never/don't, the other doesn't)
  warn     dead-reference     the body mentions a file path that doesn't exist
  warn     instruction-like   an agent-written memory tells the agent to do something risky
  warn     often-violated     agents report it violated at least as often as it held
  info     line-range         line-range anchors break on any edit above them; prefer a symbol
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

from .confidence import counts
from .store import Memory

_WORD = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "be", "it", "this",
         "that", "with", "by", "we", "our", "as", "at", "from", "so", "if", "then", "because"}
_NEG = re.compile(r"\b(never|don'?t|do not|must not|mustn'?t|shouldn'?t|should not|avoid|no longer|not)\b", re.I)
_PATH = re.compile(r"`([\w./-]+\.[A-Za-z0-9]{1,8}|[\w.-]+/[\w./-]+)`")
_RISKY = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(rules|instructions)|"
    r"curl[^`\n]*\|\s*(ba)?sh|wget[^`\n]*\|\s*(ba)?sh|rm -rf /|--dangerously-skip-permissions|"
    r"(send|upload|post) [^.\n]*(secret|token|password|credential|\.env)|"
    r"\b(always|never)\s+(ask|tell) the user\b)", re.I)


@dataclass
class Finding:
    level: str
    code: str
    memory: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "memory": self.memory, "message": self.message}


def _words(text: str) -> Set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def lint(root: Path, mems: List[Memory]) -> List[Finding]:
    out: List[Finding] = []
    for m in mems:
        if m.path and m.path.stem != m.id:
            out.append(Finding("error", "id-mismatch", m.id, f"file is {m.path.name} but id is {m.id}; rename one to match"))
        if not m.body.strip():
            out.append(Finding("warn", "empty-body", m.id, "no body. Say why this matters, so a reader can judge if it still holds"))
        for a, _ in m.anchors:
            if a.start is not None:
                out.append(Finding("info", "line-range", m.id, f"{a} is a line range; it breaks on edits above it. Prefer path#symbol"))
        for ref in sorted(set(_PATH.findall(m.body))):
            if "://" in ref or ref.startswith("."):
                continue
            if not (root / ref).exists():
                out.append(Finding("warn", "dead-reference", m.id, f"body mentions `{ref}`, which doesn't exist"))
        applied, violated = counts(m)
        if violated >= 2 and violated >= applied:
            out.append(Finding("warn", "often-violated", m.id,
                               f"violated {violated} times vs applied {applied}; it may no longer be true"))
        if m.origin == "agent" and _RISKY.search(f"{m.title}\n{m.body}"):
            out.append(Finding("warn", "instruction-like", m.id,
                               "agent-written memory contains instructions an agent might follow blindly; review it"))

    for i, a in enumerate(mems):
        wa = _words(f"{a.title} {a.body}")
        pa = {x.path for x, _ in a.anchors}
        for b in mems[i + 1:]:
            wb = _words(f"{b.title} {b.body}")
            sim = _jaccard(wa, wb)
            shared = pa & {x.path for x, _ in b.anchors}
            if sim >= 0.8 or _jaccard(_words(a.title), _words(b.title)) >= 0.85:
                out.append(Finding("warn", "duplicate", a.id, f"looks like a duplicate of {b.id}; merge them and retire one"))
            elif shared and sim >= 0.35:
                neg_a, neg_b = bool(_NEG.search(a.title + " " + a.body)), bool(_NEG.search(b.title + " " + b.body))
                if neg_a != neg_b:
                    out.append(Finding("warn", "contradiction", a.id,
                                       f"may contradict {b.id} (both about {', '.join(sorted(shared))}); check which is true"))
    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f.level], f.memory, f.code))
