"""
Unit tests for cache_index.py. Runs against an in-memory fake Firestore
(see conftest.py's `fake_firestore` fixture) -- no network, no API calls,
same speed as the old tmp-file version.

Phase 6: json_path/md_path/pdf_path are now plain strings (Cloud Storage
object names), not local Path objects -- record()/CacheEntry reflect that.
"""

from __future__ import annotations

from server.Website import cache_index


def test_lookup_returns_none_when_empty(fake_firestore):
    assert cache_index.lookup("owner", "repo", "abc123") is None


def test_record_then_lookup_round_trips(fake_firestore):
    cache_index.record(
        owner="owner",
        repo="repo",
        sha="abc123",
        generated_at="2026-08-20T12:00:00Z",
        json_path="owner__repo__20260820_report.json",
        md_path="owner__repo__20260820_report.md",
        pdf_path="owner__repo__20260820_report.pdf",
    )

    entry = cache_index.lookup("owner", "repo", "abc123")
    assert entry is not None
    assert entry.owner == "owner"
    assert entry.repo == "repo"
    assert entry.sha == "abc123"
    assert entry.pdf_path == "owner__repo__20260820_report.pdf"


def test_lookup_misses_on_different_sha(fake_firestore):
    cache_index.record(
        owner="owner", repo="repo", sha="abc123", generated_at="2026-08-20T12:00:00Z",
        json_path="a.json", md_path="a.md", pdf_path="a.pdf",
    )
    assert cache_index.lookup("owner", "repo", "different_sha") is None


def test_doc_id_sanitizes_the_slash(fake_firestore):
    # The old on-disk key was f"{owner}/{repo}@{sha}"; Firestore doc IDs
    # can't contain "/", so it must not appear in the key.
    key = cache_index._doc_id("owner", "repo", "abc123")
    assert "/" not in key
    assert key == "owner__repo__abc123"


def test_null_pdf_path_round_trips_as_none(fake_firestore):
    cache_index.record(
        owner="owner", repo="repo", sha="abc123", generated_at="2026-08-20T12:00:00Z",
        json_path="a.json", md_path="a.md", pdf_path=None,
    )
    entry = cache_index.lookup("owner", "repo", "abc123")
    assert entry is not None
    assert entry.pdf_path is None


def test_latest_for_repo_picks_most_recent_generated_at(fake_firestore):
    cache_index.record(
        owner="owner", repo="repo", sha="old_sha", generated_at="2026-08-19T12:00:00Z",
        json_path="old.json", md_path="old.md", pdf_path="old.pdf",
    )
    cache_index.record(
        owner="owner", repo="repo", sha="new_sha", generated_at="2026-08-20T12:00:00Z",
        json_path="new.json", md_path="new.md", pdf_path="new.pdf",
    )

    latest = cache_index.latest_for_repo("owner", "repo")
    assert latest is not None
    assert latest.sha == "new_sha"


def test_latest_for_repo_returns_none_when_no_entries(fake_firestore):
    assert cache_index.latest_for_repo("owner", "nonexistent-repo") is None


def test_latest_for_repo_ignores_other_repos(fake_firestore):
    cache_index.record(
        owner="owner", repo="other-repo", sha="sha1", generated_at="2026-08-20T12:00:00Z",
        json_path="a.json", md_path="a.md", pdf_path=None,
    )
    assert cache_index.latest_for_repo("owner", "repo") is None


def test_latest_for_repo_ignores_other_owners(fake_firestore):
    cache_index.record(
        owner="someone-else", repo="repo", sha="sha1", generated_at="2026-08-20T12:00:00Z",
        json_path="a.json", md_path="a.md", pdf_path=None,
    )
    assert cache_index.latest_for_repo("owner", "repo") is None


def test_load_rating_fetches_from_storage(fake_firestore, fake_storage, tmp_path):
    from server.Report_Writing.report_schema import (
        ArchitectureResult,
        DimensionStatus,
        DocumentationResult,
        Popularity,
        RecommendationsResult,
        ResultsFunctionalityResult,
        RepoRating,
    )
    from server.Website import report_storage

    rating = RepoRating(
        repo="owner/repo",
        generated_at="2026-08-20T12:00:00Z",
        quality_score=8.0,
        architecture=ArchitectureResult(status=DimensionStatus.OK, score=8, justification="x"),
        results_functionality=ResultsFunctionalityResult(status=DimensionStatus.OK, score=8, justification="x"),
        documentation=DocumentationResult(status=DimensionStatus.OK, present=False),
        recommendations=RecommendationsResult(status=DimensionStatus.OK),
        popularity=Popularity(status=DimensionStatus.OK),
    )
    local_json = tmp_path / "owner__repo.json"
    local_json.write_text(rating.model_dump_json(), encoding="utf-8")
    object_name = report_storage.upload_file(local_json, "owner__repo.json", "application/json")

    entry = cache_index.CacheEntry(
        owner="owner", repo="repo", sha="abc123", generated_at="2026-08-20T12:00:00Z",
        json_path=object_name, md_path="owner__repo.md", pdf_path="owner__repo.pdf",
    )
    loaded = entry.load_rating()
    assert loaded.repo == "owner/repo"
    assert loaded.quality_score == 8.0
