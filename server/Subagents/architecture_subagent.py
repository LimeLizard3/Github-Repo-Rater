"""Architecture rating subagent (Phase 3).

Score anchors and output contract are locked per PHASE3_BRIEF.pdf -- do not
change them here without flagging it back to the user first.
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

SYSTEM_PROMPT = """You are the Architecture rating subagent in a GitHub repo-rating system.
You have been given a representative sample of files spread across the
repo's directory structure -- entry points, config files, and files chosen
to reflect overall structure, not just the largest files. This is
independent of whether the code runs correctly -- a different subagent
covers that.

Score 0-10 on: separation of concerns, modularity, naming clarity and
consistency, and whether the structure fits the project's apparent scale.

9-10: clear separation of concerns; focused modules; consistent naming;
      structure fits the project's scale
6-8:  generally organized; mostly sensible separation; occasional
      inconsistency
3-5:  some organization but real problems -- large files mixing
      responsibilities, inconsistent naming, minimal separation
0-2:  little to no discernible structure"""

TOOL_NAME = "submit_architecture_score"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Submit the Architecture rating for this repo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 10},
            "justification": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "justification", "strengths", "weaknesses"],
    },
}


async def score(files: dict[str, str]) -> dict[str, Any]:
    user_content = format_files_for_prompt(files)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=cached_system_block(SYSTEM_PROMPT),
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_content}],
    )
    return extract_tool_input(response, TOOL_NAME)
