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


def test_tgapi_non_dict_json_returns_none(monkeypatch):
    calls = []
    monkeypatch.setattr(tg.urllib.request, "urlopen", _fake_urlopen([1, 2, 3], calls))
    api = tg.TgApi("TOK")
    assert api.get_updates(0) == []              # không raise, không nổ AttributeError
    assert api.send("hi") is None


def test_tgapi_answer_callback_and_clear_buttons_request_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(tg.urllib.request, "urlopen",
                        _fake_urlopen({"ok": True, "result": True}, calls))
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    api = tg.TgApi("TOK")
    api.answer_callback("cb9", "x" * 300)
    url, body, _ = calls[0]
    assert "answerCallbackQuery" in url and body["callback_query_id"] == "cb9"
    assert len(body["text"]) == 190              # cắt 190 chars theo giới hạn Telegram
    api.clear_buttons(55)
    url, body, _ = calls[1]
    assert "editMessageReplyMarkup" in url and body["message_id"] == 55
    assert body["reply_markup"] == {"inline_keyboard": []}


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
    assert mem.reg["tg-9"]["status"] == "awaiting_approval"
    assert mem.reg["tg-9"]["door_msg"]                   # message_id lưu để gỡ nút
    assert any(k for _, k in api.sent if k)              # có message kèm keyboard Approve
