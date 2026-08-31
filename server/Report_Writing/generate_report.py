"""
Phase 5 CLI entry point: rate a repo via the coordinator (Phase 4), validate
the result into RepoRating, and write JSON/Markdown/PDF views to disk.

coordinator.py is untouched -- its docstring already says "Phase 4 stops
here" (returns/prints a raw dict); this script is the downstream consumer
that formalizes and persists it.

Usage:
    python -m server.Report_Writing.generate_report <owner> <repo> [--tag TAG]

--tag appends an extra label to the output filenames (e.g. "--tag foo" on
LimeLizard3/Incident-Reporter produces
LimeLizard3__Incident-Reporter__20260820_report_foo.pdf) so a repo can be
rated more than once on the same day without each run overwriting the last.
Omit it and the default same-day-overwrite behavior is unchanged.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from server import config
from server.coordinator import rate_repo
from server.github_client import GitHubClient
from server.Report_Writing.report_writer import generate_report, parse_rating


_USAGE = "Usage: python -m server.Report_Writing.generate_report <owner> <repo> [--tag TAG]"


async def _main() -> None:
    args = sys.argv[1:]
    tag: str | None = None
    if "--tag" in args:
        idx = args.index("--tag")
        if idx + 1 >= len(args):
            print(_USAGE, file=sys.stderr)
            raise SystemExit(1)
        tag = args[idx + 1]
        del args[idx : idx + 2]

    if len(args) != 2:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(1)
    owner, repo = args

    config.validate()
    config.validate_anthropic()

    client = GitHubClient(config.GITHUB_TOKEN)
    try:
        raw = await rate_repo(client, owner, repo)
    finally:
        await client.aclose()

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rating = parse_rating(raw, generated_at)
    json_path, md_path, pdf_path = generate_report(rating, tag=tag)

    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"PDF: {pdf_path}" if pdf_path else "PDF: (generation failed, see warning above)")


if __name__ == "__main__":
    asyncio.run(_main())
