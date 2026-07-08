"""T3 verifier — shield.py DoD made runnable."""
import shield


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
