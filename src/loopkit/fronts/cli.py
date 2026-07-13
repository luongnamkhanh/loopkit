"""loopkit CLI front — cwd = repo đích. Lệnh: run | idea | status.

Cùng một engine với front Slack; khác biệt duy nhất: door là prompt terminal và
workspace lấy từ cwd (git repo -> worktree per ticket; không phải git -> tmp dir).
"""
import argparse, subprocess, time

from loopkit import __version__, config, deliver, gates, refine, shield
from loopkit.engine import Ticket, run_loop, read_agents_md, finish_suspended
from loopkit.memory import Memory
from loopkit.workspace import make_workspace


def _mask(s: str) -> str:
    return shield.mask(s) if config.ENABLE_SHIELD else s


def _mem():
    return Memory(config.MEMORY_DIR) if config.ENABLE_MEMORY else None


def terminal_door(artifact: str, deliver: str = None, gate: str = None) -> bool:
    print("\n🚪 HUMAN DOOR — artifact chờ duyệt:\n")
    print(_mask((artifact or "")[:2500]))
    if gate:
        print(f"\n🛡 Gate: {gate}")
    if deliver:
        print(f"\n📦 Deliver: {deliver}")        # duyệt = duyệt cả chỗ đặt
    try:
        return input("\nApprove? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:                                 # non-interactive: fail-closed
        return False


def _cwd_repo() -> str:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _build_verifier(mem, goal, dod, tests_src, wd):
    """-> (verifier, frozen_tests_src) — tests để door payload re-materialize được."""
    if mem and mem.recall(goal, dod) is not None:
        return gates.make_compile_gate(wd), ""       # unused: run_loop recall trước gate
    if tests_src:
        print("🧪 gate = pytest (tests từ ticket)")
        return gates.make_pytest_gate(tests_src, wd), tests_src
    derived = gates.derive_tests(goal, dod)          # fresh call TRƯỚC generation; frozen
    if derived:
        print(f"🧪 gate = pytest (derived, frozen):\n{_mask(derived[:1200])}")
        return gates.make_pytest_gate(derived, wd), derived
    print("⚠️ Không derive được test — gate compile-only (YẾU).")
    return gates.make_compile_gate(wd), ""


def cmd_run(text: str, thread=None) -> int:
    repo_name, text = gates.parse_repo(text)
    if repo_name:
        print(f"⚠️ CLI bỏ qua 'Repo: {repo_name}' — cwd là repo đích.")
    deliver_path, text = gates.parse_deliver(text)
    gate_cmd, text = gates.parse_gate_cmd(text)
    if gate_cmd and deliver_path:
        print("⚠️ Gate: là edit-mode — bỏ qua Deliver:")
        deliver_path = None
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        print("🙅 Thiếu DoD. Cú pháp: loopkit run '<goal> DoD: <EARS> [Tests: <pytest>]'")
        return 1
    thread = thread or f"cli-{int(time.time() * 1000)}"
    mem = _mem()
    repo = _cwd_repo()
    wd, kind = make_workspace(thread, repo=repo)
    if kind == "worktree":
        print(f"🌿 workspace = worktree {wd}")
    if gate_cmd:
        if not (repo and config.ENABLE_TOOLS):
            print("🙅 Gate: cần repo (cwd git) + LOOPKIT_ENABLE_TOOLS=1.")
            return 1
        verifier, frozen_tests = gates.make_cmd_gate(gate_cmd, wd), ""
        pre_ok, _ = verifier("")
        gate_label = ("⚠️ gate XANH trước khi sửa — chỉ chống vỡ, không chứng minh DoD"
                      if pre_ok else "🔴 acceptance gate (đỏ trước khi sửa)")
        print(f"🛡 Gate: {gate_cmd}")
        print(gate_label)
        deliver_path = None                              # edit-mode: bỏ freeze_deliver hoàn toàn
    else:
        verifier, frozen_tests = _build_verifier(mem, goal, dod, tests_src, wd)
        recalled = bool(mem and mem.recall(goal, dod) is not None)
        deliver_path = None if recalled else deliver.freeze_deliver(deliver_path, goal, repo)     # chốt TRƯỚC generation
    ctx = "" if (repo and config.ENABLE_TOOLS) else read_agents_md(".")
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True,
               deliver=deliver_path, repo=repo, tests_src=frozen_tests,
               gate_cmd=gate_cmd or "")
    res = run_loop(t, human_door=lambda a: terminal_door(a, deliver=deliver_path, gate=gate_cmd),
                   notify=print, project_context=ctx,
                   memory=mem, thread_id=str(thread), workspace=wd)
    if res.get("ok"):
        status = "✅ approved" if res.get("approved") else "⏸️ done — chưa duyệt"
        print(f"{status} (worker={res.get('worker')}, turns={res['turns']})")
        return 0
    print(f"❌ {res.get('reason')}")
    return 1


def cmd_idea(idea: str) -> int:
    mem = _mem()
    thread = f"cli-{int(time.time() * 1000)}"
    if mem:
        mem.register(thread, status="refining", idea=_mask(idea[:500]), refine_turns=0)
    history, turns = [], 0
    while True:
        kind, text = refine.refine_turn(idea, history, turns, config.REFINE_MAX_TURNS)
        if kind == "error":
            print("💥 refinement lỗi — chạy lại lệnh.")
            return 1
        if kind == "ask":
            turns += 1
            history.append({"role": "analyst", "text": text})
            print(f"\n❓ ({turns}/{config.REFINE_MAX_TURNS}) {_mask(text)}")
            try:
                answer = input("> ").strip()
            except EOFError:
                return 130
            history.append({"role": "user", "text": answer})
            if mem:
                mem.append_event(thread, {"stage": "refine", "role": "analyst",
                                          "text": _mask(text)})
                mem.append_event(thread, {"stage": "refine", "role": "user",
                                          "text": _mask(answer)})
                mem.register(thread, refine_turns=turns)
            continue
        warn = " (⚠️ Tests chưa hợp lệ — sẽ derive từ DoD)" if kind == "draft_unvalidated" else ""
        print(f"\n🎫 Ticket draft{warn}:\n{_mask(text[:2500])}")
        try:
            choice = input("[y] run / [e] góp ý / [n] huỷ > ").strip().lower()
        except EOFError:
            choice = "n"
        if choice == "y":
            if mem:
                mem.register(thread, status="ticket_approved", draft=text)
            return cmd_run(text, thread=thread)
        if choice == "e":
            try:
                fb = input("góp ý > ").strip()
            except EOFError:
                return 130
            history.append({"role": "user", "text": fb})
            continue
        if mem:
            mem.register(thread, status="refine_cancelled")
        print("🚫 Đã huỷ.")
        return 130


# ---- agent-mode verbs (Claude-session front): mỗi lệnh một bước, state trên disk ----
def _agent_refine_step(mem, thread) -> int:
    run = mem.get_run(thread)
    history = [{"role": e["role"], "text": e["text"]}
               for e in mem.events(thread) if e.get("stage") == "refine"]
    turns = run.get("refine_turns", 0)
    kind, text = refine.refine_turn(run.get("idea", ""), history, turns,
                                    config.REFINE_MAX_TURNS)
    if kind == "error":
        print("FAILED: refinement error — thử lại lệnh")
        return 1
    if kind == "ask":
        mem.append_event(thread, {"stage": "refine", "role": "analyst", "text": _mask(text)})
        mem.register(thread, refine_turns=turns + 1)
        print(f"QUESTION: {_mask(text)}")
        return 0
    mem.register(thread, status="ticket_drafted", draft=text)
    if kind == "draft_unvalidated":
        print("DRAFT_UNVALIDATED: tests trong draft không hợp lệ — run sẽ derive từ DoD")
    print("DRAFT:")
    print(_mask(text))
    print("DRAFT_END")
    return 0


def cmd_idea_start(idea: str) -> int:
    mem = _mem()
    if mem is None:
        print("FAILED: cần LOOPKIT_ENABLE_MEMORY=1")
        return 1
    thread = f"cli-{int(time.time() * 1000)}"
    mem.register(thread, status="refining", idea=_mask(idea[:500]), refine_turns=0)
    print(f"THREAD: {thread}")
    return _agent_refine_step(mem, thread)


def cmd_idea_answer(thread: str, answer: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    if run.get("status") not in ("refining", "ticket_drafted"):
        print(f"STALE: thread không ở refinement (status={run.get('status')})")
        return 1
    mem.append_event(thread, {"stage": "refine", "role": "user", "text": _mask(answer)})
    if run.get("status") == "ticket_drafted":                   # góp ý trên draft -> redraft
        mem.register(thread, status="refining")
    return _agent_refine_step(mem, thread)


def make_suspend_door(mem, thread, goal, dod, deliver="", repo="", ws="", tests="",
                      gate_cmd="", mode="module", gate_label=""):
    """Door không chặn cho agent-mode: persist rồi trả False — approve là lệnh riêng."""
    def door(artifact: str) -> bool:
        mem.door_open(thread, {"channel": "cli", "artifact": artifact,
                               "goal": goal, "dod": dod, "deliver": deliver,
                               "repo": repo, "workspace": ws, "tests": tests,
                               "gate_cmd": gate_cmd, "mode": mode, "gate_label": gate_label})
        return False
    return door


def cmd_ticket_run(thread: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    draft = run.get("draft")
    if run.get("status") != "ticket_drafted" or not draft:
        print(f"STALE: thread chưa có draft (status={run.get('status')})")
        return 1
    repo_name, text = gates.parse_repo(draft)
    deliver_path, text = gates.parse_deliver(text)
    gate_cmd, text = gates.parse_gate_cmd(text)
    if gate_cmd and deliver_path:
        print("⚠️ Gate: là edit-mode — bỏ qua Deliver:")
        deliver_path = None
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        print("FAILED: draft không parse được DoD")
        return 1
    mem.register(thread, status="ticket_approved")
    repo = _cwd_repo()
    wd, kind = make_workspace(thread, repo=repo)
    if kind == "worktree":
        print(f"🌿 workspace = worktree {wd}")
    gate_label = ""
    if gate_cmd:
        if not (repo and config.ENABLE_TOOLS):
            print("FAILED: Gate: cần repo (cwd git) + LOOPKIT_ENABLE_TOOLS=1")
            return 1
        verifier, frozen_tests = gates.make_cmd_gate(gate_cmd, wd), ""
        pre_ok, _ = verifier("")
        gate_label = ("⚠️ gate XANH trước khi sửa — chỉ chống vỡ, không chứng minh DoD"
                      if pre_ok else "🔴 acceptance gate (đỏ trước khi sửa)")
        print(f"🛡 Gate: {gate_cmd}")
        print(gate_label)
        deliver_path = None                              # edit-mode: bỏ freeze_deliver hoàn toàn
    else:
        verifier, frozen_tests = _build_verifier(mem, goal, dod, tests_src, wd)
        recalled = bool(mem and mem.recall(goal, dod) is not None)
        deliver_path = None if recalled else deliver.freeze_deliver(deliver_path, goal, repo)     # chốt TRƯỚC generation
    ctx = "" if (repo and config.ENABLE_TOOLS) else read_agents_md(".")
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True,
               deliver=deliver_path, repo=repo, tests_src=frozen_tests,
               gate_cmd=gate_cmd or "")
    res = run_loop(t, human_door=make_suspend_door(mem, thread, goal, dod,
                                                   deliver=deliver_path or "", repo=repo,
                                                   ws=wd, tests=frozen_tests,
                                                   gate_cmd=gate_cmd or "",
                                                   mode="edit" if gate_cmd else "module",
                                                   gate_label=gate_label),
                   notify=print, project_context=ctx, memory=mem,
                   thread_id=str(thread), workspace=wd)
    if res.get("ok") and mem.door_get(thread):
        print("AWAITING_APPROVAL")
        print("ARTIFACT:")
        print(_mask((res.get("artifact") or "")[:2500]))
        print("ARTIFACT_END")
        if deliver_path:
            print(f"DELIVER: {deliver_path}")
        if gate_cmd:
            print(f"GATE: {gate_cmd}")
        return 0
    if res.get("ok"):                                           # phòng hờ: ok mà không door
        print("DONE")
        return 0
    print(f"FAILED: {res.get('reason')}")
    return 1


def cmd_resolve(thread: str, decision: bool) -> int:
    mem = _mem()
    door = mem.door_get(thread) if mem else None
    if not door:
        print("STALE: không có door đang mở cho thread này")
        return 1
    mem.audit(thread, approver="cli-human", decision=decision)  # người đã gõ duyệt ở session
    finish_suspended(mem, thread, door, decision, print)
    mem.door_close(thread)
    print("APPROVED" if decision else "REJECTED")
    return 0


def cmd_show(thread: str) -> int:
    mem = _mem()
    run = mem.get_run(thread) if mem else {}
    if not run:
        print("STALE: không có run cho thread này")
        return 1
    door = mem.door_get(thread)
    print(f"STATUS: {'awaiting_approval' if door else run.get('status', '?')}")
    if door:
        print("ARTIFACT:")
        print(_mask((door.get("artifact") or "")[:2500]))
        print("ARTIFACT_END")
    elif run.get("draft"):
        print("DRAFT:")
        print(_mask(run["draft"][:2500]))
        print("DRAFT_END")
    return 0


def cmd_status() -> int:
    reg = Memory(config.MEMORY_DIR).runs()
    if not reg:
        print("(chưa có run nào trong repo này)")
        return 0
    for t, r in sorted(reg.items(), key=lambda kv: kv[1].get("updated_at", 0), reverse=True):
        goal = (r.get("goal") or r.get("idea") or "")[:48]
        print(f"{t:<24} {r.get('status', '?'):<18} {goal}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="loopkit",
                                 description="loop framework — gated, reviewed agent runs")
    ap.add_argument("--version", action="version", version=f"loopkit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="chạy một ticket đầy đủ (door tương tác)").add_argument("ticket")
    p_idea = sub.add_parser("idea", help="refinement: '<ý tưởng>' (tương tác) | start/answer (agent)")
    p_idea.add_argument("args", nargs="+")
    p_ticket = sub.add_parser("ticket", help="agent-mode: ticket run <thread>")
    p_ticket.add_argument("args", nargs=2)                      # ("run", thread)
    for name in ("approve", "reject", "show"):
        sub.add_parser(name).add_argument("thread")
    sub.add_parser("status", help="registry của repo hiện tại (cwd)")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.ticket)
    if args.cmd == "idea":
        a = args.args
        if a[0] == "start" and len(a) == 2:
            return cmd_idea_start(a[1])
        if a[0] == "answer" and len(a) == 3:
            return cmd_idea_answer(a[1], a[2])
        if len(a) == 1:
            return cmd_idea(a[0])                               # tương tác như cũ
        print("FAILED: dùng: idea '<ý tưởng>' | idea start '<ý tưởng>' | idea answer <thread> '<trả lời>'")
        return 1
    if args.cmd == "ticket":
        if args.args[0] == "run":
            return cmd_ticket_run(args.args[1])
        print("FAILED: dùng: ticket run <thread>")
        return 1
    if args.cmd == "approve":
        return cmd_resolve(args.thread, True)
    if args.cmd == "reject":
        return cmd_resolve(args.thread, False)
    if args.cmd == "show":
        return cmd_show(args.thread)
    return cmd_status()
