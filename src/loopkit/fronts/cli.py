"""loopkit CLI front — cwd = repo đích. Lệnh: run | idea | status.

Cùng một engine với front Slack; khác biệt duy nhất: door là prompt terminal và
workspace lấy từ cwd (git repo -> worktree per ticket; không phải git -> tmp dir).
"""
import argparse, subprocess, time

from loopkit import __version__, config, gates, refine, shield
from loopkit.engine import Ticket, run_loop, read_agents_md
from loopkit.memory import Memory
from loopkit.workspace import make_workspace


def _mask(s: str) -> str:
    return shield.mask(s) if config.ENABLE_SHIELD else s


def _mem():
    return Memory(config.MEMORY_DIR) if config.ENABLE_MEMORY else None


def terminal_door(artifact: str) -> bool:
    print("\n🚪 HUMAN DOOR — artifact chờ duyệt:\n")
    print(_mask((artifact or "")[:2500]))
    try:
        return input("\nApprove? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:                                 # non-interactive: fail-closed
        return False


def _cwd_repo() -> str:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def cmd_run(text: str, thread=None) -> int:
    repo_name, text = gates.parse_repo(text)
    if repo_name:
        print(f"⚠️ CLI bỏ qua 'Repo: {repo_name}' — cwd là repo đích.")
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
    if mem and mem.recall(goal, dod) is not None:
        verifier = gates.make_compile_gate(wd)       # unused: run_loop recall trước gate
    elif tests_src:
        verifier = gates.make_pytest_gate(tests_src, wd)
        print("🧪 gate = pytest (tests từ ticket)")
    else:
        derived = gates.derive_tests(goal, dod)      # fresh call TRƯỚC generation; frozen
        if derived:
            verifier = gates.make_pytest_gate(derived, wd)
            print(f"🧪 gate = pytest (derived, frozen):\n{_mask(derived[:1200])}")
        else:
            verifier = gates.make_compile_gate(wd)
            print("⚠️ Không derive được test — gate compile-only (YẾU).")
    ctx = "" if (repo and config.ENABLE_TOOLS) else read_agents_md(".")
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True)
    res = run_loop(t, human_door=terminal_door, notify=print, project_context=ctx,
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
    sub.add_parser("run", help="chạy một ticket đầy đủ").add_argument("ticket")
    sub.add_parser("idea", help="refinement Q&A từ ý tưởng thô").add_argument("idea")
    sub.add_parser("status", help="registry của repo hiện tại (cwd)")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args.ticket)
    if args.cmd == "idea":
        return cmd_idea(args.idea)
    return cmd_status()
