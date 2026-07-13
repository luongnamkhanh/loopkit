# CLAUDE.md — loopkit

Claude-specific notes for working ON this repo interactively. The universal project rules live
in **AGENTS.md** — read that first; this file does not duplicate it.

- Tests: `python3 -m pytest tests -q` — all must pass before claiming anything is done.
- The loop's brain (`engine.ask_claude`) deliberately runs `claude -p` in a **neutral cwd**
  (`config.BRAIN_CWD`): project context reaches the brain ONLY via the explicit AGENTS.md
  injection (`project_context`). Do not "fix" this by removing the `cwd` — it prevents
  double-injection and keeps the engine brain-agnostic.
- `BUILD-MAP.md` is the design↔implementation source of truth: any newly discovered gap gets
  a row there first.
- Never print or commit `SLACK_*_TOKEN` values; they live in env vars only.
- Restart bot (slack/telegram): pkill PHẢI chạy ngoài sandbox (dangerouslyDisableSandbox) —
  pkill trong sandbox không giết được process ngoài sandbox, để lại bot zombie chạy code cũ.
  Verify bằng `ps -o lstart` (giờ start phải MỚI), đừng tin mỗi pgrep.
