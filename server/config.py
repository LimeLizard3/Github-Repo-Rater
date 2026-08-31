"""
Environment/config loading for the GitHub Repo-Rater MCP server.

Required env vars (see .env.example):
  GITHUB_TOKEN  - a GitHub PAT (classic or fine-grained) for now.
                  Swap for a GitHub App installation token before this
                  server is public-facing (see plan.md, Phase 7).
  MCP_API_KEY   - shared secret your website backend sends as
                  `Authorization: Bearer <MCP_API_KEY>`. This server
                  has exactly one legitimate client (your own backend),
                  so a static bearer token is used instead of full
                  OAuth 2.1 (which the MCP auth spec makes optional and
                  is meant for arbitrary third-party clients).

Optional:
  HOST              - default 0.0.0.0
  PORT              - default 8000
  ANTHROPIC_API_KEY - only required by the Phase 3 test harness
                       (server/test_subagent.py), not by the MCP server or
                       triage.py -- checked separately by validate_anthropic(),
                       not bundled into validate() below.
"""

import os

from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def validate() -> None:
    # MCP_API_KEY is intentionally not required here -- the rating pipeline
    # (triage.py/coordinator.py) calls GitHubClient directly and never goes
    # through app.py's MCP layer, so nothing on that path needs it. Still
    # defined above and in .env.example in case app.py gets used again.
    missing = [
        name
        for name, val in [("GITHUB_TOKEN", GITHUB_TOKEN)]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )


def validate_anthropic() -> None: #Some tests don't require anthropic key, if we added this to validate those tests would wonder where this key is
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "Missing required env var: ANTHROPIC_API_KEY. "
            "Copy .env.example to .env and fill it in."
        )
