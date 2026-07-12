# loopkit — gated, reviewed, human-approved agent runs

A disciplined agent loop you can point at any git repo:

```
ticket (Goal + EARS DoD) → GENERATOR → DETERMINISTIC GATE (first, frozen before generation)
   → separate skeptical REVIEWER → bounded retry → HUMAN DOOR
   → auto-DELIVER: place file → re-run gate → commit feat/<module> → push → MR
```

The human keeps exactly one job: pressing **Approve** with the artifact and its
destination path in front of them. Everything before and after that click is
deterministic and journaled to disk.

## Install

Requires Python ≥ 3.10 and the [`claude` CLI](https://claude.com/claude-code) on PATH (the brain).

```bash
pip install "git+https://github.com/luongnamkhanh/loopkit"                      # core — zero deps
pip install "loopkit[slack] @ git+https://github.com/luongnamkhanh/loopkit"     # + Slack front
```

## Quickstart (CLI)

`cd` into the target repo, then:

```bash
# full ticket in one line — gate is built from Tests: (or derived from the DoD and frozen)
loopkit run 'normalize VN phone numbers DoD: WHEN input has +84/84/0 prefix SHALL return 0-prefixed ... Tests: ```python
from solution import normalize_phone_vn
def test_plus84(): assert normalize_phone_vn("+84912345678") == "0912345678"
```'

loopkit idea "vague idea here"   # analyst Q&A → drafts the ticket → you approve the draft
loopkit status                   # registry of runs in this repo
```

What happens on `run`: recall check (identical verified ticket → skip generation) →
route to a worker role → generate inside an isolated **git worktree** → deterministic
gate (pytest) → skeptical reviewer (separate model) → feedback loop, bounded by
`MAX_TURNS` → human door. If the ticket carries `Deliver: path/to/module.py`, an
approved artifact is then **shipped**: moved to that path, test imports rewritten,
gate re-run on the moved files, committed on `feat/<module>`, pushed, MR created.

## Four fronts, one engine

- **CLI** — terminal y/N door. `Deliver:` shown before you approve.
- **Claude session** — non-interactive verbs (`idea start/answer`, `ticket run`,
  `approve/reject/show`) plus a skill so any Claude Code session can drive the loop.
  Install: symlink `skills/loopkit` into `~/.claude/skills/`. Hard rule baked into the
  skill: only a human's explicit word triggers `approve`.
- **Slack** — `@mention` the bot with a ticket (or a bare idea), steps stream into the
  thread, door is an **Approve/Reject** button. Doors persist on disk: click after a
  restart still completes the run. Multi-repo routing via `Repo: <name>` against the
  `LOOPKIT_REPOS` allowlist. Run with `loopkit-slack` (`SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` env).
- **Telegram** — message the bot directly (no mention needed): a ticket with `DoD:` runs it,
  anything else starts idea refinement; door is an inline **Approve/Reject** keyboard. Zero
  extra deps. Run `loopkit-telegram` with `LOOPKIT_TG_TOKEN` (BotFather) +
  `LOOPKIT_TG_CHAT_ID` (your chat — everything else is silently dropped).

## Delivery

Placement is decided **at freeze time, before generation** — explicit `Deliver:` token
in the ticket, or inferred by a small brain call that reads the repo tree. The door
shows the path (with an `(overwrites existing)` warning when applicable): approving the
artifact approves its placement. After that, no LLM sits between your click and the
commit — the ship chain is pure git + pytest. Any step failing reports loudly, keeps
the branch local, and never rolls back the approval. MR creation uses `glab`/`gh` when
available, else posts the create-MR link from the push output.

## Knobs (all env, prefix `LOOPKIT_`)

| Knob | Default | What |
|---|---|---|
| `MAX_TURNS` | 4 | bounded retry per run |
| `ENABLE_TOOLS` | 0 | generator/reviewer act inside the worktree (headless Claude Code) |
| `TARGET_REPO` | — | repo-mode default target (CLI uses cwd) |
| `REPOS` / `REPOS_PENDING` | — | Slack multi-repo allowlist: `name=/path;...` |
| `DELIVER` | 1 | post-approve ship chain on/off |
| `MR_TOOL` | auto | `auto\|glab\|gh\|link\|off` |
| `MODEL_<ROLE>` | haiku/sonnet/opus | per-role model tiering (router/workers/reviewer) |
| `ENABLE_SHIELD` | 1 | secret/PII masking at every notify/persistence boundary |
| `ENABLE_MEMORY` | 1 | journal + registry + per-thread sessions + semantic cache |

## Layout

`src/loopkit/`: `engine.py` (the loop) · `gates.py` (ticket parsing, EARS→pytest, frozen
gates) · `deliver.py` (post-approve ship) · `memory.py` · `shield.py` · `workspace.py`
(worktree per ticket) · `refine.py` (idea→ticket analyst) · `roles.py` · `fronts/`
(cli, slack). Design↔implementation traceability lives in `BUILD-MAP.md` — every gap
gets a row there first.

## v1 status — honest edges

- 114 tests green; live E2E passed: Slack approve → auto-deliver, zero manual git.
- The real-MR path (`glab`/`gh`) is unit-tested against mocks; live runs have proven the
  chain up to push. The push-output-link fallback is fully tested.
- Reviewer is uncalibrated (no gold-set TPR/TNR yet) and there is no golden regression
  set — both tracked in `BUILD-MAP.md` §8, deliberately deferred until real-run data.
- Cache-hit re-approval does not re-deliver (tracked, deferred until live demand).

## License

MIT
