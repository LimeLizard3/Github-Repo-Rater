"""
Shared Anthropic client, model constant, and helpers for the Phase 3 rating
subagents (server/subagents/results_subagent.py, server/subagents/
architecture_subagent.py, server/subagents/docs_subagent.py).

Each subagent forces exactly ONE tool call as its output format -- this is
NOT the tool-use loop pattern used elsewhere for real action tools (e.g.
Incident-Reporter's agents/diagnostics.py, which loops because its tool
fetches real data the model chooses to call). Here the "tool" IS the
structured score the subagent returns, so tool_choice pins the model to
calling it exactly once and extract_tool_input() reads the result straight
back out -- no loop, no tool execution.

Async (AsyncAnthropic), not sync, specifically so Phase 4's coordinator can
run all three subagents' API calls concurrently via asyncio.gather() -- a
synchronous client would block the event loop and serialize them despite
gather() being used.
"""

from __future__ import annotations

from typing import Any

import anthropic

from server import config

MODEL = "claude-sonnet-5"  # swap here to change every subagent at once

client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


def cached_system_block(prompt: str) -> list[dict[str, Any]]:
    """Wrap a subagent's fixed SYSTEM_PROMPT for cache_control. Currently a
    no-op in production -- all 3 subagents' prompts are under Sonnet 5's
    1024-token cache minimum -- but correct and ready if a prompt grows.
    See tests/test_prompt_caching.py for a synthetic proof the mechanism
    itself works, since real prompts can't trigger it today."""
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]


def format_files_for_prompt(files: dict[str, str]) -> str:
    if not files:
        return "(No files were available -- triage selected none, or all selected files failed to fetch.)"
    sections = [f"--- {path} ---\n{content}" for path, content in files.items()]
    return "\n\n".join(sections)


def extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict[str, Any]:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise RuntimeError(
        f"Expected a forced '{tool_name}' tool call but got none. "
        f"stop_reason={response.stop_reason!r}"
    )
