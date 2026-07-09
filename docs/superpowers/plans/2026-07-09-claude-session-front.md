# Claude-Session Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bộ verbs CLI không-tương-tác (idea start/answer, ticket run, approve/reject, show) + skill file để bất kỳ Claude Code session nào lái trọn vòng loopkit qua bash — door duyệt cuối luôn là người.

**Architecture:** Không sửa engine: suspend-door là door factory mới trong `fronts/cli.py` (persist + return False), `approve/reject` dùng nguyên `finish_suspended` + `door_close` của §8.1; refinement per-step đọc state từ disk y hệt `_refine_step` của Slack. Output có marker đầu dòng cho agent parse. Spec: `docs/superpowers/specs/2026-07-09-claude-session-front-design.md`.

**Tech Stack:** Python stdlib; không dependency mới.

## Global Constraints

- Baseline: **70 passed**. Verbs cũ (`run`, `idea` tương tác, `status`) không đổi hành vi.
- Markers ở ĐẦU DÒNG, một marker/dòng: `THREAD:` `QUESTION:` `DRAFT:`/`DRAFT_END` `DRAFT_UNVALIDATED:` `AWAITING_APPROVAL` `ARTIFACT:`/`ARTIFACT_END` `APPROVED` `REJECTED` `FAILED:` `STALE:` `STATUS:`.
- Exit codes: 0 = thành công (kể cả `AWAITING_APPROVAL`); 1 = lỗi/exhausted/stale.
- Mọi output qua `_mask`. Không AI attribution trong git.

---

### Task 1: Agent verbs trong `fronts/cli.py` (TDD)

**Files:**
- Modify: `src/loopkit/fronts/cli.py` (thêm import `finish_suspended`, `json`; thêm 7 hàm; sửa `main()` dispatch)
- Test: `tests/test_cli_agent.py` (create)

**Interfaces:**
- Consumes: `finish_suspended(mem, thread_id, payload, decision, notify)`; `Memory.door_open/door_get/door_close/audit/get_run/events/append_event/register/recall`; `refine.refine_turn(idea, history, turns_used, max_turns, repos=None, ask=)`; `gates.parse_repo/parse_ticket/make_pytest_gate/make_compile_gate/derive_tests`; `run_loop`, `make_workspace`.
- Produces: `cmd_idea_start(idea) -> int`, `cmd_idea_answer(thread, answer) -> int`, `cmd_ticket_run(thread) -> int`, `cmd_resolve(thread, decision: bool) -> int`, `cmd_show(thread) -> int`, `make_suspend_door(mem, thread, goal, dod) -> callable`, `_build_verifier(mem, goal, dod, tests_src, wd)`. CLI: `loopkit idea start|answer …`, `loopkit ticket run <t>`, `loopkit approve|reject|show <t>`.

- [ ] **Step 1: Failing tests** — tạo `tests/test_cli_agent.py`:

```python
"""Agent-drivable verbs — state trên disk giữa các lần gọi; door luôn tách khỏi run."""
import json
from loopkit import config
from loopkit.fronts import cli
from loopkit.memory import Memory


def _mem_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Memory(config.MEMORY_DIR)


def test_idea_start_prints_thread_and_question(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: ("ask", "A hay B?"))
    assert cli.main(["idea", "start", "muốn hàm f"]) == 0
    out = capsys.readouterr().out
    assert "THREAD: cli-" in out and "QUESTION: A hay B?" in out
    t = [l.split(": ")[1] for l in out.splitlines() if l.startswith("THREAD:")][0]
    assert mem.get_run(t)["status"] == "refining"


def test_idea_answer_reads_history_from_disk(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="refining", idea="ý tưởng", refine_turns=1)
    mem.append_event("t1", {"stage": "refine", "role": "analyst", "text": "Q1?"})
    seen = {}
    def fake(idea, history, turns, mx, **kw):
        seen["h"] = history
        return "draft", "viết f DoD: WHEN x SHALL y"
    monkeypatch.setattr(cli.refine, "refine_turn", fake)
    assert cli.main(["idea", "answer", "t1", "B"]) == 0
    out = capsys.readouterr().out
    assert "DRAFT:" in out and "DRAFT_END" in out
    assert [h["text"] for h in seen["h"]] == ["Q1?", "B"]      # history từ DISK, không RAM
    assert mem.get_run("t1")["status"] == "ticket_drafted"


def test_idea_answer_on_draft_means_feedback_redraft(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="ticket_drafted", idea="i", refine_turns=2, draft="cũ")
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: ("ask", "sửa gì?"))
    assert cli.main(["idea", "answer", "t1", "đổi tên hàm"]) == 0
    assert "QUESTION:" in capsys.readouterr().out


def test_idea_answer_wrong_status_stale(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done")
    assert cli.main(["idea", "answer", "t1", "B"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_suspend_door_persists_and_returns_false(tmp_path, monkeypatch):
    mem = _mem_cwd(tmp_path, monkeypatch)
    door = cli.make_suspend_door(mem, "t1", "goal g", "dod d")
    assert door("artifact X") is False
    d = mem.door_get("t1")
    assert d["artifact"] == "artifact X" and d["goal"] == "goal g" and d["channel"] == "cli"


def test_ticket_run_ends_awaiting(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="ticket_drafted", draft="viết f DoD: WHEN x SHALL y")
    def fake_run_loop(t, human_door=None, **kw):
        human_door("code XYZ")                                  # loop chạm door
        return {"ok": True, "approved": False, "worker": "code", "turns": 1,
                "artifact": "code XYZ"}
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    assert cli.main(["ticket", "run", "t1"]) == 0
    out = capsys.readouterr().out
    assert "AWAITING_APPROVAL" in out and "ARTIFACT:" in out and "code XYZ" in out
    assert mem.door_get("t1") is not None                       # door còn mở trên disk


def test_ticket_run_without_draft_stale(tmp_path, monkeypatch, capsys):
    _mem_cwd(tmp_path, monkeypatch)
    assert cli.main(["ticket", "run", "nope"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_approve_completes_and_caches(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done", approved=False)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["approve", "t1"]) == 0
    out = capsys.readouterr().out
    assert "APPROVED" in out and "X=1" in out
    assert mem.get_run("t1")["approved"] is True
    assert mem.recall("g", "d") == "X=1"
    assert mem.door_get("t1") is None                           # door đã đóng


def test_reject_no_cache(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["reject", "t1"]) == 0
    assert "REJECTED" in capsys.readouterr().out
    assert mem.recall("g", "d") is None


def test_approve_without_door_stale(tmp_path, monkeypatch, capsys):
    _mem_cwd(tmp_path, monkeypatch)
    assert cli.main(["approve", "t9"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_show_reports_awaiting_when_door_open(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done", approved=False)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["show", "t1"]) == 0
    out = capsys.readouterr().out
    assert "STATUS: awaiting_approval" in out and "ARTIFACT:" in out
```

- [ ] **Step 2: Chạy fail.** `python3 -m pytest tests/test_cli_agent.py -q` — Expected: ERROR/FAIL hàng loạt (`SystemExit` từ argparse vì chưa có subcommand `ticket`/`approve`…, `AttributeError: make_suspend_door`).

- [ ] **Step 3: Implement.** Trong `src/loopkit/fronts/cli.py`:

(a) Sửa dòng import engine thành:

```python
from loopkit.engine import Ticket, run_loop, read_agents_md, finish_suspended
```

(b) Tách helper gate dùng chung (thay block gate trong `cmd_run` hiện tại bằng lời gọi helper):

```python
def _build_verifier(mem, goal, dod, tests_src, wd):
    if mem and mem.recall(goal, dod) is not None:
        return gates.make_compile_gate(wd)           # unused: run_loop recall trước gate
    if tests_src:
        print("🧪 gate = pytest (tests từ ticket)")
        return gates.make_pytest_gate(tests_src, wd)
    derived = gates.derive_tests(goal, dod)          # fresh call TRƯỚC generation; frozen
    if derived:
        print(f"🧪 gate = pytest (derived, frozen):\n{_mask(derived[:1200])}")
        return gates.make_pytest_gate(derived, wd)
    print("⚠️ Không derive được test — gate compile-only (YẾU).")
    return gates.make_compile_gate(wd)
```

(trong `cmd_run`, block `if mem and mem.recall(...) ... else: ...` thay bằng `verifier = _build_verifier(mem, goal, dod, tests_src, wd)`.)

(c) Thêm các hàm agent-mode:

```python
# ---- agent-mode verbs (Claude-session front): mỗi lệnh một bước, state trên disk ----
def _agent_refine_step(mem, thread) -> int:
    run = mem.get_run(thread)
    history = [{"role": e["role"], "text": e["text"]}
               for e in mem.events(thread) if e.get("stage") == "refine"]
    turns = run.get("refine_turns", 0)
    kind, text = refine.refine_turn(run.get("idea", ""), history, turns,
                                    config.REFINE_MAX_TURNS)
    if kind == "error":
        print("FAILED: refinement error — thử lại lệnh")
        return 1
    if kind == "ask":
        mem.append_event(thread, {"stage": "refine", "role": "analyst", "text": _mask(text)})
        mem.register(thread, refine_turns=turns + 1)
        print(f"QUESTION: {_mask(text)}")
        return 0
    mem.register(thread, status="ticket_drafted", draft=text)
    if kind == "draft_unvalidated":
        print("DRAFT_UNVALIDATED: tests trong draft không hợp lệ — run sẽ derive từ DoD")
    print("DRAFT:")
    print(_mask(text))
    print("DRAFT_END")
    return 0


def cmd_idea_start(idea: str) -> int:
    mem = _mem()
    if mem is None:
        print("FAILED: cần LOOPKIT_ENABLE_MEMORY=1")
        return 1
    thread = f"cli-{int(time.time() * 1000)}"
    mem.register(thread, status="refining", idea=_mask(idea[:500]), refine_turns=0)
    print(f"THREAD: {thread}")
    return _agent_refine_step(mem, thread)


def cmd_idea_answer(thread: str, answer: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    if run.get("status") not in ("refining", "ticket_drafted"):
        print(f"STALE: thread không ở refinement (status={run.get('status')})")
        return 1
    mem.append_event(thread, {"stage": "refine", "role": "user", "text": _mask(answer)})
    if run.get("status") == "ticket_drafted":                   # góp ý trên draft -> redraft
        mem.register(thread, status="refining")
    return _agent_refine_step(mem, thread)


def make_suspend_door(mem, thread, goal, dod):
    """Door không chặn cho agent-mode: persist rồi trả False — approve là lệnh riêng."""
    def door(artifact: str) -> bool:
        mem.door_open(thread, {"channel": "cli", "artifact": artifact,
                               "goal": goal, "dod": dod})
        return False
    return door


def cmd_ticket_run(thread: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    draft = run.get("draft")
    if run.get("status") != "ticket_drafted" or not draft:
        print(f"STALE: thread chưa có draft (status={run.get('status')})")
        return 1
    repo_name, text = gates.parse_repo(draft)
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        print("FAILED: draft không parse được DoD")
        return 1
    mem.register(thread, status="ticket_approved")
    repo = _cwd_repo()
    wd, kind = make_workspace(thread, repo=repo)
    if kind == "worktree":
        print(f"🌿 workspace = worktree {wd}")
    verifier = _build_verifier(mem, goal, dod, tests_src, wd)
    ctx = "" if (repo and config.ENABLE_TOOLS) else read_agents_md(".")
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True)
    res = run_loop(t, human_door=make_suspend_door(mem, thread, goal, dod),
                   notify=print, project_context=ctx, memory=mem,
                   thread_id=str(thread), workspace=wd)
    if res.get("ok") and mem.door_get(thread):
        print("AWAITING_APPROVAL")
        print("ARTIFACT:")
        print(_mask((res.get("artifact") or "")[:2500]))
        print("ARTIFACT_END")
        return 0
    if res.get("ok"):                                           # phòng hờ: ok mà không door
        print("DONE")
        return 0
    print(f"FAILED: {res.get('reason')}")
    return 1


def cmd_resolve(thread: str, decision: bool) -> int:
    mem = _mem()
    door = mem.door_get(thread) if mem else None
    if not door:
        print("STALE: không có door đang mở cho thread này")
        return 1
    mem.audit(thread, approver="cli-human", decision=decision)  # người đã gõ duyệt ở session
    finish_suspended(mem, thread, door, decision, print)
    mem.door_close(thread)
    print("APPROVED" if decision else "REJECTED")
    return 0


def cmd_show(thread: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    if not run:
        print("STALE: không có run cho thread này")
        return 1
    door = mem.door_get(thread)
    print(f"STATUS: {'awaiting_approval' if door else run.get('status', '?')}")
    if door:
        print("ARTIFACT:")
        print(_mask((door.get("artifact") or "")[:2500]))
        print("ARTIFACT_END")
    elif run.get("draft"):
        print("DRAFT:")
        print(_mask(run["draft"][:2500]))
        print("DRAFT_END")
    return 0
```

(d) `main()` — thay toàn bộ bằng:

```python
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="loopkit",
                                 description="loop framework — gated, reviewed agent runs")
    ap.add_argument("--version", action="version", version=f"loopkit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="chạy một ticket đầy đủ (door tương tác)").add_argument("ticket")
    p_idea = sub.add_parser("idea", help="refinement: '<ý tưởng>' (tương tác) | start/answer (agent)")
    p_idea.add_argument("args", nargs="+")
    p_ticket = sub.add_parser("ticket", help="agent-mode: ticket run <thread>")
    p_ticket.add_argument("args", nargs=2)                      # ("run", thread)
    for name in ("approve", "reject", "show"):
        sub.add_parser(name).add_argument("thread")
    sub.add_parser("status", help="registry của repo hiện tại (cwd)")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.ticket)
    if args.cmd == "idea":
        a = args.args
        if a[0] == "start" and len(a) == 2:
            return cmd_idea_start(a[1])
        if a[0] == "answer" and len(a) == 3:
            return cmd_idea_answer(a[1], a[2])
        if len(a) == 1:
            return cmd_idea(a[0])                               # tương tác như cũ
        print("FAILED: dùng: idea '<ý tưởng>' | idea start '<ý tưởng>' | idea answer <thread> '<trả lời>'")
        return 1
    if args.cmd == "ticket":
        if args.args[0] == "run":
            return cmd_ticket_run(args.args[1])
        print("FAILED: dùng: ticket run <thread>")
        return 1
    if args.cmd == "approve":
        return cmd_resolve(args.thread, True)
    if args.cmd == "reject":
        return cmd_resolve(args.thread, False)
    if args.cmd == "show":
        return cmd_show(args.thread)
    return cmd_status()
```

- [ ] **Step 4: Chạy pass.** `python3 -m pytest tests -q` — Expected: **81 passed** (70 + 11 mới; các test cũ của `cmd_run`/`cmd_idea` không đổi vì behavior giữ nguyên).

- [ ] **Step 5: Commit.**

```bash
git add src/loopkit/fronts/cli.py tests/test_cli_agent.py
git commit -m "agent verbs: idea start/answer, ticket run (suspend door), approve/reject/show"
```

---

### Task 2: Skill file

**Files:**
- Create: `skills/loopkit/SKILL.md`
- Không unit test (markdown); verify = install + đọc lại.

- [ ] **Step 1: Viết skill.** `skills/loopkit/SKILL.md`:

```markdown
---
name: loopkit
description: Use when the user wants to build a small, testable module/function with a gated, reviewed, human-approved loop (a "loopkit ticket") — drives the loopkit CLI step-by-step from any repo. Trigger words: "loopkit", "chạy loop", "build có gate", "ticket". Do NOT use for multi-file refactors or repos without loopkit installed.
---

# Driving loopkit from a Claude session

loopkit chạy vòng: idea → analyst Q&A → ticket (Goal+DoD+Tests, frozen gate) → generate →
deterministic gate → skeptical reviewer → HUMAN door. Bạn (Claude) là FRONT — người lái,
không phải người duyệt.

## Ba luật cứng (không có ngoại lệ)

1. **Tự trả lời phải khai báo.** Bạn được trả lời câu hỏi analyst khi context hội thoại đã
   chứa câu trả lời — nhưng PHẢI liệt kê cho người dùng thấy từng câu bạn đã tự trả lời và
   trả lời gì. Câu chưa biết → hỏi người dùng (AskUserQuestion nếu dạng A/B/C).
2. **Draft phải qua mắt người.** Khi `DRAFT:` xuất hiện, đưa nguyên văn cho người dùng.
   Chỉ chạy `loopkit ticket run` sau khi họ đồng ý (họ có thể góp ý → `idea answer` để redraft).
3. **Approve chỉ sau chữ duyệt tường minh.** KHÔNG BAO GIỜ chạy `loopkit approve <thread>`
   nếu người dùng chưa gõ duyệt rõ ràng trong lượt chat hiện tại. "Chắc ok" / im lặng /
   suy diễn = hỏi lại. Bạn là relay của four-eyes, không phải con mắt thứ hai.

## Protocol

1. Kiểm tra: `loopkit --version` (thiếu → `pip install "git+https://github.com/luongnamkhanh/loopkit"`).
2. `cd` vào repo đích (cwd = repo; git repo → mỗi ticket một worktree `loop/<thread>`).
3. `loopkit idea start "<ý tưởng thô của người dùng>"` → ghi lại `THREAD: <id>`.
4. Lặp: đọc `QUESTION:` → trả lời theo Luật 1 → `loopkit idea answer <thread> "<trả lời>"`.
5. `DRAFT:`…`DRAFT_END` → áp Luật 2. (`DRAFT_UNVALIDATED:` = tests hỏng, nói rõ cho người dùng.)
6. Người dùng OK → `loopkit ticket run <thread>` (chạy vài phút — gate, generate, review).
7. `AWAITING_APPROVAL` + `ARTIFACT:` → đưa artifact cho người dùng, áp Luật 3.
8. Duyệt tường minh → `loopkit approve <thread>`; từ chối → `loopkit reject <thread>`.
   Mất dấu → `loopkit show <thread>` / `loopkit status`.

## Markers

`THREAD:` `QUESTION:` `DRAFT:`/`DRAFT_END` `DRAFT_UNVALIDATED:` `AWAITING_APPROVAL`
`ARTIFACT:`/`ARTIFACT_END` `APPROVED` `REJECTED` `FAILED:` `STALE:` `STATUS:` — luôn ở đầu
dòng. Exit 0 = bước thành công (kể cả AWAITING_APPROVAL); 1 = FAILED/STALE.

## Sau approve

Artifact nằm ở worktree branch `loop/<thread>` của repo — nhắc người dùng bước giao hàng
(move file theo convention repo, commit, push, MR) vẫn là bước tay cho tới khi MR-delivery
được build.
```

- [ ] **Step 2: Install + verify.**

```bash
mkdir -p ~/.claude/skills/loopkit && cp skills/loopkit/SKILL.md ~/.claude/skills/loopkit/SKILL.md
head -4 ~/.claude/skills/loopkit/SKILL.md
```
Expected: frontmatter với `name: loopkit`.

- [ ] **Step 3: Commit.**

```bash
git add skills/
git commit -m "skill: drive loopkit from any claude session (human-only approve)"
```

---

### Task 3: E2E dogfood (inline — chính session này lái)

**Files:** `BUILD-MAP.md` (§9 update). Không code mới.

- [ ] **Step 1: Reinstall editable** (verbs mới vào PATH): `/Users/khanhluong/miniconda3/bin/pip install -e . -q && loopkit --version` → `loopkit 0.1.0`.
- [ ] **Step 2: Dogfood trọn vòng** tại repo thật (vd annamgt-streaming-pipeline), do CHÍNH Claude session lái qua bash: `idea start` → tự trả lời câu đã biết (khai báo với người dùng) / relay câu chưa biết → đưa `DRAFT:` cho người dùng → OK → `ticket run` → relay `ARTIFACT:` → **chờ người dùng gõ duyệt** → `loopkit approve` → verify: registry `approved=True`, cache có ticket, door đóng.
- [ ] **Step 3: BUILD-MAP.** §9 thêm row:

```markdown
| Claude-session front (agent verbs + skill) | ✅ | spec 2026-07-09: idea start/answer + ticket run (suspend door — persist, không block) + approve/reject/show qua `finish_suspended` §8.1; markers đầu dòng; skill 3 luật cứng (tự-trả-lời-khai-báo · draft-qua-mắt-người · approve-chỉ-sau-chữ-duyệt); E2E = chính Claude session lái |
```

- [ ] **Step 4: Commit + push.**

```bash
git add BUILD-MAP.md && git commit -m "claude-session front done: agent verbs + skill" && git push
```

---

## Self-review (done at write time)

- **Spec coverage:** verbs + markers + exit codes (T1), suspend door không sửa engine + độ lệch status done-vs-door-mở xử lý ở `show` (T1 `cmd_show` đọc doors trước), góp-ý-trên-draft → redraft (T1 `cmd_idea_answer`), skill 3 luật + protocol + install + check version (T2), E2E tự-ăn (T3), roadmap/BUILD-MAP (T3).
- **Placeholders:** không có.
- **Type consistency:** `make_suspend_door(mem, thread, goal, dod)` khớp def/call/test; `cmd_resolve(thread, decision: bool)` khớp approve/reject dispatch; `finish_suspended(mem, thread, door, decision, print)` khớp signature §8.1; `_build_verifier(mem, goal, dod, tests_src, wd)` dùng ở cả `cmd_run` (refactor) lẫn `cmd_ticket_run`; test đếm 81 = 70 + 11.
