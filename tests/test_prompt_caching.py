"""
Brief test 4: proves the cache_control mechanism itself works.

None of the 3 real subagent SYSTEM_PROMPTs clear Claude Sonnet 5's
1024-token minimum cacheable prefix (352-835 tokens combined with their
TOOL_SCHEMA -- see PHASE7_BRIEF plan notes), so calling a real subagent
twice would show 0 for cache_read_input_tokens/cache_creation_input_tokens
regardless of whether cache_control is wired correctly. This test instead
builds synthetic filler content padded past 1024 tokens -- NOT touching
any production subagent prompt -- purely to prove the mechanism engages.

Makes real Anthropic API calls and costs real money (two small requests).
Excluded from ordinary `pytest` runs via the `live` marker (see pytest.ini)
-- run explicitly with `pytest -m live tests/test_prompt_caching.py`.
"""

from __future__ import annotations

import pytest

from server import config
from server.anthropic_client import MODEL, client

# Synthetic filler only -- roughly 4 chars/token (this project's own rough
# token-count convention, see triage.py), repeated well past the ~4096
# chars (~1024 tokens) minimum cacheable prefix for Sonnet 5.
_FILLER_SENTENCE = (
    "This is synthetic filler text used only to test whether Anthropic "
    "prompt caching activates once a system prompt exceeds Sonnet 5's "
    "1024-token minimum cacheable prefix. It is not a real subagent "
    "prompt and is never used in production. "
)
_PADDED_SYSTEM_PROMPT = _FILLER_SENTENCE * 40  # ~6,200 chars, ~1,550 est. tokens


@pytest.mark.live
@pytest.mark.asyncio
async def test_cache_control_engages_on_second_call():
    try:
        config.validate_anthropic()
    except RuntimeError:
        pytest.skip("ANTHROPIC_API_KEY not configured")

    system_block = [
        {"type": "text", "text": _PADDED_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    messages = [{"role": "user", "content": "Reply with exactly one word: OK."}]

    first = await client.messages.create(
        model=MODEL, max_tokens=10, system=system_block, messages=messages
    )
    second = await client.messages.create(
        model=MODEL, max_tokens=10, system=system_block, messages=messages
    )

    # First call creates the cache entry, second call should read from it.
    assert first.usage.cache_creation_input_tokens > 0
    assert second.usage.cache_read_input_tokens > 0
