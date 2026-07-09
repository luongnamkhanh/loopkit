"""Agent-drivable verbs — state trên disk giữa các lần gọi; door luôn tách khỏi run."""
import json
from loopkit import config
from loopkit.fronts import cli
from loopkit.memory import Memory


def _mem_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Memory(config.MEMORY_DIR)


def test_idea_start_prints_thread_and_question(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: ("ask", "A hay B?"))
    assert cli.main(["idea", "start", "muốn hàm f"]) == 0
    out = capsys.readouterr().out
    assert "THREAD: cli-" in out and "QUESTION: A hay B?" in out
    t = [l.split(": ")[1] for l in out.splitlines() if l.startswith("THREAD:")][0]
    assert mem.get_run(t)["status"] == "refining"


def test_idea_answer_reads_history_from_disk(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="refining", idea="ý tưởng", refine_turns=1)
    mem.append_event("t1", {"stage": "refine", "role": "analyst", "text": "Q1?"})
    seen = {}
    def fake(idea, history, turns, mx, **kw):
        seen["h"] = history
        return "draft", "viết f DoD: WHEN x SHALL y"
    monkeypatch.setattr(cli.refine, "refine_turn", fake)
    assert cli.main(["idea", "answer", "t1", "B"]) == 0
    out = capsys.readouterr().out
    assert "DRAFT:" in out and "DRAFT_END" in out
    assert [h["text"] for h in seen["h"]] == ["Q1?", "B"]      # history từ DISK, không RAM
    assert mem.get_run("t1")["status"] == "ticket_drafted"


def test_idea_answer_on_draft_means_feedback_redraft(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="ticket_drafted", idea="i", refine_turns=2, draft="cũ")
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: ("ask", "sửa gì?"))
    assert cli.main(["idea", "answer", "t1", "đổi tên hàm"]) == 0
    assert "QUESTION:" in capsys.readouterr().out


def test_idea_answer_wrong_status_stale(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done")
    assert cli.main(["idea", "answer", "t1", "B"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_suspend_door_persists_and_returns_false(tmp_path, monkeypatch):
    mem = _mem_cwd(tmp_path, monkeypatch)
    door = cli.make_suspend_door(mem, "t1", "goal g", "dod d")
    assert door("artifact X") is False
    d = mem.door_get("t1")
    assert d["artifact"] == "artifact X" and d["goal"] == "goal g" and d["channel"] == "cli"


def test_ticket_run_ends_awaiting(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="ticket_drafted", draft="viết f DoD: WHEN x SHALL y")
    def fake_run_loop(t, human_door=None, **kw):
        human_door("code XYZ")                                  # loop chạm door
        return {"ok": True, "approved": False, "worker": "code", "turns": 1,
                "artifact": "code XYZ"}
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    assert cli.main(["ticket", "run", "t1"]) == 0
    out = capsys.readouterr().out
    assert "AWAITING_APPROVAL" in out and "ARTIFACT:" in out and "code XYZ" in out
    assert mem.door_get("t1") is not None                       # door còn mở trên disk


def test_ticket_run_without_draft_stale(tmp_path, monkeypatch, capsys):
    _mem_cwd(tmp_path, monkeypatch)
    assert cli.main(["ticket", "run", "nope"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_approve_completes_and_caches(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done", approved=False)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["approve", "t1"]) == 0
    out = capsys.readouterr().out
    assert "APPROVED" in out and "X=1" in out
    assert mem.get_run("t1")["approved"] is True
    assert mem.recall("g", "d") == "X=1"
    assert mem.door_get("t1") is None                           # door đã đóng


def test_reject_no_cache(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["reject", "t1"]) == 0
    assert "REJECTED" in capsys.readouterr().out
    assert mem.recall("g", "d") is None


def test_approve_without_door_stale(tmp_path, monkeypatch, capsys):
    _mem_cwd(tmp_path, monkeypatch)
    assert cli.main(["approve", "t9"]) == 1
    assert "STALE:" in capsys.readouterr().out


def test_show_reports_awaiting_when_door_open(tmp_path, monkeypatch, capsys):
    mem = _mem_cwd(tmp_path, monkeypatch)
    mem.register("t1", status="done", approved=False)
    mem.door_open("t1", {"channel": "cli", "artifact": "X=1", "goal": "g", "dod": "d"})
    assert cli.main(["show", "t1"]) == 0
    out = capsys.readouterr().out
    assert "STATUS: awaiting_approval" in out and "ARTIFACT:" in out
