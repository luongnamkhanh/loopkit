"""loopkit Telegram front (front #4) — personal-use mobile front, stdlib thuần.

Long-poll getUpdates (không webhook, không public URL — local-first). Import sạch:
token/chat_id chỉ check trong main() nên module unit-test được (khác slack.py).
Trust boundary: chỉ nhận update từ LOOPKIT_TG_CHAT_ID — còn lại drop im lặng.
Door kiểu suspend (persist rồi trả False) → nút Approve xử lý ở poll kế tiếp,
kể cả sau restart (doors.json + finish_suspended, §8.1 reuse nguyên).
"""
import http.client, json, pathlib, time, urllib.request

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
        except (OSError, ValueError, http.client.HTTPException):
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


def make_tg_door(mem, thread, goal, dod, deliver_path, repo, ws, tests, api,
                 gate_cmd="", gate_label="", mode="module"):
    """Suspend door: persist đủ payload cho finish_suspended rồi trả False — không block poll."""
    def door(artifact: str) -> bool:
        mem.door_open(thread, {"channel": "telegram", "artifact": artifact, "goal": goal,
                               "dod": dod, "deliver": deliver_path, "repo": repo,
                               "workspace": ws, "tests": tests, "gate_cmd": gate_cmd,
                               "mode": mode, "gate_label": gate_label})
        if mode == "edit":
            text = (f"🚪 Diff chờ duyệt:\n🛡 Gate: {gate_cmd}\n{gate_label}\n"
                    f"{_mask((artifact or '')[:2500])}")
        else:
            dline = f"\n📦 Deliver: {deliver_path}" if deliver_path else ""
            text = f"🚪 Artifact chờ duyệt:{dline}\n{_mask((artifact or '')[:2500])}"
        api.send(text,
                keyboard=[[{"text": "✅ Approve", "callback_data": f"door:yes:{thread}"},
                           {"text": "🚫 Reject", "callback_data": f"door:no:{thread}"}]])
        # KHÔNG set status ở đây: run_loop sẽ register "done" ngay sau khi door trả False
        # (tiền lệ cli.make_suspend_door) — doors.json mới là nguồn chân lý cho "awaiting".
        return False
    return door


def launch_ticket(text: str, thread: str, mem, api) -> None:
    repo_name, text = gates.parse_repo(text)
    deliver_path, text = gates.parse_deliver(text)
    gate_cmd, text = gates.parse_gate_cmd(text)
    if gate_cmd and deliver_path:
        api.send("⚠️ Gate: là edit-mode — bỏ qua Deliver:")
        deliver_path = None
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        api.send("🙅 Thiếu DoD.")
        return
    if repo_name and repo_name not in config.REPOS:            # fail-closed trước LLM
        api.send(f"🙅 Repo `{repo_name}` không có trong allowlist. "
                 f"Hợp lệ: {', '.join(sorted(config.REPOS)) or '(trống)'}")
        return
    repo_path = config.REPOS.get(repo_name) if repo_name else config.TARGET_REPO
    if repo_name in config.REPOS_PENDING and gate_cmd is None:
        gate_cmd = deliver.infer_gate(goal, dod, repo_path)
        if gate_cmd:
            api.send(f"🛡 Gate (AI đề xuất): {gate_cmd}")
        else:
            api.send("🙅 repo này cần Gate: — mô tả cách verify trong ticket/idea")
            return
    api.send(_mask(f"🧩 Nhận ticket.\nGoal: {goal}\nDoD: {dod}"))
    ws_key = f"{repo_name}-{thread}" if repo_name else thread
    wd, kind = make_workspace(ws_key, repo=repo_path)
    gate_label = ""
    if gate_cmd:
        if not (repo_path and config.ENABLE_TOOLS):
            api.send("🙅 Gate: cần repo hợp lệ + LOOPKIT_ENABLE_TOOLS=1.")
            return
        verifier, frozen_tests = gates.make_cmd_gate(gate_cmd, wd), ""
        pre_ok, _ = verifier("")
        gate_label = ("⚠️ gate XANH trước khi sửa — chỉ chống vỡ, không chứng minh DoD"
                      if pre_ok else "🔴 acceptance gate (đỏ trước khi sửa)")
        api.send(gate_label)
        dpath = None                                          # edit-mode: bỏ freeze_deliver hoàn toàn
    else:
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
               deliver=dpath, repo=repo_path or "", tests_src=frozen_tests,
               gate_cmd=gate_cmd or "")
    res = run_loop(t, human_door=make_tg_door(mem, thread, goal, dod, dpath or "",
                                              repo_path or "", wd, frozen_tests, api,
                                              gate_cmd=gate_cmd or "", gate_label=gate_label,
                                              mode="edit" if gate_cmd else "module"),
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
    if text.startswith("/"):                             # lệnh — không bao giờ là idea/ticket
        if text.split()[0].split("@")[0] == "/status":
            runs = sorted(mem.runs().items(),
                          key=lambda kv: kv[1].get("updated_at", 0), reverse=True)[:10]
            if not runs:
                api.send("(chưa có run nào)")
                return
            lines = [f"{'🚪' if mem.door_get(t) else '·'} {t} · {r.get('status', '?')} · "
                     f"{(r.get('goal') or r.get('idea') or '')[:48]}" for t, r in runs]
            api.send("\n".join(lines))
        else:
            api.send("Lệnh không biết — chỉ có /status. (idea/ticket thì nhắn thường)")
        return
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
    shield.init_dedupe(pathlib.Path(config.MEMORY_DIR) / "events.seen")   # §8.1: dedupe bền qua restart
    dead = mem.reap_running()                            # 'running' lúc boot = run đã chết
    if dead:
        print(f"[loopkit] reaped {len(dead)} interrupted run(s): {', '.join(dead)}")
    api = TgApi(config.TG_TOKEN)
    print("🤖 loopkit-telegram polling… (Ctrl-C để dừng)")
    offset = 0
    try:
        while True:
            updates = api.get_updates(offset)
            if not updates:
                time.sleep(1)                            # backoff nhẹ khi lỗi mạng/không có gì
                continue
            for u in updates:
                offset = max(offset, u.get("update_id", 0) + 1)
                try:
                    handle_update(u, mem, api)
                except Exception as e:                   # một update hỏng không giết bot
                    api.send(_mask(f"💥 error: {e}"))
    except KeyboardInterrupt:
        print("bye")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
