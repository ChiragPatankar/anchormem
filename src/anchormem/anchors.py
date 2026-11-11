"""Anchors: pointers from a memory to the code it describes.

Anchor syntax (always relative to the repo root):
    path/to/file.py                 the whole file
    path/to/file.py#name            a function, class, constant or method
    path/to/file.py#Class.method    a nested symbol
    path/to/file.py:10-20           a line range (also :L10-L20, or a single :10)

A fingerprint is a short hash of the anchored code with all whitespace removed,
so reformatting or re-indenting does not count as a change.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

SKIP_DIRS = {".git", ".anchormem", "node_modules", ".venv", "venv", "__pycache__",
             "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "target"}
MAX_FILE_BYTES = 1_000_000


class FileMissing(Exception):
    """The anchored file does not exist."""


class TargetMissing(Exception):
    """The file exists but the symbol or line range is not in it."""


@dataclass(frozen=True)
class Anchor:
    path: str
    symbol: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None

    @classmethod
    def parse(cls, text: str) -> "Anchor":
        text = text.strip()
        if not text:
            raise ValueError("empty anchor")
        if "#" in text:
            path, symbol = text.split("#", 1)
            if not path or not symbol or not re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*", symbol):
                raise ValueError(f"bad symbol anchor: {text!r} (expected path#name or path#Class.method)")
            return cls(_clean(path), symbol=symbol)
        m = re.fullmatch(r"(.+?):L?(\d+)(?:-L?(\d+))?", text)
        if m:
            start, end = int(m.group(2)), int(m.group(3) or m.group(2))
            if start < 1 or end < start:
                raise ValueError(f"bad line range in anchor: {text!r}")
            return cls(_clean(m.group(1)), start=start, end=end)
        return cls(_clean(text))

    def with_path(self, path: str) -> "Anchor":
        return Anchor(_clean(path), self.symbol, self.start, self.end)

    def __str__(self) -> str:
        if self.symbol:
            return f"{self.path}#{self.symbol}"
        if self.start is not None:
            return f"{self.path}:{self.start}" if self.start == self.end else f"{self.path}:{self.start}-{self.end}"
        return self.path


def _clean(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def fingerprint(text: str) -> str:
    # drop all whitespace so formatter runs (re-indenting, `a=1` -> `a = 1`) don't count as changes
    norm = re.sub(r"\s+", "", text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def read_text(file: Path) -> str:
    return file.read_text(encoding="utf-8", errors="replace")


def extract(root: Path, anchor: Anchor) -> str:
    """Return the anchored source text, or raise FileMissing / TargetMissing."""
    file = root / anchor.path
    if not file.is_file():
        raise FileMissing(anchor.path)
    source = read_text(file)
    if anchor.symbol:
        span = find_symbol(source, anchor.symbol, file.suffix)
        if span is None:
            raise TargetMissing(f"{anchor.symbol} not found in {anchor.path}")
        lines = source.splitlines()
        return "\n".join(lines[span[0] - 1:span[1]])
    if anchor.start is not None:
        lines = source.splitlines()
        if anchor.end > len(lines):
            raise TargetMissing(f"{anchor.path} has {len(lines)} lines, anchor wants {anchor.start}-{anchor.end}")
        return "\n".join(lines[anchor.start - 1:anchor.end])
    return source


# ---------------------------------------------------------------- symbols

def find_symbol(source: str, symbol: str, suffix: str = "") -> Optional[Tuple[int, int]]:
    """Return the 1-based (start, end) line span of a symbol's definition."""
    if suffix in (".py", ".pyi"):
        try:
            return _python_symbol(ast.parse(source), symbol)
        except SyntaxError:
            pass  # fall through to the text-based finder
    return _generic_symbol(source.splitlines(), symbol)


def _python_symbol(tree: ast.Module, symbol: str) -> Optional[Tuple[int, int]]:
    parts = symbol.split(".")
    body: List[ast.stmt] = tree.body
    found = None
    for i, part in enumerate(parts):
        found = None
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part:
                found = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == part for t in targets):
                    found = node
            if found is not None:
                break
        if found is None:
            return None
        if i < len(parts) - 1:
            if not isinstance(found, ast.ClassDef):
                return None
            body = found.body
    decorators = getattr(found, "decorator_list", [])
    start = min([found.lineno] + [d.lineno for d in decorators])
    return start, found.end_lineno or found.lineno


_DEF_PATTERNS = [
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*{n}\b",
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+{n}\b",
    r"^\s*(?:export\s+)?(?:const|let|var)\s+{n}\b",
    r"^\s*(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+{n}\b",
    r"^\s*(?:async\s+)?def\s+{n}\b",
    r"^\s*func\s+(?:\([^)]*\)\s*)?{n}\b",
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+{n}\b",
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type|mod)\s+{n}\b",
    r"^\s*impl(?:<[^>]*>)?\s+{n}\b",
    r"^\s*(?:(?:public|private|protected|internal|static|async|override|readonly|final|virtual|abstract)\s+)*"
    r"(?:[\w<>\[\],]+\s+)?{n}\s*\([^;]*$",
]


def _generic_symbol(lines: List[str], symbol: str) -> Optional[Tuple[int, int]]:
    lo, hi = 0, len(lines)
    span = None
    for part in symbol.split("."):
        pats = [re.compile(p.replace("{n}", re.escape(part))) for p in _DEF_PATTERNS]
        span = None
        for i in range(lo, hi):
            if any(p.search(lines[i]) for p in pats):
                span = (i + 1, _block_end(lines, i, hi) + 1)
                break
        if span is None:
            return None
        lo, hi = span[0], span[1]  # search the next part inside this block (skipping its first line)
    return span


def _block_end(lines: List[str], i: int, hi: int) -> int:
    """0-based index of the last line of the block that starts at line i."""
    # brace languages: match braces from the first '{' (if it comes before a ';')
    depth, opened, quote = 0, False, None
    for j in range(i, hi):
        line = lines[j]
        k = 0
        while k < len(line):
            ch = line[k]
            if quote:
                if ch == "\\":
                    k += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif line.startswith("//", k):
                break
            elif ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return j
            elif ch == ";" and not opened and depth == 0:
                return j
            k += 1
        if quote == "`":
            continue  # template strings may span lines
        quote = None
        if not opened and j == i and lines[j].rstrip().endswith(":"):
            return _indent_end(lines, i, hi)
        if not opened and j - i >= 3:
            return i  # no block found nearby; anchor the single line
    return hi - 1 if opened else i


def _indent_end(lines: List[str], i: int, hi: int) -> int:
    base = len(lines[i]) - len(lines[i].lstrip())
    last = i
    for j in range(i + 1, hi):
        if not lines[j].strip():
            continue
        if len(lines[j]) - len(lines[j].lstrip()) <= base:
            break
        last = j
    return last


# ---------------------------------------------------------------- searching the repo

def iter_source_files(root: Path, suffix: Optional[str] = None) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if suffix and not name.endswith(suffix):
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield full.relative_to(root).as_posix()


def find_symbol_elsewhere(root: Path, anchor: Anchor, limit: int = 3) -> List[str]:
    """Other files (same extension) that define the anchor's symbol."""
    if not anchor.symbol:
        return []
    suffix = Path(anchor.path).suffix or None
    hits = []
    for rel in iter_source_files(root, suffix):
        if rel == anchor.path:
            continue
        try:
            if find_symbol(read_text(root / rel), anchor.symbol, Path(rel).suffix):
                hits.append(rel)
        except (OSError, RecursionError):
            continue
        if len(hits) >= limit:
            break
    return hits


def find_file_by_fingerprint(root: Path, anchor: Anchor, fp: str) -> List[str]:
    """Files whose whole content still matches a whole-file anchor's fingerprint."""
    suffix = Path(anchor.path).suffix or None
    hits = []
    for rel in iter_source_files(root, suffix):
        try:
            if fingerprint(read_text(root / rel)) == fp:
                hits.append(rel)
        except OSError:
            continue
    return hits
