"""
Phase 4 coordinator -- runs a repo through the full rating pipeline end to
end: triage -> fetch selected file content -> dispatch all 3 subagents in
parallel -> aggregate into one result.

Pipeline logic only, per PHASE4_BRIEF.pdf. No caching, no HTTP endpoint, no
4th synthesis LLM call. This module returns/prints a plain dict; formalizing
that into a validated JSON schema and persisting it is Phase 5's job.

Results/Functionality score: PHASE3_BRIEF.pdf's issues_found schema has no
numeric per-issue deduction field, only a severity CATEGORY (critical /
major / minor / inferred), so "compute the score in code" (this phase's
locked decision) means picking one fixed deduction per tier here rather
than summing per-issue values the model would otherwise report -- resolved
with Liam before this file was written; changing results_subagent.py's
schema to add a numeric field was the alternative, and was declined.

2026-08-31 rubric revision, following an external review (Gemini) of the
grading rubric that Liam requested and I independently checked against
this project's own real run data before implementing:
  - quality_score was (architecture + results_functionality) / 2, with
    documentation excluded entirely. Now a weighted blend of all three
    when docs are present, and a capped/discounted average of the other
    two when they're not -- see _compute_quality_score(). The exact
    weights/multiplier/cap are the reviewer's proposed numbers, not
    derived from this project's own data; easy to retune later, not
    treated as final.
  - The inferred-issue deduction cap in _compute_results_score() no
    longer caps at a flat -1 regardless of count; it's -1 for exactly one
    inferred issue, -2 for two or more. Unlike the quality_score change
    above, this was NOT motivated by anything actually observed going
    wrong in this project's data -- it's the reviewer's speculative
    addition, implemented because it's cheap and low-risk, not because a
    real gap was confirmed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from server.github_client import GitHubAPIError, GitHubClient
from server.triage import (
    SelectionPlan,
    build_repo_map,
    select_architecture_files,
    select_docs_files,
    select_results_files,
)
from server.Subagents.architecture_subagent import score as score_architecture
from server.Subagents.results_subagent import score as score_results
from server.Subagents.docs_subagent import score as score_docs

_CRITICAL_DEDUCTION = -5
_MAJOR_DEDUCTION = -3
_MINOR_DEDUCTION = -1
_MINOR_CAP = -3
_INFERRED_CAP_ONE = -1
_INFERRED_CAP_MULTI = -2

# quality_score weights when documentation is present -- must sum to 1.0.
_ARCHITECTURE_WEIGHT = 0.40
_RESULTS_WEIGHT = 0.40
_DOCS_WEIGHT = 0.20
# When documentation is absent (or that subagent failed), quality_score
# falls back to a discounted average of the other two, capped below a
# perfect score -- undocumented code can't be rated "complete."
_DOCS_ABSENT_MULTIPLIER = 0.85
_DOCS_ABSENT_CAP = 7.0


def _compute_results_score(issues_found: list[dict[str, Any]]) -> int:
    score = 10
    minor_count = 0
    inferred_count = 0
    for issue in issues_found:
        severity = issue["severity"]
        if severity == "critical":
            score += _CRITICAL_DEDUCTION
        elif severity == "major":
            score += _MAJOR_DEDUCTION
        elif severity == "minor":
            minor_count += 1
        elif severity == "inferred":
            inferred_count += 1
    score += max(_MINOR_CAP, minor_count * _MINOR_DEDUCTION)
    if inferred_count == 1:
        score += _INFERRED_CAP_ONE
    elif inferred_count >= 2:
        score += _INFERRED_CAP_MULTI
    return max(0, score)
#Minor and inferred issues can be very commmon and cosmetic. We put a cap on them because we don't want the model to unnecessarily reduce the score by too much


def _compute_quality_score(
    architecture: dict[str, Any],
    results_functionality: dict[str, Any],
    documentation: dict[str, Any],
) -> float | None:
    if architecture["status"] != "ok" or architecture["score"] is None:
        return None
    if results_functionality["status"] != "ok" or results_functionality["score"] is None:
        return None

    arch_score = architecture["score"]
    results_score = results_functionality["score"]

    docs_available = (
        documentation["status"] == "ok"
        and documentation.get("present") is True
        and documentation.get("completeness") is not None
    )
    if docs_available:
        return (
            _ARCHITECTURE_WEIGHT * arch_score
            + _RESULTS_WEIGHT * results_score
            + _DOCS_WEIGHT * documentation["completeness"]
        )
    return min(_DOCS_ABSENT_CAP, ((arch_score + results_score) / 2) * _DOCS_ABSENT_MULTIPLIER)


def _aggregate_strengths_weaknesses(
    architecture: dict[str, Any],
    results_functionality: dict[str, Any],
    documentation: dict[str, Any],
) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if architecture["status"] == "ok":
        strengths += [f"[Architecture] {s}" for s in architecture["strengths"]]
        weaknesses += [f"[Architecture] {w}" for w in architecture["weaknesses"]]
    if results_functionality["status"] == "ok":
        strengths += [f"[Results/Functionality] {s}" for s in results_functionality["strengths"]]
        weaknesses += [f"[Results/Functionality] {w}" for w in results_functionality["weaknesses"]]
    if documentation["status"] == "ok" and documentation["present"]:
        strengths += [f"[Design/Docs] {s}" for s in documentation["strengths"]]
        weaknesses += [f"[Design/Docs] {w}" for w in documentation["weaknesses"]]
    return strengths, weaknesses

async def _fetch_plan_files(
    client: GitHubClient, owner: str, repo: str, ref: str, plan: SelectionPlan
) -> dict[str, str]:
    async def fetch_one(path: str) -> tuple[str, str] | None:
        try:
            content = await client.get_file_content(owner, repo, path, ref=ref)
            return path, content["content"]
        except GitHubAPIError as e:
            print(f"warning: skipping {path}, fetch failed: {e}")
            return None

    fetched = await asyncio.gather(*(fetch_one(f.path) for f in plan.files))
    return {path: content for result in fetched if result is not None for path, content in [result]}


async def rate_repo(client: GitHubClient, owner: str, repo: str) -> dict[str, Any]:
    repo_map = await build_repo_map(client, owner, repo)
    plans = {
        "architecture": select_architecture_files(repo_map),
        "results_functionality": select_results_files(repo_map),
        "design_docs": select_docs_files(repo_map),
    }

    arch_files, results_files, docs_files = await asyncio.gather(
        _fetch_plan_files(client, owner, repo, repo_map.default_branch, plans["architecture"]),
        _fetch_plan_files(client, owner, repo, repo_map.default_branch, plans["results_functionality"]),
        _fetch_plan_files(client, owner, repo, repo_map.default_branch, plans["design_docs"]),
    )

    arch_raw, results_raw, docs_raw = await asyncio.gather(
        score_architecture(arch_files),
        score_results(results_files),
        score_docs(docs_files),
        return_exceptions=True,
    )

    # .get(key, default) rather than [key] on every field below -- a subagent's
    # tool-use response can come back missing a field the schema marks
    # "required" (Anthropic's `required` is guidance to the model, not a
    # hard-enforced contract), and that shouldn't crash the whole pipeline.
    # Defaults mirror report_schema.py's own field defaults.
    if isinstance(arch_raw, Exception):
        architecture: dict[str, Any] = {"status": "failed", "error": str(arch_raw)}
    else:
        architecture = {
            "score": arch_raw.get("score"), #Straight from the model itself, coordi doesn't compute it again
            "justification": arch_raw.get("justification"),
            "strengths": arch_raw.get("strengths", []),
            "weaknesses": arch_raw.get("weaknesses", []),
            "status": "ok",
        }

    if isinstance(results_raw, Exception):
        results_functionality: dict[str, Any] = {"status": "failed", "error": str(results_raw)}
    else:
        results_functionality = {
            "score": _compute_results_score(results_raw.get("issues_found", [])),
            "issues_found": results_raw.get("issues_found", []),
            "justification": results_raw.get("justification"),
            "strengths": results_raw.get("strengths", []),
            "weaknesses": results_raw.get("weaknesses", []),
            "status": "ok",
        }

    if isinstance(docs_raw, Exception):
        documentation: dict[str, Any] = {"status": "failed", "error": str(docs_raw)}
    else:
        documentation = {
            "present": docs_raw.get("documentation_present"), #Straight from the model itself, coordi doesn't compute it again
            "completeness": docs_raw.get("documentation_completeness"),
            "justification": docs_raw.get("justification"),
            "strengths": docs_raw.get("strengths", []),
            "weaknesses": docs_raw.get("weaknesses", []),
            "status": "ok",
        }

    quality_score = _compute_quality_score(architecture, results_functionality, documentation)
    strengths, weaknesses = _aggregate_strengths_weaknesses(architecture, results_functionality, documentation)

    return {
        "repo": f"{owner}/{repo}",
        "quality_score": quality_score,
        "architecture": architecture,
        "results_functionality": results_functionality,
        "documentation": documentation,
        "strengths": strengths,
        "weaknesses": weaknesses,
    }


if __name__ == "__main__":
    import json
    import sys
    import time

    from server import config

    async def _main() -> None:
        if len(sys.argv) != 3:
            print("Usage: python -m server.coordinator <owner> <repo>", file=sys.stderr)
            raise SystemExit(1)
        owner, repo = sys.argv[1], sys.argv[2]
        config.validate()
        config.validate_anthropic()
        client = GitHubClient(config.GITHUB_TOKEN)
        try:
            start = time.monotonic()
            result = await rate_repo(client, owner, repo)
            elapsed = time.monotonic() - start
        finally:
            await client.aclose()
        print(json.dumps(result, indent=2))
        print(f"\nElapsed: {elapsed:.2f}s", file=sys.stderr)

    asyncio.run(_main())
