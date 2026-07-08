# Idea-Refinement Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `@bot <ý tưởng thô>` (không `DoD:`) mở một vòng Q&A có giới hạn ngay trong thread Slack, kết thúc bằng ticket đầy đủ (Goal + EARS DoD + Tests) qua gate deterministic, bấm **Approve & Run** là loop hiện tại chạy tiếp trong cùng thread.

**Architecture:** `refine.py` mới chứa toàn bộ logic analyst (Slack-free, stateless per turn — history đọc từ `Memory.sessions`, nên restart-safe by construction); `slack_app.py` chỉ wire 3 chỗ (intake không-DoD, reply trong thread refining, 2 button action). Draft lưu trong registry → button đọc từ disk, bấm sau restart vẫn chạy. Spec: `docs/superpowers/specs/2026-07-08-idea-refinement-design.md`.

**Tech Stack:** Python 3 stdlib + slack_bolt (đã có). Không dependency mới.

## Global Constraints

- Tests xanh trước mọi claim: `python3 -m pytest tests -q` (chạy từ `loopkit/`). Baseline hiện tại: 46 passed.
- Không print/commit `SLACK_*_TOKEN`; không AI attribution trong git.
- Output protocol fail-closed: reply thiếu marker `QUESTION:`/`TICKET:` → LUÔN coi là question, không bao giờ auto-draft.
- Gate retry tối đa 2 lần; trần câu hỏi `LOOPKIT_REFINE_MAX_TURNS` default **5**; model analyst default **sonnet**.
- `ENABLE_MEMORY=0` → refinement TẮT, mention không DoD trả message lỗi cú pháp như cũ.
- Message bot user-facing: giữ style VN/EN lẫn hiện có.

---

### Task 1: role analyst + config + `refine.py` (TDD)

**Files:**
- Create: `refine.py`
- Create: `tests/test_refine.py`
- Modify: `roles.py` (thêm `ANALYST` sau `REVIEWER`, thêm vào `REGISTRY` line 43)
- Modify: `config.py` (thêm `REFINE_MAX_TURNS`; thêm key `analyst` vào `ROLE_MODELS`)

**Interfaces:**
- Consumes: `gates.parse_ticket(text) -> (goal, dod, tests)|None×3`; `engine.ask_claude(prompt, soul, model=None) -> str`; `roles.Role`/`roles.REGISTRY`.
- Produces (Task 2 gọi đúng các tên này): `refine.refine_turn(idea: str, history: list[dict], turns_used: int, max_turns: int, ask=ask_claude) -> tuple[str, str]` với kind ∈ {`"ask"`, `"draft"`, `"draft_unvalidated"`, `"error"`}; history item = `{"role": "analyst"|"user", "text": str}`; `config.REFINE_MAX_TURNS: int`; `config.ROLE_MODELS["analyst"]`.

- [ ] **Step 1: Viết failing tests** — tạo `tests/test_refine.py`:

```python
"""Idea-refinement verifiers — refine_turn là một vòng loop framework thu nhỏ."""
import importlib
import config, gates, refine


VALID_TICKET = ('viết hàm foo(x) DoD: WHEN 1 SHALL return 2 Tests: ```python\n'
                'from solution import foo\n\ndef test_foo():\n    assert foo(1) == 2\n```')


def test_config_and_role_defaults():
    importlib.reload(config)
    assert config.REFINE_MAX_TURNS == 5
    assert config.ROLE_MODELS["analyst"] == "sonnet"
    import roles
    assert roles.REGISTRY["analyst"].tools == ()        # analyst không có tool


def test_question_passthrough():
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    ask=lambda p, s, model=None: "QUESTION: A hay B?")
    assert kind == "ask" and text == "A hay B?"


def test_no_marker_treated_as_question():
    """Fail-closed: thiếu marker -> question, KHÔNG BAO GIỜ là draft."""
    kind, _ = refine.refine_turn("idea", [], 0, 5,
                                 ask=lambda p, s, model=None: "tôi nghĩ nên làm X trước")
    assert kind == "ask"


def test_valid_draft_passes_gate():
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    ask=lambda p, s, model=None: "TICKET: " + VALID_TICKET)
    assert kind == "draft"
    goal, dod, tests = gates.parse_ticket(text)
    assert goal and dod and tests                       # draft parse được y như intake thật


def test_bad_draft_retries_twice_then_unvalidated():
    calls = []
    def fake(p, s, model=None):
        calls.append(p)
        return "TICKET: không có dod gì cả"
    kind, _ = refine.refine_turn("idea", [], 0, 5, ask=fake)
    assert kind == "draft_unvalidated" and len(calls) == 3    # initial + 2 gate-retry


def test_budget_exhausted_forces_draft():
    def fake(p, s, model=None):
        if "BUDGET EXHAUSTED" in p:
            return "TICKET: " + VALID_TICKET
        return "QUESTION: gì nữa?"
    kind, _ = refine.refine_turn("idea", [], 5, 5, ask=fake)
    assert kind == "draft"


def test_history_and_turncount_in_prompt():
    seen = {}
    def fake(p, s, model=None):
        seen["p"] = p
        return "QUESTION: ok?"
    refine.refine_turn("làm cache", [{"role": "analyst", "text": "Q1?"},
                                     {"role": "user", "text": "A1"}], 1, 5, ask=fake)
    assert "làm cache" in seen["p"] and "Q1?" in seen["p"] and "A1" in seen["p"]
    assert "1/5" in seen["p"]


def test_empty_reply_retries_once_then_error():
    calls = []
    def fake(p, s, model=None):
        calls.append(1)
        return ""
    kind, _ = refine.refine_turn("idea", [], 0, 5, ask=fake)
    assert kind == "error" and len(calls) == 2
```

- [ ] **Step 2: Chạy để thấy fail**

Run: `python3 -m pytest tests/test_refine.py -q`
Expected: FAIL/ERROR với `ModuleNotFoundError: No module named 'refine'`.

- [ ] **Step 3: Implement.** (a) `roles.py` — thêm sau block `REVIEWER` (trước dòng `REGISTRY = ...`):

```python
ANALYST = Role(
    "analyst",
    "You are a business analyst turning a raw software idea into ONE well-scoped ticket for a "
    "code-generation loop. Ask AT MOST one short question per turn, in the user's language, "
    "multiple-choice (A/B/C) when possible — the user is often on a phone. When you have enough "
    "for a small, self-contained, testable deliverable (or when told the budget is exhausted), "
    "output the final ticket instead. Reply format — EXACTLY one of:\n"
    "QUESTION: <one question, options may span lines>\n"
    "TICKET: <goal, one self-contained module, state any assumptions> DoD: <EARS: WHEN <x> "
    "SHALL <y>; ...> Tests: ```python\n<pytest importing from module `solution`, one test per "
    "criterion, deterministic, no network/filesystem>\n```",
)
```

và sửa dòng REGISTRY thành:

```python
REGISTRY = {r.name: r for r in (ORCHESTRATOR, CODE, INFRA, REVIEWER, ANALYST)}
```

(b) `config.py` — thêm sau block agent tool-mode:

```python
# --- idea refinement (intake stage) ---
REFINE_MAX_TURNS = _env_int("REFINE_MAX_TURNS", 5)   # trần câu hỏi trước khi BUỘC draft
```

và thêm key vào `ROLE_MODELS`:

```python
ROLE_MODELS = {"orchestrator": _env_model("orchestrator", "haiku"),
               "code": _env_model("code", "sonnet"),
               "infra": _env_model("infra", "sonnet"),
               "analyst": _env_model("analyst", "sonnet"),
               "reviewer": _env_model("reviewer", "opus")}
```

(c) tạo `refine.py`:

```python
"""
loopkit.refine — idea-refinement stage: ý tưởng thô -> Q&A có giới hạn -> ticket đầy đủ.

Một instance loop framework đúng nghĩa:
  worker = role analyst (mỗi lượt MỘT câu hỏi, hoặc ticket cuối)
  gate   = gates.parse_ticket (goal + DoD + Tests AST-valid) — deterministic, TRƯỚC human
  retry  = gate fail feedback về analyst, tối đa 2 lần
  stop   = max_turns (config.REFINE_MAX_TURNS); chạm trần -> BUỘC draft
  door   = nút Approve & Run (wire ở slack_app)
  memory = STATELESS per turn: caller đưa full history đọc từ disk -> restart-safe by construction

Protocol fail-closed (bài học vụ VERDICT bị chôn): thiếu marker QUESTION:/TICKET: -> coi là
question, KHÔNG BAO GIỜ tự thành draft.
"""
import config, gates, roles
from engine import ask_claude


def _parse_reply(reply: str):
    """Marker sớm nhất thắng; text từ marker đến hết (question có thể nhiều dòng A/B/C)."""
    text = reply or ""
    up = text.upper()
    found = [(i, kind, m) for i, kind, m in
             ((up.find("TICKET:"), "draft", "TICKET:"),
              (up.find("QUESTION:"), "ask", "QUESTION:")) if i >= 0]
    if not found:
        return "ask", text.strip()
    i, kind, marker = min(found)
    return kind, text[i + len(marker):].strip()


def _valid_draft(draft: str) -> bool:
    goal, dod, tests = gates.parse_ticket(draft)
    return bool(goal and dod and tests)


def refine_turn(idea, history, turns_used, max_turns, ask=ask_claude):
    """Một lượt analyst. history = [{'role': 'analyst'|'user', 'text': ...}, ...].
    -> ('ask', q) | ('draft', ticket) | ('draft_unvalidated', ticket) | ('error', '')."""
    soul = roles.REGISTRY["analyst"].soul
    model = config.ROLE_MODELS.get("analyst")
    convo = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    forced = turns_used >= max_turns
    prompt = (f"RAW IDEA:\n{idea}\n\nCONVERSATION SO FAR:\n{convo or '(none)'}\n\n"
              + ("QUESTION BUDGET EXHAUSTED: output the TICKET now; state assumptions in the goal."
                 if forced else f"Questions used: {turns_used}/{max_turns}."))
    reply = ask(prompt, soul, model=model)
    if not (reply or "").strip():
        reply = ask(prompt, soul, model=model)        # brain trả rỗng: retry đúng 1 lần
        if not (reply or "").strip():
            return "error", ""
    kind, text = _parse_reply(reply)
    if kind == "ask":
        if not forced:
            return "ask", text
        kind, text = _parse_reply(ask(prompt + "\n\nOutput ONLY the TICKET now.",
                                      soul, model=model))
        if kind == "ask":
            return "draft_unvalidated", text          # ponytail: đưa human cái đang có
    for _ in range(2):                                # gate deterministic + bounded retry
        if _valid_draft(text):
            return "draft", text
        _, text = _parse_reply(ask(
            f"{prompt}\n\nYour draft FAILED the format gate. Required: '<goal> DoD: <EARS "
            f"criteria> Tests: ```python ...```' — tests import from `solution`, define test_* "
            f"functions. Output the corrected TICKET.\n\nPREVIOUS DRAFT:\n{text}",
            soul, model=model))
    return ("draft", text) if _valid_draft(text) else ("draft_unvalidated", text)
```

- [ ] **Step 4: Chạy pass**

Run: `python3 -m pytest tests/test_refine.py -q`
Expected: `8 passed`. Rồi full suite: `python3 -m pytest tests -q` → `54 passed`.

- [ ] **Step 5: Commit**

```bash
git add refine.py tests/test_refine.py roles.py config.py
git commit -m "refine: analyst Q&A loop — idea to gated ticket draft"
```

---

### Task 2: wire `slack_app.py` + BUILD-MAP + live E2E

**Files:**
- Modify: `slack_app.py` (import line ~30; `on_mention` lines ~131-141; `on_followup` lines ~143-160; thêm `start_refinement`/`_refine_step` sau `launch_ticket`; thêm 2 action handler sau `_resolve`)
- Modify: `BUILD-MAP.md` (§5 souls ×4→×5; §7 thêm row)
- Không unit test mới (module cần token khi import; logic đã test ở Task 1; xác nhận bằng compile + suite + E2E).

**Interfaces:**
- Consumes: `refine.refine_turn(...)` (Task 1), `config.REFINE_MAX_TURNS`, `Memory.register/get_run/append_event/events`, `launch_ticket(client, channel, thread, text)` (sẵn có).
- Produces: bot chạy với intake mới.

- [ ] **Step 1: Import.** Thêm `import refine` vào dòng import module nội bộ (`import config, gates, shield` → `import config, gates, refine, shield`).

- [ ] **Step 2: Intake không-DoD.** Thay `on_mention` bằng:

```python
@app.event("app_mention")                           # INTAKE
def on_mention(event, client, body):
    if event.get("bot_id"):
        return
    if shield.seen_event(body.get("event_id", "")):  # Slack retries -> process each event once
        return
    thread = event.get("thread_ts", event["ts"])
    if launch_ticket(client, event["channel"], thread, event.get("text", "")):
        return
    if MEM is None:                                  # refinement cần registry+session làm state
        client.chat_postMessage(channel=event["channel"], thread_ts=thread,
            text="🙅 Thiếu Definition of Done. Cú pháp:\n"
                 "`@bot <objective+context>   DoD: <EARS criteria>   [Tests: <pytest code>]`")
        return
    start_refinement(client, event["channel"], thread, event.get("text", ""))
```

- [ ] **Step 3: Refinement core.** Thêm sau `launch_ticket` (trước `on_mention`):

```python
# ---- idea refinement (spec 2026-07-08): mention không DoD -> Q&A -> ticket draft -> button ----
def start_refinement(client, channel, thread, text):
    idea = re.sub(r"<@[^>]+>", "", text or "").strip()
    MEM.register(str(thread), status="refining", idea=_guard(idea[:500]), refine_turns=0)
    client.chat_postMessage(channel=channel, thread_ts=thread,
        text="💡 Chưa có DoD — vào chế độ refinement. Trả lời vài câu hỏi để build ticket "
             "(reply thường trong thread, không cần mention).")
    threading.Thread(target=_refine_step, args=(client, channel, thread), daemon=True).start()


def _refine_step(client, channel, thread):
    """Một lượt refinement: đọc state từ DISK (registry + session) -> analyst -> post.
    Stateless: restart giữa chừng không mất gì."""
    try:
        run = MEM.get_run(str(thread))
        history = [{"role": e["role"], "text": e["text"]}
                   for e in MEM.events(str(thread)) if e.get("stage") == "refine"]
        turns = run.get("refine_turns", 0)
        kind, text = refine.refine_turn(run.get("idea", ""), history, turns,
                                        config.REFINE_MAX_TURNS)
        if kind == "error":
            client.chat_postMessage(channel=channel, thread_ts=thread,
                                    text="💥 refinement lỗi — reply để thử lại.")
            return
        if kind == "ask":
            MEM.append_event(str(thread), {"stage": "refine", "role": "analyst",
                                           "text": _guard(text)})
            MEM.register(str(thread), refine_turns=turns + 1)
            client.chat_postMessage(channel=channel, thread_ts=thread,
                text=_guard(f"❓ ({turns + 1}/{config.REFINE_MAX_TURNS}) {text}"))
            return
        MEM.register(str(thread), status="ticket_drafted", draft=text)   # draft RAW (như artifact)
        warn = ("\n⚠️ Tests trong draft KHÔNG hợp lệ — Approve sẽ rơi về derive-from-DoD."
                if kind == "draft_unvalidated" else "")
        client.chat_postMessage(channel=channel, thread_ts=thread,
            text="Ticket draft — approve để chạy loop?",
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                     "text": _guard(f"🎫 *Ticket draft:*\n```{text[:2500]}```{warn}\n"
                                    f"_Reply để góp ý (analyst sửa lại), hoặc:_")}},
                    {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "ticket_approve",
                 "text": {"type": "plain_text", "text": "Approve & Run"}, "value": str(thread)},
                {"type": "button", "style": "danger", "action_id": "ticket_reject",
                 "text": {"type": "plain_text", "text": "Hủy"}, "value": str(thread)}]}])
    except Exception as e:
        client.chat_postMessage(channel=channel, thread_ts=thread,
                                text=_guard(f"💥 refinement error: {e}"))
```

- [ ] **Step 4: Reply trong thread refining/drafted.** Trong `on_followup`, sau `run = MEM.get_run(str(thread))` / `if not run: return`, thêm TRƯỚC check `dod:`:

```python
    if run.get("status") in ("refining", "ticket_drafted"):   # refinement: mọi reply đều nhận
        if shield.seen_event(body.get("event_id", "")):
            return
        MEM.append_event(str(thread), {"stage": "refine", "role": "user",
                                       "text": _guard(event.get("text", ""))})
        if run.get("status") == "ticket_drafted":             # góp ý trên draft -> redraft
            MEM.register(str(thread), status="refining")
        threading.Thread(target=_refine_step, args=(client, event["channel"], thread),
                         daemon=True).start()
        return
```

- [ ] **Step 5: Buttons.** Thêm sau `_resolve`:

```python
@app.action("ticket_approve")
def _ticket_approve(ack, body):
    ack()
    ts = body["actions"][0]["value"]
    run = MEM.get_run(str(ts)) if MEM else {}
    ch = body.get("channel", {}).get("id")
    if run.get("status") == "ticket_drafted" and run.get("draft") and ch:
        MEM.register(str(ts), status="ticket_approved")       # chặn double-click double-run
        MEM.append_event(str(ts), {"stage": "ticket_approved",
                                   "approver": body.get("user", {}).get("id", "?")})
        launch_ticket(app.client, ch, ts, run["draft"])
    # else: click stale (đã chạy/đã hủy) -> im lặng, không overwrite


@app.action("ticket_reject")
def _ticket_reject(ack, body):
    ack()
    ts = body["actions"][0]["value"]
    if MEM and MEM.get_run(str(ts)).get("status") == "ticket_drafted":
        MEM.register(str(ts), status="refine_cancelled")
        ch = body.get("channel", {}).get("id")
        if ch:
            app.client.chat_postMessage(channel=ch, thread_ts=ts, text="🚫 Draft đã hủy.")
```

- [ ] **Step 6: Compile + full suite**

Run: `python3 -m py_compile slack_app.py && python3 -m pytest tests -q`
Expected: compile im lặng; `54 passed`.

- [ ] **Step 7: BUILD-MAP.** §5: đổi `| Souls ×4 (orchestrator/code/infra/reviewer) | ✅ | \`roles.py\` |` thành `| Souls ×5 (orchestrator/code/infra/reviewer/analyst) | ✅ | \`roles.py\` |`. §7 thêm row sau "Intake (@mention + mandatory DoD)":

```markdown
| Idea-refinement intake (idea → Q&A → ticket) | ✅ | spec 2026-07-08: mention KHÔNG DoD → analyst hỏi ≤`REFINE_MAX_TURNS` câu (reply thường trong thread), draft Goal+DoD+Tests qua gate `parse_ticket`+AST TRƯỚC khi post; [Approve & Run] đọc draft từ registry (restart-safe, event-driven — không cần doors.json analog); statuses refining/ticket_drafted/ticket_approved/refine_cancelled (reaper không đụng); TẮT khi ENABLE_MEMORY=0 |
```

- [ ] **Step 8: Commit**

```bash
git add slack_app.py BUILD-MAP.md
git commit -m "slack: idea-refinement intake — mention without DoD starts analyst Q&A"
```

- [ ] **Step 9: Live E2E (acceptance — PHONE-ONLY)**

1. Restart bot: `pgrep -f slack_app.py` → `kill <pid>` → `./run.sh` (background).
2. Từ điện thoại: `@Khanh's bot muốn có hàm chuẩn hoá số điện thoại VN cho pipeline` (ý tưởng thô, không DoD).
3. Bot vào refinement → trả lời 2–3 câu hỏi bằng reply thường (không mention).
4. **Giữa chừng Q&A: kill bot, restart, reply tiếp** → phiên tiếp tục đúng chỗ (stateless resume — registry `refining` không bị reaper đụng).
5. Nhận `🎫 Ticket draft` (đủ Goal + DoD + Tests) → bấm **Approve & Run**.
6. Loop cũ chạy trong CÙNG thread: freeze gate từ Tests trong draft → worktree → generate → reviewer → door artifact → Approve.
7. Kiểm chứng disk: registry thread có chuỗi status refining → ticket_drafted → ticket_approved → awaiting_approval → done; session có Q&A events (masked); cache có ticket mới.

---

## Self-review (done at write time)

- **Spec coverage:** trigger không-DoD (T2 S2), Q&A reply thường (T2 S4), bounded stop + forced draft (T1 `forced`), gate+retry (T1 loop), protocol fail-closed (T1 `_parse_reply` + test), buttons restart-safe từ registry (T2 S5), statuses mới không bị reaper đụng (không status nào là `running`), ENABLE_MEMORY=0 tắt (T2 S2), edge analyst rỗng (T1 error path + test), reinforcement (TDD per task, subagent writer + review, doors = spec/plan/E2E đã đi qua), E2E phone-only + kill-giữa-Q&A (T2 S9).
- **Placeholders:** không có — mọi step code đầy đủ.
- **Type consistency:** `refine_turn(idea, history, turns_used, max_turns, ask=)` khớp giữa T1 def, T1 tests, T2 `_refine_step`; kind set {ask, draft, draft_unvalidated, error} khớp; `REFINE_MAX_TURNS`/`ROLE_MODELS["analyst"]` khớp T1↔T2; history item keys `role`/`text` khớp append_event T2 ↔ đọc T2 ↔ format T1.
