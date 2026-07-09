"""T2 verifier — memory.py + engine wiring DoD made runnable.
The brain (engine.ask_claude) is monkeypatched: these tests verify the LOOP+MEMORY machinery,
not the LLM.
"""
import json
from loopkit import engine, roles
from loopkit.engine import Ticket, run_loop
from loopkit.memory import Memory


def make_fake(counter):
    def fake(prompt, soul, model=None):
        if soul == roles.REGISTRY["orchestrator"].soul:
            return "code"
        if soul == roles.REGISTRY["reviewer"].soul:
            return "VERDICT: PASS\n- fine"
        counter["gen"] += 1                      # a worker/generator call
        return "```python\nX = 1\n```"
    return fake


def _run(ticket, mem, tmp_path, thread, door=None):
    kw = dict(memory=mem, thread_id=thread, journal_dir=str(tmp_path), notify=lambda m: None)
    if door is not None:
        kw["human_door"] = door
    return run_loop(ticket, **kw)


def test_memory_unit_roundtrip(tmp_path):
    mem = Memory(str(tmp_path / "m"))
    mem.store("Write  X ", "X == 1", "artifact-body")
    assert mem.recall("write x", "x == 1") == "artifact-body"   # normalized match
    assert mem.recall("other", "dod") is None


def test_semantic_recall_skips_generation(tmp_path, monkeypatch):
    counter = {"gen": 0}
    monkeypatch.setattr(engine, "ask_claude", make_fake(counter))
    mem = Memory(str(tmp_path / "m"))
    t = Ticket(goal="write X", dod="X == 1", verifier=lambda a: (True, "ok"))
    r1 = _run(t, mem, tmp_path, "t1")
    assert r1["ok"] and not r1.get("cached") and counter["gen"] == 1
    r2 = _run(t, mem, tmp_path, "t2")
    assert r2["ok"] and r2["cached"] is True
    assert counter["gen"] == 1                  # no new generation on recall


def test_risky_cached_only_after_approval(tmp_path, monkeypatch):
    counter = {"gen": 0}
    monkeypatch.setattr(engine, "ask_claude", make_fake(counter))
    mem = Memory(str(tmp_path / "m"))
    t = Ticket(goal="risky thing", dod="d", verifier=lambda a: (True, "ok"), risky=True)
    r1 = _run(t, mem, tmp_path, "a", door=lambda art: False)     # human REJECTS
    assert r1["ok"] and r1["approved"] is False
    assert mem.recall(t.goal, t.dod) is None                     # rejected -> NOT cached
    r2 = _run(t, mem, tmp_path, "b", door=lambda art: True)      # human APPROVES
    assert r2["approved"] and not r2.get("cached")
    assert mem.recall(t.goal, t.dod) is not None                 # approved -> cached
    r3 = _run(t, mem, tmp_path, "c", door=lambda art: True)      # recall still passes the door
    assert r3["cached"] is True and counter["gen"] == 2


def test_journal_appends_across_runs(tmp_path, monkeypatch):
    counter = {"gen": 0}
    monkeypatch.setattr(engine, "ask_claude", make_fake(counter))
    mem = Memory(str(tmp_path / "m"))
    _run(Ticket(goal="g1", dod="d1", verifier=lambda a: (True, "ok")), mem, tmp_path, "j1")
    _run(Ticket(goal="g2", dod="d2", verifier=lambda a: (True, "ok")), mem, tmp_path, "j2")
    lines = (tmp_path / "run_journal.jsonl").read_text().splitlines()
    run_ids = {json.loads(l)["run_id"] for l in lines if l.strip()}
    assert len(run_ids) >= 2                    # appended across runs, never wiped


def test_audit_trail_on_disk(tmp_path):
    """T5: a human door decision (who + what) must persist to session + registry."""
    mem = Memory(str(tmp_path / "m"))
    mem.audit("th1", approver="U123", decision=True)
    ev = mem.events("th1")[-1]
    assert ev["stage"] == "human_door" and ev["approver"] == "U123" and ev["approved"] is True
    assert mem.get_run("th1")["approver"] == "U123"


def test_session_and_registry(tmp_path, monkeypatch):
    counter = {"gen": 0}
    monkeypatch.setattr(engine, "ask_claude", make_fake(counter))
    mem = Memory(str(tmp_path / "m"))
    _run(Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok")), mem, tmp_path, "s1")
    assert len(mem.events("s1")) >= 1           # session history readable back from disk
    assert mem.get_run("s1")["status"] == "done"


def test_reap_running_flips_only_dead_runs(tmp_path):
    """§8.1 reaper: at boot every 'running' entry is dead — flip to interrupted, audit, idempotent."""
    mem = Memory(str(tmp_path / "m"))
    mem.register("dead", status="running")
    mem.register("fine", status="done", approved=True)
    assert mem.reap_running() == ["dead"]
    assert mem.get_run("dead")["status"] == "interrupted"
    assert mem.get_run("fine")["status"] == "done"          # untouched
    assert mem.events("dead")[-1]["stage"] == "interrupted"  # evidence on disk
    assert mem.reap_running() == []                          # idempotent


def test_doors_roundtrip_and_idempotent_close(tmp_path):
    """§8.1: a door persisted on disk survives the process; close is idempotent."""
    mem = Memory(str(tmp_path / "m"))
    assert mem.door_get("t1") is None
    mem.door_open("t1", {"channel": "C1", "artifact": "A", "goal": "g", "dod": "d"})
    door = mem.door_get("t1")
    assert door["channel"] == "C1" and door["artifact"] == "A" and "opened_at" in door
    mem2 = Memory(str(tmp_path / "m"))                       # simulated restart
    assert mem2.door_get("t1")["artifact"] == "A"
    mem2.door_close("t1")
    assert mem2.door_get("t1") is None
    mem2.door_close("t1")                                    # no raise


def test_reaper_leaves_awaiting_approval(tmp_path):
    """§8.1: a run suspended at a persisted door is NOT dead — reaper must skip it."""
    mem = Memory(str(tmp_path / "m"))
    mem.register("suspended", status="awaiting_approval")
    assert mem.reap_running() == []
    assert mem.get_run("suspended")["status"] == "awaiting_approval"
