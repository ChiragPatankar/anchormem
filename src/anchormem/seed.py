"""`anchormem seed`: turn the explanations a repo already has in its comments into memories.

Comments tagged NOTE, WHY, HACK, IMPORTANT, WARNING, SAFETY, INVARIANT or CAUTION usually say
something true and non-obvious about the code right next to them, which is exactly what a
memory is. Each one becomes an agent-written, unconfirmed memory anchored to the function it
sits in (or the file). TODO and FIXME are skipped: they are tasks, not facts.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .anchors import Anchor, extract, fingerprint, iter_source_files, read_text
from .codemap import CODE_EXT, enclosing_symbol, inside

TAGS = ("NOTE", "WHY", "HACK", "IMPORTANT", "WARNING", "WARN", "SAFETY", "INVARIANT", "CAUTION")
_START = re.compile(r"^\s*(?P<mark>#+|//+|/\*+|\*|--|;+)\s*(?P<tag>" + "|".join(TAGS) + r")\b\s*(?:\([^)]*\))?\s*[:\-]\s*(?P<text>.+?)\s*(?:\*/)?\s*$")
_CONT = re.compile(r"^\s*(?:#+|//+|\*(?!/)|--|;+)\s?(?P<text>.*?)\s*(?:\*/)?\s*$")
_ANY_TAG = re.compile(r"^\s*(?:#+|//+|/\*+|\*|--|;+)\s*(?:TODO|FIXME|" + "|".join(TAGS) + r")\b")
MIN_LEN = 12


@dataclass
class Found:
    path: str
    line: int
    tag: str
    text: str
    symbol: Optional[str]

    @property
    def anchor(self) -> Anchor:
        return Anchor(self.path, symbol=self.symbol) if self.symbol else Anchor(self.path)

    @property
    def title(self) -> str:
        first = re.split(r"(?<=[.!?])\s", self.text, maxsplit=1)[0].rstrip(".")
        return first if len(first) <= 100 else first[:97].rstrip() + "…"


def scan(root: Path, paths: Optional[List[str]] = None) -> List[Found]:
    files = paths or [p for p in iter_source_files(root) if os.path.splitext(p)[1].lower() in CODE_EXT]
    out: List[Found] = []
    seen = set()
    for rel in files:
        if not inside(root, root / rel):
            continue
        try:
            source = read_text(root / rel)
        except OSError:
            continue
        if not any(t in source for t in TAGS):
            continue
        lines = source.splitlines()
        suffix = Path(rel).suffix.lower()
        i = 0
        while i < len(lines):
            m = _START.match(lines[i])
            if not m:
                i += 1
                continue
            text = [m.group("text")]
            j = i + 1
            while j < len(lines) and j - i <= 8:
                if _ANY_TAG.match(lines[j]) or lines[j].strip().startswith(("/*", "*/")):
                    break
                c = _CONT.match(lines[j])
                if not c or not c.group("text"):
                    break
                text.append(c.group("text"))
                j += 1
            body = " ".join(t.strip() for t in text).strip()
            key = (rel, body.lower())
            if len(body) >= MIN_LEN and key not in seen:
                seen.add(key)
                sym = enclosing_symbol(source, suffix, i + 1)
                if sym:
                    try:
                        extract(root, Anchor(rel, symbol=sym))
                    except Exception:
                        sym = None  # the finder disagrees about this name: anchor the file instead
                out.append(Found(rel, i + 1, {"WARN": "WARNING"}.get(m.group("tag"), m.group("tag")),
                                 body, sym))
            i = j
    return out


def fingerprint_of(root: Path, f: Found) -> str:
    return fingerprint(extract(root, f.anchor))
