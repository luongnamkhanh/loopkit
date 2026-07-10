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
