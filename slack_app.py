"""
slack_app.py — Slack front door for loopkit (Socket Mode).

Run:
  ./run.sh          (loads SLACK_BOT_TOKEN / SLACK_APP_TOKEN from ~/.loopkit.env)

Ticket syntax in a channel the bot is invited to:
  @yourbot <objective + context>   DoD: <EARS acceptance criteria>   [Tests: <pytest code>]

Follow-ups (P3): a threaded REPLY (no mention needed) containing "DoD:" in a thread the bot
owns starts a new run there, seeded with the previous artifact as revision base. Requires
Slack app scopes channels:history (+groups:history) and event subscriptions message.channels
(+message.groups), then Reinstall. Replies without DoD are ignored silently.

Workspaces (P3): per-thread dir under /tmp/loopkit_runs, or a git WORKTREE per thread when
LOOPKIT_TARGET_REPO points at a repo. Tool-mode (LOOPKIT_ENABLE_TOOLS=1): agents act inside
the workspace with role-scoped tools; in repo mode claude reads THAT repo's AGENTS.md natively,
so loopkit's own context is not injected (single source of context).

Human door blocks on threading.Event; doors also persist to disk (§8.1), so a click arriving
after a restart resumes and completes the run via engine.finish_suspended.
"""
import os, pathlib, re, threading
from slack_bolt import App
try:                                             # websocket-client transport: much stabler than the
    from slack_bolt.adapter.socket_mode.websocket_client import SocketModeHandler  # builtin on flaky
    _ADAPTER = "websocket_client"                # nets/VPN (fixes repeated BrokenPipe on sock check)
except ImportError:                              # pip install websocket-client  to enable
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    _ADAPTER = "builtin"
import config, gates, shield
from engine import Ticket, run_loop, read_agents_md, finish_suspended
from memory import Memory
from workspace import make_workspace

BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
if not (BOT_TOKEN and APP_TOKEN):
    raise SystemExit("Set SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in your environment.")

HERE = pathlib.Path(__file__).parent
WORKDIR = pathlib.Path("/tmp/loopkit_runs"); WORKDIR.mkdir(exist_ok=True)
PROJECT_CTX = read_agents_md(str(HERE))
# Repo+tool mode: the agent reads the TARGET repo's AGENTS.md natively from its worktree cwd;
# injecting loopkit's own rules there would be the wrong repo's context.
EFFECTIVE_CTX = "" if (config.TARGET_REPO and config.ENABLE_TOOLS) else PROJECT_CTX
MEM = Memory(config.MEMORY_DIR) if config.ENABLE_MEMORY else None
_guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
app = App(token=BOT_TOKEN)
_pending = {}   # thread_ts -> {"event": threading.Event, "approved": bool}

# ---- Slack human door: post artifact + Approve/Reject, block until a human clicks ----
def make_door(thread_ts, client, channel, goal, dod):
    def door(artifact: str) -> bool:
        ev = threading.Event(); _pending[thread_ts] = {"event": ev, "approved": False}
        if MEM:                                  # §8.1: persist so a restart can resume it
            MEM.door_open(thread_ts, {"channel": channel, "artifact": artifact,
                                      "goal": goal, "dod": dod})
            MEM.register(thread_ts, status="awaiting_approval")
        preview = _guard((artifact or "")[:1500])    # never ask a blind approval
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
            text="Reviewer PASS — approve this change?",
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                     "text": f"Reviewer PASS — artifact chờ duyệt:\n```{preview}```"}},
                    {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "approve",
                 "text": {"type": "plain_text", "text": "Approve"}, "value": thread_ts},
                {"type": "button", "style": "danger", "action_id": "reject",
                 "text": {"type": "plain_text", "text": "Reject"}, "value": thread_ts}]}])
        ev.wait(timeout=3600)                       # in-process wait; disk is the recovery path
        if MEM:
            MEM.door_close(thread_ts)
        return _pending.pop(thread_ts, {"approved": False})["approved"]
    return door

# ---- shared ticket launcher (used by mention intake AND thread follow-ups) ----
def launch_ticket(client, channel, thread, text, prev_artifact=None) -> bool:
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        return False
    client.chat_postMessage(channel=channel, thread_ts=thread,
        text=_guard(f"🧩 Nhận ticket.\n*Goal:* {goal}\n*DoD:* {dod}"))
    if tests_src is None and re.search(r"(?i)\btests:", text):
        client.chat_postMessage(channel=channel, thread_ts=thread,
            text="ℹ️ Có chữ `Tests:` nhưng không nhận diện được test hợp lệ "
                 "(cần `from solution import ...` + `def test_*`) — gate sẽ derive từ DoD.")
    if prev_artifact:                                # follow-up: revise on top of the last artifact
        goal = f"{goal}\n\nPREVIOUS ARTIFACT (revision base):\n```\n{prev_artifact[:3000]}\n```"

    def notify(msg):                                 # engine already masks everything it emits
        client.chat_postMessage(channel=channel, thread_ts=thread, text=msg)

    def work():
        try:
            wd, kind = make_workspace(str(thread))   # isolated dir, or git worktree in repo mode
            if kind == "worktree":
                notify(f"🌿 workspace = git worktree `{wd}` (branch loop/…)")
            # Live finding: check recall BEFORE deriving — a recalled ticket was burning an
            # LLM call on tests that would never run (and posting a misleading 🧪 message).
            if MEM and MEM.recall(goal, dod) is not None:
                verifier = gates.make_compile_gate(wd)   # unused: run_loop recalls before gating
            elif tests_src:
                verifier = gates.make_pytest_gate(tests_src, wd)
                notify("🧪 gate = pytest (tests from the ticket)")
            else:
                derived = gates.derive_tests(goal, dod)      # fresh call, BEFORE generation; frozen
                if derived:
                    verifier = gates.make_pytest_gate(derived, wd)
                    notify(_guard(f"🧪 gate = pytest (derived from DoD, frozen):\n```\n{derived[:1200]}\n```"))
                else:
                    verifier = gates.make_compile_gate(wd)
                    notify("⚠️ Không derive được test từ DoD — gate = compile-only (YẾU). "
                           "Cân nhắc gửi lại kèm `Tests:`.")
            t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True)
            res = run_loop(t, human_door=make_door(thread, client, channel, goal, dod),
                           notify=notify, project_context=EFFECTIVE_CTX,
                           journal_dir=str(WORKDIR), memory=MEM, thread_id=str(thread),
                           workspace=wd)
            if res.get("ok"):
                approved = res.get("approved")
                if res.get("cached"):
                    status = ("♻️ recalled (đã verify trước đó)" if approved
                              else "🚫 recalled nhưng bạn đã TỪ CHỐI áp dụng")
                else:
                    status = "✅ approved" if approved else "⏸️ done — chưa/không được duyệt"
                notify(f"{status}  (worker={res.get('worker')}, turns={res['turns']})")
                if approved or not res.get("cached"):        # never deliver a rejected recall
                    art = _guard((res.get("artifact") or "")[:2500])
                    notify(f"📦 artifact:\n```\n{art}\n```")
            else:
                notify(f"❌ {res.get('reason')} (worker={res.get('worker')})")
        except Exception as e:
            notify(_guard(f"💥 error: {e}"))

    threading.Thread(target=work, daemon=True).start()
    return True

@app.event("app_mention")                           # INTAKE
def on_mention(event, client, body):
    if event.get("bot_id"):
        return
    if shield.seen_event(body.get("event_id", "")):  # Slack retries -> process each event once
        return
    thread = event.get("thread_ts", event["ts"])
    if not launch_ticket(client, event["channel"], thread, event.get("text", "")):
        client.chat_postMessage(channel=event["channel"], thread_ts=thread,
            text="🙅 Thiếu Definition of Done. Cú pháp:\n"
                 "`@bot <objective+context>   DoD: <EARS criteria>   [Tests: <pytest code>]`")

@app.event("message")                               # THREAD FOLLOW-UPS (P3)
def on_followup(event, client, body):
    if event.get("bot_id") or event.get("subtype"):
        return
    if "<@" in event.get("text", ""):               # mentions are owned by app_mention (no double-fire)
        return
    thread = event.get("thread_ts")
    if not thread or MEM is None:
        return
    run = MEM.get_run(str(thread))
    if not run:                                     # only threads loopkit owns
        return
    if not re.search(r"(?i)\bdod:", event.get("text", "")):
        return                                      # silent: ordinary chatter in the thread
    if shield.seen_event(body.get("event_id", "")):
        return
    launch_ticket(client, event["channel"], thread, event.get("text", ""),
                  prev_artifact=run.get("artifact"))

@app.action("approve")
def _approve(ack, body): ack(); _resolve(body, True)
@app.action("reject")
def _reject(ack, body): ack(); _resolve(body, False)
def _resolve(body, decision):
    ts = body["actions"][0]["value"]
    user = body.get("user", {}).get("id", "?")
    if ts in _pending:                               # live click: run_loop thread finishes it
        if MEM:                                      # four-eyes audit trail on disk (who + what)
            MEM.audit(str(ts), approver=user, decision=decision)
        _pending[ts]["approved"] = decision
        _pending[ts]["event"].set()
        return
    door = MEM.door_get(str(ts)) if MEM else None
    if door:                                         # §8.1 resume: process died at this door
        MEM.audit(str(ts), approver=user, decision=decision)
        finish_suspended(MEM, str(ts), door, decision,
                         lambda msg: app.client.chat_postMessage(
                             channel=door["channel"], thread_ts=ts, text=msg))
        MEM.door_close(str(ts))
    elif MEM:                                        # truly stale click: evidence, no overwrite
        MEM.append_event(str(ts), {"stage": "human_door_stale", "approver": user,
                                   "approved": decision})

if __name__ == "__main__":
    if MEM:
        dead = MEM.reap_running()                    # a 'running' entry at boot is a dead run
        if dead:
            print(f"[loopkit] reaped {len(dead)} interrupted run(s): {', '.join(dead)}")
    shield.init_dedupe(pathlib.Path(config.MEMORY_DIR) / "events.seen")
    mode = f"transport={_ADAPTER}, tools={'ON' if config.ENABLE_TOOLS else 'off'}"
    if config.TARGET_REPO:
        mode += f", repo={config.TARGET_REPO}"
    print(f"loopkit Slack bot starting (Socket Mode, {mode})…")
    SocketModeHandler(app, APP_TOKEN).start()
