"""`anchormem seed`: turn the explanations a repo already has in its comments into memories.

Two kinds of comment qualify:
  - tagged ones: NOTE, WHY, HACK, IMPORTANT, WARNING, SAFETY, INVARIANT, CAUTION, NB, in any case
    ("# NOTE: ...", "# Note that ...", "// Important - ...");
  - untagged ones that give a reason ("... because ...", "must go after ...", "never ...",
    "to avoid ...", "otherwise ...") and are long enough to say something.
Both usually state something true and non-obvious about the code right next to them, which is
exactly what a memory is. Each becomes an agent-written, unconfirmed memory anchored to the
function it sits in (or the file). To-dos (TODO, FIXME, XXX), license headers, commented-out
code and linter directives are skipped.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .anchors import Anchor, extract, fingerprint, iter_source_files, read_text
from .codemap import CODE_EXT, enclosing_symbol, inside, list_symbols

TAGS = ("NOTE", "WHY", "HACK", "IMPORTANT", "WARNING", "WARN", "SAFETY", "INVARIANT", "CAUTION", "NB")
HASH_LANGS = {".py", ".pyi", ".rb", ".sh", ".ex", ".exs"}
_MARK = r"(?:#+:?|//+|/\*+|\*(?!/)|--)"
_LINE = re.compile(r"^\s*" + _MARK + r"\s?(?P<text>.*?)\s*(?:\*/)?\s*$")
_TAGGED = re.compile(r"^(?P<tag>" + "|".join(TAGS) + r")\b\s*(?:\([^)]*\))?\s*(?:[:\-–]\s*|that\s+|well[:,]?\s+)(?P<text>.+)$", re.I)
_TASK = re.compile(r"^(?:TODO|FIXME|XXX|TBD|BUG)\b", re.I)
_SKIP = re.compile(r"^(?:!|-\*-|noqa|type:|pragma|pylint:|eslint|prettier|ts-|istanbul|@ts-|flake8|fmt:|isort|mypy|"
                   r"nolint|go:|nosec|coverage|region|endregion|<editor-fold|\$Id)", re.I)
_LICENSE = re.compile(r"\b(copyright|license|licensed|spdx|all rights reserved|warranty)\b", re.I)
_REASON = re.compile(r"\b(because|otherwise|must|never|always|so that|in order to|work ?around|required|careful|"
                     r"don'?t|do not|cannot|can'?t|has to|have to|needs? to|important|note that|unless|instead of|"
                     r"to avoid|to prevent|ensures?|guarantees?|assumes?|relies on|depends on|on purpose|deliberately)\b", re.I)
_CODE = re.compile(r"^\s*(?:def|class|import|from|return|if|elif|else|for|while|try|except|with|print|const|let|var|"
                   r"function|async|await|public|private|func|fn)\b|[;{}]\s*$|^\s*[\w.\[\]'\"]+\s*[-+*/]?=\s*\S|^\s*\w+\(.*\)\s*$")
_NOT_SOURCE = re.compile(r"/(docs?|tests?|spec|specs|examples?|samples?|__tests__|testdata|fixtures)/|/(test_[^/]*|[^/]*_test\.\w+|[^/]*\.(test|spec)\.\w+|conftest\.py|conf\.py)$")
MIN_TAGGED = 12
MIN_REASON = 40
MAX_LINES = 8


@dataclass
class Found:
    path: str
    line: int
    tag: str          # NOTE, WHY, ... or COMMENT for an untagged comment that gives a reason
    text: str
    symbol: Optional[str]

    @property
    def anchor(self) -> Anchor:
        return Anchor(self.path, symbol=self.symbol) if self.symbol else Anchor(self.path)

    @property
    def title(self) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", self.text)
        title = sentences[0]
        for nxt in sentences[1:]:  # "If length exists, set it." alone says too little
            if len(title) >= 40 or len(title) + len(nxt) > 100:
                break
            title = f"{title} {nxt}"
        title = title.rstrip(" .*:;,")
        word = title.split(" ", 1)[0]
        if word.isalpha() and word.islower():  # never capitalise code like prepare_auth or `x`
            title = title[0].upper() + title[1:]
        return title if len(title) <= 100 else title[:97].rstrip() + "…"


def _blocks(lines: List[str], hash_lang: bool):
    """Yield (first line number, [comment lines]) for each run of full-line comments."""
    i = 0
    while i < len(lines):
        m = _LINE.match(lines[i])
        if not m or (not hash_lang and lines[i].lstrip().startswith("#")):
            i += 1
            continue
        start, texts = i, []
        while i < len(lines):
            m = _LINE.match(lines[i])
            if not m or (not hash_lang and lines[i].lstrip().startswith("#")):
                break
            texts.append(m.group("text"))
            i += 1
        yield start + 1, texts


def _split(start: int, texts: List[str]):
    """Split a comment run where a new tag or to-do starts, or at a blank comment line."""
    cur_start, cur = start, []
    for k, t in enumerate(texts):
        t = t.strip()
        if not t or _TAGGED.match(t) or _TASK.match(t):
            if cur:
                yield cur_start, cur
            cur = [t] if t else []
            cur_start = start + k + (0 if t else 1)
            continue
        cur.append(t)
    if cur:
        yield cur_start, cur


def _defined_below(source: str, suffix: str, end: int, gap: int = 3):
    """The function/class that starts within `gap` lines after a comment (the code it introduces)."""
    for name, s_line, _ in list_symbols(source, suffix):
        if end < s_line <= end + gap and "." not in name:
            return name
    return None


def scan(root: Path, paths: Optional[List[str]] = None, reasons: bool = True) -> List[Found]:
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
        suffix = Path(rel).suffix.lower()
        lines = source.splitlines()
        for bstart, texts in _blocks(lines, suffix in HASH_LANGS):
            for start, parts in _split(bstart, texts[:200]):
                parts = parts[:MAX_LINES]
                head = parts[0]
                if _SKIP.match(head) or _TASK.match(head):
                    continue
                tm = _TAGGED.match(head)
                if tm:
                    tag = tm.group("tag").upper()
                    tag = {"WARN": "WARNING", "NB": "NOTE"}.get(tag, tag)
                    text = " ".join([tm.group("text")] + parts[1:])
                    if len(text) < MIN_TAGGED:
                        continue
                else:
                    text = " ".join(parts)
                    if (not reasons or len(text) < MIN_REASON or not _REASON.search(text)
                            or (start <= 30 and _LICENSE.search(text))
                            or sum(1 for p in parts if _CODE.search(p)) * 2 > len(parts)):
                        continue
                    if _NOT_SOURCE.search("/" + rel.lower()):
                        continue  # untagged comments in docs, tests and examples are mostly noise
                    tag = "COMMENT"
                text = re.sub(r"\s+", " ", text).strip().lstrip(":").strip()
                key = (rel, text.lower())
                if key in seen:
                    continue
                seen.add(key)
                end = start + len(parts) - 1
                sym = enclosing_symbol(source, suffix, start) or _defined_below(source, suffix, end)
                if not sym and tag == "COMMENT":
                    continue  # a loose comment anchored to a whole file would be flagged by every edit to it
                if sym:
                    try:
                        extract(root, Anchor(rel, symbol=sym))
                    except Exception:
                        sym = None  # the finder disagrees about this name: anchor the file instead
                out.append(Found(rel, start, tag, text, sym))
    # tagged comments first: they were written to be read
    return sorted(out, key=lambda f: (f.tag == "COMMENT", f.path, f.line))


def fingerprint_of(root: Path, f: Found) -> str:
    return fingerprint(extract(root, f.anchor))
