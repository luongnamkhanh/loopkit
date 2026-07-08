"""
Named role-agents (rung 3). Each role = a persona (soul) + declarative tool scope.
The orchestrator ROUTES a ticket to one worker; the reviewer is the evaluator.
Map these to your project's real agents; bind real tools/MCP where `tools` is declared.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Role:
    name: str
    soul: str
    tools: tuple = ()      # declarative least-privilege scope; real project binds real tools here

ORCHESTRATOR = Role(
    "orchestrator",
    "You are the orchestrator. Given a ticket, decide which ONE worker handles it. "
    "Reply EXACTLY one word: 'code' or 'infra'. "
    "code = application/ETL/script/logic; infra = Kubernetes/Helm/Terraform/deploy manifests.",
)

CODE = Role(
    "code",
    "You are the Code agent (application/ETL/logic). First restate the DoD in ONE line, then output "
    "ONLY the solution in a fenced code block. No prose after the code.",
    tools=("read", "write", "run_tests"),
)

INFRA = Role(
    "infra",
    "You are the Infra agent (Kubernetes/Helm/Terraform). First restate the DoD in ONE line, then output "
    "ONLY the manifest in a fenced block. No prose after.",
    tools=("read", "write", "helm", "kubectl"),
)

REVIEWER = Role(
    "reviewer",
    "You are the Reviewer. Assume the work is BROKEN until proven otherwise. Judge ONLY against the "
    "Definition of Done. First line EXACTLY 'VERDICT: PASS' or 'VERDICT: REJECT', then 1-4 bullet reasons. "
    "If any DoD item is unclear, REJECT.",
    tools=("read", "run_pytest"),   # pytest-only bash: Bash(python3:*) would let a reviewer
)                                   # write files via `python3 -c` = maker/checker collusion

REGISTRY = {r.name: r for r in (ORCHESTRATOR, CODE, INFRA, REVIEWER)}
WORKERS = ("code", "infra")

# --- declarative tool scope -> real Claude Code --allowedTools values (P3 tool mode) ---
# Least-privilege is enforced here: the reviewer maps to read+run ONLY (no Write/Edit — a
# reviewer that can edit can "fix then pass" = maker/checker collusion).
TOOLMAP = {
    "read": ("Read", "Glob", "Grep"),
    "write": ("Write", "Edit"),
    "run_tests": ("Bash(python3:*)", "Bash(pytest:*)"),   # workers (can write anyway)
    "run_pytest": ("Bash(pytest:*)",),                    # reviewer: run tests, no python -c escape
    "helm": ("Bash(helm:*)",),
    "kubectl": ("Bash(kubectl:*)",),
}


def allowed_tools(role: Role) -> tuple:
    out = []
    for t in role.tools:
        out.extend(TOOLMAP.get(t, ()))
    return tuple(dict.fromkeys(out))
