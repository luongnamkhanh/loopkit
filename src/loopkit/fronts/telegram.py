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
