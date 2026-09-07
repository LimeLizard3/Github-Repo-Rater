"""
Thin async wrapper around the GitHub REST API.

Deliberately NOT using an existing GitHub MCP server / SDK wrapper here —
this project's whole point is practicing hand-building the MCP tool layer.

Gotchas baked in as comments because they're easy to relearn the hard way:
  - Contents API (`get_file_content`) only returns inline base64 content for
    files <= ~1MB. Larger files come back with `content: null` and a
    `download_url` instead — v1 surfaces that as an error rather than
    silently returning nothing.
  - The Search API has its OWN, much stricter rate limit (10 req/min
    authenticated) separate from the 5,000/hr general REST limit. Don't
    let a subagent call search_code in a loop.
  - `list_repo_tree` needs a real branch name or commit SHA, not "HEAD" -
    we resolve the repo's default branch first if no ref is given.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

GITHUB_API_BASE = "https://api.github.com"


class GitHubAPIError(RuntimeError):
    """Raised for any non-2xx response from the GitHub API, with enough
    detail for a subagent (or a human) to understand what happened."""


class GitHubClient:
    def __init__(self, token: str, timeout: float = 20.0) -> None: #Checks and verifies who you are and what URL is pasted
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None: #Closes client when user is done
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: #If no error codes were found, hands back the response
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset", "unknown")
            raise GitHubAPIError(
                f"GitHub API rate limit exhausted. Resets at unix time {reset}."
            )
        if resp.status_code == 404:
            raise GitHubAPIError(f"Not found: {url}")
        if resp.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub API error {resp.status_code} for {url}: {resp.text[:500]}"
            )
        return resp

    async def get_repo_metadata(self, owner: str, repo: str) -> dict[str, Any]: #Literally just gets all the data
        resp = await self._request("GET", f"/repos/{owner}/{repo}")
        data = resp.json()
        return {
            "full_name": data["full_name"],
            "description": data.get("description"),
            "default_branch": data["default_branch"],
            "primary_language": data.get("language"),
            "size_kb": data["size"],
            "stargazers_count": data["stargazers_count"],
            "forks_count": data["forks_count"],
            "subscribers_count": data.get("subscribers_count", 0),
            "license": (data.get("license") or {}).get("spdx_id"),
            "topics": data.get("topics", []),
            "created_at": data["created_at"],
            "pushed_at": data["pushed_at"],
            "archived": data.get("archived", False),
        }

    async def get_release_downloads(self, owner: str, repo: str) -> int | None:
        """Total download count across all Release assets, or None if the
        repo has never published a Release. Unlike clone/traffic stats
        (owner-only), this is public for any repo.

        Only sums the first page (up to 100 releases) rather than paginating
        fully -- a good-enough approximation for a "how popular is this"
        stat; a repo with more releases than that is rare, and this isn't
        the kind of number that needs to be exact.
        """
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/releases", params={"per_page": 100}
        )
        releases = resp.json()
        if not releases:
            return None
        return sum(
            asset["download_count"] for release in releases for asset in release.get("assets", [])
        )

    async def _resolve_ref(self, owner: str, repo: str, ref: str | None) -> str: #Gives default branch incase one was not provided
        if ref:
            return ref
        meta = await self.get_repo_metadata(owner, repo)
        return meta["default_branch"]

    async def list_repo_tree( 
        self, owner: str, repo: str, ref: str | None = None
    ) -> dict[str, Any]: # Gets repo tree, and 
        resolved_ref = await self._resolve_ref(owner, repo, ref) #Gets default branch if none was provided
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{resolved_ref}",
            params={"recursive": "1"}, #Gets all folders sub folders and docs with only one call
        )
        data = resp.json()
        entries = [
            {"path": e["path"], "type": e["type"], "size": e.get("size")}
            for e in data.get("tree", [])
        ]
        return {
            "ref": resolved_ref,
            "truncated": data.get("truncated", False),
            "entry_count": len(entries),
            "entries": entries,
        }

    async def get_file_content( #Reads files and ensures that it's a file that's passed, not a directory, and it's of the correct size
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> dict[str, Any]:
        params = {"ref": ref} if ref else {}
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params
        )
        data = resp.json()
        if isinstance(data, list):
            raise GitHubAPIError(
                f"'{path}' is a directory, not a file. Use list_repo_tree to browse it."
            )
        if data.get("content") is None:
            raise GitHubAPIError(
                f"'{path}' is too large for inline content (>1MB) — "
                f"not supported in v1. download_url: {data.get('download_url')}"
            )
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return {"path": path, "size": data["size"], "content": content}

    async def get_branch_commit_sha(self, owner: str, repo: str, branch: str) -> str:
        """Resolve a branch name to its current head commit SHA -- used for
        the website's (owner, repo, sha) cache key so a repo that hasn't
        changed since its last rating doesn't burn Anthropic budget on a
        re-rate. Uses /branches/{branch} rather than /commits/{ref} -- both
        resolve to a SHA, but this response is much smaller since only the
        SHA is needed."""
        resp = await self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}")
        data = resp.json()
        return data["commit"]["sha"]

    async def get_readme(
        self, owner: str, repo: str, ref: str | None = None
    ) -> dict[str, Any]:
        params = {"ref": ref} if ref else {}
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/readme", params=params
        )
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return {"path": data["path"], "content": content}

    async def search_code(self, owner: str, repo: str, query: str) -> dict[str, Any]:
        """NOTE: subject to a strict 10 req/min rate limit, separate from
        the general 5,000/hr REST quota. Use sparingly, not in a loop."""
        resp = await self._request(
            "GET",
            "/search/code",
            params={"q": f"{query} repo:{owner}/{repo}"},
        )
        data = resp.json()
        return {
            "total_count": data["total_count"],
            "incomplete_results": data["incomplete_results"], #No .get() for these 2 as they are always present on successful call
            "items": [
                {"path": item["path"], "name": item["name"]}
                for item in data.get("items", [])
            ],
        }
