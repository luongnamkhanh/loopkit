from loopkit import gates


def test_parse_deliver_extracts_and_strips():
    path, rest = gates.parse_deliver(
        "Tinh bearing Deliver: flink/bearing.py DoD: WHEN x SHALL y")
    assert path == "flink/bearing.py"
    assert rest == "Tinh bearing DoD: WHEN x SHALL y"


def test_parse_deliver_absent():
    path, rest = gates.parse_deliver("goal DoD: WHEN x SHALL y")
    assert path is None and rest == "goal DoD: WHEN x SHALL y"


def test_parse_deliver_case_insensitive_and_none_input():
    path, _ = gates.parse_deliver("x deliver: a/b_c.py DoD: y")
    assert path == "a/b_c.py"
    assert gates.parse_deliver(None) == (None, "")


import pathlib
import subprocess

from loopkit import deliver


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def make_ws(tmp_path):
    """Workspace giả lập worktree: có solution.py + test_ticket.py xanh."""
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_ticket.py").write_text(
        "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return ws


def test_validate_path(tmp_path):
    repo = tmp_path
    assert deliver.validate_path("pkg/mod.py", str(repo))
    assert not deliver.validate_path("/abs/mod.py", str(repo))
    assert not deliver.validate_path("../out.py", str(repo))
    assert not deliver.validate_path("pkg/mod.txt", str(repo))
    assert not deliver.validate_path("", str(repo))


def test_place_and_verify_moves_rewrites_and_regates(tmp_path):
    ws = make_ws(tmp_path)
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert ok, detail
    assert (ws / "pkg" / "adder.py").exists()
    assert not (ws / "solution.py").exists()
    tsrc = (ws / "pkg" / "test_adder.py").read_text()
    assert "from adder import add" in tsrc and "solution" not in tsrc


def test_place_and_verify_regate_fails_on_broken_artifact(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "solution.py").write_text("def add(a, b):\n    return a - b\n")  # sai
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert not ok


def test_place_and_verify_compile_only_when_no_tests(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "test_ticket.py").unlink()
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert ok


def test_place_and_verify_rewrite_is_word_boundary(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "solution.py").write_text(
        "class resolution:\n    value = 3\n\ndef add(a, b):\n    return a + b\n")
    (ws / "test_ticket.py").write_text(
        "import solution\nfrom solution import add\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
        "def test_res():\n    assert solution.resolution.value == 3\n")
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert ok, detail
    tsrc = (ws / "pkg" / "test_adder.py").read_text()
    assert "resolution" in tsrc and "adder.resolution.value" in tsrc


def make_repo_with_ws(tmp_path):
    """Repo thật + bare origin + worktree có artifact xanh (mô phỏng sau approve)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-qu", "origin", "main")
    ws = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(ws), "-b", "loop/t1")
    (ws / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_ticket.py").write_text(
        "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return repo, bare, ws


def test_ship_commits_and_pushes(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    events = []
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add two numbers",
                       "WHEN ints SHALL sum", emit=events.append,
                       record=lambda e: events.append(e))
    assert res["ok"], res
    assert res["branch"] == "feat/adder"
    r = subprocess.run(["git", "-C", str(bare), "log", "--oneline", "feat/adder"],
                       capture_output=True, text=True)
    assert "add two numbers" in r.stdout          # commit lên remote, message từ goal


def test_ship_push_fail_keeps_branch_and_reports(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    hook = pathlib.Path(bare) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'read-only group' >&2\nexit 1\n")
    hook.chmod(0o755)
    msgs = []
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add two numbers",
                       "WHEN ints SHALL sum", emit=msgs.append)
    assert not res["ok"] and res["error"] == "push"
    assert res["branch"] == "feat/adder"
    local = _git(repo, "log", "--oneline", "feat/adder")
    assert "add two numbers" in local.stdout      # commit còn local
    assert any("push FAIL" in m for m in msgs)    # báo rõ, có stderr


def test_ship_aborts_on_regate_fail_no_commit(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    (ws / "solution.py").write_text("def add(a, b):\n    return a - b\n")
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add", "dod")
    assert not res["ok"] and res["error"] == "regate"
    assert _git(repo, "log", "--oneline", "feat/adder").returncode != 0  # branch không tồn tại


def test_ship_compile_only_no_test_file(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    (ws / "test_ticket.py").unlink()
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add two numbers", "dod")
    assert res["ok"], res
    r = subprocess.run(["git", "-C", str(bare), "log", "--oneline", "feat/adder"],
                       capture_output=True, text=True)
    assert "add two numbers" in r.stdout


import loopkit.deliver as dmod
from loopkit import config


GITLAB_PUSH = ("remote:\nremote: To create a merge request for feat/adder, visit:\n"
               "remote:   https://gitlab.com/g/p/-/merge_requests/new?"
               "merge_request%5Bsource_branch%5D=feat%2Fadder\nremote:\n")
GITHUB_PUSH = ("remote:\nremote: Create a pull request for 'feat/adder' on GitHub by visiting:\n"
               "remote:      https://github.com/o/r/pull/new/feat/adder\nremote:\n")


def test_create_mr_fallback_link_gitlab(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda _: None)
    url, note = dmod.create_mr(str(tmp_path), "feat/adder", "t", "b",
                               push_output=GITLAB_PUSH,
                               remote_url="https://gitlab.com/g/p.git")
    assert url and "merge_requests/new" in url


def test_create_mr_fallback_link_github(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda _: None)
    url, note = dmod.create_mr(str(tmp_path), "feat/adder", "t", "b",
                               push_output=GITHUB_PUSH,
                               remote_url="git@github.com:o/r.git")
    assert url and "pull/new" in url


def test_create_mr_via_glab(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda name: "/bin/" + name)
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class R:  # noqa: N801
            returncode = 0
            stdout = "https://gitlab.com/g/p/-/merge_requests/7\n"
            stderr = ""
        return R()

    monkeypatch.setattr(dmod.subprocess, "run", fake_run)
    url, note = dmod.create_mr(str(tmp_path), "feat/adder", "tiêu đề", "dod",
                               remote_url="https://gitlab.com/g/p.git")
    assert url == "https://gitlab.com/g/p/-/merge_requests/7"
    assert calls["cmd"][0] == "glab"


def test_create_mr_off(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPKIT_MR_TOOL", "off")
    import importlib
    importlib.reload(config)
    try:
        url, note = dmod.create_mr(str(tmp_path), "b", "t", "d",
                                   remote_url="https://gitlab.com/g/p.git")
        assert url is None and "off" in note
    finally:
        monkeypatch.delenv("LOOPKIT_MR_TOOL")
        importlib.reload(config)


def test_create_mr_cli_fails_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda name: "/bin/" + name)

    def fake_run(cmd, **kw):
        class R:  # noqa: N801
            returncode = 1
            stdout = ""
            stderr = "api error"
        return R()

    monkeypatch.setattr(dmod.subprocess, "run", fake_run)
    url, note = deliver.create_mr(str(tmp_path), "feat/x", "t", "b",
                                  push_output=GITLAB_PUSH,
                                  remote_url="https://gitlab.com/g/p.git")
    assert url and "merge_requests/new" in url


def test_create_mr_timeout_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda name: "/bin/" + name)

    def fake_run(cmd, **kw):
        raise dmod.subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(dmod.subprocess, "run", fake_run)
    url, note = deliver.create_mr(str(tmp_path), "feat/x", "t", "b",
                                  push_output=GITHUB_PUSH,
                                  remote_url="git@github.com:o/r.git")
    assert url and "pull/new" in url


def test_infer_path_valid_reply(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    got = deliver.infer_path("thêm hàm cộng", str(repo),
                             ask=lambda p, s, model=None: "pkg/adder.py")
    assert got == "pkg/adder.py"


def test_infer_path_junk_reply_degrades_to_none(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    for junk in ("/etc/x.py", "../up.py", "tôi nghĩ là pkg nhé", ""):
        assert deliver.infer_path("g", str(repo),
                                  ask=lambda p, s, model=None, j=junk: j) is None


def test_infer_path_prompt_contains_tree_and_goal(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    seen = {}

    def fake_ask(prompt, soul, model=None):
        seen["prompt"] = prompt
        return "pkg/adder.py"

    deliver.infer_path("thêm hàm cộng", str(repo), ask=fake_ask)
    assert "pkg/__init__.py" in seen["prompt"] and "thêm hàm cộng" in seen["prompt"]


def test_ensure_workspace_rematerializes(tmp_path, monkeypatch):
    repo, _, ws = make_repo_with_ws(tmp_path)
    import shutil as _sh
    _git(repo, "worktree", "remove", "--force", str(ws))   # mô phỏng /tmp bay sau reboot
    monkeypatch.setattr(dmod, "make_workspace",
                        lambda t, repo=None: (str(tmp_path / "re"), "worktree"))
    (tmp_path / "re").mkdir()
    out = deliver.ensure_workspace("t1", str(repo), "ART", "TESTS", workspace=str(ws))
    assert out == str(tmp_path / "re")
    assert (tmp_path / "re" / "solution.py").read_text() == "ART"
    assert (tmp_path / "re" / "test_ticket.py").read_text() == "TESTS"


def test_ensure_workspace_keeps_existing(tmp_path):
    _, _, ws = make_repo_with_ws(tmp_path)
    assert deliver.ensure_workspace("t1", "unused", "X", workspace=str(ws)) == str(ws)
    assert (ws / "solution.py").read_text() != "X"          # không ghi đè file đang có
