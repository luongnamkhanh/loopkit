# Phase 1 Package + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** loopkit thành package chuẩn src-layout cài được mọi nơi (`uv tool install`/pipx), với CLI front `loopkit run|idea|status` (cwd = repo đích) và Slack thành front ngang hàng.

**Architecture:** Task 1 là restructure THUẦN CƠ HỌC (move file + đổi import, zero logic change) với gate = 61 test hiện có; Task 2 mới viết code mới (cli.py, TDD); Task 3 acceptance (install thật, chạy từ repo khác, Slack E2E). Spec: `docs/superpowers/specs/2026-07-09-package-cli-design.md`.

**Tech Stack:** Python ≥3.10, hatchling, stdlib-only core; extras `slack = slack-bolt, slack-sdk`.

## Global Constraints

- Baseline: **61 passed** (`python3 -m pytest tests -q`). Task 1 kết thúc phải đúng 61 — không thêm không bớt, không sửa logic.
- Core `dependencies = []` — engine thuần stdlib; Slack chỉ nằm trong extra `loopkit[slack]`.
- Import nội bộ: tuyệt đối `from loopkit import X` / `from loopkit.Y import Z` — không còn `import config` trần.
- Không print/commit token; không AI attribution trong git.
- CLI: cwd = repo đích; `Repo:` token bị strip + warning; exit 0 = ok, 1 = lỗi/exhausted, 130 = user huỷ.

---

### Task 1: Restructure src-layout (cơ học, gate = suite hiện có)

**Files:**
- Create: `pyproject.toml`, `src/loopkit/__init__.py`, `src/loopkit/fronts/__init__.py`
- Move (git mv): `config.py engine.py gates.py refine.py memory.py shield.py workspace.py roles.py` → `src/loopkit/`; `slack_app.py` → `src/loopkit/fronts/slack.py`
- Modify: imports trong 4 module + slack.py + 7 test file + `tests/conftest.py` + `run.sh` + `example_local.py` + `example_multiagent.py`

**Interfaces:**
- Produces: package `loopkit` importable từ `src/` (qua conftest path hoặc editable install); `loopkit.fronts.slack.main()` — Task 3 chạy; mọi API hiện có giữ nguyên tên dưới namespace `loopkit.*`.

- [ ] **Step 1: Move files.**

```bash
mkdir -p src/loopkit/fronts
git mv config.py engine.py gates.py refine.py memory.py shield.py workspace.py roles.py src/loopkit/
git mv slack_app.py src/loopkit/fronts/slack.py
```

- [ ] **Step 2: Package files.** `src/loopkit/__init__.py`:

```python
__version__ = "0.1.0"
```

`src/loopkit/fronts/__init__.py`: file rỗng. `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "loopkit"
version = "0.1.0"
description = "Loop framework: gated, reviewed, human-approved agent runs — CLI and Slack fronts"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
slack = ["slack-bolt>=1.18", "slack-sdk>=3.26"]

[project.scripts]
loopkit = "loopkit.fronts.cli:main"
loopkit-slack = "loopkit.fronts.slack:main"

# reserved cho P3 (plugin gate/role bên thứ ba, pattern pytest):
# [project.entry-points."loopkit.plugins"]

[tool.hatch.build.targets.wheel]
packages = ["src/loopkit"]
```

(Lưu ý: `loopkit.fronts.cli` chưa tồn tại đến Task 2 — chấp nhận, console script chỉ resolve lúc GỌI.)

- [ ] **Step 3: Đổi import — bảng chính xác từng file.** Trong `src/loopkit/`:

| File | Dòng cũ | Dòng mới |
|---|---|---|
| `engine.py` | `import config, shield` | `from loopkit import config, shield` |
| `engine.py` | `from memory import Memory` | `from loopkit.memory import Memory` |
| `engine.py` | `from roles import REGISTRY, allowed_tools` | `from loopkit.roles import REGISTRY, allowed_tools` |
| `gates.py` | `import config` | `from loopkit import config` |
| `gates.py` | `from engine import ask_claude, extract_code` | `from loopkit.engine import ask_claude, extract_code` |
| `refine.py` | `import config, gates, roles` | `from loopkit import config, gates, roles` |
| `refine.py` | `from engine import ask_claude` | `from loopkit.engine import ask_claude` |
| `workspace.py` | `import config` | `from loopkit import config` |
| `workspace.py` | `from memory import _safe` | `from loopkit.memory import _safe` |

(`config.py`, `memory.py`, `shield.py`, `roles.py`: stdlib-only, không đổi gì.)

`fronts/slack.py` — 4 việc:
1. Import guard + đổi import nội bộ:

```python
try:
    from slack_bolt import App
except ImportError as e:                             # core không kéo Slack deps
    raise SystemExit("Slack front cần extras: pip install 'loopkit[slack]'") from e
```

và: `import config, gates, refine, shield` → `from loopkit import config, gates, refine, shield`; `from engine import Ticket, run_loop, read_agents_md, finish_suspended` → `from loopkit.engine import ...`; `from memory import Memory` → `from loopkit.memory import Memory`; `from workspace import make_workspace` → `from loopkit.workspace import make_workspace`. (Adapter try/except cho `SocketModeHandler` giữ nguyên, nằm SAU guard trên.)

2. **AGENTS.md context**: `HERE = pathlib.Path(__file__).parent` giờ trỏ vào `src/loopkit/fronts/` — SAI. Thay 2 dòng:

```python
HERE = pathlib.Path(__file__).parent
PROJECT_CTX = read_agents_md(str(HERE))
```

bằng:

```python
PROJECT_CTX = read_agents_md(".")        # bot chạy với cwd = repo root (run.sh cd sẵn)
```

(`HERE` không còn ai dùng — xoá.)

3. Bọc `main()`: block `if __name__ == "__main__":` hiện tại đổi thành:

```python
def main() -> None:
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


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Tests + conftest + run.sh + examples.** `tests/conftest.py` thay toàn bộ bằng:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
```

Đổi import đầu các test file (giữ nguyên phần stdlib/pytest):

| File | Cũ | Mới |
|---|---|---|
| `test_config.py` | `import config` | `from loopkit import config` |
| `test_engine.py` | `import config, engine, roles` / `from engine import Ticket, run_loop` / `from memory import Memory` | `from loopkit import config, engine, roles` / `from loopkit.engine import Ticket, run_loop` / `from loopkit.memory import Memory` |
| `test_gates.py` | `import gates` | `from loopkit import gates` |
| `test_memory.py` | `import engine, roles` / `from engine import Ticket, run_loop` / `from memory import Memory` | `from loopkit import engine, roles` / `from loopkit.engine import Ticket, run_loop` / `from loopkit.memory import Memory` |
| `test_p3.py` | `import config, engine, roles, workspace` / `from engine import ...` / `from memory import ...` | `from loopkit import config, engine, roles, workspace` / `from loopkit.engine import Ticket, run_loop` / `from loopkit.memory import Memory` |
| `test_refine.py` | `import config, gates, refine` | `from loopkit import config, gates, refine` |
| `test_shield.py` | `import shield` | `from loopkit import shield` |

`run.sh`: dòng `exec python slack_app.py` → 

```bash
PYTHONPATH="$(dirname "$0")/src" exec python -m loopkit.fronts.slack
```

`example_local.py` + `example_multiagent.py`: thêm ngay trên import engine `import sys; sys.path.insert(0, "src")` và đổi `from engine import Ticket, run_loop` → `from loopkit.engine import Ticket, run_loop`.

- [ ] **Step 5: Gate.**

Run: `python3 -m pytest tests -q`
Expected: **61 passed** — đúng con số cũ. Thêm: `python3 -c "import sys; sys.path.insert(0,'src'); import loopkit.fronts.slack" 2>&1 | head -1` → SystemExit than thiếu token (tức import graph ĐÚNG, chỉ chặn ở token check) hoặc thiếu slack deps message.

- [ ] **Step 6: Commit.**

```bash
git add -A
git commit -m "restructure: src-layout package, slack becomes fronts/slack (no logic change)"
```

---

### Task 2: CLI front (TDD)

**Files:**
- Create: `src/loopkit/fronts/cli.py`, `tests/test_cli.py`
- Modify: `src/loopkit/memory.py` (thêm `runs()` sau `get_run`)

**Interfaces:**
- Consumes: toàn bộ API `loopkit.*` (Task 1); `gates.parse_repo/parse_ticket/make_pytest_gate/make_compile_gate/derive_tests`; `refine.refine_turn(idea, history, turns_used, max_turns, repos=None, ask=)`; `run_loop(...)`; `make_workspace(thread_id, repo=)`.
- Produces: `loopkit.fronts.cli.main(argv=None) -> int`; `Memory.runs() -> dict` (toàn bộ registry).

- [ ] **Step 1: Failing tests** — tạo `tests/test_cli.py`:

```python
"""CLI front verifiers — mọi LLM/loop đều fake; test wiring + door + exit codes."""
import json
from loopkit import config
from loopkit.fronts import cli
from loopkit.memory import Memory


def test_run_missing_dod(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", "làm gì đó không DoD"]) == 1
    assert "Thiếu DoD" in capsys.readouterr().out


def test_run_repo_token_stripped_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    seen = {}
    def fake_run_loop(t, **kw):
        seen["goal"] = t.goal
        return {"ok": True, "approved": True, "worker": "code", "turns": 1, "artifact": "X"}
    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    rc = cli.main(["run", "Repo: iac viết hàm f DoD: WHEN x SHALL y"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLI bỏ qua 'Repo: iac'" in out
    assert "Repo:" not in seen["goal"]                       # token đã strip khỏi ticket


def test_terminal_door_yes_no_eof(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert cli.terminal_door("code") is True
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert cli.terminal_door("code") is False
    def raise_eof(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    assert cli.terminal_door("code") is False                 # fail-closed


def test_run_exhausted_exit_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(cli, "run_loop",
                        lambda t, **kw: {"ok": False, "reason": "budget exhausted -> escalate"})
    monkeypatch.setattr(cli.gates, "derive_tests", lambda g, d: None)
    assert cli.main(["run", "viết f DoD: WHEN x SHALL y"]) == 1


def test_idea_flow_ask_then_draft_then_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    replies = iter([("ask", "A hay B?"), ("draft", "viết f DoD: WHEN x SHALL y")])
    monkeypatch.setattr(cli.refine, "refine_turn", lambda *a, **k: next(replies))
    answers = iter(["B", "y"])                                # trả lời câu hỏi, rồi duyệt draft
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    called = {}
    monkeypatch.setattr(cli, "cmd_run", lambda text, thread=None: called.setdefault("t", text) or 0)
    assert cli.main(["idea", "muốn có hàm f"]) == 0
    assert "DoD:" in called["t"]


def test_idea_cancel_exit_130(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.refine, "refine_turn",
                        lambda *a, **k: ("draft", "viết f DoD: WHEN x SHALL y"))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert cli.main(["idea", "ý tưởng"]) == 130


def test_status_lists_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Memory(".loopkit_memory").register("t1", status="done", goal="làm x", approved=True)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "t1" in out and "done" in out
```

và append vào `tests/test_memory.py`:

```python
def test_runs_returns_full_registry(tmp_path):
    mem = Memory(str(tmp_path / "m"))
    mem.register("a", status="done")
    mem.register("b", status="refining")
    assert set(mem.runs()) == {"a", "b"}
```

- [ ] **Step 2: Chạy fail.** `python3 -m pytest tests/test_cli.py tests/test_memory.py -q` — Expected: test_cli ERROR `ModuleNotFoundError: No module named 'loopkit.fronts.cli'`; test_runs FAIL `AttributeError: 'Memory' object has no attribute 'runs'`.

- [ ] **Step 3: Implement.** `src/loopkit/memory.py` thêm sau `get_run`:

```python
    def runs(self) -> dict:
        return self._load(self.reg_path)
```

Tạo `src/loopkit/fronts/cli.py`:

```python
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
```

- [ ] **Step 4: Chạy pass.** `python3 -m pytest tests -q` — Expected: **69 passed** (61 + 7 cli + 1 memory).

- [ ] **Step 5: Commit.**

```bash
git add src/loopkit/fronts/cli.py src/loopkit/memory.py tests/test_cli.py tests/test_memory.py
git commit -m "cli front: loopkit run|idea|status — terminal door, cwd = target repo"
```

---

### Task 3: Acceptance — install thật + chạy từ repo khác + Slack E2E (inline)

**Files:**
- Modify: `BUILD-MAP.md` (thêm section §9 Product)
- Không code mới.

- [ ] **Step 1: Editable install + version.**

```bash
/Users/khanhluong/miniconda3/bin/pip install -e ".[slack]" -q && loopkit --version
```
Expected: `loopkit 0.1.0`. (`loopkit` binary vào PATH của miniconda.)

- [ ] **Step 2: CLI thật từ repo khác.** Từ `~/code/annamgt/annamgt-streaming-pipeline`:

```bash
cd ~/code/annamgt/annamgt-streaming-pipeline && loopkit run "viết hàm speed_kmh(prev, cur) tính tốc độ km/h giữa 2 điểm GPS dict {lat,lon,ts} DoD: WHEN 2 điểm cách ~1km và 60s SHALL return ~60 (sai số 1); WHEN ts bằng nhau SHALL return 0.0 Tests: \`\`\`python
from solution import speed_kmh

def _pt(lat, lon, ts):
    return {\"lat\": lat, \"lon\": lon, \"ts\": ts}

def test_speed_normal():
    assert abs(speed_kmh(_pt(10.0, 106.0, 0), _pt(10.008993, 106.0, 60)) - 60) < 1

def test_same_ts_zero():
    assert speed_kmh(_pt(10.0, 106.0, 0), _pt(10.001, 106.0, 0)) == 0.0
\`\`\`"
```
Expected: worktree mới trong repo pipeline, gate pass, reviewer PASS, door terminal → `y` → `✅ approved`. Exit 0.

- [ ] **Step 3: `loopkit idea` + `loopkit status`** — một vòng idea ngắn ở repo bất kỳ; `loopkit status` in bảng.

- [ ] **Step 4: `uv tool install` (nếu có uv; không thì pipx).**

```bash
uv tool install "loopkit @ git+https://github.com/luongnamkhanh/loopkit" 2>/dev/null || pipx install "git+https://github.com/luongnamkhanh/loopkit"
loopkit --version
```
Expected: chạy từ binary cài global (độc lập repo source).

- [ ] **Step 5: Slack E2E sau restructure.** Restart bot (`./run.sh`), một ticket ngắn quen thuộc (đã cache → recall `♻️` là đủ chứng minh full path import đúng) + click door nếu có.

- [ ] **Step 6: BUILD-MAP + tag.** Thêm section mới trước "Build order":

```markdown
## 9 · Product / fronts (Phase 1 productionize — spec 2026-07-09)
| Item | Status | Note |
|---|---|---|
| src-layout package + pyproject | ✅ | hatchling; core deps RỖNG; extras `loopkit[slack]`; entry-points plugin reserved (P3) |
| CLI front `loopkit run\|idea\|status` | ✅ | cwd = repo đích (worktree per ticket); door terminal y/N fail-closed; `Repo:` bị strip + warning (allowlist là chuyện front slack) |
| Slack front = `loopkit-slack` | ✅ | behavior không đổi; AGENTS.md đọc từ cwd |
| `.loopkit.*` per-repo config (cascade kiểu aider) | ⬜ | P2 |
| Roles-as-data + MCP + recipes | ⬜ | P3 (CrewAI/Goose patterns đã research) |
| Server / multi-tenant | ⬜ | P4 — chỉ khi có user ngoài |
```

```bash
git add BUILD-MAP.md && git commit -m "phase 1 done: package + cli front (v0.1.0)"
git tag v0.1.0 && git push && git push --tags
```

---

## Self-review (done at write time)

- **Spec coverage:** cấu trúc package + bảng import (T1 S1–S4), pyproject đầy đủ với extras/scripts/reserved entry-points (T1 S2), AGENTS.md-từ-cwd fix (T1 S3.2), main() wrapper slack (T1 S3.3), gate 61 test (T1 S5), CLI 3 lệnh + door + exit codes + Repo: strip (T2), Memory.runs (T2), acceptance 4 mục của spec = T3 S1–S5, BUILD-MAP + tag v0.1.0 (T3 S6).
- **Placeholders:** không có.
- **Type consistency:** `terminal_door(artifact: str) -> bool` khớp `human_door` contract của `run_loop`; `main(argv=None) -> int` khớp console script; `Memory.runs() -> dict` khớp T2 test và `cmd_status`; import paths `loopkit.fronts.cli`/`loopkit.fronts.slack` khớp pyproject scripts.
