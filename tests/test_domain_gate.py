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


def test_parse_gate_cmd_strips_markdown_backticks():
    # live 2026-07-19: draft ghi Gate: `pytest ...` — backtick sống sót tới sh -c
    # thành command substitution: pytest chạy rồi stdout bị execute ("....: not found")
    cmd, _ = gates.parse_gate_cmd("Gate: `python -m pytest backend/tests -q`\nDoD: WHEN a SHALL b")
    assert cmd == "python -m pytest backend/tests -q"


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


def test_gate_env_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPKIT_ENABLE_TOOLS", "1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    ok, detail = gates.make_cmd_gate(
        'echo "tools=${LOOPKIT_ENABLE_TOOLS:-none} slack=${SLACK_BOT_TOKEN:-none} '
        'pp=${PYTHONPATH:-none} brain=$LOOPKIT_NO_BRAIN"', str(tmp_path))("")
    assert ok and "tools=none" in detail and "slack=none" in detail
    assert "pp=none" in detail and "brain=1" in detail


def test_brains_timeout_returns_sentinel_not_raise(tmp_path, monkeypatch):
    import loopkit.engine as eng
    monkeypatch.delenv("LOOPKIT_NO_BRAIN", raising=False)

    def slow(*a, **k):
        raise eng.subprocess.TimeoutExpired("claude", 600)
    monkeypatch.setattr(eng.subprocess, "run", slow)
    out = eng.ask_claude("p", "s")
    assert "LOOPKIT_TIMEOUT" in out            # không raise — loop retry được
    out2 = eng.run_agent("p", "s", workdir=str(tmp_path), tools=["Read"])
    assert "LOOPKIT_TIMEOUT" in out2


def test_issues_command_lists_via_gh(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    seen = {}
    def fr(cmd, cwd=None, capture_output=None, text=None, timeout=None):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stdout": "#7 fix bug\n#8 add feature"})()
    monkeypatch.setattr(tgf.subprocess, "run", fr)
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/issues"}, mem, api)
    assert seen["cmd"] == ["gh", "issue", "list"]
    assert any("#7 fix bug" in s for s in api.sent)


def test_issues_command_repo_arg_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "REPOS", {"loopkit": str(tmp_path)})
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/o/loopkit.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    seen = {}
    monkeypatch.setattr(tgf.subprocess, "run",
                        lambda cmd, **k: seen.update(cwd=k.get("cwd")) or
                        type("R", (), {"returncode": 0, "stdout": "no open issues"})())
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/issues loopkit"}, mem, api)
    assert seen["cwd"] == str(tmp_path) and any("no open issues" in s for s in api.sent)


def test_issues_command_empty_or_no_cli_friendly(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: None)     # gh thiếu
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/issues"}, mem, api)
    assert any("không" in s.lower() or "không tra" in s.lower() for s in api.sent)  # báo hiền, không crash


def test_fetch_issue_success_returns_stdout(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    monkeypatch.setattr(tgf.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "body"})())
    assert tgf.fetch_issue(str(tmp_path), 6) == "body"


def test_fetch_issue_nonzero_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    monkeypatch.setattr(tgf.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})())
    assert tgf.fetch_issue(str(tmp_path), 6) is None


def test_fetch_issue_missing_cli_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: None)
    assert tgf.fetch_issue(str(tmp_path), 6) is None


def test_resolve_fetches_issue_and_seeds_refine(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "REPOS", {"loopkit": str(tmp_path)})
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/o/loopkit.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    seen = {}

    def fr(cmd, cwd=None, capture_output=None, text=None, timeout=None):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return type("R", (), {"returncode": 0, "stdout": "Bug: crash on save"})()
    monkeypatch.setattr(tgf.subprocess, "run", fr)
    calls = []
    monkeypatch.setattr(tgf, "refine_step",
                        lambda thread, answer, mem, api: calls.append((thread, answer)))
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 5, "text": "/resolve #6 loopkit"}, mem, api)
    assert seen["cmd"] == ["gh", "issue", "view", "6"] and seen["cwd"] == str(tmp_path)
    assert len(calls) == 1 and calls[0][1] is None
    run = mem.get_run(calls[0][0])
    assert run["status"] == "refining" and run["refine_turns"] == 0
    idea = run["idea"]
    assert "Bug: crash on save" in idea and "Repo: loopkit" in idea and "#6" in idea


def test_resolve_unknown_repo_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "REPOS", {"loopkit": str(tmp_path)})
    calls = []
    monkeypatch.setattr(tgf, "refine_step", lambda *a, **k: calls.append(1))
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 6, "text": "/resolve #6 nope"}, mem, api)
    assert not calls
    assert any("loopkit" in s for s in api.sent)


def test_resolve_fetch_fail_friendly(monkeypatch, tmp_path):
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(tgf.config, "REPOS", {})
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: None)     # gh thiếu
    calls = []
    monkeypatch.setattr(tgf, "refine_step", lambda *a, **k: calls.append(1))
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 7, "text": "/resolve #6"}, mem, api)
    assert not calls
    assert any("#6" in s for s in api.sent)
    assert not mem.runs()


def test_resolve_number_without_hash(monkeypatch, tmp_path):
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(tgf.config, "REPOS", {})
    monkeypatch.setattr(tgf.deliver, "_remote_url", lambda r: "https://github.com/x/y.git")
    monkeypatch.setattr(tgf.shutil, "which", lambda t: "/usr/bin/gh")
    seen = {}

    def fr(cmd, cwd=None, capture_output=None, text=None, timeout=None):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stdout": "issue body"})()
    monkeypatch.setattr(tgf.subprocess, "run", fr)
    calls = []
    monkeypatch.setattr(tgf, "refine_step",
                        lambda thread, answer, mem, api: calls.append(thread))
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 8, "text": "/resolve 6"}, mem, api)
    assert seen["cmd"] == ["gh", "issue", "view", "6"]
    idea = mem.get_run(calls[0])["idea"]
    assert "pytest" in idea and "#6" in idea


def test_repos_command_lists_names_with_marks(monkeypatch):
    monkeypatch.setattr(tgf.config, "REPOS", {"loopkit": "/a", "pipeline": "/b", "iac": "/c"})
    monkeypatch.setattr(tgf.config, "TARGET_REPO", "/b")     # pipeline là default
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"iac"})
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/repos"}, mem, api)
    out = api.sent[-1]
    assert "loopkit" in out and "pipeline" in out and "iac" in out
    assert "⭐default" in out and "pipeline ⭐default" in out    # default đúng repo
    assert "iac ⏳pending" in out                               # pending đúng repo


def test_repos_command_empty(monkeypatch):
    monkeypatch.setattr(tgf.config, "REPOS", {})
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/repos"}, mem, api)
    assert any("chưa cấu hình" in s for s in api.sent)


def _await_mem(*threads):
    """MStub với các thread đang refining."""
    m = MStub()
    for t in threads:
        m.reg[t] = {"status": "refining"}
    return m


def test_cancel_by_thread_id(monkeypatch):
    api, mem = TgStub(), _await_mem("tg-5", "tg-9")
    tgf.handle_message({"message_id": 1, "text": "/cancel tg-5"}, mem, api)
    assert mem.reg["tg-5"]["status"] == "refine_cancelled"
    assert mem.reg["tg-9"]["status"] == "refining"          # cái kia không đụng
    assert any("Đã huỷ" in s and "tg-5" in s for s in api.sent)


def test_cancel_bare_one_awaiting(monkeypatch):
    api, mem = TgStub(), _await_mem("tg-7")
    tgf.handle_message({"message_id": 1, "text": "/cancel"}, mem, api)
    assert mem.reg["tg-7"]["status"] == "refine_cancelled"


def test_cancel_bare_two_awaiting_lists_not_cancels(monkeypatch):
    api, mem = TgStub(), _await_mem("tg-7", "tg-8")
    tgf.handle_message({"message_id": 1, "text": "/cancel"}, mem, api)
    assert mem.reg["tg-7"]["status"] == "refining" and mem.reg["tg-8"]["status"] == "refining"
    out = api.sent[-1]
    assert "tg-7" in out and "tg-8" in out                   # liệt kê để tự chọn


def test_cancel_bare_none_awaiting(monkeypatch):
    api, mem = TgStub(), MStub()
    tgf.handle_message({"message_id": 1, "text": "/cancel"}, mem, api)
    assert any("không có thread" in s for s in api.sent)


def test_cancel_unknown_or_not_awaiting(monkeypatch):
    api, mem = TgStub(), MStub()
    mem.reg["tg-3"] = {"status": "done"}
    tgf.handle_message({"message_id": 1, "text": "/cancel tg-3"}, mem, api)     # done, không chờ
    tgf.handle_message({"message_id": 2, "text": "/cancel tg-nope"}, mem, api)  # lạ
    assert sum("không ở trạng thái chờ" in s for s in api.sent) == 2
    assert mem.reg["tg-3"]["status"] == "done"               # không đổi
