"""
loopkit.engine — a minimal, reusable loop framework in the top-tier production pattern.

Flow:  [memory recall] -> orchestrator ROUTES -> WORKER (generator) -> DETERMINISTIC GATE (first)
       -> REVIEWER (separate skeptical evaluator) -> feedback -> bounded stop -> HUMAN DOOR

Cross-cutting (flow-level, shared — built once, not per role):
  config  — knobs & feature flags (env-overridable), incl. the per-role model tiering knob
  shield  — masks secrets/PII at every notify/persistence boundary (ENABLE_SHIELD)
  memory  — registry + session + semantic cache of VERIFIED solutions (ENABLE_MEMORY);
            the journal APPENDS per run (never wipes) — agents forget, the disk doesn't.

Context assembly for a worker call = SOUL (roles.py) + AGENTS.md (project rules) + TICKET + feedback.
Seams for a real project: Ticket.verifier, human_door, the roles registry, AGENTS.md.
"""
import os, subprocess, re, json, pathlib, time, itertools
from dataclasses import dataclass
from typing import Callable, Optional
from loopkit import config, shield
from loopkit.memory import Memory
from loopkit.roles import REGISTRY, allowed_tools

# ---------- brain ----------
def ask_claude(prompt: str, soul: str, model: Optional[str] = None) -> str:
    if os.environ.get("LOOPKIT_NO_BRAIN"):     # gate context: cấm brain — chống loop lồng nhau
        return "LOOPKIT_NO_BRAIN: brain bị cấm trong gate context (test không hermetic?)"
    # Runs in a NEUTRAL cwd on purpose: the claude CLI auto-reads AGENTS.md/CLAUDE.md from its
    # cwd, which would DOUBLE-inject project context (we already inject it explicitly via
    # project_context). Neutral cwd keeps one source of truth and stays brain-agnostic.
    wd = pathlib.Path(config.BRAIN_CWD)
    wd.mkdir(parents=True, exist_ok=True)
    cmd = ["claude", "-p", f"{soul}\n\n{prompt}"]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=config.CLAUDE_TIMEOUT, cwd=str(wd))
    return (r.stdout or r.stderr).strip()

def run_agent(prompt: str, soul: str, *, workdir, tools, model: Optional[str] = None) -> str:
    """Tool-enabled agent session (headless Claude Code) acting INSIDE the ticket workspace.
    Unlike ask_claude (neutral cwd, text-only): cwd IS the workspace — the agent reads/writes
    files there, and when the workspace is a real repo's worktree, claude natively picks up
    THAT repo's AGENTS.md/CLAUDE.md (callers should pass project_context='' in that case)."""
    if os.environ.get("LOOPKIT_NO_BRAIN"):     # gate context: cấm brain — chống loop lồng nhau
        return "LOOPKIT_NO_BRAIN: brain bị cấm trong gate context (test không hermetic?)"
    cmd = ["claude", "-p", f"{soul}\n\n{prompt}", "--allowedTools", ",".join(tools)]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=config.AGENT_TIMEOUT, cwd=str(workdir))
    return (r.stdout or r.stderr).strip()


def extract_code(text: str) -> str:
    m = re.search(r"```[a-zA-Z0-9+_-]*\n?(.*?)```", text, re.DOTALL)   # any fenced block
    return (m.group(1) if m else text).strip()

def read_agents_md(directory: str = ".") -> str:
    """Standing project context (AGENTS.md) — repo-scoped, read by every agent."""
    p = pathlib.Path(directory) / "AGENTS.md"
    return p.read_text() if p.exists() else ""

# ---------- ticket ----------
@dataclass
class Ticket:
    goal: str
    dod: str                                    # Definition of Done (the loop's stop condition)
    verifier: Callable[[str], tuple]            # deterministic gate: artifact -> (passed, detail)
    risky: bool = False                         # True -> require human_door before "done"
    deliver: Optional[str] = None               # Deliver: path (chốt lúc freeze) — spec 2026-07-10
    repo: str = ""                              # repo đích (worktree gốc) cho delivery
    tests_src: str = ""                         # frozen tests (cho door payload re-materialize)
    gate_cmd: str = ""   # Gate: lệnh domain — truthy = edit-in-place mode (spec 2026-07-13)

def default_human_door(artifact: str) -> bool:
    print("\n🚪 HUMAN DOOR — approval required (wire a Slack [Approve] button here).")
    print("   Non-interactive default: 'awaiting approval' (not auto-approving a risky change).")
    return False

# ---------- orchestrator: route the ticket to one worker ----------
def route(ticket: "Ticket", roles: dict) -> str:
    # LLM routing is stochastic -> guard it with a deterministic keyword backstop.
    infra_kw = any(k in ticket.goal.lower() for k in
                   ("kubernetes", "k8s", "helm", "terraform", "manifest", "kubectl",
                    "deployment", "ingress", "namespace", "dockerfile"))
    reply = ask_claude(f"TICKET:\n{ticket.goal}", roles["orchestrator"].soul,
                       model=config.ROLE_MODELS.get("orchestrator")).lower()
    if "infra" in reply:
        return "infra"
    if "code" in reply:
        return "infra" if infra_kw else "code"
    return "infra" if infra_kw else "code"

# ---------- engine ----------
_RUN_SEQ = itertools.count()

def _worktree_diff(ws) -> str:
    """Artifact edit-mode: intent-to-add để file MỚI hiện trong diff, không stage nội dung."""
    try:
        subprocess.run(["git", "-C", str(ws), "add", "-N", "."], capture_output=True, timeout=30)
        r = subprocess.run(["git", "-C", str(ws), "diff", "HEAD"],
                           capture_output=True, text=True, timeout=60)
        return r.stdout or ""
    except (subprocess.SubprocessError, OSError):    # fail-closed: no diff beats a crashed run
        return ""

def run_loop(ticket: Ticket, *, roles: dict = REGISTRY, max_turns: Optional[int] = None,
             human_door: Callable[[str], bool] = default_human_door,
             notify: Callable[[str], None] = print, project_context: str = "",
             journal_dir: Optional[str] = None, memory: Optional[Memory] = None,
             thread_id: str = "local", workspace: Optional[str] = None) -> dict:
    max_turns = max_turns or config.MAX_TURNS
    jp = pathlib.Path(journal_dir or config.JOURNAL_DIR) / "run_journal.jsonl"
    run_id = f"r{int(time.time() * 1000)}-{next(_RUN_SEQ)}"   # counter: unique even within 1ms
    mem = memory if memory is not None else (Memory(config.MEMORY_DIR) if config.ENABLE_MEMORY else None)
    guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)

    def emit(msg: str):                          # single notify boundary: mask -> notify
        notify(guard(msg))

    def record(entry: dict):                     # single persistence boundary: mask -> journal + session
        entry = {**entry, "run_id": run_id, "thread_id": thread_id, "ts": round(time.time(), 1)}
        entry = {k: (guard(v) if isinstance(v, str) else v) for k, v in entry.items()}
        with open(jp, "a") as f:                 # APPEND — never wipe (survives across runs)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if mem:
            mem.append_event(thread_id, entry)

    # --- semantic recall: an identical VERIFIED ticket skips generation entirely ---
    if mem and not ticket.gate_cmd:
        cached = mem.recall(ticket.goal, ticket.dod)
        if cached is not None:
            emit("♻️ recalled a previously verified solution for this exact ticket")
            approved = human_door(cached) if ticket.risky else True   # four-eyes per application
            record({"stage": "recall", "approved": approved})
            mem.register(thread_id, status="done_cached", approved=approved,
                         artifact=cached[:4000])
            return {"ok": True, "cached": True, "worker": None, "turns": 0,
                    "approved": approved, "artifact": cached}

    ws = pathlib.Path(workspace) if workspace else None
    tool_mode = bool(config.ENABLE_TOOLS and ws)
    edit_mode = bool(ticket.gate_cmd)
    if edit_mode and not tool_mode:
        reason = "edit-mode cần LOOPKIT_ENABLE_TOOLS=1 + workspace"
        if mem:
            mem.register(thread_id, status="refused")     # terminal — reaper không đụng
        record({"stage": "refused", "reason": reason})
        return {"ok": False, "worker": None, "turns": 0, "reason": reason}

    worker = route(ticket, roles)                                     # ORCHESTRATOR
    if mem:
        mem.register(thread_id, status="running", worker=worker, goal=guard(ticket.goal[:200]))
    gen_soul, eval_soul = roles[worker].soul, roles["reviewer"].soul
    emit(f"🧩 routed → {worker} agent")
    ctx = f"PROJECT CONTEXT (AGENTS.md):\n{project_context}\n\n" if project_context else ""
    feedback = "no attempt yet"
    for turn in range(1, max_turns + 1):
        gen_prompt = (f"{ctx}GOAL:\n{ticket.goal}\n\nDEFINITION OF DONE:\n{ticket.dod}\n\n"
                      f"FEEDBACK on last attempt:\n{feedback}")
        agent_reply = ""
        if tool_mode:                                                           # GENERATOR (acts)
            act = ("\n\nACT: sửa các file trong repo (worktree hiện tại) để đạt GOAL và "
                   "DEFINITION OF DONE. KHÔNG tạo solution.py. Trả lời MỘT dòng tóm tắt."
                   if edit_mode else
                   "\n\nACT: write the complete solution to the file `solution.py` in the "
                   "current directory (overwrite it). Reply with a one-line summary only.")
            agent_reply = run_agent(gen_prompt + act, gen_soul, workdir=ws,
                                    tools=allowed_tools(roles[worker]),
                                    model=config.ROLE_MODELS.get(worker))
            if edit_mode:
                artifact = _worktree_diff(ws)             # diff rỗng -> fail-closed ở dưới
            else:
                sol = ws / "solution.py"
                artifact = sol.read_text() if sol.exists() else ""
        else:                                                                   # GENERATOR (text)
            artifact = extract_code(ask_claude(gen_prompt, gen_soul,
                                               model=config.ROLE_MODELS.get(worker)))
        if edit_mode and not artifact.strip():            # cmd-gate bỏ qua artifact -> phải chặn ở đây
            gate_pass, gate_detail = False, "empty diff — generator không sửa file nào"
        else:
            gate_pass, gate_detail = ticket.verifier(artifact)                  # GATE (first)
        entry = {"turn": turn, "worker": worker, "gate_pass": gate_pass,
                 "gate": (gate_detail.splitlines()[-1][:120] if gate_detail else "")}
        if not gate_pass:
            feedback = f"Deterministic gate FAILED:\n{gate_detail}"
            entry["stage"] = "gate_fail"
            hint = ""
            if tool_mode and not artifact:       # observability (live gap): surface WHY the
                tail = agent_reply[-200:] if agent_reply else "EMPTY — claude produced no output"
                entry["agent_reply_tail"] = tail  # agent session left no file
                hint = f"\n↳ tool session said: {tail}"
            record(entry)
            emit(f"🚦 turn {turn}: gate=FAIL — {entry['gate']}{hint}")
            continue
        if tool_mode and not edit_mode:                                         # REVIEWER (acts)
            eval_prompt = (f"{ctx}GOAL:\n{ticket.goal}\n\nDEFINITION OF DONE:\n{ticket.dod}\n\n"
                           f"Deterministic gate PASSED: {entry['gate']}\n\n"
                           "The artifact is `./solution.py` (ticket tests, if any, are "
                           "`./test_ticket.py`). ACT: run the tests yourself and inspect the "
                           "code, then judge the DoD items the gate does NOT cover.")
            reply = run_agent(eval_prompt, eval_soul, workdir=ws,
                              tools=allowed_tools(roles["reviewer"]),
                              model=config.ROLE_MODELS.get("reviewer"))
        else:                                                                   # REVIEWER (text)
            eval_prompt = (f"{ctx}GOAL:\n{ticket.goal}\n\nDEFINITION OF DONE:\n{ticket.dod}\n\n"
                           f"Deterministic gate PASSED: {entry['gate']}\n\n"
                           + (f"ARTIFACT UNDER REVIEW là git diff của thay đổi:\n```\n{artifact}\n```\n"
                              if edit_mode else
                              f"ARTIFACT UNDER REVIEW:\n```\n{artifact}\n```\n")
                           + "Judge the DoD items the gate does NOT cover.")
            reply = ask_claude(eval_prompt, eval_soul, model=config.ROLE_MODELS.get("reviewer"))
        # Live finding: reviewers sometimes bury the verdict under reasoning. Accept the first
        # line starting with VERDICT: anywhere in the reply; absent -> fail-closed (REJECT).
        verdict = next((l.strip() for l in reply.splitlines()
                        if l.strip().upper().startswith("VERDICT:")), "")
        eval_pass = verdict.upper().startswith("VERDICT: PASS")
        entry.update({"stage": "evaluated", "eval_pass": eval_pass,
                      "verdict": verdict or (reply.splitlines()[0][:120] if reply else "")})
        record(entry)
        emit(f"🔎 turn {turn}: gate=PASS · reviewer={entry['verdict']}")
        if eval_pass:                                                           # STOP (DoD met)
            approved = human_door(artifact) if ticket.risky else True           # HUMAN DOOR
            if mem:
                mem.register(thread_id, status="done", turns=turn, approved=approved,
                             artifact=artifact[:4000])             # revision base for follow-ups
                if ((not ticket.risky) or approved) and not ticket.gate_cmd:
                    mem.store(ticket.goal, ticket.dod, artifact)   # cache only VERIFIED(+approved);
                    # never an edit-mode diff into the semantic cache (locked decision #4)
            record({"stage": "done", "turn": turn, "approved": approved})
            if (approved and ticket.risky and ticket.gate_cmd and ticket.repo
                    and ws and config.DELIVER):
                from loopkit import deliver as _deliver
                _deliver.ship_diff(str(ws), ticket.repo, ticket.gate_cmd,
                                   ticket.goal, ticket.dod, emit=emit, record=record)
            elif (approved and ticket.risky and ticket.deliver and ticket.repo
                    and ws and config.DELIVER):
                from loopkit import deliver as _deliver       # lazy: deliver imports engine
                _deliver.ship(str(ws), ticket.repo, ticket.deliver,
                              ticket.goal, ticket.dod, emit=emit, record=record)
            return {"ok": True, "cached": False, "worker": worker, "turns": turn,
                    "approved": approved, "artifact": artifact}
        feedback = f"Reviewer REJECTED:\n{reply}"                               # FEEDBACK -> loop
    if mem:
        mem.register(thread_id, status="exhausted", turns=max_turns)
    record({"stage": "exhausted", "turns": max_turns})
    return {"ok": False, "worker": worker, "turns": max_turns,
            "reason": "budget exhausted -> escalate"}


def finish_suspended(mem, thread_id: str, payload: dict, decision: bool,
                     notify: Callable[[str], None]) -> None:
    """§8.1 resume path: complete a run whose process died while suspended at the human
    door. Mirrors run_loop's post-door tail (register done -> cache only if approved ->
    deliver) from the persisted door payload. `turns` is unknown here and stays absent.
    Edit-mode (payload["mode"] == "edit") branches to ship_diff instead of ship — it ships
    the already-applied worktree diff rather than re-materializing an artifact file."""
    guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
    artifact = payload.get("artifact", "")
    mem.register(thread_id, status="done", approved=decision, artifact=artifact[:4000])
    if decision:
        if payload.get("mode") != "edit":     # edit-mode diffs never enter the cache (#4)
            mem.store(payload.get("goal", ""), payload.get("dod", ""), artifact)
        notify("✅ approved (resumed sau restart)")
        notify(f"📦 artifact:\n```\n{guard(artifact[:2500])}\n```")
        if payload.get("mode") == "edit" and payload.get("gate_cmd") and config.DELIVER:
            ws = payload.get("workspace", "")
            if not (ws and pathlib.Path(ws).exists()):
                notify("🚫 không ship được: worktree đã mất (edit-mode không re-materialize "
                       "từ diff) — chạy lại ticket.")
                return
            from loopkit import deliver as _deliver
            _deliver.ship_diff(ws, payload.get("repo", ""), payload["gate_cmd"],
                               payload.get("goal", ""), payload.get("dod", ""),
                               emit=lambda m: notify(guard(m)),
                               record=lambda e: mem.append_event(thread_id, {
                                   k: (guard(v) if isinstance(v, str) else v)
                                   for k, v in e.items()}))
            return
        if payload.get("deliver") and config.DELIVER:
            from loopkit import deliver as _deliver           # lazy: tránh vòng import
            ws = _deliver.ensure_workspace(thread_id, payload.get("repo", ""), artifact,
                                           tests_src=payload.get("tests", ""),
                                           workspace=payload.get("workspace", ""))
            _deliver.ship(ws, payload.get("repo", ""), payload["deliver"],
                          payload.get("goal", ""), payload.get("dod", ""),
                          emit=lambda m: notify(guard(m)),
                          record=lambda e: mem.append_event(thread_id, {
                              k: (guard(v) if isinstance(v, str) else v)
                              for k, v in e.items()}))
    else:
        notify("🚫 rejected (resumed sau restart) — không áp dụng artifact")
