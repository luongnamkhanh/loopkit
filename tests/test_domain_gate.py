import subprocess

from loopkit import gates


def test_parse_gate_cmd_extracts_to_end_of_line():
    cmd, rest = gates.parse_gate_cmd(
        "goal Repo: x\nGate: helm template c | grep -q foo && helm lint c\nDoD: WHEN a SHALL b")
    assert cmd == "helm template c | grep -q foo && helm lint c"
    assert "Gate:" not in rest and "DoD: WHEN a SHALL b" in rest
    assert gates.parse_gate_cmd("no gate here") == (None, "no gate here")
    assert gates.parse_gate_cmd(None) == (None, "")


def test_make_cmd_gate_pass_fail_and_ignores_artifact(tmp_path):
    ok, detail = gates.make_cmd_gate("echo hi && true", str(tmp_path))("IGNORED")
    assert ok and "hi" in detail
    ok, detail = gates.make_cmd_gate("echo bad >&2 && false", str(tmp_path))("")
    assert not ok and "bad" in detail


def test_make_cmd_gate_timeout_fails_closed(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("cmd", 300)
    monkeypatch.setattr(gates.subprocess, "run", boom)
    ok, detail = gates.make_cmd_gate("sleep 999", str(tmp_path))("")
    assert not ok and "timeout" in detail


def test_parse_gate_cmd_ignores_prose_gate_inside_dod():
    text = ("Repo: x\nDoD: WHEN user reaches the checkout gate: THEN system SHALL notify\n"
            "Tests: pass")
    cmd, rest = gates.parse_gate_cmd(text)
    assert cmd is None and rest == text            # DoD nguyên vẹn, không nuốt gì
    # token thật TRƯỚC DoD vẫn ăn, kể cả one-liner:
    cmd, rest = gates.parse_gate_cmd("goal Gate: helm lint c DoD: WHEN a SHALL b")
    assert cmd == "helm lint c" and "DoD: WHEN a SHALL b" in rest
