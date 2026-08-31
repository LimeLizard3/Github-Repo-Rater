"""Design/Docs rating subagent (Phase 3).

Score anchors and output contract are locked per PHASE3_BRIEF.pdf -- do not
change them here without flagging it back to the user first. Note the
null-vs-0 handling for absent documentation: documentation_completeness
must be null (not 0) when documentation_present is false -- this is a
deliberate refinement over PROJECT_BRIEF.pdf's earlier general note, per
the more specific and more recent PHASE3_BRIEF.pdf.
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

SYSTEM_PROMPT = """You are the Design/Docs rating subagent in a GitHub repo-rating system.
You will typically receive: the README (if any), LICENSE, CONTRIBUTING.md,
and anything under a docs folder.

First: documentation_present = false if there is no README and nothing
under a docs folder at all. In that case set documentation_completeness
to null and stop -- do not score completeness for documentation that
doesn't exist.

If documentation_present = true, score documentation_completeness 0-10:
9-10: purpose, setup/install, usage examples, and notable configuration
      all covered clearly
6-8:  purpose and setup covered, missing usage examples or has real gaps
3-5:  minimal -- a title and maybe one short paragraph
0-2:  technically exists but barely more than a placeholder"""

TOOL_NAME = "submit_docs_score"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Submit the Design/Docs rating for this repo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "documentation_present": {"type": "boolean"},
            "documentation_completeness": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 10,
                "description": "0-10 if documentation_present is true, otherwise null.",
            },
            "justification": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "documentation_present",
            "documentation_completeness",
            "justification",
            "strengths",
            "weaknesses",
        ],
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
