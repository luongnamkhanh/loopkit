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
