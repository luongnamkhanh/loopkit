"""Idea-refinement verifiers — refine_turn là một vòng loop framework thu nhỏ."""
import importlib
import config, gates, refine


VALID_TICKET = ('viết hàm foo(x) DoD: WHEN 1 SHALL return 2 Tests: ```python\n'
                'from solution import foo\n\ndef test_foo():\n    assert foo(1) == 2\n```')


def test_config_and_role_defaults():
    importlib.reload(config)
    assert config.REFINE_MAX_TURNS == 5
    assert config.ROLE_MODELS["analyst"] == "sonnet"
    import roles
    assert roles.REGISTRY["analyst"].tools == ()        # analyst không có tool


def test_question_passthrough():
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    ask=lambda p, s, model=None: "QUESTION: A hay B?")
    assert kind == "ask" and text == "A hay B?"


def test_no_marker_treated_as_question():
    """Fail-closed: thiếu marker -> question, KHÔNG BAO GIỜ là draft."""
    kind, _ = refine.refine_turn("idea", [], 0, 5,
                                 ask=lambda p, s, model=None: "tôi nghĩ nên làm X trước")
    assert kind == "ask"


def test_valid_draft_passes_gate():
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    ask=lambda p, s, model=None: "TICKET: " + VALID_TICKET)
    assert kind == "draft"
    goal, dod, tests = gates.parse_ticket(text)
    assert goal and dod and tests                       # draft parse được y như intake thật


def test_bad_draft_retries_twice_then_unvalidated():
    calls = []
    def fake(p, s, model=None):
        calls.append(p)
        return "TICKET: không có dod gì cả"
    kind, _ = refine.refine_turn("idea", [], 0, 5, ask=fake)
    assert kind == "draft_unvalidated" and len(calls) == 3    # initial + 2 gate-retry


def test_budget_exhausted_forces_draft():
    def fake(p, s, model=None):
        if "BUDGET EXHAUSTED" in p:
            return "TICKET: " + VALID_TICKET
        return "QUESTION: gì nữa?"
    kind, _ = refine.refine_turn("idea", [], 5, 5, ask=fake)
    assert kind == "draft"


def test_history_and_turncount_in_prompt():
    seen = {}
    def fake(p, s, model=None):
        seen["p"] = p
        return "QUESTION: ok?"
    refine.refine_turn("làm cache", [{"role": "analyst", "text": "Q1?"},
                                     {"role": "user", "text": "A1"}], 1, 5, ask=fake)
    assert "làm cache" in seen["p"] and "Q1?" in seen["p"] and "A1" in seen["p"]
    assert "1/5" in seen["p"]


def test_empty_reply_retries_once_then_error():
    calls = []
    def fake(p, s, model=None):
        calls.append(1)
        return ""
    kind, _ = refine.refine_turn("idea", [], 0, 5, ask=fake)
    assert kind == "error" and len(calls) == 2


def test_repos_listed_in_prompt():
    seen = {}
    def fake(p, s, model=None):
        seen["p"] = p
        return "QUESTION: repo nào?"
    refine.refine_turn("idea", [], 0, 5,
                       repos={"active": ["pipeline", "loopkit"], "pending": ["iac"]}, ask=fake)
    assert "pipeline" in seen["p"] and "iac" in seen["p"]


def test_draft_with_unknown_repo_retries_then_ok():
    calls = []
    def fake(p, s, model=None):
        calls.append(p)
        if len(calls) == 1:
            return "TICKET: Repo: sai-ten " + VALID_TICKET
        return "TICKET: Repo: pipeline " + VALID_TICKET
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    repos={"active": ["pipeline"], "pending": []}, ask=fake)
    assert kind == "draft" and len(calls) == 2                 # 1 lần fail gate vì tên sai
    assert gates.parse_repo(text)[0] == "pipeline"


def test_draft_without_repo_still_valid():
    kind, _ = refine.refine_turn("idea", [], 0, 5,
                                 repos={"active": ["pipeline"], "pending": []},
                                 ask=lambda p, s, model=None: "TICKET: " + VALID_TICKET)
    assert kind == "draft"                                     # không Repo: -> TARGET_REPO default
