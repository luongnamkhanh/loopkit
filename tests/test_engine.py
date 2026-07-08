"""Regression tests from live operation: reviewer-verdict parsing (fail-closed but tolerant)."""
import config, engine, roles
from engine import Ticket, run_loop


def make_fake(reviewer_reply):
    def fake(prompt, soul, model=None):
        if soul == roles.REGISTRY["orchestrator"].soul:
            return "code"
        if soul == roles.REGISTRY["reviewer"].soul:
            return reviewer_reply
        return "```python\nX = 1\n```"
    return fake


def _run(tmp_path, monkeypatch, reviewer_reply):
    monkeypatch.setattr(engine, "ask_claude", make_fake(reviewer_reply))
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)     # no cache side effects
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"))
    return run_loop(t, journal_dir=str(tmp_path), notify=lambda m: None, max_turns=2)


def test_buried_verdict_is_found(tmp_path, monkeypatch):
    """Live repro: reviewer wrote reasoning first, VERDICT below -> must count as PASS."""
    reply = "Suy luận: f-string luôn đặt dấu trừ trước.\nVERDICT: PASS\n- ok"
    res = _run(tmp_path, monkeypatch, reply)
    assert res["ok"] is True and res["turns"] == 1


def test_no_verdict_anywhere_fails_closed(tmp_path, monkeypatch):
    res = _run(tmp_path, monkeypatch, "just prose, no verdict line at all")
    assert res["ok"] is False and res["reason"].startswith("budget exhausted")
