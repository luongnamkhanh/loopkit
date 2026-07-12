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
        mem.register(thread, status="awaiting_approval", door_msg=mid)
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
    recalled = bool(mem.recall(goal, dod) is not None)
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
