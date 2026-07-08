"""
Rung 3 demo: two tickets, one orchestrator, two named workers.
Shows the orchestrator routing a CODE ticket -> code agent (pytest gate) and an
INFRA ticket -> infra agent (manifest gate). Reviewer is the shared evaluator.
Run:  python3 example_multiagent.py
"""
import pathlib, subprocess, re
from engine import Ticket, run_loop

HERE = pathlib.Path(__file__).parent

# ---- CODE ticket: deterministic gate = pytest ----
CODE_TESTS = '''
import pytest
from solution import parse_int_list
def test_basic(): assert parse_int_list("1, 2,3") == [1, 2, 3]
def test_empty(): assert parse_int_list("  ") == []
def test_bad():
    with pytest.raises(ValueError):
        parse_int_list("1,x,3")
'''
def code_gate(code: str):
    (HERE / "solution.py").write_text(code)
    (HERE / "test_solution.py").write_text(CODE_TESTS)
    r = subprocess.run(["python3", "-m", "pytest", "-q", "test_solution.py"],
                       cwd=HERE, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-400:]

# ---- INFRA ticket: deterministic gate = structural checks on the manifest (real gate = kubeconform) ----
def infra_gate(manifest: str):
    checks = {
        "kind: Deployment": re.search(r"kind:\s*Deployment", manifest),
        "replicas: 2":      re.search(r"replicas:\s*2\b", manifest),
        "image nginx:1.27": "nginx:1.27" in manifest,
        "containerPort 80": re.search(r"containerPort:\s*80\b", manifest),
    }
    missing = [k for k, v in checks.items() if not v]
    return (not missing), ("all checks pass" if not missing else "missing: " + ", ".join(missing))

code_ticket = Ticket(
    goal="Write parse_int_list(s: str) -> list[int]: split on commas, strip whitespace, parse ints.",
    dod="1) tests pass; 2) empty/whitespace -> []; 3) bad token -> ValueError naming the token; 4) clean.",
    verifier=code_gate, risky=False)

infra_ticket = Ticket(
    goal="Write a Kubernetes Deployment manifest (YAML) for nginx.",
    dod="apps/v1 Deployment; replicas: 2; image nginx:1.27; containerPort 80; a matching label selector.",
    verifier=infra_gate, risky=True)

if __name__ == "__main__":
    for name, t in [("CODE", code_ticket), ("INFRA", infra_ticket)]:
        print(f"\n================ {name} TICKET ================")
        res = run_loop(t, max_turns=3, journal_dir=str(HERE))
        print("RESULT:", {k: v for k, v in res.items() if k != "artifact"})
