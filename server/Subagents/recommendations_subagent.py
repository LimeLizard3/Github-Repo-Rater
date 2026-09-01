"""Recommendations subagent (added 2026-08-31).

The only one of the 4 subagents that is NOT part of the rubric -- it never
affects quality_score, and its findings never feed the other 3 dimensions.
It runs sequentially, after the other 3 finish, taking their combined
findings (via coordinator._build_dimension_summary()) plus the repo's own
README/docs content as context, rather than re-fetching/re-sending the
whole repo again.

This is a deliberate reversal of a Phase 4 locked decision --
coordinator.py's own docstring said "no 4th synthesis LLM call." Reversed
explicitly with Liam's direction (a real 4th API call, with the real added
per-rating cost that implies), not introduced silently.
"""

from __future__ import annotations

from typing import Any

from server.anthropic_client import (
    MODEL,
    cached_system_block,
    client,
    extract_tool_input,
    format_files_for_prompt,
)

SYSTEM_PROMPT = """You are the Recommendations subagent in a GitHub repo-rating system.
You will be given a summary of an already-completed Architecture,
Results/Functionality, and Design/Docs assessment of a repo, plus the
repo's README/docs content if available.

Your job is different from those three: they evaluate the repo's CURRENT
state. You suggest forward-looking ways the repo's owner could build on
it -- concrete next steps, meaningful feature or scope expansions, or
architectural changes worth considering if the project grows beyond its
current scope.

Do not simply restate the weaknesses/issues you were given as
recommendations -- a recommendation describes a constructive next step or
direction, not a repeated pointer at an existing flaw. Ground every
recommendation in what this specific repo actually is and does -- generic
advice that could apply to any repo (e.g. "add more tests", "write better
docs") is not useful here unless phrased with real specificity to this
project's actual domain and design.

Give 3-6 recommendations. Each should be a self-contained sentence or two:
what to build or change, and why it would be a meaningful next step for
this specific project."""

TOOL_NAME = "submit_recommendations"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Submit forward-looking recommendations for this repo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "description": "3-6 concrete, specific, forward-looking suggestions for how this repo could be built upon or expanded.",
                "items": {"type": "string"},
            },
        },
        "required": ["recommendations"],
    },
}


async def score(files: dict[str, str], dimension_summary: str) -> dict[str, Any]:
    user_content = (
        f"{dimension_summary}\n\n--- README / docs content (if any) ---\n"
        f"{format_files_for_prompt(files)}"
    )
    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=cached_system_block(SYSTEM_PROMPT),
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_content}],
    )
    return extract_tool_input(response, TOOL_NAME)
