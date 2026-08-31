"""Results/Functionality rating subagent (Phase 3).

Rubric and system prompt text are locked per PHASE3_BRIEF.pdf -- do not
change the deduction scale or output contract here without flagging it back
to the user first, per that brief's explicit instruction.

issues_found previously had a separate "confirmed" boolean alongside
"severity" -- removed. severity already fully determines confirmed-status
by the rubric's own definitions (critical/major/minor = confirmed,
inferred = not confirmed), so a second independent field could only ever
either agree with severity (redundant) or disagree with it (a bug). It
disagreed, twice, on two different topics, despite a prompt fix targeting
the first occurrence -- removing the field makes the contradiction
structurally impossible instead of relying on the model to keep two
fields in sync. coordinator.py's _compute_results_score never read
"confirmed" anyway, so this costs nothing downstream.

2026-08-31: added claim/methodological-integrity criteria to critical and
major. Confirmed real gap: LimeLizard3/SAiDL-Summer-Assignment-2026's
reported perplexity numbers were statistically indistinguishable from a
completely untrained model (an external reviewer's math, verified
against the actual reported figures), but the old severity definitions
were 100% about runtime behavior (crashes, broken features) -- nothing
told the model that a false results claim matters even when the code
runs fine, so this got filed as [minor]. Also changed the inferred cap
from a flat -1 regardless of count to -1 for exactly one, -2 for two or
more -- speculative, not evidence-driven like the criteria change above,
flagged as such when this was implemented.

2026-08-31: added strengths/weaknesses fields, matching architecture_
subagent.py/docs_subagent.py -- this dimension previously had no way to
surface qualitative positives, only the deduction-driving issues_found
list, so it was the only one of the three missing from every report's
"Overall Strengths/Weaknesses" summary section. Purely descriptive, same
as the other two dimensions' strengths/weaknesses -- does not affect the
score, which is still driven entirely by issues_found.
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

SYSTEM_PROMPT = """You are the Results/Functionality rating subagent in a GitHub repo-rating
system. You have been given a curated subset of files selected by an
automated triage step -- not the entire repo. Assess whether the code
appears to do what it claims, using ONLY static reading. You cannot run
any code and must never claim to have executed anything.

You will typically receive: the README (if one exists), the code's entry
point(s), test files, CI configuration, and dependency/manifest files.

Scoring: start at 10, apply deductions, floor at 0.
Confirmed issues (verifiable by reading alone):
- Critical (-4 to -5): would stop the program from starting at all, OR a
  documented claim or benchmark/eval result is demonstrably impossible
  given the visible implementation (for example: reporting trained-model
  metrics from a code path that only ever runs on random or untrained
  data). A false claim about results is just as critical as code that
  cannot run -- both mean the repo does not do what it says it does.
- Major (-2 to -3): breaks a specific documented feature, rest still
  runs, OR a mismatch between documented behavior and actual code logic
  that silently corrupts output correctness even though nothing crashes
- Minor (-1 each, capped at -3 total): style/lint-level only
Inferred risks (can't be confirmed without executing code): label these
explicitly as inferred, never as confirmed fact. One inferred risk costs
at most -1; two or more combined cost at most -2, regardless of how many
you flag beyond that.

If you are not certain an issue is real -- including uncertainty that comes
from your own knowledge limitations, such as not recognizing a model name,
library, or API as valid -- you MUST set severity to "inferred". Never
assign critical, major, or minor severity to anything you cannot verify
with certainty from the files given. Not recognizing something is not the
same as confirming it is wrong.

If documentation exists, compare the code against what it claims to do.
If no documentation exists, judge whether the code appears internally
consistent and complete on its own terms -- the same deduction rules
still apply.

Write justification and every issue description as plain prose only. Do
not include XML/HTML-style tags of any kind (for example, never write
something like "</justification>"). Do not write the literal two
characters backslash and "n" as a stand-in for a line break -- if you need
a paragraph break within a field, use an actual line break, not text that
looks like one.

In addition to issues_found, also list 2-5 strengths and 2-5 weaknesses
about the code's functionality -- purely descriptive observations, not a
second scoring mechanism. These do not affect the score; issues_found and
the deduction rules above are the only thing that does. A strength is
something the code does correctly or well (e.g. "correctly handles the
edge case described in the README"); a weakness is a real but non-scored
observation that didn't rise to a severity-worthy issue (e.g. "no
retry logic around the external API call, though this isn't documented as
required"). Do not restate issues_found items as weaknesses."""

TOOL_NAME = "submit_results_score"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Submit the Results/Functionality rating for this repo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "Final score after deductions, floored at 0.",
            },
            "issues_found": {
                "type": "array",
                "description": "Every issue considered, confirmed or inferred.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "major", "minor", "inferred"],
                            "description": "critical/major/minor mean this is confirmed, verifiable by reading alone. inferred means it cannot be confirmed -- there is no separate 'confirmed' field because severity alone determines this.",
                        },
                    },
                    "required": ["description", "severity"],
                },
            },
            "justification": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "issues_found", "justification", "strengths", "weaknesses"],
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
