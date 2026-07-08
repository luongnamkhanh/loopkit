"""T4 verifier — gates.py DoD made runnable."""
import gates

TESTS = "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def test_pytest_gate_pass(tmp_path):
    v = gates.make_pytest_gate(TESTS, str(tmp_path))
    ok, detail = v("def add(a, b):\n    return a + b\n")
    assert ok is True


def test_pytest_gate_fail_with_detail(tmp_path):
    v = gates.make_pytest_gate(TESTS, str(tmp_path))
    ok, detail = v("def add(a, b):\n    return a - b\n")
    assert ok is False
    assert "assert" in detail or "failed" in detail


def test_gate_isolated_workdir(tmp_path):
    v1 = gates.make_pytest_gate(TESTS, str(tmp_path / "a"))
    v2 = gates.make_pytest_gate("from solution import mul\n\ndef test_m():\n    assert mul(2,3)==6\n",
                                str(tmp_path / "b"))
    ok1, _ = v1("def add(a, b):\n    return a + b\n")
    ok2, _ = v2("def mul(a, b):\n    return a * b\n")
    assert ok1 and ok2                      # frozen tests don't clobber each other


def test_parse_ticket_with_tests_block():
    g, d, t = gates.parse_ticket(
        "<@U1> build add   DoD: WHEN called SHALL add   "
        "Tests: ```python\nfrom solution import add\ndef test_a():\n    assert add(1,1)==2\n```")
    assert "build add" in g and "SHALL add" in d and "def test_a" in t


def test_parse_ticket_without_tests():
    g, d, t = gates.parse_ticket("do X DoD: works")
    assert g == "do X" and d == "works" and t is None


def test_parse_ticket_missing_dod():
    assert gates.parse_ticket("just some text") == (None, None, None)


def test_derive_tests_from_fake_llm():
    fake = lambda p, s, model=None: ("```python\nfrom solution import f\n"
                                     "def test_f():\n    assert f() == 1\n```")
    src = gates.derive_tests("goal", "dod", ask=fake)
    assert src and "def test_f" in src and "solution" in src


def test_derive_tests_rejects_nonsense():
    assert gates.derive_tests("g", "d", ask=lambda p, s, model=None: "sorry, no code") is None
    assert gates.derive_tests("g", "d", ask=lambda p, s, model=None: "```python\nx = 1\n```") is None


# --- regression tests from the Phase-2 adversarial review (real failures -> test cases) ---

def test_derive_tests_rejects_vacuous_gate_no_import():
    """Reviewer repro: 'solution' only in a comment -> would pass ANY artifact. Must be None."""
    fake = lambda p, s, model=None: ("```python\n# tests for solution\n"
                                     "def add(a, b):\n    return a + b\n"
                                     "def test_add():\n    assert add(1, 2) == 3\n```")
    assert gates.derive_tests("g", "d", ask=fake) is None


def test_parse_dod_containing_word_tests_not_truncated():
    """Reviewer repro: DoD prose with 'tests:' must NOT truncate the DoD."""
    g, d, t = gates.parse_ticket("do X DoD: WHEN run, SHALL pass all tests: unit and integration")
    assert d == "WHEN run, SHALL pass all tests: unit and integration"
    assert t is None


def test_parse_explicit_tests_must_import_solution():
    g, d, t = gates.parse_ticket("do X DoD: works Tests: def test_a():\n    assert 1")
    assert t is None and "works" in d          # not real tests -> stays prose, no garbage gate


def test_compile_gate_rejects_empty_artifact(tmp_path):
    """P3 reviewer repro: py_compile passes '' -> a tool agent writing nothing sailed through."""
    v = gates.make_compile_gate(str(tmp_path))
    ok, detail = v("")
    assert ok is False and "empty" in detail
    ok2, _ = v("x = 1\n")
    assert ok2 is True
