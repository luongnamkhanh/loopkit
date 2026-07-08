# §8.1 Durable execution — door suspend/resume + durable dedupe

**Date:** 2026-07-08 · **Status:** approved (design) · **Scope decision:** "door + dedupe"
(full run-resume and Temporal explicitly rejected — see Out of scope).

## Problem

Lived twice: the bot process dies while a run is blocked at the human door
(`slack_app.make_door` waits on an in-process `threading.Event`). The waiting thread dies
with the process, the registry entry sticks at `running` (now reaped to `interrupted` at
startup), and clicks on the old door message hit the stale path — the run can never
complete. Separately, Slack event dedupe (`shield.seen_event`) is in-memory, so a Slack
retry delivered shortly after a restart double-runs a ticket.

## Design

### 1. Durable doors (`memory.py`)

`Memory` gains a fourth store, `doors.json` (same dir, same lock/JSON pattern as
`registry.json`):

- `door_open(thread_id, payload: dict)` — persist the door; payload keys:
  `channel`, `artifact` (full, raw — same rationale as the semantic cache), `goal`,
  `dod`, `opened_at`.
- `door_get(thread_id) -> dict | None`
- `door_close(thread_id)` — remove entry (missing key is a no-op).

New registry status **`awaiting_approval`**, set via `register()` when a door opens.
Consequence: `reap_running()` (which only touches `running`) never reaps a suspended
run — no reaper change.

### 2. Door flow (`slack_app.py` + `engine.py`)

Normal path (process survives) is unchanged except bookkeeping:

- `make_door.door()`: `door_open(...)` + `register(status="awaiting_approval")` before
  posting buttons; `door_close(...)` after `ev.wait` resolves (click **or** 1h timeout).
  `run_loop` then finishes as today (it will register `done` over `awaiting_approval`).

Resume path (process restarted while door open) — in `_resolve`, replacing today's
two-way branch with three:

1. `ts in _pending` → today's live-click behavior (audit + set event).
2. else if `MEM.door_get(ts)` → **resume**: call `engine.finish_suspended(...)`, then
   `door_close(ts)`.
3. else → today's stale-click audit event.

`engine.finish_suspended(mem, thread_id, payload, decision, notify)` — Slack-free so it
is unit-testable; `slack_app` passes `notify` = post-to-thread:

- `mem.audit(thread_id, approver, decision)` stays in `_resolve` (it has `body`);
  `finish_suspended` does the rest:
- `mem.register(thread_id, status="done", approved=decision, artifact=payload["artifact"][:4000])`
- if approved: `mem.store(goal, dod, artifact)` (cache only verified+approved, as in
  `run_loop`)
- `notify` the outcome (`✅ approved (resumed)` / `⏸️ rejected (resumed)`) and, if
  approved, the artifact (masked, truncated 2500 — same as the normal path).

Known limitation (accepted): `turns` is unknown at resume time (the door never receives
it); resumed registry entries omit `turns`.

### 3. Durable dedupe (`shield.py`)

- `init_dedupe(path)` — read existing file (if any), keep the **last 1000** ids, load
  them into `_seen`, rewrite the file to exactly those ids, remember the path.
- `seen_event(id)` — unchanged logic; additionally appends each **new** id to the file
  when a path is set. In-memory eviction at 1000 stays; the file is re-trimmed on each
  startup, so it cannot grow unbounded across a session in any way that matters.
- Wiring: `slack_app.__main__` calls `shield.init_dedupe(<MEMORY_DIR>/events.seen)`
  unconditionally (dedupe is not a memory-tier feature).

### 4. Verification

Unit (all monkeypatched, no Slack, no LLM):
- `doors` roundtrip: open → get → close; close is idempotent.
- `finish_suspended` approve: registry `done/approved=True`, cache stores, notify called
  with artifact. Reject: no cache store, no artifact message.
- Reaper leaves `awaiting_approval` untouched (extend existing reaper test).
- Dedupe: ids seen before `init_dedupe` re-load survive a simulated restart (second
  `init_dedupe` on the same file → `seen_event` returns True); trim to 1000 verified.

Live E2E (P4 acceptance, mirrors the original failure): run a ticket to the door → kill
the bot → restart → click Approve on the old message → thread gets the resumed-approval
message and the artifact; registry `done/approved=True`; cache contains the ticket.

## Out of scope (deliberate)

- Full run-resume from the journal (mid-generation kills stay `interrupted`; re-mention
  is cheap and the semantic cache absorbs repeats).
- Door TTL/expiry — an unclicked door sits visible in `doors.json`/registry.
- Temporal or any external durable-execution runtime.
- Durable `_pending` for the *live* wait (the `threading.Event` stays in-memory; disk is
  only the recovery path).
