# Ticket template (agentic loop)

A ticket must be **self-contained** — the agent has no implicit context (it won't ask, won't
browse, won't ping the PM). Fill these 6 parts. The **Acceptance Criteria = the loop's Definition
of Done** (its stop condition); **Verification** is how it's proven. Repo-wide rules live in
`AGENTS.md` — don't repeat them here.

## The 6 parts

```markdown
## 1. Objective (why)
<one sentence + rationale, so the agent doesn't optimize the wrong thing>

## 2. Scope / Non-goals
<exactly one PR-sized task>   Out of scope: <...>

## 3. Context
Files: <paths> · Stack: <lang + versions> · Links: <design doc / related ticket / example to mirror>

## 4. Constraints
<off-limits files · no new deps without approval · invariants that must NOT break>
(Repo-wide boundaries already in AGENTS.md.)

## 5. Acceptance Criteria (= Definition of Done) — EARS, verifiable
- WHEN <trigger>, THE SYSTEM SHALL <response>
- IF <condition>, THEN THE SYSTEM SHALL <response>
- WHILE <state>, THE SYSTEM SHALL <response>

## 6. Verification
Runnable check: <pytest / build / lint / scan> · Evidence: <...> · Human gate: <yes/no + when>
```

**EARS** = Easy Approach to Requirements Syntax (what Kiro auto-generates). It forces acceptance
criteria that are testable and cover edge cases — the difference between a DoD a machine can check
and a vague wish.

## Filled example

```markdown
1. Objective: parse comma-separated ints so the CSV importer can accept int columns.
2. Scope: add parse_int_list() in utils.py. Non-goals: no file IO, no CSV parsing.
3. Context: utils.py; Python 3.11; mirror the existing parse_float_list().
4. Constraints: don't touch importer.py; no new dependencies.
5. Acceptance Criteria (EARS):
   - WHEN input is "1, 2,3", THE SYSTEM SHALL return [1, 2, 3].
   - WHEN input is empty or whitespace-only, THE SYSTEM SHALL return [].
   - IF a token is not an integer, THEN THE SYSTEM SHALL raise ValueError naming the token.
6. Verification: `pytest tests/test_utils.py -q` all pass. Human gate: no (dev-only change).
```

## In Slack (what the bot accepts today)

Minimal form — objective+context in the goal, EARS criteria after `DoD:`:

```
@bot parse comma-separated ints in utils.py (mirror parse_float_list)   DoD: WHEN "1,2,3" SHALL return [1,2,3]; WHEN empty SHALL return []; IF bad token THEN raise ValueError naming it
```

Optionally pin the gate yourself with a `Tests:` block (highest trust — the gate runs exactly
your tests; import from module `solution`):

```
@bot ...   DoD: ...   Tests: from solution import parse_int_list
def test_basic(): assert parse_int_list("1,2") == [1, 2]
```

Without `Tests:`, loopkit derives pytest tests from your EARS DoD **before** generation starts
and freezes them (the generator can never influence its own gate). If derivation fails, the gate
falls back to compile-only and warns you.

Constraints and repo conventions are pulled in automatically from `AGENTS.md` — you don't retype them.

## Deliver (tuỳ chọn — thường để AI điền)
`Deliver: <path/to/module.py>` — chỗ đặt file trong repo đích. Thiếu thì loopkit tự đề xuất
lúc freeze (đọc cây repo); path hiện ở door — approve là duyệt cả chỗ đặt.
