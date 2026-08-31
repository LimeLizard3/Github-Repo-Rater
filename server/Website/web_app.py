"""
Phase 7 website: the public HTTP layer wrapping the existing coordinator +
Phase 5 report pipeline. Calls GitHubClient directly, same as the CLI
pipeline -- server/app.py's MCP server stays inactive, not reintroduced.

Run locally:
    python -m server.Website.web_app

Routes:
    GET  /                                  -- a form page: paste a repo
        link or "owner/repo", submits to /rate via fetch(), replaces the
        page with the result. Not part of PHASE7_BRIEF.pdf's spec (that
        brief only described the JSON API); added afterward so the site is
        actually usable from a browser without a separate frontend.
    POST /rate {"owner": ..., "repo": ...}  -- rate a repo (cached by
        (owner, repo, commit_sha); a cache hit costs nothing and doesn't
        count against the daily usage cap)
    GET  /report/{owner}/{repo}             -- most recent cached rating
        for that repo, regardless of SHA
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from server import config
from server.coordinator import rate_repo
from server.github_client import GitHubAPIError, GitHubClient
from server.Report_Writing.report_writer import (
    generate_report,
    parse_rating,
    render_html,
    render_markdown,
)
from server.Website import cache_index, usage_cap

config.validate()
config.validate_anthropic()

_gh = GitHubClient(config.GITHUB_TOKEN)
# Serializes cache-index + usage-cap file access across concurrent requests.
# Correct for uvicorn's default single-process run; NOT safe across
# multiple worker processes -- horizontal scaling is explicitly out of
# scope for this phase (see PHASE7_BRIEF.pdf).
_lock = asyncio.Lock()


def _rating_response(rating) -> HTMLResponse:
    return HTMLResponse(
        render_html(
            render_markdown(rating),
            title=rating.repo,
            quality_score=rating.quality_score,
            generated_at=rating.generated_at,
        )
    )


_HOME_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>GitHub Repo-Rater</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 40em; margin: 4em auto; padding: 0 1em; }
  input { width: 100%; box-sizing: border-box; font-size: 1.1em; padding: 0.5em; }
  button { margin-top: 0.75em; font-size: 1.1em; padding: 0.5em 1.5em; cursor: pointer; }
  #status { color: #555; }
</style>
</head>
<body>
<h1>GitHub Repo-Rater</h1>
<p>Paste a GitHub repo link (or just "owner/repo") to rate it.</p>
<form id="rate-form">
  <input type="text" id="repo-input" placeholder="https://github.com/owner/repo" required>
  <button type="submit">Rate</button>
</form>
<p id="status"></p>
<script>
document.getElementById('rate-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById('status');
  let cleaned = document.getElementById('repo-input').value.trim()
    .replace(/^https?:\\/\\/(www\\.)?github\\.com\\//, '')
    .replace(/\\.git$/, '')
    .replace(/\\/$/, '');
  const parts = cleaned.split('/').filter(Boolean);
  if (parts.length < 2) {
    statusEl.textContent = 'Could not find an owner/repo in that -- try "owner/repo" or a full github.com link.';
    return;
  }
  const [owner, repo] = parts;
  statusEl.textContent = `Rating ${owner}/${repo}... this can take up to ~30 seconds on a cache miss.`;

  const resp = await fetch('/rate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({owner, repo}),
  });
  const text = await resp.text();
  if (resp.ok) {
    document.open();
    document.write(text);
    document.close();
  } else {
    try {
      statusEl.textContent = 'Error: ' + JSON.parse(text).error;
    } catch {
      statusEl.textContent = 'Error: ' + resp.status;
    }
  }
});
</script>
</body>
</html>"""


async def home(request: Request) -> HTMLResponse:
    return HTMLResponse(_HOME_PAGE_HTML)


async def rate(request: Request) -> JSONResponse | HTMLResponse:
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

    async with _lock:
        entry = cache_index.lookup(owner, repo, sha)
        if entry is not None:
            return _rating_response(entry.load_rating())

        if not await usage_cap.check_and_increment():
            return JSONResponse(
                {
                    "error": "Daily rating limit reached -- try again tomorrow. "
                    "Already-rated repos are still available via /report/{owner}/{repo}."
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

    return _rating_response(rating)


async def get_report(request: Request) -> JSONResponse | HTMLResponse:
    owner = request.path_params["owner"]
    repo = request.path_params["repo"]
    entry = cache_index.latest_for_repo(owner, repo)
    if entry is None:
        return JSONResponse({"error": f"{owner}/{repo} hasn't been rated yet."}, status_code=404)
    return _rating_response(entry.load_rating())


@asynccontextmanager
async def lifespan(app: Starlette):
    yield
    await _gh.aclose()


app = Starlette(
    routes=[
        Route("/", home, methods=["GET"]),
        Route("/rate", rate, methods=["POST"]),
        Route("/report/{owner}/{repo}", get_report, methods=["GET"]),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)
