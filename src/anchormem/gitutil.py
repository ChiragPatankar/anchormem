"""Small, optional git helpers. Everything works without git; git just adds detail."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def _git(root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def is_ignored(root: Path, rel: str) -> Optional[bool]:
    """True/False if git says the path is ignored; None when this isn't a git repo."""
    if toplevel(root) is None:
        return None
    return _git(root, "check-ignore", "-q", rel) is not None


def toplevel(start: Path) -> Optional[Path]:
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def head(root: Path) -> Optional[str]:
    out = _git(root, "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def commit_exists(root: Path, commit: str) -> bool:
    return _git(root, "cat-file", "-e", f"{commit}^{{commit}}") is not None


def changed_files(root: Path, limit: int = 30) -> List[str]:
    """Files with uncommitted changes (modified, added, renamed, deleted, untracked), outside .anchormem/."""
    out = _git(root, "status", "--porcelain", "-z", "-uall")
    if not out:
        return []
    entries = out.split("\0")
    files: List[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            i += 1  # the next entry is the old path
        if not path.startswith(".anchormem/") and path not in files:
            files.append(path)
        if len(files) >= limit:
            break
    return files


def renamed_to(root: Path, commit: Optional[str], path: str) -> List[str]:
    """Where `path` went since `commit`, using git's rename detection (committed and staged changes)."""
    if not commit or not commit_exists(root, commit):
        return []
    hits: List[str] = []
    for args in (("diff", "--name-status", "-M", commit, "HEAD"), ("diff", "--name-status", "-M", "--cached", commit)):
        out = _git(root, *args)
        for line in (out or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].startswith("R") and parts[1] == path and parts[2] not in hits:
                hits.append(parts[2])
    return hits
