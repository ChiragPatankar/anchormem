"""Append-only change log at .anchormem/log.md.

One line per change, with a greppable prefix:
    ## [2026-11-10 14:03] verify | pool-size-why | fp 9a525c65 -> 1b3c0e7d @ 6ca7e9a

`.anchormem/.gitattributes` marks the file `merge=union`, so two branches that both
append lines merge without conflicts.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .store import DIR

HEADER = "# anchormem log\n\nAppend-only record of memory changes. Newest at the bottom.\n\n"


def append(root: Path, op: str, mem_id: str, detail: str = "") -> None:
    f = root / DIR / "log.md"
    try:
        new = not f.exists()
        with f.open("a", encoding="utf-8") as fh:
            if new:
                fh.write(HEADER)
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            fh.write(f"## [{stamp}] {op} | {mem_id}" + (f" | {detail}" if detail else "") + "\n")
    except OSError:
        pass  # the log is a convenience; never fail a command because of it
