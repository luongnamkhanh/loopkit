# AGENTS.md — loopkit

Standing context for **any** agent working in THIS repo.
(Not a persona — that's `roles.py`. Not a task — that's a ticket. This is *how this project
works + what's off-limits*. Authored per target project; injected into every worker/reviewer call.)

## Project overview
loopkit: a minimal agentic loop framework — orchestrator routes a ticket to a worker
(generator) → deterministic gate → separate skeptical reviewer → feedback → bounded stop →
human door. The "brain" is the `claude` CLI.

## Commands
- Local example:    `python example_local.py`
- Multi-agent demo: `python example_multiagent.py`
- Slack bot:        `python slack_app.py`   (needs `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` in env)
- Tests:            `python3 -m pytest tests -q`

## Architecture
- `engine.py`     — the loop (`run_loop`) + orchestrator `route()`
- `roles.py`      — named agents (orchestrator / code / infra / reviewer): soul + tool scope
- `slack_app.py`  — Slack Socket-Mode front door (intake + human-door buttons)
- `README.md`, `TICKET_TEMPLATE.md` — docs

## Conventions
- Every ticket MUST carry a **checkable Definition of Done** — it is the loop's stop condition.
- The **deterministic gate runs BEFORE** the LLM reviewer.
- Keep it minimal: stdlib-first, no speculative abstraction.
- Persist state to disk (`run_journal.jsonl`), never rely on in-context memory.

## Boundaries (never)
- **NEVER commit secrets/tokens.** `SLACK_*_TOKEN` live in env vars only.
- Do not add heavy dependencies without a clear reason.
- Do not auto-merge / auto-apply a risky change — it goes through the human door.
