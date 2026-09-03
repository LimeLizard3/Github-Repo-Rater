"""
End-to-end tests for web_app.py's routes, through the real HTTP layer
(starlette's TestClient), not by calling handler functions directly --
that's the point of brief test 1.

GitHub reads are free, so test_bad_input_returns_4xx hits the real API for
a nonexistent repo. Everything that would otherwise call Anthropic
(rate_repo) is monkeypatched to a canned result -- no API cost.

All file-backed state (cache index, usage cap, and report_writer's
REPORTS_DIR itself, since generate_report() really runs against the canned
data) is redirected into tmp_path so these tests never touch the real
reports/ratings/ directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.Report_Writing import report_writer
from server.Website import cache_index, usage_cap, web_app

CANNED_RAW = {
    "repo": "placeholder/placeholder",
    "quality_score": 8.0,
    "architecture": {
        "status": "ok",
        "score": 8,
        "justification": "Looks reasonably organized.",
        "strengths": ["Clear module boundaries"],
        "weaknesses": ["Some duplication"],
    },
    "results_functionality": {
        "status": "ok",
        "score": 8,
        "issues_found": [],
        "justification": "Appears to do what it claims.",
        "strengths": ["Handles the documented edge case correctly"],
        "weaknesses": ["No retry logic around the external API call"],
    },
    "documentation": {
        "status": "ok",
        "present": True,
        "completeness": 7,
        "justification": "README covers the basics.",
        "strengths": ["Clear setup instructions"],
        "weaknesses": [],
    },
    "recommendations": {
        "status": "ok",
        "recommendations": ["Consider adding a CLI flag for custom output paths."],
    },
    "strengths": ["[Architecture] Clear module boundaries"],
    "weaknesses": ["[Architecture] Some duplication"],
}


@pytest.fixture
def redirect_storage(tmp_path, monkeypatch):
    """Points every file-backed store this test touches at tmp_path."""
    monkeypatch.setattr(report_writer, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(cache_index, "CACHE_INDEX_PATH", tmp_path / "cache_index.json")
    monkeypatch.setattr(usage_cap, "USAGE_CAP_PATH", tmp_path / "usage_cap.json")
    return tmp_path


@pytest.fixture
def mock_github(monkeypatch):
    """Fixed default_branch/sha regardless of owner/repo -- distinct owner/repo
    pairs still get distinct cache keys since the key is (owner, repo, sha)."""

    async def fake_get_repo_metadata(owner, repo):
        return {"default_branch": "main"}

    async def fake_get_branch_commit_sha(owner, repo, branch):
        return "a" * 40

    monkeypatch.setattr(web_app._gh, "get_repo_metadata", fake_get_repo_metadata)
    monkeypatch.setattr(web_app._gh, "get_branch_commit_sha", fake_get_branch_commit_sha)


@pytest.fixture
def client():
    return TestClient(web_app.app)


def test_rate_endpoint_shape(redirect_storage, mock_github, client, monkeypatch):
    """Brief test 1: a real POST /rate through the actual HTTP layer, not
    calling rate_repo() directly -- proves the web layer itself works."""

    async def fake_rate_repo(gh_client, owner, repo):
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    resp = client.post("/rate", json={"owner": "someowner", "repo": "somerepo"})
    assert resp.status_code == 200
    assert "someowner/somerepo" in resp.text


def test_cache_hit_skips_pipeline(redirect_storage, mock_github, client, monkeypatch):
    """Brief test 2: rate the same repo twice, confirm the pipeline (and
    thus any Anthropic call) only runs once."""
    call_count = 0

    async def fake_rate_repo(gh_client, owner, repo):
        nonlocal call_count
        call_count += 1
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    first = client.post("/rate", json={"owner": "someowner", "repo": "cachetest"})
    second = client.post("/rate", json={"owner": "someowner", "repo": "cachetest"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count == 1


def test_usage_cap_rejects_over_limit(redirect_storage, mock_github, client, monkeypatch):
    """Brief test 3: exceed a (temporarily lowered) daily cap with distinct
    repos, confirm a graceful rejection, not a crash."""
    monkeypatch.setattr(usage_cap, "DAILY_LIMIT", 2)

    async def fake_rate_repo(gh_client, owner, repo):
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    r1 = client.post("/rate", json={"owner": "someowner", "repo": "cap-repo-1"})
    r2 = client.post("/rate", json={"owner": "someowner", "repo": "cap-repo-2"})
    r3 = client.post("/rate", json={"owner": "someowner", "repo": "cap-repo-3"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert "try again tomorrow" in r3.json()["error"]
    assert "still available" in r3.json()["error"]


def test_bad_input_returns_4xx(redirect_storage, client):
    """Brief test 5: a real nonexistent repo returns a clear 4xx with a
    plain-language message, not a stack trace. Free GitHub call, not mocked."""
    resp = client.post(
        "/rate",
        json={"owner": "this-owner-should-not-exist-xyz", "repo": "this-repo-should-not-exist-xyz"},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()
    assert "Couldn't access" in resp.json()["error"]


def test_missing_owner_or_repo_returns_400(redirect_storage, client):
    resp = client.post("/rate", json={"owner": "", "repo": "somerepo"})
    assert resp.status_code == 400


def test_get_report_returns_404_when_never_rated(redirect_storage, client):
    resp = client.get("/report/nobody/never-rated-repo")
    assert resp.status_code == 404
    assert "hasn't been rated yet" in resp.json()["error"]


def test_get_report_serves_latest_cached_entry(redirect_storage, mock_github, client, monkeypatch):
    async def fake_rate_repo(gh_client, owner, repo):
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    client.post("/rate", json={"owner": "someowner", "repo": "get-report-test"})

    resp = client.get("/report/someowner/get-report-test")
    assert resp.status_code == 200
    assert "someowner/get-report-test" in resp.text


# --- /api/rate and /api/report/{owner}/{repo} -- the PDF-serving routes ---


def test_api_rate_returns_a_real_pdf_file(redirect_storage, mock_github, client, monkeypatch):
    async def fake_rate_repo(gh_client, owner, repo):
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    resp = client.post("/api/rate", json={"owner": "someowner", "repo": "pdf-test"})

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert "someowner__pdf-test.pdf" in resp.headers["content-disposition"]
    assert resp.content[:4] == b"%PDF"  # real PDF file signature, not a stub


def test_api_rate_and_html_rate_share_the_same_cache(redirect_storage, mock_github, client, monkeypatch):
    """The whole point of factoring out _get_or_create_entry(): rating via
    the HTML route, then requesting the PDF route for the same repo,
    should hit the cache -- not re-run the pipeline a second time."""
    call_count = 0

    async def fake_rate_repo(gh_client, owner, repo):
        nonlocal call_count
        call_count += 1
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    html_resp = client.post("/rate", json={"owner": "someowner", "repo": "shared-cache-test"})
    pdf_resp = client.post("/api/rate", json={"owner": "someowner", "repo": "shared-cache-test"})

    assert html_resp.status_code == 200
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert call_count == 1  # only the first request actually ran the pipeline


def test_api_get_report_returns_404_when_never_rated(redirect_storage, client):
    resp = client.get("/api/report/nobody/never-rated-repo")
    assert resp.status_code == 404
    assert "hasn't been rated yet" in resp.json()["error"]


def test_api_get_report_serves_cached_pdf(redirect_storage, mock_github, client, monkeypatch):
    async def fake_rate_repo(gh_client, owner, repo):
        return {**CANNED_RAW, "repo": f"{owner}/{repo}"}

    monkeypatch.setattr(web_app, "rate_repo", fake_rate_repo)

    client.post("/api/rate", json={"owner": "someowner", "repo": "api-get-report-test"})

    resp = client.get("/api/report/someowner/api-get-report-test")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_api_rate_returns_same_errors_as_html_route(redirect_storage, client):
    resp = client.post("/api/rate", json={"owner": "", "repo": "somerepo"})
    assert resp.status_code == 400


def test_pdf_response_returns_500_when_pdf_generation_failed():
    """Unit test, no HTTP layer -- _pdf_response() must degrade gracefully
    (not crash) when pdf_path is None, matching generate_report()'s own
    graceful PDF-failure handling."""
    entry = cache_index.CacheEntry(
        owner="someowner",
        repo="norender",
        sha="a" * 40,
        generated_at="2026-09-03T00:00:00Z",
        json_path=Path("fake.json"),
        md_path=Path("fake.md"),
        pdf_path=None,
    )
    resp = web_app._pdf_response(entry, "someowner", "norender")
    assert resp.status_code == 500
    assert "PDF generation failed" in resp.body.decode()
