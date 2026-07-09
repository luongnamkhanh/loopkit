"""T3 verifier — shield.py DoD made runnable."""
from loopkit import shield
from collections import OrderedDict


def test_masks_slack_tokens():
    out = shield.mask("bot xoxb-1234567890-abcdefghij app xapp-1-A0B-123-deadbeefdeadbeef")
    assert "xoxb-" not in out and "xapp-" not in out
    assert shield.MASK in out


def test_masks_aws_key_and_kv_secret():
    out = shield.mask("key AKIAABCDEFGHIJKLMNOP and api_key = 'sk_live_abcdef123456'")
    assert "AKIA" not in out
    assert "sk_live" not in out


def test_masks_email_and_vn_phone():
    out = shield.mask("mail a.b@test.com phone 0912345678")
    assert "@" not in out
    assert "0912345678" not in out


def test_plain_code_untouched():
    code = "def f(x):\n    return x + 1  # token = get_token(x) is a call, not a secret"
    assert shield.mask(code) == code


def test_seen_event_dedupe():
    assert shield.seen_event("Ev_test_123") is False   # first delivery -> process
    assert shield.seen_event("Ev_test_123") is True    # retry -> skip
    assert shield.seen_event("") is False              # no id -> never dedupe
    assert shield.seen_event("") is False


def test_dedupe_survives_restart(tmp_path, monkeypatch):
    """§8.1: a Slack retry delivered after a bot restart must still be deduped."""
    monkeypatch.setattr(shield, "_seen", OrderedDict())
    monkeypatch.setattr(shield, "_seen_path", None)
    f = tmp_path / "events.seen"
    shield.init_dedupe(f)
    assert shield.seen_event("ev1") is False
    assert shield.seen_event("ev1") is True
    monkeypatch.setattr(shield, "_seen", OrderedDict())      # simulated restart
    monkeypatch.setattr(shield, "_seen_path", None)
    shield.init_dedupe(f)
    assert shield.seen_event("ev1") is True                  # remembered across restart


def test_dedupe_file_trimmed_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(shield, "_seen", OrderedDict())
    monkeypatch.setattr(shield, "_seen_path", None)
    f = tmp_path / "events.seen"
    f.write_text("\n".join(f"e{i}" for i in range(1500)) + "\n")
    shield.init_dedupe(f)
    lines = f.read_text().splitlines()
    assert len(lines) == 1000 and lines[0] == "e500"         # last 1000 kept
    assert shield.seen_event("e1499") is True                # loaded into memory too
