# loopkit — a minimal agentic loop framework (top-tier pattern)

The reusable **engine** behind the design in `../docs/loop-framework-flow.md`. It implements the
pattern production teams actually use, and nothing more:

```
ticket(+DoD) → GENERATOR → DETERMINISTIC GATE (first, cheap, hard)
             → SEPARATE skeptical EVALUATOR → feedback → bounded stop → HUMAN DOOR
             (every turn journaled to disk)
```

`engine.py` is ~90 lines. The brain is the `claude` CLI. This is the **walls**; Slack and durable
execution (Temporal) are the **roof** — added incrementally (see *Hardening*).

## Apply it to a real project — implement THREE seams

The engine is generic; your project plugs in via three things:

| Seam | What you supply | Real-project example |
|---|---|---|
| **`Ticket.verifier(artifact) -> (passed, detail)`** | the **deterministic gate** | `helm lint` → `kubeconform` → `conftest` → `kubectl --dry-run`; or `pytest` |
| **`human_door(artifact) -> approved: bool`** | the **approval mechanism** | a Slack Block Kit `[Approve]` button handler |
| **a `roles` registry** (`roles.py`) | your **named agents** (orchestrator/code/infra/reviewer): soul + tool scope | Code/Infra/Reviewer personas |

```python
from engine import Ticket, run_loop
from roles import REGISTRY   # or your own registry of Role(name, soul, tools)

ticket = Ticket(goal="...", dod="...checkable Definition of Done...", verifier=my_gate, risky=True)
run_loop(ticket, roles=REGISTRY, human_door=my_slack_door)
```

The **orchestrator** routes each ticket to one worker (LLM decision + a deterministic keyword
backstop, since LLM routing is stochastic — see `route()`). The **reviewer** is the evaluator.

**The DoD is mandatory and must be checkable** — it is the loop's stop condition (see
`../docs/loop-framework-flow.md`). A vague ticket cannot drive an autonomous loop.

## Run the local example

```bash
cd loopkit && python3 example_local.py   # requires: claude CLI logged in, pytest
```

You'll see: generator → gate=PASS → evaluator verdict → human door; each turn in `run_journal.jsonl`.

## Hardening (add incrementally, when the real loop demands it)

These bolt onto existing seams — do **not** build them up front (design is captured here; build when a
real ticket needs it):

- **§8.3 trustworthy evaluator** → harden `eval_soul`/`evaluate`: calibrate against a human gold set
  (TPR/TNR > 90%) before gating; score the trajectory, not just the final; measure `pass^k`.
- **§8.2 risk-based gates** → make `human_door` fire only at high-blast-radius boundaries.
- **§8.1 durable execution** → wrap `run_loop` in Temporal/Inngest. The engine is already
  durable-friendly (state journaled per turn, steps separable); keep side effects **idempotent**.
- **§8.4 continuous eval** → feed real production failures back into `verifier` as new checks.

## Honest boundaries

- The engine calls a **real** model (`claude` CLI) in both generator and evaluator roles.
- It has **not** been run against real infra (Databricks / K8s / MinIO) — wire your real `verifier`
  and `human_door` to do that.
- `max_turns` is the only bound today; a **token/cost budget** is a hardening step.
