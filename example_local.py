"""
Working example: a real ticket run locally with pytest as the deterministic gate.
Run:  python3 example_local.py
(Replace `verifier`, `human_door`, and the souls to point loopkit at your real project.)
"""
import pathlib, subprocess
import sys; sys.path.insert(0, "src")
from loopkit.engine import Ticket, run_loop

HERE = pathlib.Path(__file__).parent
TESTS = '''
import pytest
from solution import parse_int_list
def test_basic():  assert parse_int_list("1, 2,3") == [1, 2, 3]
def test_empty():  assert parse_int_list("   ") == []
def test_bad():
    with pytest.raises(ValueError):
        parse_int_list("1,x,3")
'''

def verifier(code: str):
    (HERE / "solution.py").write_text(code)
    (HERE / "test_solution.py").write_text(TESTS)
    r = subprocess.run(["python3", "-m", "pytest", "-q", "test_solution.py"],
                       cwd=HERE, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-500:]

ticket = Ticket(
    goal="Write parse_int_list(s: str) -> list[int]: split on commas, strip whitespace, parse ints.",
    dod=("1) tests pass; 2) empty/whitespace-only -> []; "
         "3) non-integer token -> ValueError whose message NAMES the bad token; "
         "4) clean and readable (no bare 'except')."),
    verifier=verifier,
    risky=True,
)

if __name__ == "__main__":
    result = run_loop(ticket, max_turns=4, journal_dir=str(HERE))
    print("\nRESULT:", {k: v for k, v in result.items() if k != "artifact"})
