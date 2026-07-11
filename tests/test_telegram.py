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
