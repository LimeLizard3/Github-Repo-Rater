"""
Unit tests for cache_index.py. Pure file I/O against a tmp_path-redirected
index file -- no network, no API calls.
"""

from __future__ import annotations

from pathlib import Path

from server.Website import cache_index


def test_lookup_returns_none_when_no_index_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    assert cache_index.lookup("owner", "repo", "abc123") is None


def test_record_then_lookup_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    cache_index.record(
        owner="owner",
        repo="repo",
        sha="abc123",
        generated_at="2026-08-20T12:00:00Z",
        json_path=Path("reports/ratings/owner__repo__20260820_report.json"),
        md_path=Path("reports/ratings/owner__repo__20260820_report.md"),
        pdf_path=Path("reports/ratings/owner__repo__20260820_report.pdf"),
    )

    entry = cache_index.lookup("owner", "repo", "abc123")
    assert entry is not None
    assert entry.owner == "owner"
    assert entry.repo == "repo"
    assert entry.sha == "abc123"
    assert entry.pdf_path == Path("reports/ratings/owner__repo__20260820_report.pdf")


def test_lookup_misses_on_different_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    cache_index.record(
        owner="owner", repo="repo", sha="abc123", generated_at="2026-08-20T12:00:00Z",
        json_path=Path("a.json"), md_path=Path("a.md"), pdf_path=Path("a.pdf"),
    )
    assert cache_index.lookup("owner", "repo", "different_sha") is None


def test_null_pdf_path_round_trips_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    cache_index.record(
        owner="owner", repo="repo", sha="abc123", generated_at="2026-08-20T12:00:00Z",
        json_path=Path("a.json"), md_path=Path("a.md"), pdf_path=None,
    )
    entry = cache_index.lookup("owner", "repo", "abc123")
    assert entry is not None
    assert entry.pdf_path is None


def test_latest_for_repo_picks_most_recent_generated_at(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    cache_index.record(
        owner="owner", repo="repo", sha="old_sha", generated_at="2026-08-19T12:00:00Z",
        json_path=Path("old.json"), md_path=Path("old.md"), pdf_path=Path("old.pdf"),
    )
    cache_index.record(
        owner="owner", repo="repo", sha="new_sha", generated_at="2026-08-20T12:00:00Z",
        json_path=Path("new.json"), md_path=Path("new.md"), pdf_path=Path("new.pdf"),
    )

    latest = cache_index.latest_for_repo("owner", "repo")
    assert latest is not None
    assert latest.sha == "new_sha"


def test_latest_for_repo_returns_none_when_no_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    assert cache_index.latest_for_repo("owner", "nonexistent-repo") is None


def test_latest_for_repo_ignores_other_repos(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    cache_index.record(
        owner="owner", repo="other-repo", sha="sha1", generated_at="2026-08-20T12:00:00Z",
        json_path=Path("a.json"), md_path=Path("a.md"), pdf_path=None,
    )
    assert cache_index.latest_for_repo("owner", "repo") is None
