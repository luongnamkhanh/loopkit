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
