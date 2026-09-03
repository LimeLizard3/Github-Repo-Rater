"""
Phase 7 website: the public HTTP layer wrapping the existing coordinator +
Phase 5 report pipeline. Calls GitHubClient directly, same as the CLI
pipeline -- server/app.py's MCP server stays inactive, not reintroduced.

2026-09-03: browser/HTML routes (/, /rate, /report/{owner}/{repo}) removed
-- this backend is app-only now, serving the React Native mobile app
exclusively. No webpage view exists anymore; every rating-related route
returns either a PDF file or a small JSON error.

Run locally:
    python -m server.Website.web_app

Routes:
    POST /api/rate {"owner": ..., "repo": ...}  -- rate a repo (cached by
        (owner, repo, commit_sha); a cache hit costs nothing and doesn't
        count against the daily usage cap), returns the actual PDF file.
    GET  /api/report/{owner}/{repo}             -- most recent cached
        rating's PDF for that repo, regardless of SHA.

    Both routes share _pdf_response() for turning a CacheEntry into the
    final PDF (or error) response. rate_pdf() used to also share a
    _get_or_create_entry()/_parse_rate_body() pair with a since-removed
    HTML route; once that second caller was gone, those two had exactly
    one caller left each, so they were folded back into rate_pdf()
    directly -- no remaining reason to keep them separate.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from server import config
from server.coordinator import rate_repo
from server.github_client import GitHubAPIError, GitHubClient
from server.Report_Writing.report_writer import generate_report, parse_rating
from server.Website import cache_index, usage_cap

config.validate()
config.validate_anthropic()

_gh = GitHubClient(config.GITHUB_TOKEN)
# Serializes cache-index + usage-cap file access across concurrent requests.
# Correct for uvicorn's default single-process run; NOT safe across
# multiple worker processes -- horizontal scaling is explicitly out of
# scope for this phase (see PHASE7_BRIEF.pdf).
_lock = asyncio.Lock() #Guarantees only 1 piece of code at a time can be inside a section that touches cache-index/usage files


def _pdf_response(entry: cache_index.CacheEntry, owner: str, repo: str) -> JSONResponse | FileResponse:
    if entry.pdf_path is None:
        return JSONResponse(
            {
                "error": f"PDF generation failed for this rating of {owner}/{repo}. "
                f"Try again later."
            },
            status_code=500,
        )
    return FileResponse(entry.pdf_path, media_type="application/pdf", filename=f"{owner}__{repo}.pdf")


async def rate_pdf(request: Request) -> JSONResponse | FileResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Request body must be JSON."}, status_code=400)

    owner = (body.get("owner") or "").strip()
    repo = (body.get("repo") or "").strip()
    if not owner or not repo:
        return JSONResponse({"error": "owner and repo are required"}, status_code=400)

    # Resolve the current commit SHA BEFORE touching the cache or spending
    # any Anthropic budget -- a bad repo/owner should fail here, cheaply.
    try:
        meta = await _gh.get_repo_metadata(owner, repo)
        sha = await _gh.get_branch_commit_sha(owner, repo, meta["default_branch"])
    except GitHubAPIError:
        # GitHub 404s both a nonexistent repo AND a private one the PAT
        # can't see, identically -- it never reveals which, so neither can we.
        return JSONResponse(
            {
                "error": f"Couldn't access {owner}/{repo} -- check the name; "
                f"private repos aren't supported."
            },
            status_code=404,
        )

    async with _lock: #With the lock, only one request at a time. VERY important (USeful for funcs touching files)
        entry = cache_index.lookup(owner, repo, sha)
        if entry is not None:
            return _pdf_response(entry, owner, repo)

        if not await usage_cap.check_and_increment():
            return JSONResponse(
                {
                    "error": "Daily rating limit reached -- try again tomorrow. "
                    "Already-rated repos are still available via /api/report/{owner}/{repo}."
                },
                status_code=429,
            )

    # Outside the lock -- a slow Anthropic call (the full 3-subagent
    # pipeline) shouldn't block unrelated cache-hit requests from returning.
    raw = await rate_repo(_gh, owner, repo)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rating = parse_rating(raw, generated_at)
    json_path, md_path, pdf_path = generate_report(rating)

    async with _lock:
        cache_index.record(owner, repo, sha, generated_at, json_path, md_path, pdf_path)

    return _pdf_response(
        cache_index.CacheEntry(
            owner=owner,
            repo=repo,
            sha=sha,
            generated_at=generated_at,
            json_path=json_path,
            md_path=md_path,
            pdf_path=pdf_path,
        ),
        owner,
        repo,
    )


async def get_report_pdf(request: Request) -> JSONResponse | FileResponse:
    owner = request.path_params["owner"]
    repo = request.path_params["repo"]
    entry = cache_index.latest_for_repo(owner, repo)
    if entry is None:
        return JSONResponse({"error": f"{owner}/{repo} hasn't been rated yet."}, status_code=404)
    return _pdf_response(entry, owner, repo)


@asynccontextmanager
async def lifespan(app: Starlette): #Starts up and shuts down the server
    yield
    await _gh.aclose()


app = Starlette(
    routes=[
        Route("/api/rate", rate_pdf, methods=["POST"]),
        Route("/api/report/{owner}/{repo}", get_report_pdf, methods=["GET"]),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)
