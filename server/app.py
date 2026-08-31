"""
GitHub Repo-Rater MCP server.

Not currently used by the rating pipeline -- triage.py/coordinator.py call
GitHubClient directly instead, for performance (skips the MCP round-trip).
Kept as a standalone, working MCP server in case it's needed later (e.g.
exposing these tools to an external client).

Run locally:
    python -m server.app

Exposes 5 tools over Streamable HTTP at http://<HOST>:<PORT>/mcp
Every request must carry: Authorization: Bearer <MCP_API_KEY>
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from server import config
from server.auth import BearerAuthMiddleware
from server.github_client import GitHubClient

config.validate()

mcp = FastMCP("github-repo-rater", host=config.HOST, port=config.PORT)
_gh = GitHubClient(config.GITHUB_TOKEN)

Owner = Annotated[str, Field(description="Repo owner or org, e.g. 'anthropics'")]
Repo = Annotated[str, Field(description="Repo name, e.g. 'claude-code'")]
Ref = Annotated[
    str | None,
    Field(default=None, description="Branch, tag, or commit SHA. Defaults to the repo's default branch."),
]


@mcp.tool()
async def get_repo_metadata(owner: Owner, repo: Repo) -> dict:
    """Fetch top-level repo metadata: description, default branch, primary
    language, size, stars, license, topics, and last-pushed date. Call this
    FIRST for any new repo — it's one cheap call and gives you the default
    branch needed by the other tools."""
    return await _gh.get_repo_metadata(owner, repo)


@mcp.tool()
async def list_repo_tree(owner: Owner, repo: Repo, ref: Ref = None) -> dict:
    """List every file and directory path in the repo (recursive) in a
    single call. Use this for context triage BEFORE fetching any file
    content — decide what's worth reading from the tree, don't fetch
    blindly. Response includes `truncated: true` if the repo exceeds
    GitHub's single-response tree size limit."""
    return await _gh.list_repo_tree(owner, repo, ref)


@mcp.tool()
async def get_file_content(owner: Owner, repo: Repo, path: str, ref: Ref = None) -> dict:
    """Fetch the decoded text content of one file. Files over ~1MB are not
    supported (GitHub's Contents API limit) and will return an error —
    that's expected, not a bug; skip such files."""
    return await _gh.get_file_content(owner, repo, path, ref)


@mcp.tool()
async def get_readme(owner: Owner, repo: Repo, ref: Ref = None) -> dict:
    """Fetch the repo's root README, decoded. Raises an error if no README
    exists at the root — that absence is itself a meaningful signal for
    the Design/Docs rating, don't treat the error as a failure to recover
    from."""
    return await _gh.get_readme(owner, repo, ref)


@mcp.tool()
async def search_code(
    owner: Owner,
    repo: Repo,
    query: Annotated[str, Field(description="GitHub code search query, e.g. 'def test_' or 'TODO'")],
) -> dict:
    """Search for a code pattern within this repo. RATE-LIMITED TO 10
    REQUESTS/MINUTE regardless of your general quota — use it for a
    handful of targeted checks (e.g. 'does a test file exist'), never in
    a loop over many queries."""
    return await _gh.search_code(owner, repo, query)


def build_asgi_app():
    """Returns the Streamable HTTP ASGI app wrapped with bearer-token auth.
    This is what you point uvicorn (or any ASGI server) at."""
    inner_app = mcp.streamable_http_app()
    return BearerAuthMiddleware(inner_app, expected_token=config.MCP_API_KEY)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_asgi_app(), host=config.HOST, port=config.PORT)
