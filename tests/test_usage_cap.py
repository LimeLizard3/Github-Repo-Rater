"""
Unit tests for usage_cap.py. Runs against an in-memory fake Firestore
(see conftest.py's `fake_firestore` fixture) -- no network, no API calls.

The fake neutralizes the transactional decorator to a pass-through, so
these tests cover the read/check/write LOGIC of the daily cap, not
Firestore's atomicity guarantee (that's a Firestore promise, not something
provable here).
"""

from __future__ import annotations

import pytest

from server.Website import usage_cap

_COLL = "usage_cap"
_DOC = "usage_cap/daily"


@pytest.mark.asyncio
async def test_first_call_starts_at_one(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    assert await usage_cap.check_and_increment() is True
    data = fake_firestore._store[_DOC]
    assert data["count"] == 1
    assert data["date"] == usage_cap._today()


@pytest.mark.asyncio
async def test_allows_up_to_the_limit_then_rejects(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 2)

    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is False  # 3rd call, over the limit of 2


@pytest.mark.asyncio
async def test_rejecting_does_not_increment_further(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 1)

    assert await usage_cap.check_and_increment() is True
    assert await usage_cap.check_and_increment() is False
    assert await usage_cap.check_and_increment() is False
    assert fake_firestore._store[_DOC]["count"] == 1


@pytest.mark.asyncio
async def test_counter_resets_on_a_new_day(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 1)

    fake_firestore._store[_DOC] = {"date": "2020-01-01", "count": 99}

    # Stored date is long in the past -- should reset to today's count of 0,
    # then increment to 1, well under the limit of 1.
    assert await usage_cap.check_and_increment() is True
    data = fake_firestore._store[_DOC]
    assert data["date"] == usage_cap._today()
    assert data["count"] == 1


def test_remaining_today_reflects_usage(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    assert usage_cap.remaining_today() == 5

    fake_firestore._store[_DOC] = {"date": usage_cap._today(), "count": 3}
    assert usage_cap.remaining_today() == 2


def test_remaining_today_full_on_stale_date(fake_firestore, monkeypatch):
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 5)

    fake_firestore._store[_DOC] = {"date": "2020-01-01", "count": 5}
    assert usage_cap.remaining_today() == 5
