# github-repo-rater

Rates a public GitHub repo across three dimensions — Architecture,
Results/Functionality, and Design/Docs — using three concurrent Claude
subagents, then produces a JSON/Markdown/PDF report. Available two ways:
a one-shot CLI, and a small HTTP backend + companion mobile app for
triggering a rating from a phone.

**Note on `server/app.py`:** an earlier MCP server, now **inactive**. The
CLI and website both call `GitHubClient` directly — the MCP hop was never
part of the real pipeline. `app.py`/`auth.py` stay in the repo but aren't
used by anything below.

## How a rating actually happens

```
triage.py            -- picks a representative sample of files from the repo
  -> coordinator.py   -- runs the 3 rating subagents concurrently, blends
                          their scores into one quality_score
  -> report_writer.py -- validates the result, writes .json/.md/.pdf
```

The three subagents (`server/Subagents/`) each score independently:
**Architecture** (structure, naming, separation of concerns),
**Results/Functionality** (does the code do what it claims, via static
reading only — no code execution), and **Design/Docs** (completeness of
README/docs). A fourth subagent, **Recommendations**, writes suggestions
into the report but never affects the score. Full deduction rules and
score anchors for all three graded dimensions: see
`grading_rubric.pdf` (generated — ask for a fresh copy if the rubric in
the subagent files has since changed).

## Setup — backend

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in GITHUB_TOKEN (a GitHub PAT) and ANTHROPIC_API_KEY
```

**Run a single rating from the CLI:**
```bash
python -m server.Report_Writing.generate_report <owner> <repo>
```
Writes `.json`/`.md`/`.pdf` to `reports/ratings/`.

**Run the website backend** (what the mobile app talks to):
```bash
python -m server.Website.web_app
```
Listens on `http://<HOST>:<PORT>` (default `0.0.0.0:8000`).

| Route | Purpose |
|---|---|
| `POST /api/rate` `{"owner": ..., "repo": ...}` | Rate a repo (cached by `owner+repo+commit SHA` — a repeat request for an unchanged repo costs nothing and doesn't count against the daily cap). Returns the PDF file directly. |
| `GET /api/report/{owner}/{repo}` | Most recent cached rating's PDF for that repo, regardless of commit. |

A daily rating cap (`server/Website/usage_cap.py`) protects the
Anthropic budget behind this server's own credentials, since this is a
public-facing endpoint with no per-user auth.

**Run the tests:**
```bash
python -m pytest tests/
```

## Setup — mobile app

A small Expo/React Native app (`mobile-app/`) — an owner/repo input, a
Rate button, and a share sheet for the resulting PDF. It talks to
whatever backend `API_BASE_URL` in `mobile-app/App.tsx` points at.

```bash
cd mobile-app
npm install
npx expo start        # live preview via the Expo Go app, or:
npx eas-cli build --platform android --profile preview   # standalone .apk
```

`API_BASE_URL` points at the real backend, deployed on Cloud Run (see
`CLOUD_RUN_MIGRATION_PLAN.md`) — a permanent public URL, independent of
any laptop or local network. Earlier this pointed at a raw LAN IP, then
an ngrok tunnel, since a phone can't reach `localhost` on your computer
and a LAN IP breaks every time the computer changes networks — Cloud Run
removes the need for either workaround entirely.

## Known limitations (by design, for now)

- Static analysis only for Results/Functionality — no code execution,
  so "does it run" is inferred from reading, not verified.
- `GITHUB_TOKEN` is a personal PAT — fine at this scale; a GitHub App
  installation token would be the upgrade for real public traffic.
- No user accounts — the website is fully public and anonymous, gated
  only by the shared daily usage cap.
- `--allow-unauthenticated` on the Cloud Run deploy means anyone with
  the URL can call it — the daily usage cap is the only real protection
  right now (see `CLOUD_RUN_MIGRATION_PLAN.md`'s optional Phase 9 for
  adding real auth).

## Project layout

```
server/
  coordinator.py          -- runs the 3 subagents, blends quality_score
  triage.py                -- picks which files each subagent sees
  github_client.py         -- GitHub REST API wrapper
  anthropic_client.py      -- shared Anthropic client + prompt-caching helper
  Subagents/                -- architecture / results / docs / recommendations
  Report_Writing/
    generate_report.py     -- CLI entry point
    report_writer.py       -- JSON/Markdown/PDF rendering
    report_schema.py       -- RepoRating pydantic schema
  Website/
    web_app.py              -- Starlette app, /api/rate + /api/report routes
    cache_index.py           -- (owner, repo, sha) -> cached rating lookup
    usage_cap.py              -- daily rating limit
  app.py, auth.py            -- inactive Phase 1 MCP server, unused
mobile-app/                  -- Expo/React Native client
tests/                        -- pytest suite
reports/ratings/               -- generated reports + cache/usage-cap state
```
