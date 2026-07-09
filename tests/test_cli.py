"""CLI front verifiers — mọi LLM/loop đều fake; test wiring + door + exit codes."""
import json
from loopkit import config
from loopkit.fronts import cli
from loopkit.memory import Memory


def test_run_missing_dod(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", "làm gì đó không DoD"]) == 1
    assert "Thiếu DoD" in capsys.readouterr().out


def test_run_repo_token_stripped_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    seen = {}
    def fake_run_loop(t, **kw):
        seen["goal"] = t.goal
        return {"ok": True, "approved": True, "worker": "code", "turns": 1, "artifact": "X"}
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    rc = cli.main(["run", "Repo: iac viết hàm f DoD: WHEN x SHALL y"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLI bỏ qua 'Repo: iac'" in out
    assert "Repo:" not in seen["goal"]                       # token đã strip khỏi ticket


def test_terminal_door_yes_no_eof(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert cli.terminal_door("code") is True
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert cli.terminal_door("code") is False
    def raise_eof(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    assert cli.terminal_door("code") is False                 # fail-closed


def test_run_exhausted_exit_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(cli, "run_loop",
                        lambda t, **kw: {"ok": False, "reason": "budget exhausted -> escalate"})
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    assert cli.main(["run", "viết f DoD: WHEN x SHALL y"]) == 1


def test_idea_flow_ask_then_draft_then_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    replies = iter([("ask", "A hay B?"), ("draft", "viết f DoD: WHEN x SHALL y")])
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: next(replies))
    answers = iter(["B", "y"])                                # trả lời câu hỏi, rồi duyệt draft
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    called = {}
    monkeypatch.setattr(cli, "cmd_run", lambda text, thread=None: called.setdefault("t", text) and 0)
    assert cli.main(["idea", "muốn có hàm f"]) == 0
    assert "DoD:" in called["t"]


def test_idea_cancel_exit_130(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.refine, "refine_turn",
                        lambda *a, **k: ("draft", "viết f DoD: WHEN x SHALL y"))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert cli.main(["idea", "ý tưởng"]) == 130


def test_status_lists_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Memory(".loopkit_memory").register("t1", status="done", goal="làm x", approved=True)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "t1" in out and "done" in out
