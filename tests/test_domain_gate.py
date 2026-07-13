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


import pathlib

from loopkit import deliver


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def make_edit_repo(tmp_path):
    """Repo + bare origin + worktree đã bị generator sửa 2 file + thêm 1 file mới."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "values.yaml").write_text("a: 1\n")
    (repo / "chart.yaml").write_text("name: x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-qu", "origin", "main")
    ws = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(ws), "-b", "loop/e1")
    (ws / "values.yaml").write_text("a: 2\nb: true\n")
    (ws / "new.yaml").write_text("fresh: yes\n")
    return repo, bare, ws


def test_infer_gate_valid_and_junk(tmp_path):
    repo, _, _ = make_edit_repo(tmp_path)
    ok = deliver.infer_gate("g", "d", str(repo), ask=lambda p, s, model=None: "helm lint chart")
    assert ok == "helm lint chart"
    for junk in ("", "dòng một\ndòng hai", "x" * 400):
        assert deliver.infer_gate("g", "d", str(repo),
                                  ask=lambda p, s, model=None, j=junk: j) is None


def test_infer_gate_prompt_has_tree_goal_dod(tmp_path):
    repo, _, _ = make_edit_repo(tmp_path)
    seen = {}
    deliver.infer_gate("GOAL-X", "DOD-Y", str(repo),
                       ask=lambda p, s, model=None: seen.update(p=p, s=s) or "true")
    assert "values.yaml" in seen["p"] and "GOAL-X" in seen["p"] and "DOD-Y" in seen["p"]


def test_ship_diff_commits_all_changes_and_pushes(tmp_path):
    repo, bare, ws = make_edit_repo(tmp_path)
    events = []
    res = deliver.ship_diff(str(ws), str(repo), "true", "Enable b flag in values",
                            "WHEN x SHALL y", emit=events.append,
                            record=lambda e: events.append(e))
    assert res["ok"], res
    assert res["branch"].startswith("feat/enable-b-flag")
    show = subprocess.run(["git", "-C", str(bare), "show", "--stat", res["branch"]],
                          capture_output=True, text=True).stdout
    assert "values.yaml" in show and "new.yaml" in show     # cả file sửa lẫn file MỚI


def test_ship_diff_gate_red_aborts_no_commit(tmp_path):
    repo, bare, ws = make_edit_repo(tmp_path)
    res = deliver.ship_diff(str(ws), str(repo), "false", "goal", "dod", emit=lambda m: None)
    assert not res["ok"] and res["error"] == "regate"
    assert _git(repo, "log", "--oneline", "feat/goal").returncode != 0


def test_ship_existing_behavior_unchanged(tmp_path):
    # guard refactor: ship cũ vẫn chạy y hệt (fixture kiểu solution.py)
    repo, bare, ws = make_edit_repo(tmp_path)
    (ws / "solution.py").write_text("def f():\n    return 1\n")
    res = deliver.ship(str(ws), str(repo), "pkg/mod.py", "old ship path", "dod")
    assert res["ok"], res


def test_ship_diff_never_raises_on_internal_exception(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    import loopkit.gates as gmod
    def boom(cmd, wd):
        raise RuntimeError("gate factory exploded")
    monkeypatch.setattr(gmod, "make_cmd_gate", boom)
    events = []
    res = deliver.ship_diff(str(ws), str(repo), "true", "g", "d",
                            emit=events.append, record=lambda e: events.append(e))
    assert res == {"ok": False, "branch": None, "mr_url": None, "error": "exception"}
    assert any(isinstance(e, dict) and e.get("error") == "exception" for e in events)


from loopkit.engine import Ticket, run_loop, finish_suspended
import loopkit.deliver as dmod2


def _fake_brain_edit(monkeypatch, tmp_path):
    import loopkit.engine as eng
    monkeypatch.setattr(eng, "route", lambda t, roles: "code")
    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(eng.config, "ENABLE_TOOLS", True)
    # generator (run_agent) "sửa" worktree; reviewer (ask_claude) PASS
    def fake_agent(prompt, soul, workdir, tools, model=None):
        pathlib.Path(workdir, "values.yaml").write_text("a: 2\n")
        return "edited"
    monkeypatch.setattr(eng, "run_agent", fake_agent)
    monkeypatch.setattr(eng, "ask_claude", lambda p, s, model=None: "VERDICT: PASS")
    return eng


def test_run_loop_edit_mode_diff_artifact_and_ship_diff(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")             # reset worktree về sạch
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    shipped = {}
    monkeypatch.setattr(dmod2, "ship_diff",
                        lambda w, r, cmd, g, d, emit=print, record=None:
                        shipped.update(cmd=cmd) or {"ok": True})
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true")
    res = run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                   journal_dir=str(tmp_path), memory=None, workspace=str(ws))
    assert res["ok"] and "values.yaml" in res["artifact"]  # artifact là git diff
    assert shipped["cmd"] == "true"


def test_run_loop_edit_mode_requires_tools(tmp_path, monkeypatch):
    import loopkit.engine as eng
    monkeypatch.setattr(eng.config, "ENABLE_TOOLS", False)
    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", False)
    def no_llm(*a, **k):
        raise AssertionError("route/ask_claude must NOT be called on refusal")
    monkeypatch.setattr(eng, "route", no_llm)
    monkeypatch.setattr(eng, "ask_claude", no_llm)

    class RegMem:
        def __init__(self):
            self.reg = {}
        def register(self, t, **f):
            self.reg.setdefault(t, {}).update(f)
        def recall(self, g, d):
            return None
        def append_event(self, *a):
            ...
    rm = RegMem()
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, ""), gate_cmd="true",
               repo=str(tmp_path))
    res = run_loop(t, notify=lambda m: None, journal_dir=str(tmp_path),
                   memory=rm, thread_id="rt", workspace=str(tmp_path))
    assert not res["ok"] and "ENABLE_TOOLS" in res["reason"]
    assert rm.reg["rt"]["status"] == "refused"            # terminal, không kẹt "running"


def test_run_loop_edit_mode_empty_diff_fails_gate(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "run_agent", lambda *a, **k: "did nothing")  # không sửa gì
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true")
    res = run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                   journal_dir=str(tmp_path), memory=None, workspace=str(ws),
                   max_turns=1)
    assert not res["ok"]                                   # diff rỗng không bao giờ PASS


def test_finish_suspended_edit_mode_routes_and_refuses_lost_worktree(tmp_path, monkeypatch):
    class FakeMem:
        def register(self, *a, **k): ...
        def store(self, *a, **k): ...
        def append_event(self, *a, **k): ...
    shipped = []
    monkeypatch.setattr(dmod2, "ship_diff",
                        lambda w, r, cmd, g, d, emit=print, record=None:
                        shipped.append(cmd) or {"ok": True})
    payload = {"artifact": "diff", "goal": "g", "dod": "d", "mode": "edit",
               "gate_cmd": "true", "repo": str(tmp_path), "workspace": str(tmp_path)}
    finish_suspended(FakeMem(), "t", payload, True, lambda m: None)
    assert shipped == ["true"]
    msgs = []
    payload["workspace"] = str(tmp_path / "gone")
    finish_suspended(FakeMem(), "t", payload, True, msgs.append)
    assert shipped == ["true"] and any("worktree" in m for m in msgs)  # từ chối, không ship mù


def test_run_loop_gate_cmd_beats_deliver_in_tail(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(dmod2, "ship_diff",
                        lambda *a, **k: calls.append("diff") or {"ok": True})
    monkeypatch.setattr(dmod2, "ship",
                        lambda *a, **k: calls.append("module") or {"ok": True})
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true", deliver="x/y.py")
    run_loop(t, human_door=lambda a: True, notify=lambda m: None,
             journal_dir=str(tmp_path), memory=None, workspace=str(ws))
    assert calls == ["diff"]                               # ship KHÔNG được gọi


from loopkit.fronts import telegram as tgf
from loopkit.fronts import cli as clif


class TgStub:
    def __init__(self):
        self.sent = []

    def send(self, text, reply_to=None, keyboard=None):
        self.sent.append(text)
        return len(self.sent)


class MStub:
    def __init__(self):
        self.reg, self.doors = {}, {}

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

    def recall(self, g, d):
        return None

    def append_event(self, *a):
        ...


def test_tg_pending_repo_infer_gate_and_edit_ticket(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(tgf.deliver, "infer_gate", lambda g, d, r: "helm lint c")
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "wt"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("do infra Repo: deploy DoD: WHEN x SHALL y", "tg-1", mem, api)
    assert seen["t"].gate_cmd == "helm lint c"
    assert any("AI đề xuất" in s for s in api.sent)
    assert any("gate" in s.lower() for s in api.sent)      # pre-flight label emitted


def test_tg_pending_repo_no_gate_refused(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)  # past the hoisted tools-refusal (fix 2)
    monkeypatch.setattr(tgf.deliver, "infer_gate", lambda g, d, r: None)
    called = []
    monkeypatch.setattr(tgf, "run_loop", lambda *a, **k: called.append(1))
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("do infra Repo: deploy DoD: WHEN x SHALL y", "tg-2", mem, api)
    assert not called and any("cần Gate" in s for s in api.sent)


def test_tg_gate_plus_deliver_warns_and_drops_deliver(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {})
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(repo))
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "w2"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("x Gate: true Deliver: a/b.py DoD: WHEN x SHALL y", "tg-3", mem, api)
    assert seen["t"].gate_cmd == "true" and seen["t"].deliver is None
    assert any("bỏ qua Deliver" in s for s in api.sent)


def test_cli_gate_ticket_wiring(tmp_path, monkeypatch, capsys):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(clif, "_cwd_repo", lambda: str(repo))
    monkeypatch.setattr(clif.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(clif.config, "REPOS", {})
    monkeypatch.setattr(clif, "make_workspace", lambda th, repo=None: (str(tmp_path / "w3"), "worktree"))
    seen = {}
    monkeypatch.setattr(clif, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": True, "worker": "code", "turns": 1})
    monkeypatch.setattr(clif.config, "ENABLE_MEMORY", False)
    clif.cmd_run("y Gate: ./tests/run.sh DoD: WHEN a SHALL b")
    assert seen["t"].gate_cmd == "./tests/run.sh"
    assert "Gate" in capsys.readouterr().out


def test_tg_pending_repo_deliver_dropped_with_warning_after_infer(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(tgf.deliver, "infer_gate", lambda g, d, r: "helm lint c")
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "wd"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("x Repo: deploy Deliver: a/b.py DoD: WHEN a SHALL b", "tg-9", mem, api)
    assert seen["t"].gate_cmd == "helm lint c" and seen["t"].deliver is None
    assert any("bỏ qua Deliver" in s for s in api.sent)    # KHÔNG drop im lặng


def test_edit_mode_never_stores_to_cache(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    stored = []

    class CacheMem:
        def register(self, *a, **k): ...
        def append_event(self, *a, **k): ...
        def recall(self, g, d):
            return None
        def store(self, *a, **k):
            stored.append(1)

    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", True)
    monkeypatch.setattr(dmod2, "ship_diff", lambda *a, **k: {"ok": True})
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true")
    run_loop(t, human_door=lambda a: True, notify=lambda m: None,
             journal_dir=str(tmp_path), memory=CacheMem(), workspace=str(ws))
    assert not stored                                  # locked decision #4
    payload = {"artifact": "diff", "goal": "g", "dod": "d", "mode": "edit",
               "gate_cmd": "true", "repo": str(repo), "workspace": str(ws)}
    monkeypatch.setattr(dmod2, "ship_diff", lambda *a, **k: {"ok": True})
    finish_suspended(CacheMem(), "t", payload, True, lambda m: None)
    assert not stored


def test_tg_pending_repo_explicit_gate_skips_infer(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    def no_infer(*a):
        raise AssertionError("infer_gate must NOT be called when Gate: explicit")
    monkeypatch.setattr(tgf.deliver, "infer_gate", no_infer)
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "wd2"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("x Repo: deploy Gate: true DoD: WHEN a SHALL b", "tg-10", mem, api)
    assert seen["t"].gate_cmd == "true"


def test_cmd_gate_env_disables_brain(tmp_path):
    ok, detail = gates.make_cmd_gate("echo brain=$LOOPKIT_NO_BRAIN", str(tmp_path))("")
    assert ok and "brain=1" in detail          # gate context phải cấm brain


def test_pytest_gate_env_disables_brain(tmp_path):
    tests_src = ("import os\nfrom solution import x\n\n"
                 "def test_env():\n    assert os.environ.get('LOOPKIT_NO_BRAIN') == '1'\n")
    ok, detail = gates.make_pytest_gate(tests_src, str(tmp_path))("x = 1\n")
    assert ok, detail


def test_brains_short_circuit_under_no_brain(tmp_path, monkeypatch):
    import loopkit.engine as eng
    monkeypatch.setenv("LOOPKIT_NO_BRAIN", "1")
    assert "LOOPKIT_NO_BRAIN" in eng.ask_claude("p", "s")
    assert "LOOPKIT_NO_BRAIN" in eng.run_agent("p", "s", workdir=str(tmp_path),
                                               tools=["Read"])
