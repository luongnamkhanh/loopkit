# Telegram Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Front #4 `fronts/telegram.py` — chạy loopkit từ điện thoại qua Telegram bot: intake ticket/idea, Q&A refinement, door inline-keyboard, delivery report; stdlib thuần.

**Architecture:** Vỏ mỏng quanh engine sẵn có: long-poll `getUpdates` (urllib) → dispatch (chat_id gate + dedupe `shield.seen_event`) → intake (`parse_ticket`/refine) → suspend door persist qua `Memory.doors` → callback → `finish_suspended` (delivery engine-level chạy sẵn). KHÔNG threading — xử lý tuần tự, single-user (ponytail: poll dừng trong lúc generate, chấp nhận; door kiểu suspend nên nút Approve xử lý ở poll kế tiếp).

**Tech Stack:** Python stdlib (urllib, json). Spec: `docs/superpowers/specs/2026-07-11-telegram-front-design.md`.

## Global Constraints

- Stdlib THUẦN — không dependency mới, không extra; front nằm trong core.
- Import sạch: `import loopkit.fronts.telegram` không side-effect, không đòi token — token/chat_id check trong `main()`. Đây là khác biệt chủ đích với slack.py (không unit-test được).
- `LOOPKIT_TG_CHAT_ID` là trust boundary: update từ chat khác → drop IM LẶNG (không reply, không log nội dung).
- Reuse, không viết lại: `shield.seen_event` (dedupe), `Memory` doors/registry/audit, `gates.parse_repo/parse_deliver/parse_ticket/derive_tests/make_*_gate`, `deliver.freeze_deliver`, `engine.finish_suspended`, `workspace.make_workspace`, `refine.refine_turn`.
- Routing answer 3 luật KHÔNG state (spec §3); statuses `refining` VÀ `ticket_drafted` đều tính là "đang chờ input" (góp ý trên draft = redraft, như CLI/Slack).
- Toàn bộ test hiện có (114) pass sau MỖI task: `python3 -m pytest tests -q`. Không AI attribution trong commit.
- Mọi text ra ngoài mask qua shield trước khi `send` (như slack `_guard`).
- `slack.py` không đụng một dòng.

---

### Task 1: Config knobs + `TgApi` (urllib, mock được)

**Files:**
- Modify: `src/loopkit/config.py` (thêm sau block delivery)
- Create: `src/loopkit/fronts/telegram.py` (phần đầu: docstring + imports + TgApi)
- Test: `tests/test_telegram.py` (file mới)

**Interfaces:**
- Produces: `config.TG_TOKEN`, `config.TG_CHAT_ID` (str, env `LOOPKIT_TG_*`, default `""`).
- Produces: `TgApi(token)` với: `get_updates(offset:int) -> list` (long-poll 50s, lỗi mạng → `[]`); `send(text, reply_to=None, keyboard=None) -> int|None` (message_id, cắt 4000 chars); `answer_callback(cb_id, text="")`; `clear_buttons(message_id)`. Mọi lỗi mạng/parse → None/[], KHÔNG raise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telegram.py
import io
import json

from loopkit import config
from loopkit.fronts import telegram as tg


def _fake_urlopen(payload, capture):
    """urlopen giả: ghi lại (url, body), trả payload Telegram-style."""
    def fake(req, timeout=None):
        capture.append((req.full_url, json.loads(req.data.decode()), timeout))
        return io.BytesIO(json.dumps(payload).encode())
    return fake


def test_tgapi_get_updates_and_offset_params(monkeypatch):
    calls = []
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        _fake_urlopen({"ok": True, "result": [{"update_id": 7}]}, calls))
    api = tg.TgApi("TOK")
    out = api.get_updates(5)
    assert out == [{"update_id": 7}]
    url, body, timeout = calls[0]
    assert "botTOK/getUpdates" in url and body["offset"] == 5 and body["timeout"] == 50
    assert timeout == 60                      # http timeout > long-poll timeout


def test_tgapi_network_error_returns_empty(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("net down")
    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    api = tg.TgApi("TOK")
    assert api.get_updates(0) == []           # không raise — bot sống qua lỗi mạng
    assert api.send("hi") is None


def test_tgapi_send_returns_message_id_and_truncates(monkeypatch):
    calls = []
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        _fake_urlopen({"ok": True, "result": {"message_id": 42}}, calls))
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    api = tg.TgApi("TOK")
    mid = api.send("x" * 5000, keyboard=[[{"text": "A", "callback_data": "d"}]])
    assert mid == 42
    _, body, _ = calls[0]
    assert body["chat_id"] == "111" and len(body["text"]) == 4000
    assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "d"
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m pytest tests/test_telegram.py -q`
Expected: FAIL — `No module named 'loopkit.fronts.telegram'`

- [ ] **Step 3: Add knobs to `config.py`** (sau block delivery)

```python
# --- telegram front (spec 2026-07-11) ---
TG_TOKEN = _env_str("TG_TOKEN", "")
TG_CHAT_ID = _env_str("TG_CHAT_ID", "")      # trust boundary: chỉ nhận update từ chat này
```

- [ ] **Step 4: Create `src/loopkit/fronts/telegram.py`**

```python
"""loopkit Telegram front (front #4) — personal-use mobile front, stdlib thuần.

Long-poll getUpdates (không webhook, không public URL — local-first). Import sạch:
token/chat_id chỉ check trong main() nên module unit-test được (khác slack.py).
Trust boundary: chỉ nhận update từ LOOPKIT_TG_CHAT_ID — còn lại drop im lặng.
Door kiểu suspend (persist rồi trả False) → nút Approve xử lý ở poll kế tiếp,
kể cả sau restart (doors.json + finish_suspended, §8.1 reuse nguyên).
"""
import json, time, urllib.request

from loopkit import config, deliver, gates, refine, shield
from loopkit.engine import Ticket, run_loop, read_agents_md, finish_suspended
from loopkit.memory import Memory
from loopkit.workspace import make_workspace

_API = "https://api.telegram.org/bot{token}/{method}"


def _mask(s: str) -> str:
    return shield.mask(s) if config.ENABLE_SHIELD else s


class TgApi:
    """Vỏ urllib mỏng — mock được. Mọi lỗi mạng/parse → None/[], không raise."""

    def __init__(self, token: str):
        self.token = token

    def _call(self, method: str, http_timeout: int = 15, **params):
        req = urllib.request.Request(
            _API.format(token=self.token, method=method),
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                out = json.loads(r.read().decode())
            if not isinstance(out, dict):        # JSON hợp lệ nhưng không phải dict (proxy/CDN lỗi)
                return None
            return out.get("result") if out.get("ok") else None
        except (OSError, ValueError):
            return None

    def get_updates(self, offset: int) -> list:
        return self._call("getUpdates", http_timeout=60, offset=offset, timeout=50,
                          allowed_updates=["message", "callback_query"]) or []

    def send(self, text: str, reply_to=None, keyboard=None):
        p = {"chat_id": config.TG_CHAT_ID, "text": (text or "")[:4000]}
        if reply_to:
            p["reply_to_message_id"] = reply_to
        if keyboard:
            p["reply_markup"] = {"inline_keyboard": keyboard}
        r = self._call("sendMessage", **p)
        return r.get("message_id") if isinstance(r, dict) else None

    def answer_callback(self, cb_id: str, text: str = ""):
        self._call("answerCallbackQuery", callback_query_id=cb_id, text=text[:190])

    def clear_buttons(self, message_id):
        self._call("editMessageReplyMarkup", chat_id=config.TG_CHAT_ID,
                   message_id=message_id, reply_markup={"inline_keyboard": []})
```

- [ ] **Step 5: Run tests** — `python3 -m pytest tests/test_telegram.py -q` → PASS; full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add src/loopkit/config.py src/loopkit/fronts/telegram.py tests/test_telegram.py
git commit -m "telegram front: TgApi stdlib wrapper + TG_TOKEN/TG_CHAT_ID knobs"
```

---

### Task 2: `launch_ticket` + suspend door

**Files:**
- Modify: `src/loopkit/fronts/telegram.py`
- Test: `tests/test_telegram.py` (append)

**Interfaces:**
- Consumes: `TgApi` (Task 1); engine/gates/deliver/workspace theo Global Constraints.
- Produces: `launch_ticket(text: str, thread: str, mem, api) -> None` — parse Repo/Deliver/ticket, allowlist fail-closed TRƯỚC mọi LLM call, workspace, verifier+frozen_tests, freeze deliver (skip khi recall), `run_loop` với door suspend. `make_tg_door(mem, thread, goal, dod, deliver, repo, ws, tests, api)` — door persist payload keys `channel/artifact/goal/dod/deliver/repo/workspace/tests` (đúng bộ `finish_suspended` đọc) + `mem.register(thread, door_msg=<message_id>)` (không set status — run_loop sẽ đè; doors.json là nguồn chân lý), trả False.

- [ ] **Step 1: Write the failing tests** (append)

```python
class FakeTgApi:
    def __init__(self):
        self.sent = []          # (text, keyboard)
        self.answered = []
        self.cleared = []

    def send(self, text, reply_to=None, keyboard=None):
        self.sent.append((text, keyboard))
        return len(self.sent)   # message_id giả tăng dần

    def answer_callback(self, cb_id, text=""):
        self.answered.append((cb_id, text))

    def clear_buttons(self, message_id):
        self.cleared.append(message_id)


class MemStub:
    """Memory giả đủ cho front: registry + doors + events trên dict."""

    def __init__(self):
        self.reg, self.doors, self.evts, self.audits = {}, {}, {}, []

    def register(self, t, **f):
        self.reg.setdefault(t, {}).update(f)

    def get_run(self, t):
        return dict(self.reg.get(t, {}))

    def runs(self):
        return {k: dict(v) for k, v in self.reg.items()}

    def door_open(self, t, p):
        self.doors[t] = p

    def door_get(self, t):
        return self.doors.get(t)

    def door_close(self, t):
        self.doors.pop(t, None)

    def append_event(self, t, e):
        self.evts.setdefault(t, []).append(e)

    def events(self, t):
        return list(self.evts.get(t, []))

    def audit(self, t, approver, decision):
        self.audits.append((t, approver, decision))

    def recall(self, g, d):
        return None

    def store(self, *a):
        ...


def test_launch_ticket_repo_not_in_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "REPOS", {"pipeline": "/x"})
    api, mem = FakeTgApi(), MemStub()
    called = []
    monkeypatch.setattr(tg, "run_loop", lambda *a, **k: called.append(1))
    tg.launch_ticket("goal Repo: unknown DoD: WHEN x SHALL y", "tg-1", mem, api)
    assert not called                          # fail-closed TRƯỚC mọi LLM call
    assert any("allowlist" in t for t, _ in api.sent)


def test_launch_ticket_wires_ticket_and_suspend_door(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REPOS", {})
    monkeypatch.setattr(config, "TARGET_REPO", "")
    seen = {}

    def fake_run_loop(t, human_door=None, **kw):
        seen["ticket"] = t
        assert human_door("ARTIFACT") is False          # suspend: persist rồi trả False
        return {"ok": True, "approved": False, "worker": "code", "turns": 1}

    monkeypatch.setattr(tg, "run_loop", fake_run_loop)
    monkeypatch.setattr(tg, "make_workspace", lambda th, repo=None: (str(tmp_path), "dir"))
    monkeypatch.setattr(tg.gates, "derive_tests", lambda g, d: None)   # gate compile fallback
    api, mem = FakeTgApi(), MemStub()
    tg.launch_ticket("do thing DoD: WHEN x SHALL y", "tg-9", mem, api)
    assert seen["ticket"].risky is True
    door = mem.doors["tg-9"]
    assert door["channel"] == "telegram" and door["artifact"] == "ARTIFACT"
    assert set(door) >= {"goal", "dod", "deliver", "repo", "workspace", "tests"}
    assert mem.reg["tg-9"]["door_msg"]                   # message_id lưu để gỡ nút
    assert any(k for _, k in api.sent if k)              # có message kèm keyboard Approve
```

- [ ] **Step 2: Run to verify FAIL** — `no attribute 'launch_ticket'`.

- [ ] **Step 3: Implement** (append vào telegram.py)

```python
def make_tg_door(mem, thread, goal, dod, deliver_path, repo, ws, tests, api):
    """Suspend door: persist đủ payload cho finish_suspended rồi trả False — không block poll."""
    def door(artifact: str) -> bool:
        mem.door_open(thread, {"channel": "telegram", "artifact": artifact, "goal": goal,
                               "dod": dod, "deliver": deliver_path, "repo": repo,
                               "workspace": ws, "tests": tests})
        dline = f"\n📦 Deliver: {deliver_path}" if deliver_path else ""
        mid = api.send(f"🚪 Artifact chờ duyệt:{dline}\n{_mask((artifact or '')[:2500])}",
                       keyboard=[[{"text": "✅ Approve", "callback_data": f"door:yes:{thread}"},
                                  {"text": "🚫 Reject", "callback_data": f"door:no:{thread}"}]])
        # KHÔNG set status ở đây: run_loop sẽ register "done" ngay sau khi door trả False
        # (tiền lệ cli.make_suspend_door) — doors.json mới là nguồn chân lý cho "awaiting".
        mem.register(thread, door_msg=mid)
        return False
    return door


def launch_ticket(text: str, thread: str, mem, api) -> None:
    repo_name, text = gates.parse_repo(text)
    deliver_path, text = gates.parse_deliver(text)
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        api.send("🙅 Thiếu DoD.")
        return
    if repo_name and repo_name not in config.REPOS:            # fail-closed trước LLM
        api.send(f"🙅 Repo `{repo_name}` không có trong allowlist. "
                 f"Hợp lệ: {', '.join(sorted(config.REPOS)) or '(trống)'}")
        return
    if repo_name in config.REPOS_PENDING:
        api.send(f"⏳ Repo `{repo_name}` chờ domain gate.")
        return
    repo_path = config.REPOS.get(repo_name) if repo_name else config.TARGET_REPO
    api.send(_mask(f"🧩 Nhận ticket.\nGoal: {goal}\nDoD: {dod}"))
    ws_key = f"{repo_name}-{thread}" if repo_name else thread
    wd, kind = make_workspace(ws_key, repo=repo_path)
    recalled = mem.recall(goal, dod) is not None
    if recalled:
        verifier, frozen_tests = gates.make_compile_gate(wd), ""   # unused: run_loop recall trước
    elif tests_src:
        verifier, frozen_tests = gates.make_pytest_gate(tests_src, wd), tests_src
        api.send("🧪 gate = pytest (tests từ ticket)")
    else:
        derived = gates.derive_tests(goal, dod)                    # fresh, TRƯỚC generation
        if derived:
            verifier, frozen_tests = gates.make_pytest_gate(derived, wd), derived
            api.send(_mask(f"🧪 gate = pytest (derived, frozen):\n{derived[:1200]}"))
        else:
            verifier, frozen_tests = gates.make_compile_gate(wd), ""
            api.send("⚠️ Không derive được test — gate compile-only (YẾU).")
    dpath = None if recalled else deliver.freeze_deliver(deliver_path, goal,
                                                         repo_path or "", emit=api.send)
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True,
               deliver=dpath, repo=repo_path or "", tests_src=frozen_tests)
    res = run_loop(t, human_door=make_tg_door(mem, thread, goal, dod, dpath or "",
                                              repo_path or "", wd, frozen_tests, api),
                   notify=api.send,
                   project_context=("" if (repo_path and config.ENABLE_TOOLS)
                                    else read_agents_md(".")),
                   memory=mem, thread_id=thread, workspace=wd)
    if res.get("ok") and mem.door_get(thread):
        return                                            # door message đã gửi kèm nút
    if res.get("ok"):
        status = "✅ approved" if res.get("approved") else "⏸️ done — chưa duyệt"
        api.send(f"{status} (worker={res.get('worker')}, turns={res.get('turns')})")
    else:
        api.send(f"❌ {res.get('reason')}")
```

- [ ] **Step 4: Run** — focused PASS; full suite `python3 -m pytest tests -q` PASS.
- [ ] **Step 5: Commit**

```bash
git add src/loopkit/fronts/telegram.py tests/test_telegram.py
git commit -m "telegram front: ticket intake with suspend door, allowlist fail-closed"
```

---

### Task 3: refine + `handle_message` (3 luật routing, không state)

**Files:**
- Modify: `src/loopkit/fronts/telegram.py`
- Test: `tests/test_telegram.py` (append)

**Interfaces:**
- Consumes: `launch_ticket` (Task 2), `refine.refine_turn(idea, history, turns, max_turns, repos=None)`.
- Produces: `refine_step(thread, answer: str|None, mem, api)` — answer=None nghĩa là lượt đầu của idea mới; draft → keyboard `draft:run:<t>` / `draft:cancel:<t>` (KHÔNG có nút góp ý — góp ý = nhắn tin thường). `handle_message(msg: dict, mem, api)` — DoD → `launch_ticket`; không DoD → 3 luật (statuses "refining"/"ticket_drafted" đều là chờ-input).

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_handle_message_dod_launches_ticket(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    seen = {}
    monkeypatch.setattr(tg, "launch_ticket", lambda text, th, m, a: seen.update(t=text, th=th))
    tg.handle_message({"message_id": 5, "text": "goal DoD: WHEN x SHALL y"}, mem, api)
    assert seen["th"] == "tg-5" and "DoD:" in seen["t"]


def test_handle_message_three_routing_rules(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    routed = []
    monkeypatch.setattr(tg, "refine_step",
                        lambda th, ans, m, a: routed.append((th, ans)))
    # luật 2: không thread chờ-input -> idea MỚI (answer=None, thread mới đăng ký)
    tg.handle_message({"message_id": 1, "text": "make a widget"}, mem, api)
    assert routed[-1] == ("tg-1", None) and mem.reg["tg-1"]["status"] == "refining"
    # luật 1: đúng MỘT thread chờ-input -> message trần là ANSWER
    tg.handle_message({"message_id": 2, "text": "option B"}, mem, api)
    assert routed[-1] == ("tg-1", "option B")
    # ticket_drafted cũng tính là chờ-input (góp ý trên draft)
    mem.reg["tg-1"]["status"] = "ticket_drafted"
    tg.handle_message({"message_id": 3, "text": "thêm case None"}, mem, api)
    assert routed[-1] == ("tg-1", "thêm case None")
    # luật 3: >=2 thread chờ-input -> từ chối, không route
    mem.register("tg-9", status="refining")
    n = len(routed)
    tg.handle_message({"message_id": 4, "text": "answer nào?"}, mem, api)
    assert len(routed) == n and any("chốt bớt" in t for t, _ in api.sent)


def test_refine_step_ask_then_draft(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    mem.register("tg-1", status="refining", idea="widget", refine_turns=0)
    monkeypatch.setattr(tg.refine, "refine_turn",
                        lambda idea, h, t, mx, repos=None: ("ask", "Câu hỏi 1?"))
    tg.refine_step("tg-1", None, mem, api)
    assert any("Câu hỏi 1?" in t for t, _ in api.sent)
    assert mem.reg["tg-1"]["refine_turns"] == 1
    monkeypatch.setattr(tg.refine, "refine_turn",
                        lambda idea, h, t, mx, repos=None: ("draft", "g DoD: d"))
    tg.refine_step("tg-1", "trả lời", mem, api)
    assert mem.reg["tg-1"]["status"] == "ticket_drafted"
    assert mem.evts["tg-1"][-1]["role"] == "user"        # answer được ghi vào history disk
    text, kb = api.sent[-1]
    assert "Draft" in text and kb[0][0]["callback_data"] == "draft:run:tg-1"
```

- [ ] **Step 2: Run to verify FAIL** — `no attribute 'handle_message'`.

- [ ] **Step 3: Implement** (append)

```python
_AWAITING = ("refining", "ticket_drafted")     # cả hai đều nhận message trần làm input


def refine_step(thread: str, answer, mem, api) -> None:
    if answer is not None:
        if mem.get_run(thread).get("status") == "ticket_drafted":   # góp ý -> redraft
            mem.register(thread, status="refining")
        mem.append_event(thread, {"stage": "refine", "role": "user", "text": _mask(answer)})
    run = mem.get_run(thread)
    history = [{"role": e["role"], "text": e["text"]}
               for e in mem.events(thread) if e.get("stage") == "refine"]
    repos = ({"active": sorted(config.REPOS), "pending": sorted(config.REPOS_PENDING)}
             if config.REPOS else None)
    kind, text = refine.refine_turn(run.get("idea", ""), history,
                                    run.get("refine_turns", 0), config.REFINE_MAX_TURNS,
                                    repos=repos)
    if kind == "error":
        api.send("💥 refinement lỗi — gửi lại tin nhắn.")
        return
    if kind == "ask":
        mem.append_event(thread, {"stage": "refine", "role": "analyst", "text": _mask(text)})
        mem.register(thread, refine_turns=run.get("refine_turns", 0) + 1)
        api.send(f"❓ {_mask(text)}")
        return
    mem.register(thread, status="ticket_drafted", draft=text)
    warn = "\n⚠️ Tests trong draft chưa hợp lệ — run sẽ derive từ DoD." \
        if kind == "draft_unvalidated" else ""
    api.send(f"🎫 Draft:{warn}\n{_mask(text[:2500])}\n\n(góp ý = nhắn tin thường)",
             keyboard=[[{"text": "▶️ Run", "callback_data": f"draft:run:{thread}"},
                        {"text": "🚫 Huỷ", "callback_data": f"draft:cancel:{thread}"}]])


def handle_message(msg: dict, mem, api) -> None:
    text = (msg.get("text") or "").strip()
    _, stripped = gates.parse_repo(text)
    _, dod, _ = gates.parse_ticket(gates.parse_deliver(stripped)[1])
    if dod:
        launch_ticket(text, f"tg-{msg['message_id']}", mem, api)
        return
    awaiting = [t for t, r in mem.runs().items() if r.get("status") in _AWAITING]
    if len(awaiting) == 1:                               # luật 1: answer
        refine_step(awaiting[0], text, mem, api)
    elif not awaiting:                                   # luật 2: idea mới
        thread = f"tg-{msg['message_id']}"
        mem.register(thread, status="refining", idea=_mask(text[:500]), refine_turns=0)
        refine_step(thread, None, mem, api)
    else:                                                # luật 3: mơ hồ -> từ chối
        api.send("⚠️ Đang có ≥2 ticket chờ trả lời — chốt bớt một cái đã.")
    # ponytail: không map message_id→thread; khi nào cấn 2 ticket song song thật thì thêm.
```

- [ ] **Step 4: Run** — focused + full suite PASS.
- [ ] **Step 5: Commit**

```bash
git add src/loopkit/fronts/telegram.py tests/test_telegram.py
git commit -m "telegram front: idea refinement with 3-rule stateless answer routing"
```

---

### Task 4: `handle_callback` — door + draft buttons

**Files:**
- Modify: `src/loopkit/fronts/telegram.py`
- Test: `tests/test_telegram.py` (append)

**Interfaces:**
- Consumes: `finish_suspended(mem, thread, payload, decision, notify)`; `launch_ticket`; Memory doors/audit.
- Produces: `handle_callback(cb: dict, mem, api)` — `door:yes|no:<thread>`: audit (`approver=tg-<from.id>`) → finish_suspended → door_close → clear_buttons → answer_callback; door không còn → answer "door không còn mở" (stale an toàn). `draft:run:<thread>`: chỉ khi status `ticket_drafted` + có draft → register `ticket_approved` → `launch_ticket(draft, thread, ...)`; `draft:cancel:<thread>` → `refine_cancelled`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def _cb(data, mid=77):
    return {"id": "cb1", "data": data, "from": {"id": 999},
            "message": {"message_id": mid, "chat": {"id": 111}}}


def test_callback_door_approve_finishes_and_clears(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    mem.door_open("tg-1", {"artifact": "A", "goal": "g", "dod": "d"})
    fin = []
    monkeypatch.setattr(tg, "finish_suspended",
                        lambda m, t, p, dec, notify: fin.append((t, dec)))
    tg.handle_callback(_cb("door:yes:tg-1"), mem, api)
    assert fin == [("tg-1", True)]
    assert "tg-1" not in mem.doors and api.cleared == [77]
    assert mem.audits == [("tg-1", "tg-999", True)]


def test_callback_door_stale_is_safe(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    called = []
    monkeypatch.setattr(tg, "finish_suspended", lambda *a: called.append(1))
    tg.handle_callback(_cb("door:yes:tg-nope"), mem, api)
    assert not called and "không còn mở" in api.answered[-1][1]


def test_callback_draft_run_launches_with_saved_draft(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    mem.register("tg-1", status="ticket_drafted", draft="g Repo: pipeline DoD: d")
    seen = {}
    monkeypatch.setattr(tg, "launch_ticket",
                        lambda text, th, m, a: seen.update(t=text, th=th))
    tg.handle_callback(_cb("draft:run:tg-1"), mem, api)
    assert seen == {"t": "g Repo: pipeline DoD: d", "th": "tg-1"}
    assert mem.reg["tg-1"]["status"] == "ticket_approved"


def test_callback_draft_cancel_and_malformed(monkeypatch):
    api, mem = FakeTgApi(), MemStub()
    mem.register("tg-1", status="ticket_drafted", draft="x DoD: y")
    tg.handle_callback(_cb("draft:cancel:tg-1"), mem, api)
    assert mem.reg["tg-1"]["status"] == "refine_cancelled"
    tg.handle_callback(_cb("garbage"), mem, api)         # không nổ
    assert api.answered                                   # vẫn answer để Telegram tắt spinner
```

- [ ] **Step 2: Run to verify FAIL** — `no attribute 'handle_callback'`.

- [ ] **Step 3: Implement** (append)

```python
def handle_callback(cb: dict, mem, api) -> None:
    parts = (cb.get("data") or "").split(":", 2)
    mid = ((cb.get("message") or {}).get("message_id"))
    if len(parts) != 3:
        api.answer_callback(cb.get("id", ""))
        return
    kind, action, thread = parts
    if kind == "door":
        door = mem.door_get(thread)
        if not door:
            api.answer_callback(cb.get("id", ""), "door không còn mở")
            return
        decision = action == "yes"
        mem.audit(thread, approver=f"tg-{cb.get('from', {}).get('id', '?')}",
                  decision=decision)
        finish_suspended(mem, thread, door, decision, api.send)
        mem.door_close(thread)
        if mid:
            api.clear_buttons(mid)                       # chống double-click
        api.answer_callback(cb.get("id", ""), "✅ approved" if decision else "🚫 rejected")
        return
    if kind == "draft":
        run = mem.get_run(thread)
        if action == "run" and run.get("status") == "ticket_drafted" and run.get("draft"):
            mem.register(thread, status="ticket_approved")
            if mid:
                api.clear_buttons(mid)
            api.answer_callback(cb.get("id", ""), "chạy…")
            launch_ticket(run["draft"], thread, mem, api)
            return
        if action == "cancel" and run.get("status") == "ticket_drafted":
            mem.register(thread, status="refine_cancelled")
            if mid:
                api.clear_buttons(mid)
            api.answer_callback(cb.get("id", ""), "đã huỷ")
            return
    api.answer_callback(cb.get("id", ""), "stale")
```

- [ ] **Step 4: Run** — focused + full suite PASS.
- [ ] **Step 5: Commit**

```bash
git add src/loopkit/fronts/telegram.py tests/test_telegram.py
git commit -m "telegram front: door and draft callbacks, stale-safe, double-click guarded"
```

---

### Task 5: dispatch + `main()` + entry point + docs

**Files:**
- Modify: `src/loopkit/fronts/telegram.py`, `pyproject.toml`, `README.md`, `BUILD-MAP.md`
- Test: `tests/test_telegram.py` (append)

**Interfaces:**
- Consumes: mọi handler Tasks 2-4; `shield.seen_event`; `Memory.reap_running`.
- Produces: `handle_update(u: dict, mem, api)` — dedupe `shield.seen_event(f"tg-<update_id>")` → chat_id gate (drop im lặng) → route message/callback. `main() -> int` — thiếu `TG_TOKEN`/`TG_CHAT_ID`/`ENABLE_MEMORY` → exit 1 kèm hướng dẫn; reaper; poll loop offset-ack, một update hỏng không giết bot. Entry `loopkit-telegram`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_handle_update_chat_id_gate_silent_drop(monkeypatch):
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    monkeypatch.setattr(tg.shield, "seen_event", lambda i: False)
    api, mem = FakeTgApi(), MemStub()
    called = []
    monkeypatch.setattr(tg, "handle_message", lambda *a: called.append(1))
    tg.handle_update({"update_id": 1, "message":
                      {"message_id": 2, "text": "hi", "chat": {"id": 666}}}, mem, api)
    assert not called and not api.sent                   # drop IM LẶNG — không reply


def test_handle_update_routes_and_dedupes(monkeypatch):
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    seen = set()
    monkeypatch.setattr(tg.shield, "seen_event",
                        lambda i: i in seen or seen.add(i) or False)
    api, mem = FakeTgApi(), MemStub()
    msgs, cbs = [], []
    monkeypatch.setattr(tg, "handle_message", lambda m, *a: msgs.append(m))
    monkeypatch.setattr(tg, "handle_callback", lambda c, *a: cbs.append(c))
    u = {"update_id": 5, "message": {"message_id": 2, "text": "hi", "chat": {"id": 111}}}
    tg.handle_update(u, mem, api)
    tg.handle_update(u, mem, api)                        # gửi lại sau restart -> dedupe
    assert len(msgs) == 1
    tg.handle_update({"update_id": 6, "callback_query": _cb("door:yes:t")}, mem, api)
    assert len(cbs) == 1


def test_main_requires_env(monkeypatch, capsys):
    monkeypatch.setattr(config, "TG_TOKEN", "")
    monkeypatch.setattr(config, "TG_CHAT_ID", "")
    assert tg.main() == 1
    assert "LOOPKIT_TG_TOKEN" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify FAIL** — `no attribute 'handle_update'`.

- [ ] **Step 3: Implement** (append)

```python
def handle_update(u: dict, mem, api) -> None:
    if shield.seen_event(f"tg-{u.get('update_id')}"):    # Telegram re-delivers sau restart
        return
    msg, cb = u.get("message"), u.get("callback_query")
    chat = (msg or {}).get("chat") or ((cb or {}).get("message") or {}).get("chat") or {}
    if str(chat.get("id", "")) != config.TG_CHAT_ID:     # trust boundary: drop im lặng
        return
    if cb:
        handle_callback(cb, mem, api)
    elif msg and (msg.get("text") or "").strip():
        handle_message(msg, mem, api)


def main() -> int:
    if not (config.TG_TOKEN and config.TG_CHAT_ID and config.ENABLE_MEMORY):
        print("❌ cần LOOPKIT_TG_TOKEN + LOOPKIT_TG_CHAT_ID (+ LOOPKIT_ENABLE_MEMORY=1).\n"
              "   token: @BotFather /newbot\n"
              "   chat_id: nhắn bot một câu rồi curl "
              "https://api.telegram.org/bot<TOKEN>/getUpdates → message.chat.id")
        return 1
    mem = Memory(config.MEMORY_DIR)
    dead = mem.reap_running()                            # 'running' lúc boot = run đã chết
    if dead:
        print(f"[loopkit] reaped {len(dead)} interrupted run(s): {', '.join(dead)}")
    api = TgApi(config.TG_TOKEN)
    print("🤖 loopkit-telegram polling… (Ctrl-C để dừng)")
    offset = 0
    while True:
        updates = api.get_updates(offset)
        if not updates:
            time.sleep(1)                                # backoff nhẹ khi lỗi mạng/không có gì
            continue
        for u in updates:
            offset = max(offset, u.get("update_id", 0) + 1)
            try:
                handle_update(u, mem, api)
            except Exception as e:                       # một update hỏng không giết bot
                api.send(_mask(f"💥 error: {e}"))
```

- [ ] **Step 4: pyproject entry point** — trong `[project.scripts]` thêm:

```toml
loopkit-telegram = "loopkit.fronts.telegram:main"
```

- [ ] **Step 5: Docs** — `README.md`: trong "Three fronts, one engine" đổi tiêu đề thành "Four fronts, one engine" và thêm bullet:

```markdown
- **Telegram** — message the bot directly (no mention needed): a ticket with `DoD:` runs it,
  anything else starts idea refinement; door is an inline **Approve/Reject** keyboard. Zero
  extra deps. Run `loopkit-telegram` with `LOOPKIT_TG_TOKEN` (BotFather) +
  `LOOPKIT_TG_CHAT_ID` (your chat — everything else is silently dropped).
```

`BUILD-MAP.md`: thêm section sau §7:

```markdown
## 7b · Telegram layer (front #4 — spec 2026-07-11)
| Item | Status | Note |
|---|---|---|
| Long-poll intake + chat_id trust boundary | ✅ | stdlib urllib; update lạ drop im lặng; dedupe `shield.seen_event(tg-<update_id>)` |
| Idea refinement Q&A | ✅ | 3 luật routing không state (trần+1 chờ-input = answer; trần+0 = idea mới; ≥2 = từ chối) — thứ chết ở Slack private channel chạy tự nhiên ở đây |
| Door inline keyboard + durable doors | ✅ | suspend door, `finish_suspended` reuse §8.1; click sau restart OK; double-click guard = gỡ nút |
| Sync, không threading | ✅ | ponytail: poll dừng khi generate — single user chấp nhận; thêm thread khi cấn thật |
```

- [ ] **Step 6: Run** — `python3 -m pytest tests -q` → PASS toàn bộ.
- [ ] **Step 7: Commit**

```bash
git add src/loopkit/fronts/telegram.py tests/test_telegram.py pyproject.toml README.md BUILD-MAP.md
git commit -m "telegram front: dispatch + main loop + entry point + docs"
```

---

## Live E2E (sau merge, ngoài scope plan)

BotFather tạo bot thật → `loopkit-telegram` → từ điện thoại: nhắn idea → Q&A → Draft → ▶️ Run → door → ✅ Approve → delivery chain chạy. Pass khi trọn vòng không đụng bàn phím máy tính (spec Verification).
