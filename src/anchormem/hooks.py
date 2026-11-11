"""Claude Code hooks: deliver memories to the agent without it having to ask.

- SessionStart: a short summary of the repo's memories, listing any that went stale.
- PostToolUse (Read, Edit, Write, MultiEdit): the memories anchored to the file the agent
  just touched, once per session per file (again only if their status changes).

Hooks must never break the agent's session, so every failure is silent and exits 0.
Output uses Claude Code's `hookSpecificOutput.additionalContext` JSON.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import render, store
from .core import check_memory

SESSION_BUDGET = 2500
FILE_BUDGET = 3000
FILE_TOOLS = "Read|Edit|Write|MultiEdit|NotebookEdit"
TAG = "anchormem hook"


# ---------------------------------------------------------------- running hooks

def _payload() -> dict:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except (ValueError, OSError):
        return {}


def _emit(event: str, text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def run(kind: str) -> int:
    payload = _payload()
    try:
        if kind == "session-start":
            text = session_start_text(payload)
            event = "SessionStart"
        elif kind == "post-tool":
            text = post_tool_text(payload)
            event = "PostToolUse"
        else:
            return 0
        if text:
            _emit(event, text)
    except Exception:  # noqa: BLE001 - a hook must never take the session down
        return 0
    return 0


def _root(payload: dict) -> Optional[Path]:
    return store.find_root(Path(payload.get("cwd") or Path.cwd()))


def session_start_text(payload: dict) -> str:
    root = _root(payload)
    if root is None:
        return ""
    _cleanup_state(root)
    mems = store.load_all(root)
    if not mems:
        return ""
    results = [check_memory(root, m) for m in mems]
    stale = [r for r in results if not r.ok]
    unconfirmed = sum(1 for m in mems if m.unconfirmed_agent)
    def n(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"

    lines = [f"anchormem: this repo keeps {n(len(mems), 'memory', 'memories')} about its code in .anchormem/memories/. "
             "The ones about a file are shown to you automatically when you read or edit it."]
    if stale:
        lines.append(f"{n(len(stale), 'memory', 'memories')} may be out of date because the code "
                     f"{'it describes' if len(stale) == 1 else 'they describe'} changed:")
        shown = 0
        for r in render.order(stale):
            line = f"- {r.memory.id} ({r.status}): {r.memory.title} [{', '.join(str(a.anchor) for a in r.anchors if not a.ok)}]"
            if sum(len(x) + 1 for x in lines) + len(line) > SESSION_BUDGET - 300:
                break
            lines.append(line)
            shown += 1
        if shown < len(stale):
            lines.append(f"- …and {len(stale) - shown} more. Run `anchormem check` for the full list.")
        lines.append("Before relying on a stale memory, check it against the code. If it is still true run "
                     "`anchormem verify <id>`; if it moved run `anchormem verify <id> --follow`; if it is wrong, "
                     "edit it or run `anchormem retire <id> --reason \"...\"`.")
    else:
        lines.append("It matches the current code." if len(mems) == 1 else "All of them match the current code.")
    if unconfirmed:
        lines.append(f"{n(unconfirmed, 'memory was', 'memories were')} written by an agent and not yet confirmed "
                     f"by a person; treat {'it' if unconfirmed == 1 else 'them'} as notes, not rules.")
    lines.append('To save something non-obvious you learn: anchormem add "<title>" --anchor <path>#<symbol> --body "<why>" --by agent')
    return "\n".join(lines)


def post_tool_text(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return ""
    root = _root(payload)
    if root is None:
        return ""
    try:
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""
    if rel.startswith(store.DIR + "/"):
        return ""
    mems = [m for m in store.load_all(root) if any(a.path == rel for a, _ in m.anchors)]
    if not mems:
        return ""
    results = [check_memory(root, m) for m in mems]
    signature = sorted(f"{r.memory.id}:{r.status}" for r in results)
    if not _first_time(root, payload.get("session_id") or "", rel, signature):
        return ""
    header = f"anchormem: {len(results)} memor{'y' if len(results) == 1 else 'ies'} about {rel} (from .anchormem/memories/)"
    return render.memories(results, header, budget=FILE_BUDGET, more_cmd=f"anchormem context {rel}")


def _state_file(root: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:80] or "default"
    return root / store.STATE_DIR / f"hooks-{safe}.json"


def _first_time(root: Path, session_id: str, rel: str, signature: List[str]) -> bool:
    f = _state_file(root, session_id)
    try:
        seen: Dict[str, List[str]] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (ValueError, OSError):
        seen = {}
    if seen.get(rel) == signature:
        return False
    seen[rel] = signature
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass
    return True


def _cleanup_state(root: Path, max_age_days: int = 7) -> None:
    d = root / store.STATE_DIR
    if not d.is_dir():
        return
    cutoff = time.time() - max_age_days * 86400
    for f in d.glob("hooks-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- installing hooks

def command_prefix() -> str:
    if shutil.which("anchormem"):
        return "anchormem"
    exe = sys.executable.replace("\\", "/")
    return f'"{exe}" -m anchormem'


def settings_path(root: Path, local: bool) -> Path:
    return root / ".claude" / ("settings.local.json" if local else "settings.json")


def _strip(groups: list) -> list:
    out = []
    for g in groups:
        if not isinstance(g, dict):
            out.append(g)
            continue
        hooks = [h for h in g.get("hooks", []) if not (isinstance(h, dict) and TAG in str(h.get("command", "")))]
        if hooks:
            out.append(dict(g, hooks=hooks))
    return out


def plan_settings(existing: dict, remove: bool = False, prefix: Optional[str] = None) -> dict:
    data = json.loads(json.dumps(existing))  # deep copy
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    prefix = prefix or command_prefix()
    wanted: List[Tuple[str, Optional[str], str]] = [("SessionStart", None, "session-start"),
                                                    ("PostToolUse", FILE_TOOLS, "post-tool")]
    for event, matcher, kind in wanted:
        groups = _strip(hooks.get(event, []))
        if not remove:
            group = {"hooks": [{"type": "command", "command": f"{prefix} hook {kind}", "timeout": 10}]}
            if matcher:
                group = {"matcher": matcher, **group}
            groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return data


def installed(root: Path) -> List[Path]:
    found = []
    for local in (False, True):
        f = settings_path(root, local)
        try:
            if f.exists() and "hook session-start" in f.read_text(encoding="utf-8"):
                found.append(f)
        except OSError:
            pass
    return found
