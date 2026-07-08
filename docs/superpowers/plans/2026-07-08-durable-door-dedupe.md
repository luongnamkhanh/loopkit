# §8.1 Durable Door + Dedupe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run suspended at the Slack human door survives a bot restart (click still completes it), and Slack event dedupe survives restarts too.

**Architecture:** Persist open doors to `doors.json` via `Memory` with a new `awaiting_approval` registry status; a Slack-free `engine.finish_suspended()` replays the post-door tail (register → cache-if-approved → deliver) from the persisted payload when a click arrives after a restart; `shield` dedupe gains a disk-backed id log trimmed to 1000 at startup. Spec: `docs/superpowers/specs/2026-07-08-durable-door-dedupe-design.md`.

**Tech Stack:** Python 3 stdlib only (json/pathlib/threading). No new dependencies.

## Global Constraints

- All tests green before any claim of done: `python3 -m pytest tests -q` (run from `loopkit/`).
- Never print or commit `SLACK_*_TOKEN` values (repo CLAUDE.md).
- No AI attribution anywhere in git (no Co-Authored-By trailers, no generated-with footers).
- Cache rule (spec §2): a rejected artifact is NEVER stored in the semantic cache.
- Registry artifact truncation is `[:4000]`, Slack artifact delivery truncation `[:2500]` — same as `engine.run_loop`.
- The bot currently runs in the background; Task 4 restarts it. Bot user-facing strings are mixed VN/EN matching existing style.

---

### Task 1: Durable doors in `memory.py`

**Files:**
- Modify: `memory.py` (add `doors_path` in `__init__` at line ~36; add three methods after `reap_running`)
- Test: `tests/test_memory.py` (append)

**Interfaces:**
- Consumes: existing `Memory._load`, `Memory._lock`, `Memory.register`, `Memory.reap_running`.
- Produces: `Memory.door_open(thread_id: str, payload: dict) -> None` (stamps `opened_at` itself), `Memory.door_get(thread_id: str) -> dict | None`, `Memory.door_close(thread_id: str) -> None` (idempotent). Registry status string `"awaiting_approval"` (set by callers, not by these methods). Task 4 relies on these exact names.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_memory.py`:

```python
def test_doors_roundtrip_and_idempotent_close(tmp_path):
    """§8.1: a door persisted on disk survives the process; close is idempotent."""
    mem = Memory(str(tmp_path / "m"))
    assert mem.door_get("t1") is None
    mem.door_open("t1", {"channel": "C1", "artifact": "A", "goal": "g", "dod": "d"})
    door = mem.door_get("t1")
    assert door["channel"] == "C1" and door["artifact"] == "A" and "opened_at" in door
    mem2 = Memory(str(tmp_path / "m"))                       # simulated restart
    assert mem2.door_get("t1")["artifact"] == "A"
    mem2.door_close("t1")
    assert mem2.door_get("t1") is None
    mem2.door_close("t1")                                    # no raise


def test_reaper_leaves_awaiting_approval(tmp_path):
    """§8.1: a run suspended at a persisted door is NOT dead — reaper must skip it."""
    mem = Memory(str(tmp_path / "m"))
    mem.register("suspended", status="awaiting_approval")
    assert mem.reap_running() == []
    assert mem.get_run("suspended")["status"] == "awaiting_approval"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_memory.py -q`
Expected: `test_doors_roundtrip_and_idempotent_close` FAILS with `AttributeError: 'Memory' object has no attribute 'door_get'`; `test_reaper_leaves_awaiting_approval` PASSES already (reaper only touches `running`) — keep it as a pin.

- [ ] **Step 3: Implement** — in `memory.py`, add to `__init__` after `self.cache_path = self.dir / "cache.json"`:

```python
        self.doors_path = self.dir / "doors.json"
```

and add after `reap_running`:

```python
    # --- durable doors (§8.1): a run suspended at the human door survives restarts ---
    def door_open(self, thread_id: str, payload: dict):
        with self._lock:
            doors = self._load(self.doors_path)
            doors[thread_id] = {**payload, "opened_at": time.time()}
            self.doors_path.write_text(json.dumps(doors, ensure_ascii=False, indent=1))

    def door_get(self, thread_id: str):
        return self._load(self.doors_path).get(thread_id)

    def door_close(self, thread_id: str):
        with self._lock:
            doors = self._load(self.doors_path)
            if doors.pop(thread_id, None) is not None:
                self.doors_path.write_text(json.dumps(doors, ensure_ascii=False, indent=1))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_memory.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add memory.py tests/test_memory.py
git commit -m "durable doors: persist open human-doors in doors.json (§8.1)"
```

---

### Task 2: `engine.finish_suspended` — Slack-free resume completion

**Files:**
- Modify: `engine.py` (add function after `run_loop`, end of file)
- Test: `tests/test_engine.py` (append; add `from memory import Memory` to its imports)

**Interfaces:**
- Consumes: `Memory.register`, `Memory.store`, `Memory.recall` (existing); `shield.mask`; `config.ENABLE_SHIELD`. Does NOT consume Task 1's door methods — it receives the payload dict.
- Produces: `finish_suspended(mem, thread_id: str, payload: dict, decision: bool, notify: Callable[[str], None]) -> None`. Payload keys read: `artifact`, `goal`, `dod`. Task 4 calls this exact signature.

- [ ] **Step 1: Write the failing tests** — in `tests/test_engine.py`, change the import line `import config, engine, roles` to `import config, engine, roles` plus add below it `from memory import Memory`, then append:

```python
def test_finish_suspended_approve_registers_caches_delivers(tmp_path):
    """§8.1 resume: approve after restart -> registry done/approved, cached, delivered."""
    mem = Memory(str(tmp_path / "m"))
    mem.register("t1", status="awaiting_approval")
    msgs = []
    payload = {"channel": "C1", "artifact": "X=1", "goal": "g", "dod": "d"}
    engine.finish_suspended(mem, "t1", payload, True, msgs.append)
    run = mem.get_run("t1")
    assert run["status"] == "done" and run["approved"] is True
    assert mem.recall("g", "d") == "X=1"                    # cached: verified+approved
    assert any("X=1" in m for m in msgs)                    # artifact delivered


def test_finish_suspended_reject_no_cache_no_artifact(tmp_path):
    mem = Memory(str(tmp_path / "m"))
    mem.register("t1", status="awaiting_approval")
    msgs = []
    payload = {"channel": "C1", "artifact": "X=1", "goal": "g", "dod": "d"}
    engine.finish_suspended(mem, "t1", payload, False, msgs.append)
    run = mem.get_run("t1")
    assert run["status"] == "done" and run["approved"] is False
    assert mem.recall("g", "d") is None                     # NEVER cache a rejected artifact
    assert not any("X=1" in m for m in msgs)                # and never deliver it
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: both new tests FAIL with `AttributeError: module 'engine' has no attribute 'finish_suspended'`.

- [ ] **Step 3: Implement** — append to `engine.py`:

```python
def finish_suspended(mem, thread_id: str, payload: dict, decision: bool,
                     notify: Callable[[str], None]) -> None:
    """§8.1 resume path: complete a run whose process died while suspended at the human
    door. Mirrors run_loop's post-door tail (register done -> cache only if approved ->
    deliver) from the persisted door payload. `turns` is unknown here and stays absent."""
    guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
    artifact = payload.get("artifact", "")
    mem.register(thread_id, status="done", approved=decision, artifact=artifact[:4000])
    if decision:
        mem.store(payload.get("goal", ""), payload.get("dod", ""), artifact)
        notify("✅ approved (resumed sau restart)")
        notify(f"📦 artifact:\n```\n{guard(artifact[:2500])}\n```")
    else:
        notify("🚫 rejected (resumed sau restart) — không áp dụng artifact")
```

(`Callable`, `config`, `shield` are already imported at the top of `engine.py`.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_engine.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "finish_suspended: complete a door-suspended run after restart (§8.1)"
```

---

### Task 3: Durable dedupe in `shield.py`

**Files:**
- Modify: `shield.py` (add `import pathlib`; add `_seen_path` + `init_dedupe`; extend `seen_event`)
- Test: `tests/test_shield.py` (append; add `from collections import OrderedDict` to its imports)

**Interfaces:**
- Consumes: existing `_seen: OrderedDict`, `_MAX_SEEN = 1000`.
- Produces: `init_dedupe(path) -> None` (accepts `str` or `pathlib.Path`); `seen_event` behavior unchanged for callers. Task 4 calls `shield.init_dedupe(<MEMORY_DIR>/events.seen)`.

- [ ] **Step 1: Write the failing tests** — in `tests/test_shield.py`, add `from collections import OrderedDict` below `import shield`, then append:

```python
def test_dedupe_survives_restart(tmp_path, monkeypatch):
    """§8.1: a Slack retry delivered after a bot restart must still be deduped."""
    monkeypatch.setattr(shield, "_seen", OrderedDict())
    monkeypatch.setattr(shield, "_seen_path", None)
    f = tmp_path / "events.seen"
    shield.init_dedupe(f)
    assert shield.seen_event("ev1") is False
    assert shield.seen_event("ev1") is True
    monkeypatch.setattr(shield, "_seen", OrderedDict())      # simulated restart
    monkeypatch.setattr(shield, "_seen_path", None)
    shield.init_dedupe(f)
    assert shield.seen_event("ev1") is True                  # remembered across restart


def test_dedupe_file_trimmed_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(shield, "_seen", OrderedDict())
    monkeypatch.setattr(shield, "_seen_path", None)
    f = tmp_path / "events.seen"
    f.write_text("\n".join(f"e{i}" for i in range(1500)) + "\n")
    shield.init_dedupe(f)
    lines = f.read_text().splitlines()
    assert len(lines) == 1000 and lines[0] == "e500"         # last 1000 kept
    assert shield.seen_event("e1499") is True                # loaded into memory too
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_shield.py -q`
Expected: both FAIL with `AttributeError: <module 'shield'> does not have the attribute '_seen_path'`.

- [ ] **Step 3: Implement** — in `shield.py`: change `import re` to `import pathlib, re`; then replace the dedupe block (everything from `_seen: "OrderedDict[str, None]" = OrderedDict()` to the end of `seen_event`) with:

```python
_seen: "OrderedDict[str, None]" = OrderedDict()
_MAX_SEEN = 1000
_seen_path = None            # set by init_dedupe -> ids also persist to disk (§8.1)


def init_dedupe(path):
    """§8.1 durable dedupe: load the last _MAX_SEEN ids from disk into _seen, trim the
    file to exactly those, and append every new id from now on. Call once at startup."""
    global _seen_path
    p = pathlib.Path(path)
    ids = [i for i in p.read_text().splitlines() if i][-_MAX_SEEN:] if p.exists() else []
    for i in ids:
        _seen[i] = None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(ids) + ("\n" if ids else ""))
    _seen_path = p


def seen_event(event_id: str) -> bool:
    """True if this event_id was already processed (caller should skip the delivery)."""
    if not event_id:
        return False
    if event_id in _seen:
        return True
    _seen[event_id] = None
    if _seen_path:
        with open(_seen_path, "a") as f:                    # ponytail: append-only during a
            f.write(event_id + "\n")                        # session; re-trimmed at startup
    if len(_seen) > _MAX_SEEN:
        _seen.popitem(last=False)
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_shield.py -q`
Expected: all PASS (including the four pre-existing tests — `seen_event` without `init_dedupe` behaves exactly as before).

- [ ] **Step 5: Commit**

```bash
git add shield.py tests/test_shield.py
git commit -m "durable dedupe: persist slack event ids across restarts (§8.1)"
```

---

### Task 4: Wire `slack_app.py`, update BUILD-MAP, live E2E

**Files:**
- Modify: `slack_app.py` (imports line 31; `make_door` lines 52–67; `launch_ticket` call line ~108; `_resolve` lines 166–176; `__main__` lines 178–183)
- Modify: `BUILD-MAP.md` (§3 row for §8.1; §8 header line)
- No new unit test (module needs live tokens at import; covered by Tasks 1–3 units + live E2E below).

**Interfaces:**
- Consumes: `Memory.door_open/door_get/door_close` (Task 1), `engine.finish_suspended(mem, thread_id, payload, decision, notify)` (Task 2), `shield.init_dedupe(path)` (Task 3).
- Produces: the running bot.

- [ ] **Step 1: Wire the door.** In `slack_app.py` replace `make_door` with:

```python
def make_door(thread_ts, client, channel, goal, dod):
    def door(artifact: str) -> bool:
        ev = threading.Event(); _pending[thread_ts] = {"event": ev, "approved": False}
        if MEM:                                  # §8.1: persist so a restart can resume it
            MEM.door_open(thread_ts, {"channel": channel, "artifact": artifact,
                                      "goal": goal, "dod": dod})
            MEM.register(thread_ts, status="awaiting_approval")
        preview = _guard((artifact or "")[:1500])    # never ask a blind approval
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
            text="Reviewer PASS — approve this change?",
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                     "text": f"Reviewer PASS — artifact chờ duyệt:\n```{preview}```"}},
                    {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "approve",
                 "text": {"type": "plain_text", "text": "Approve"}, "value": thread_ts},
                {"type": "button", "style": "danger", "action_id": "reject",
                 "text": {"type": "plain_text", "text": "Reject"}, "value": thread_ts}]}])
        ev.wait(timeout=3600)                    # in-process wait; disk is the recovery path
        if MEM:
            MEM.door_close(thread_ts)
        return _pending.pop(thread_ts, {"approved": False})["approved"]
    return door
```

and in `launch_ticket` change the `run_loop(...)` call's door argument to `human_door=make_door(thread, client, channel, goal, dod),` — note `goal` here is the possibly-augmented revision goal, which keeps the resume cache key identical to `run_loop`'s.

- [ ] **Step 2: Wire the resume branch.** Change the engine import line to `from engine import Ticket, run_loop, read_agents_md, finish_suspended` and replace `_resolve` with:

```python
def _resolve(body, decision):
    ts = body["actions"][0]["value"]
    user = body.get("user", {}).get("id", "?")
    if ts in _pending:                               # live click: run_loop thread finishes it
        if MEM:                                      # four-eyes audit trail on disk (who + what)
            MEM.audit(str(ts), approver=user, decision=decision)
        _pending[ts]["approved"] = decision
        _pending[ts]["event"].set()
        return
    door = MEM.door_get(str(ts)) if MEM else None
    if door:                                         # §8.1 resume: process died at this door
        MEM.audit(str(ts), approver=user, decision=decision)
        finish_suspended(MEM, str(ts), door, decision,
                         lambda msg: app.client.chat_postMessage(
                             channel=door["channel"], thread_ts=ts, text=msg))
        MEM.door_close(str(ts))
    elif MEM:                                        # truly stale click: evidence, no overwrite
        MEM.append_event(str(ts), {"stage": "human_door_stale", "approver": user,
                                   "approved": decision})
```

- [ ] **Step 3: Wire dedupe at startup.** In `__main__`, before the `mode = ...` line add:

```python
    shield.init_dedupe(pathlib.Path(config.MEMORY_DIR) / "events.seen")
```

- [ ] **Step 4: Compile + full suite**

Run: `python3 -m py_compile slack_app.py && python3 -m pytest tests -q`
Expected: compile silent; all tests PASS (46 expected: 40 existing + 6 new from Tasks 1–3).

- [ ] **Step 5: Update BUILD-MAP.** In §3 replace the §8.1 row with:

```markdown
| §8.1 durable execution | ✅ | scoped "door+dedupe" (spec 2026-07-08): startup reaper; doors persist (`doors.json`, status `awaiting_approval`, reaper skips) → click-after-restart completes via `engine.finish_suspended`; slack event ids persist (`events.seen`, trimmed 1000 at boot). Full run-resume deliberately out of scope |
```

and in §8's header paragraph change `§8.1 durable execution ·` to `§8.1 durable execution (✅ scoped door+dedupe) ·`. Also in §7 update the "Socket transport resilience" row note `true restart-safety → §8.1` to `door restart-safety ✅; mid-generation kills stay interrupted (accepted)`.

- [ ] **Step 6: Commit**

```bash
git add slack_app.py BUILD-MAP.md
git commit -m "wire durable door resume + persistent dedupe into slack app (§8.1)"
```

- [ ] **Step 7: Live E2E (the P4 acceptance — replays the original failure)**

1. Restart the bot to load new code: `pgrep -f slack_app.py`, `kill <pid>`, `./run.sh` (background).
2. In Slack, @mention the bot with a ticket using a goal never run before (avoid cache recall), e.g. `@bot viết hàm int_to_roman(n) đổi số nguyên 1..3999 sang số La Mã   DoD: WHEN 4 SHALL return "IV"; WHEN 1994 SHALL return "MCMXCIV"; WHEN 3999 SHALL return "MMMCMXCIX"`.
3. Wait for the door message ("Reviewer PASS — artifact chờ duyệt"). Do NOT click. Verify `doors.json` has the thread and registry says `awaiting_approval`.
4. `kill <pid>`, `./run.sh` again. Startup must NOT reap the suspended run (registry still `awaiting_approval`).
5. Click **Approve** on the old door message.
6. Expected: thread gets `✅ approved (resumed sau restart)` + the artifact; registry flips to `done, approved: true`; `doors.json` no longer has the thread; cache contains the ticket. Also verify a fresh identical @mention now recalls (`♻️`).

---

## Self-review (done at write time)

- **Spec coverage:** doors (§1→Task 1), finish_suspended + three-way `_resolve` + `awaiting_approval` (§2→Tasks 2,4,1), durable dedupe (§3→Task 3), unit+live verification (§4→each task + Task 4 Step 7). `turns`-absent limitation carried into `finish_suspended` docstring.
- **Placeholders:** none; every code step has full code.
- **Type consistency:** `door_get -> dict|None` consumed as truthy-dict in `_resolve`; `finish_suspended` signature identical in Task 2 (def) and Task 4 (call); `init_dedupe(path)` accepts the `pathlib.Path` passed in Task 4.
