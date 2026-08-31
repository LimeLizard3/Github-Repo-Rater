"""
Unit tests for usage_cap.py. Pure file I/O against a tmp_path-redirected
counter file -- no network, no API calls.
"""

from __future__ import annotations

import json

import pytest

from server.Website import usage_cap


@pytest.mark.asyncio
async def test_first_call_starts_at_one(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", tmp_path / "usage_cap.json")
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    assert await usage_cap.check_and_increment() is True
    data = json.loads((tmp_path / "usage_cap.json").read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["date"] == usage_cap._today()


@pytest.mark.asyncio
async def test_allows_up_to_the_limit_then_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", tmp_path / "usage_cap.json")
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 2)

    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is False  # 3rd call, over the limit of 2


@pytest.mark.asyncio
async def test_rejecting_does_not_increment_further(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", tmp_path / "usage_cap.json")
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 1)

    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is False
    assert await usage_cap.check_and_increment() is False
    data = json.loads((tmp_path / "usage_cap.json").read_text(encoding="utf-8"))
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_counter_resets_on_a_new_day(tmp_path, monkeypatch):
    path = tmp_path / "usage_cap.json"
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", path)
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 1)

    path.write_text(json.dumps({"date": "2020-01-01", "count": 99}), encoding="utf-8")

    # Stored date is long in the past -- should reset to today's count of 0,
    # then increment to 1, well under the limit of 1.
    assert await usage_cap.check_and_increment() is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["date"] == usage_cap._today()
    assert data["count"] == 1


def test_remaining_today_reflects_usage(tmp_path, monkeypatch):
    path = tmp_path / "usage_cap.json"
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", path)
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    assert usage_cap.remaining_today() == 5

    path.write_text(json.dumps({"date": usage_cap._today(), "count": 3}), encoding="utf-8")
    assert usage_cap.remaining_today() == 2


def test_remaining_today_full_on_stale_date(tmp_path, monkeypatch):
    path = tmp_path / "usage_cap.json"
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", path)
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    path.write_text(json.dumps({"date": "2020-01-01", "count": 5}), encoding="utf-8")
    assert usage_cap.remaining_today() == 5
