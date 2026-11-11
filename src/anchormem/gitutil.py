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


def toplevel(start: Path) -> Optional[Path]:
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def head(root: Path) -> Optional[str]:
    out = _git(root, "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def commit_exists(root: Path, commit: str) -> bool:
    return _git(root, "cat-file", "-e", f"{commit}^{{commit}}") is not None


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
