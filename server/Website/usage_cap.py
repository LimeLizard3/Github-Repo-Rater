"""
Phase 7 usage cap: one global counter, resets daily, caps full-pipeline
runs (cache misses only -- a cache hit never reaches this check). Sized
for a personal project on a small Anthropic budget, not production
infrastructure -- deliberately not per-IP, not a queue, see PHASE7_BRIEF.pdf.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from server.Report_Writing.report_writer import REPORTS_DIR

USAGE_CAP_PATH = REPORTS_DIR / "usage_cap.json"

DAILY_LIMIT = 5


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read() -> dict[str, Any]:
    if not USAGE_CAP_PATH.exists():
        return {"date": _today(), "count": 0}
    return json.loads(USAGE_CAP_PATH.read_text(encoding="utf-8"))


def _write(data: dict[str, Any]) -> None:
    USAGE_CAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_CAP_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def check_and_increment() -> bool:
    """Resets the counter if the stored date isn't today. Returns True and
    increments if under DAILY_LIMIT, False (no increment) if at/over it.
    Caller is expected to hold a shared lock around this -- not atomic
    against a concurrent caller on its own."""
    data = _read()
    if data["date"] != _today():
        data = {"date": _today(), "count": 0}
    if data["count"] >= DAILY_LIMIT:
        _write(data)
        return False
    data["count"] += 1
    _write(data)
    return True


def remaining_today() -> int:
    data = _read()
    if data["date"] != _today():
        return DAILY_LIMIT
    return max(0, DAILY_LIMIT - data["count"])
