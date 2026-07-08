"""P3 verifiers — tool execution, model tiering, worktree isolation, registry artifact."""
import pathlib, subprocess
import config, engine, roles, workspace
from engine import Ticket, run_loop
from memory import Memory


# ---------- fakes ----------
def fake_ask(prompt, soul, model=None):
    if soul == roles.REGISTRY["orchestrator"].soul:
        return "code"
    if soul == roles.REGISTRY["reviewer"].soul:
        return "VERDICT: PASS\n- ok"
    return "```python\nX=1\n```"


def fake_agent_factory(write_content):
    def fake_run_agent(prompt, soul, *, workdir, tools, model=None):
        if soul == roles.REGISTRY["reviewer"].soul:
            return "VERDICT: PASS\n- ok"
        if write_content is not None:
            (pathlib.Path(workdir) / "solution.py").write_text(write_content)
        return "done"
    return fake_run_agent


# ---------- T6a: tool execution ----------
def test_toolmode_artifact_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(engine, "ask_claude", fake_ask)          # routing stays text-mode
    monkeypatch.setattr(engine, "run_agent", fake_agent_factory("def f():\n    return 1\n"))
    ws = tmp_path / "ws"; ws.mkdir()
    t = Ticket(goal="g", dod="d", verifier=lambda a: ("def f" in a, "checked"))
    res = run_loop(t, journal_dir=str(tmp_path), notify=lambda m: None,
                   workspace=str(ws), max_turns=2)
    assert res["ok"] is True and "def f()" in res["artifact"]


def test_toolmode_no_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(engine, "ask_claude", fake_ask)
    monkeypatch.setattr(engine, "run_agent", fake_agent_factory(None))   # agent writes nothing
    ws = tmp_path / "ws"; ws.mkdir()
    t = Ticket(goal="g", dod="d", verifier=lambda a: (bool(a.strip()), "empty artifact"))
    res = run_loop(t, journal_dir=str(tmp_path), notify=lambda m: None,
                   workspace=str(ws), max_turns=2)
    assert res["ok"] is False                                            # exhausted, fail-closed


def test_textmode_default_unchanged(tmp_path, monkeypatch):
    """ENABLE_TOOLS defaults False -> P2 text path, workspace ignored."""
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(engine, "ask_claude", fake_ask)
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"))
    res = run_loop(t, journal_dir=str(tmp_path), notify=lambda m: None,
                   workspace=str(tmp_path), max_turns=2)
    assert res["ok"] is True and "X=1" in res["artifact"]


def test_allowed_tools_least_privilege():
    rev = roles.allowed_tools(roles.REGISTRY["reviewer"])
    code = roles.allowed_tools(roles.REGISTRY["code"])
    orch = roles.allowed_tools(roles.REGISTRY["orchestrator"])
    assert "Write" in code and "Edit" in code
    assert "Write" not in rev and "Edit" not in rev            # reviewer can NEVER write...
    assert "Bash(python3:*)" not in rev                        # ...not even via `python3 -c`
    assert "Bash(pytest:*)" in rev                             # ...but can ACT (run tests)
    assert orch == ()                                          # orchestrator: no tools


def test_workspace_sanitize_collision_proof(monkeypatch):
    """Reviewer repro: 'a/b' and 'a b' both sanitize to 'a_b' -> must NOT share a workspace."""
    monkeypatch.setattr(config, "TARGET_REPO", "")
    p1, _ = workspace.make_workspace("a/b")
    p2, _ = workspace.make_workspace("a b")
    assert p1 != p2


# ---------- T6b: model tiering ----------
def test_role_models_tiering_defaults_and_override(monkeypatch):
    import importlib
    importlib.reload(config)
    assert config.ROLE_MODELS["reviewer"] != config.ROLE_MODELS["code"]  # separate-model rule
    assert config.ROLE_MODELS["orchestrator"] == "haiku"
    monkeypatch.setenv("LOOPKIT_MODEL_REVIEWER", "sonnet")
    importlib.reload(config)
    assert config.ROLE_MODELS["reviewer"] == "sonnet"
    monkeypatch.delenv("LOOPKIT_MODEL_REVIEWER")
    importlib.reload(config)


# ---------- T6c: workspaces ----------
def test_workspace_standalone_dirs_isolated(monkeypatch):
    monkeypatch.setattr(config, "TARGET_REPO", "")
    p1, k1 = workspace.make_workspace("th one")
    p2, k2 = workspace.make_workspace("th two")
    assert k1 == k2 == "dir" and p1 != p2 and pathlib.Path(p1).is_dir()


def test_workspace_git_worktrees_isolated_and_idempotent(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setattr(workspace, "WT_BASE", tmp_path / "wts")
    p1, k1 = workspace.make_workspace("tA", repo=str(repo))
    p2, k2 = workspace.make_workspace("tB", repo=str(repo))
    assert k1 == k2 == "worktree" and p1 != p2
    assert (pathlib.Path(p1) / ".git").exists()                # a real worktree
    p1b, _ = workspace.make_workspace("tA", repo=str(repo))    # idempotent per thread
    assert p1b == p1


# ---------- T6d: registry keeps the latest artifact (revision base for follow-ups) ----------
def test_registry_stores_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ask_claude", fake_ask)
    mem = Memory(str(tmp_path / "m"))
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"))
    run_loop(t, memory=mem, thread_id="tr", journal_dir=str(tmp_path), notify=lambda m: None)
    assert "X=1" in mem.get_run("tr")["artifact"]
