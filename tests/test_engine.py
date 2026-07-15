"""Regression tests from live operation: reviewer-verdict parsing (fail-closed but tolerant)."""
from loopkit import config, engine, roles
from loopkit.memory import Memory
from loopkit.engine import Ticket, run_loop


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


def test_finish_suspended_approve_registers_caches_delivers(tmp_path):
    """§8.1 resume: approve after restart -> registry done/approved, cached, delivered."""
    mem = Memory(str(tmp_path / "m"))
    mem.register("t1", status="awaiting_approval")
    msgs = []
    payload = {"channel": "C1", "artifact": "X=1", "goal": "g", "dod": "d"}
    engine.finish_suspended(mem, "t1", payload, True, msgs.append)
    run = mem.get_run("t1")
    assert run["status"] == "done" and run["approved"] is True
    assert mem.recall("g", "d") == "X=1"                    # cached: verified+approved
    assert any("X=1" in m for m in msgs)                    # artifact delivered


def test_finish_suspended_reject_no_cache_no_artifact(tmp_path):
    mem = Memory(str(tmp_path / "m"))
    mem.register("t1", status="awaiting_approval")
    msgs = []
    payload = {"channel": "C1", "artifact": "X=1", "goal": "g", "dod": "d"}
    engine.finish_suspended(mem, "t1", payload, False, msgs.append)
    run = mem.get_run("t1")
    assert run["status"] == "done" and run["approved"] is False
    assert mem.recall("g", "d") is None                     # NEVER cache a rejected artifact
    assert not any("X=1" in m for m in msgs)                # and never deliver it


def test_feedback_accumulates_across_turns(tmp_path, monkeypatch):
    """Issue #6: turn 3's generator prompt must carry BOTH turn 1's and turn 2's reject reason,
    not just the immediately preceding one."""
    gen_prompts = []
    state = {"reviewer_calls": 0}

    def fake(prompt, soul, model=None):
        if soul == roles.REGISTRY["orchestrator"].soul:
            return "code"
        if soul == roles.REGISTRY["reviewer"].soul:
            state["reviewer_calls"] += 1
            if state["reviewer_calls"] < 3:
                return f"VERDICT: REJECT because of turn {state['reviewer_calls']} problem"
            return "VERDICT: PASS"
        gen_prompts.append(prompt)
        return "```python\nX = 1\n```"

    monkeypatch.setattr(engine, "ask_claude", fake)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"))
    res = run_loop(t, journal_dir=str(tmp_path), notify=lambda m: None, max_turns=3)

    assert res["ok"] is True and res["turns"] == 3
    assert len(gen_prompts) == 3
    assert "no attempt yet" in gen_prompts[0]
    assert "turn 1 problem" in gen_prompts[1] and "turn 2 problem" not in gen_prompts[1]
    assert "turn 1 problem" in gen_prompts[2] and "turn 2 problem" in gen_prompts[2]


def test_ask_claude_retries_connection_error_then_succeeds(monkeypatch):
    """Issue #7: transient ConnectionError on the claude subprocess -> retry (2s, 4s backoff),
    3rd attempt succeeds -> caller sees the good result, not a sentinel."""
    monkeypatch.delenv("LOOPKIT_NO_BRAIN", raising=False)
    calls = {"n": 0}
    sleeps = []

    def fake_run(cmd, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        class R: stdout, stderr = "ok", ""
        return R()

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    monkeypatch.setattr(engine.time, "sleep", lambda s: sleeps.append(s))
    assert engine.ask_claude("p", "s") == "ok"
    assert calls["n"] == 3
    assert sleeps == [2, 4]


def test_ask_claude_timeout_not_retried(monkeypatch):
    """TimeoutExpired must keep the old sentinel behavior — never retried (a hung 20-min
    call must not be repeated)."""
    monkeypatch.delenv("LOOPKIT_NO_BRAIN", raising=False)
    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        raise engine.subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    result = engine.ask_claude("p", "s")
    assert calls["n"] == 1
    assert result.startswith("LOOPKIT_TIMEOUT")


def test_brain_calls_never_inherit_stdin(monkeypatch):
    monkeypatch.delenv("LOOPKIT_NO_BRAIN", raising=False)   # test đường subprocess thật — phải hermetic với env gate

    """CLI bug found live: claude subprocess ate the piped door answer -> door hit EOF.
    Brain subprocesses must run with stdin=DEVNULL."""
    import subprocess as sp
    seen = {}
    def fake_run(cmd, **kw):
        seen["stdin"] = kw.get("stdin")
        class R: stdout, stderr = "ok", ""
        return R()
    monkeypatch.setattr(engine.subprocess, "run", fake_run)
    engine.ask_claude("p", "s")
    assert seen["stdin"] == sp.DEVNULL
    engine.run_agent("p", "s", workdir="/tmp", tools=("Read",))
    assert seen["stdin"] == sp.DEVNULL
