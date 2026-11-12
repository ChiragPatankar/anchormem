"""A light map of a repo's code: which files exist and which functions/classes they define.

Used to draw the code behind the memories on the dashboard map, and by `anchormem seed` to
find the function a comment sits in. It reads files only; nothing is executed.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .anchors import MAX_FILE_BYTES, _block_end, iter_source_files, read_text

CODE_EXT = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".kts",
            ".cs", ".swift", ".rb", ".php", ".scala", ".c", ".h", ".cc", ".cpp", ".hpp", ".m", ".dart", ".lua",
            ".sh", ".ex", ".exs", ".vue", ".svelte"}

# top-level definitions in brace / keyword languages; group "n" is the name
_GENERIC = [re.compile(p) for p in (
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<n>[A-Za-z_$][\w$]*)",
    r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<n>[A-Za-z_$][\w$]*)",
    r"^(?:export\s+)?(?:const|let|var)\s+(?P<n>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    r"^(?:export\s+)?(?:declare\s+)?(?:interface|enum)\s+(?P<n>[A-Za-z_$][\w$]*)",
    r"^func\s+(?:\([^)]*\)\s*)?(?P<n>[A-Za-z_]\w*)",
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<n>[A-Za-z_]\w*)",
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+(?P<n>[A-Za-z_]\w*)",
    r"^(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|final\s+|abstract\s+|sealed\s+|partial\s+)*"
    r"(?:class|interface|record|object)\s+(?P<n>[A-Za-z_]\w*)",
    r"^def\s+(?P<n>[A-Za-z_]\w*[?!]?)",
)]

Symbol = Tuple[str, int, int]  # qualified name, first line, last line (1-based)


def list_symbols(source: str, suffix: str) -> List[Symbol]:
    if suffix in (".py", ".pyi"):
        try:
            return _python_symbols(ast.parse(source))
        except (SyntaxError, ValueError):
            pass
    return _generic_symbols(source.splitlines())


def _python_symbols(tree: ast.Module) -> List[Symbol]:
    out: List[Symbol] = []

    def walk(body, prefix: str, depth: int) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                out.append((prefix + node.name, start, node.end_lineno or node.lineno))
                if isinstance(node, ast.ClassDef) and depth == 0:
                    walk(node.body, prefix + node.name + ".", depth + 1)
    walk(tree.body, "", 0)
    return out


def _generic_symbols(lines: List[str]) -> List[Symbol]:
    out: List[Symbol] = []
    seen = set()
    for i, line in enumerate(lines):
        if not line or line[0] in " \t":
            continue  # top level only: indented definitions are too noisy without a parser
        for pat in _GENERIC:
            m = pat.match(line)
            if m and m.group("n") not in seen:
                seen.add(m.group("n"))
                try:
                    end = _block_end(lines, i, len(lines)) + 1
                except Exception:
                    end = i + 1
                out.append((m.group("n"), i + 1, max(end, i + 1)))
                break
    return out


def enclosing_symbol(source: str, suffix: str, line: int) -> Optional[str]:
    """The innermost function/class whose definition contains `line` (1-based)."""
    best = None
    for name, start, end in list_symbols(source, suffix):
        if start <= line <= end and (best is None or end - start < best[2] - best[1]):
            best = (name, start, end)
    return best[0] if best else None


def inside(root: Path, full: Path) -> bool:
    """False for a symlink that points outside the repo: never read files from elsewhere."""
    try:
        return os.path.commonpath([str(full.resolve()), str(root.resolve())]) == str(root.resolve())
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------- skeleton

_CACHE: Dict[str, Tuple[float, int, List[Symbol]]] = {}


def symbols_for(root: Path, rel: str) -> List[Symbol]:
    full = root / rel
    if not inside(root, full):
        return []
    try:
        st = full.stat()
    except OSError:
        return []
    key = str(full)
    hit = _CACHE.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    syms = list_symbols(read_text(full), full.suffix) if st.st_size <= MAX_FILE_BYTES else []
    _CACHE[key] = (st.st_mtime, st.st_size, syms)
    return syms


def _is_test(path: str) -> bool:
    p = "/" + path.lower()
    return "/test" in p or "/spec" in p or ".test." in p or ".spec." in p or p.rsplit("/", 1)[-1].startswith("test_")


def skeleton(root: Path, keep: List[str] = (), max_files: int = 40, max_symbols: int = 6) -> List[Tuple[str, List[str]]]:
    """(file, [symbol names]) for the repo's code files: files in `keep` first, then the files
    that define the most symbols. Capped so a big repo still draws quickly."""
    files = [p for p in iter_source_files(root) if os.path.splitext(p)[1].lower() in CODE_EXT]
    keep = [k for k in dict.fromkeys(keep) if k in set(files)]
    rest = sorted((p for p in files if p not in keep), key=lambda p: (_is_test(p), -len(symbols_for(root, p)), p))
    chosen = keep + [p for p in rest if symbols_for(root, p)][:max(0, max_files - len(keep))]
    out = []
    for p in chosen:
        names = [n for n, _, _ in symbols_for(root, p)]
        top = sorted(names, key=lambda n: ("." in n, n.split(".")[-1].startswith("_")))  # public top-level first
        out.append((p, top[:max_symbols]))
    return out
