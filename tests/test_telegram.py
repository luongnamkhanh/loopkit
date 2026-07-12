import io
import json

import pytest

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
    assert any(k for _, k in api.sent if k)              # có message kèm keyboard Approve


def test_launch_ticket_repos_pending_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "REPOS", {"iac": "/x"})
    monkeypatch.setattr(config, "REPOS_PENDING", {"iac"})
    api, mem = FakeTgApi(), MemStub()
    called = []
    monkeypatch.setattr(tg, "run_loop", lambda *a, **k: called.append(1))
    tg.launch_ticket("goal Repo: iac DoD: WHEN x SHALL y", "tg-2", mem, api)
    assert not called and any("domain gate" in t for t, _ in api.sent)


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


def test_main_poll_loop_offset_and_error_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))   # không ghi events.seen thật
    monkeypatch.setattr(config, "TG_TOKEN", "TOK")
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    monkeypatch.setattr(config, "ENABLE_MEMORY", True)

    class Stop(Exception):
        ...

    class FakeApi:
        def __init__(self, token):
            self.offsets = []
            self.notified = []

        def get_updates(self, offset):
            self.offsets.append(offset)
            if len(self.offsets) == 1:
                return [{"update_id": 7}, {"update_id": 9}]
            raise Stop()                               # thoát vòng while True cho test

        def send(self, text, reply_to=None, keyboard=None):
            self.notified.append(text)

    holder = {}

    def fake_ctor(token):
        holder["api"] = FakeApi(token)
        return holder["api"]

    class RMem(MemStub):
        def reap_running(self):
            return []

    monkeypatch.setattr(tg, "TgApi", fake_ctor)
    monkeypatch.setattr(tg, "Memory", lambda d: RMem())
    handled = []

    def fake_handle_update(u, mem, api):
        handled.append(u["update_id"])
        if u["update_id"] == 7:
            raise RuntimeError("bad update")

    monkeypatch.setattr(tg, "handle_update", fake_handle_update)
    with pytest.raises(Stop):
        tg.main()
    assert handled == [7, 9]                    # update hỏng không giết bot
    assert holder["api"].offsets == [0, 10]     # offset-ack = max(update_id)+1
    assert any("error" in s for s in holder["api"].notified)  # 💥 notify masked


def test_handle_update_foreign_callback_dropped(monkeypatch):
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    monkeypatch.setattr(tg.shield, "seen_event", lambda i: False)
    api, mem = FakeTgApi(), MemStub()
    called = []
    monkeypatch.setattr(tg, "handle_callback", lambda *a: called.append(1))
    cb = {"id": "x", "data": "door:yes:t", "from": {"id": 1},
          "message": {"message_id": 3, "chat": {"id": 666}}}
    tg.handle_update({"update_id": 2, "callback_query": cb}, mem, api)
    assert not called and not api.sent


def test_main_boot_inits_durable_dedupe(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TG_TOKEN", "TOK")
    monkeypatch.setattr(config, "TG_CHAT_ID", "111")
    monkeypatch.setattr(config, "ENABLE_MEMORY", True)
    monkeypatch.setattr(config, "MEMORY_DIR", str(tmp_path))
    inited = []
    monkeypatch.setattr(tg.shield, "init_dedupe", lambda p: inited.append(str(p)))

    class Stop(Exception):
        ...

    class FakeApi:
        def __init__(self, token):
            ...

        def get_updates(self, offset):
            raise Stop()

        def send(self, *a, **k):
            ...

    class RMem2(MemStub):
        def reap_running(self):
            return []

    monkeypatch.setattr(tg, "TgApi", FakeApi)
    monkeypatch.setattr(tg, "Memory", lambda d: RMem2())
    with pytest.raises(Stop):
        tg.main()
    assert inited and inited[0].endswith("events.seen")
