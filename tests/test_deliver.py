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
