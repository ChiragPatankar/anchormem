"""Confidence from evidence: how often a memory held up when an agent relied on it.

Agents report `anchormem feedback <id> applied|violated|outdated`. The score is the
Wilson 95% lower bound on applied / (applied + violated), so a memory needs several
good reports before it scores high, multiplied by a freshness factor that falls
linearly to 0 over FRESH_DAYS since the last evidence or verification.

"outdated" doesn't change the score: it flags the memory, and `check` treats it as
stale until someone runs `verify`.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional, Tuple

from .store import Memory

FRESH_DAYS = 180
KINDS = ("applied", "violated", "outdated")


def counts(m: Memory) -> Tuple[int, int]:
    def num(key: str) -> int:
        try:
            return max(0, int(m.extra.get(key, "0") or 0))
        except ValueError:
            return 0
    return num("applied"), num("violated")


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def _date(text: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def score(m: Memory, today: Optional[dt.date] = None) -> Optional[float]:
    applied, violated = counts(m)
    if applied + violated == 0:
        return None
    today = today or dt.date.today()
    dates = [d for d in (_date(m.extra.get("last_evidence", "")), _date(m.verified)) if d]
    age = (today - max(dates)).days if dates else FRESH_DAYS
    fresh = max(0.0, 1 - max(0, age) / FRESH_DAYS)
    return wilson_lower(applied, applied + violated) * fresh


def band(s: Optional[float]) -> str:
    if s is None:
        return "no evidence"
    return "low" if s < 0.3 else "medium" if s < 0.6 else "high"


def describe(m: Memory) -> str:
    applied, violated = counts(m)
    if applied + violated == 0:
        return ""
    return f"confidence {band(score(m))} ({applied} applied, {violated} violated)"


def record(m: Memory, kind: str, when: str, note: str = "") -> None:
    if kind not in KINDS:
        raise ValueError(f"feedback must be one of {', '.join(KINDS)}")
    m.extra["last_evidence"] = when
    if kind == "outdated":
        m.extra["flagged"] = f"{when}: {note}" if note else f"{when}: reported outdated"
        return
    applied, violated = counts(m)
    m.extra["applied"] = str(applied + (kind == "applied"))
    m.extra["violated"] = str(violated + (kind == "violated"))
