"""
Usage cap: one global counter, resets daily, caps full-pipeline runs
(cache misses only -- a cache hit never reaches this check). Sized for a
personal project on a small Anthropic budget, not production infra --
deliberately not per-IP, not a queue.

Phase 5 of the Cloud Run migration (CLOUD_RUN_MIGRATION_PLAN.md): moved
from a local `usage_cap.json` file to a single Firestore document
(`usage_cap/daily`). check_and_increment() is now a Firestore transaction:
read the count, check the limit, write count+1 -- one atomic operation
enforced by Firestore's servers. That replaces the old asyncio.Lock(),
which only worked within a single process; the transaction stays correct
even once multiple Cloud Run instances run concurrently (Phase 7).

The Firestore calls are synchronous. check_and_increment() keeps its
`async def` signature only because web_app.py awaits it -- the body does no
actual awaiting, and a brief blocking round-trip here is negligible next to
the multi-second pipeline run a cache miss is about to trigger anyway.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google.cloud import firestore

_COLLECTION = "usage_cap"
_DOC_ID = "daily" #Single ID works here as usage_cap only ever has one document, unlike cache_index
_db: firestore.Client | None = None

DAILY_LIMIT = 5


def _get_db() -> firestore.Client:
    """Lazily-created module singleton -- lazy so tests can monkeypatch this
    seam and so import never opens a connection. Project id auto-detected
    (GOOGLE_CLOUD_PROJECT locally, metadata server on Cloud Run)."""
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _doc_ref():
    return _get_db().collection(_COLLECTION).document(_DOC_ID)


async def check_and_increment() -> bool:
    """Returns True and increments if under DAILY_LIMIT for today, False (no
    increment) if at/over it. The counter resets when the stored date isn't
    today. Atomic across concurrent callers -- and across instances -- via a
    Firestore transaction, so no external lock is needed for correctness."""
    doc_ref = _doc_ref()
    transaction = _get_db().transaction()

    @firestore.transactional
    def _run(txn) -> bool: #Nested so that it can see doc_ref automatically (this is called closure); also keeps private stuff private
                           #Also, because of this every tie c_&_i is run, a new _run is called, allowing for 2 requests to run separately parallelly
        snap = doc_ref.get(transaction=txn)
        data = snap.to_dict() if snap.exists else None
        if not data or data.get("date") != _today():
            data = {"date": _today(), "count": 0}
        if data["count"] >= DAILY_LIMIT:
            txn.set(doc_ref, data)
            return False
        data["count"] += 1
        txn.set(doc_ref, data)
        return True

    return _run(transaction)


def remaining_today() -> int:
    snap = _doc_ref().get()
    data = snap.to_dict() if snap.exists else None
    if not data or data.get("date") != _today():
        return DAILY_LIMIT
    return max(0, DAILY_LIMIT - data["count"])
