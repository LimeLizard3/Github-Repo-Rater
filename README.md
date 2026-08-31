# github-repo-rater — MCP server (Phase 1)

Custom MCP server exposing 5 read-only GitHub tools over Streamable HTTP,
built for a CCAR-F practice project (see `plan.md` in the project for the
full architecture). Auth is a single static bearer token — this server
has exactly one intended client (the coordinator agent / website
backend), so full OAuth 2.1 wasn't warranted; see comments in
`server/auth.py` for the reasoning.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in GITHUB_TOKEN (a GitHub PAT — see .env.example for where to get one)
# and MCP_API_KEY (any long random string)
python3 -m server.app
```

Server listens on `http://<HOST>:<PORT>/mcp` (defaults `0.0.0.0:8000`).
Every request must include `Authorization: Bearer <MCP_API_KEY>`.

## Tools

| Tool | Purpose |
|---|---|
| `get_repo_metadata(owner, repo)` | Description, default branch, language, size, stars, license. Call first. |
| `list_repo_tree(owner, repo, ref=None)` | Full recursive file/dir listing in one call — use for context triage before fetching content. |
| `get_file_content(owner, repo, path, ref=None)` | Decoded text of one file. Files >1MB unsupported in v1. |
| `get_readme(owner, repo, ref=None)` | Decoded root README. Errors if none exists — that's a real signal, not a bug. |
| `search_code(owner, repo, query)` | Code search within the repo. Rate-limited to 10 req/min — use sparingly. |

## Known limitations (by design, for v1)

- Static analysis only — no code execution, so "does it run" is inferred, not verified.
- Files over ~1MB via `get_file_content` aren't supported (Contents API limit).
- `GITHUB_TOKEN` is a personal PAT for now. Swap for a GitHub App installation
  token before this is public-facing (better quota, scoped/revocable independent
  of your personal account).
- No result caching yet (planned for Phase 7, keyed by repo+commit SHA).

## Verified working

- `python3 -m py_compile` passes on all files.
- All 5 tools register with correct schemas (`mcp.list_tools()`).
- Live HTTP check: missing/wrong bearer token → 401; correct token → 200
  with a valid MCP `initialize` handshake response.

Not yet tested against a real GitHub repo (needs a real `GITHUB_TOKEN` —
the checks above used a dummy token and never called the GitHub API).
