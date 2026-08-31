"""
Phase 3 standalone test harness -- NOT the coordinator (that's Phase 4).

Runs exactly ONE subagent against ONE repo's triaged files and prints the
result for a human to read. Throwaway scaffolding for iterating on prompt
quality -- must not grow into asyncio.gather() over all three subagents.

Usage:
    python -m server.test_subagent <results|architecture|docs> <owner> <repo>
"""

from __future__ import annotations

import asyncio
import json
import sys

from server import config
from server.github_client import GitHubAPIError, GitHubClient
from server.triage import (
    build_repo_map,
    select_architecture_files,
    select_docs_files,
    select_results_files,
)
from server.anthropic_client import MODEL
from server.Subagents.results_subagent import score as score_results
from server.Subagents.architecture_subagent import score as score_architecture
from server.Subagents.docs_subagent import score as score_docs

SUBAGENTS = {
    "results": (select_results_files, score_results),
    "architecture": (select_architecture_files, score_architecture),
    "docs": (select_docs_files, score_docs),
}


async def _main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in SUBAGENTS:
        print(
            f"Usage: python -m server.test_subagent <{'|'.join(SUBAGENTS)}> <owner> <repo>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    subagent, owner, repo = sys.argv[1], sys.argv[2], sys.argv[3]

    config.validate()
    config.validate_anthropic()
    select_fn, score_fn = SUBAGENTS[subagent] #Calls and tests one sub agent ON PURPOSE

    client = GitHubClient(config.GITHUB_TOKEN)
    try:
        repo_map = await build_repo_map(client, owner, repo)
        plan = select_fn(repo_map)

        files: dict[str, str] = {}
        for f in plan.files:
            try:
                content = await client.get_file_content(
                    owner, repo, f.path, ref=repo_map.default_branch
                )
                files[f.path] = content["content"]
            except GitHubAPIError as e:
                print(f"warning: skipping {f.path}, fetch failed: {e}", file=sys.stderr)
    finally:
        await client.aclose()

    result = await score_fn(files)
    output = {
        "subagent": subagent,
        "owner": owner,
        "repo": repo,
        "model": MODEL,
        "files_included": sorted(files.keys()),
        "result": result,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
