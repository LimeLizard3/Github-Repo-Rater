"""
Shared test fixtures.

`fake_firestore` -- an in-memory stand-in for the narrow slice of the
Firestore API that cache_index.py / usage_cap.py actually use (Phase 5 of
the Cloud Run migration moved both off local JSON files onto Firestore).
Keeps those unit tests network-free and fast, same as the tmp-file
redirection did before.

It models the read/check/write LOGIC, not Firestore's real atomicity
guarantee -- the transactional decorator is neutralized to a pass-through,
since "the transaction is actually atomic across instances" is a Firestore
promise, not something a unit test here should try to prove.
"""

from __future__ import annotations

import pytest

from server.Website import cache_index, usage_cap


class _FakeSnapshot:
    def __init__(self, data: dict | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    def __init__(self, store: dict, key: str) -> None:
        self._store = store
        self._key = key

    def get(self, transaction=None) -> _FakeSnapshot:
        return _FakeSnapshot(self._store.get(self._key))

    def set(self, data: dict) -> None:
        self._store[self._key] = dict(data)


class _FakeQuery:
    def __init__(self, store: dict, coll: str, predicates: list) -> None:
        self._store = store
        self._coll = coll
        self._predicates = predicates  # list of (field, op, value)

    def where(self, filter=None) -> "_FakeQuery":
        return _FakeQuery(
            self._store,
            self._coll,
            self._predicates + [(filter.field_path, filter.op_string, filter.value)],
        )

    def stream(self):
        prefix = self._coll + "/"
        for key, data in self._store.items():
            if not key.startswith(prefix):
                continue
            if all(self._match(data, f, op, v) for (f, op, v) in self._predicates):
                yield _FakeSnapshot(data)

    @staticmethod
    def _match(data: dict, field: str, op: str, value) -> bool:
        if op == "==":
            return data.get(field) == value
        raise NotImplementedError(f"fake Firestore query op not supported: {op!r}")


class _FakeCollection:
    def __init__(self, store: dict, name: str) -> None:
        self._store = store
        self._name = name

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, f"{self._name}/{doc_id}")

    def where(self, filter=None) -> _FakeQuery:
        return _FakeQuery(self._store, self._name, []).where(filter=filter)

    def stream(self):
        return _FakeQuery(self._store, self._name, []).stream()


class _FakeTransaction:
    def set(self, doc_ref: _FakeDocRef, data: dict) -> None:
        doc_ref.set(data)


class FakeFirestore:
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}  # "collection/doc_id" -> fields

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store, name)

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


@pytest.fixture
def fake_firestore(monkeypatch) -> FakeFirestore:
    fake = FakeFirestore()
    monkeypatch.setattr(cache_index, "_get_db", lambda: fake)
    monkeypatch.setattr(usage_cap, "_get_db", lambda: fake)
    monkeypatch.setattr(usage_cap.firestore, "transactional", lambda fn: fn)
    return fake
