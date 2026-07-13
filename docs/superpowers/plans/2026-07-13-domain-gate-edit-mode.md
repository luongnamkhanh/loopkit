# Domain Gate + Edit-in-place Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ticket mang `Gate: <lệnh shell>` chạy edit-in-place mode: generator sửa thẳng file trong worktree, gate = lệnh deterministic (helm/terraform/pytest/golden), artifact = git diff, door hiện lệnh + nhãn pre-flight + diff, delivery commit tại chỗ → MR. Mở khoá 4 repo pending per-ticket. Đóng GitHub issue #1.

**Architecture:** `Gate:` token (gates) → `make_cmd_gate` verifier + `infer_gate` freeze-time (deliver) → engine nhánh edit-mode (generator ACT-edit, artifact=`git diff` qua intent-to-add, reviewer judge diff, tail route `ship_diff`) → `ship_diff` dùng chung git-tail refactor với `ship` → 3 fronts wire parse/infer/pre-flight/door. Spec: `docs/superpowers/specs/2026-07-13-domain-gate-edit-mode-design.md`.

**Tech Stack:** Python stdlib. Không dependency mới.

## Global Constraints

- Gate chốt TRƯỚC generation, kể cả AI-infer — không ngoại lệ (anti-Goodhart).
- Lệnh gate: `shell=True`, cwd=worktree, `timeout=300`; timeout/lỗi → gate FAIL, KHÔNG raise.
- Edit-mode: không solution.py, không `Deliver:` (cùng lúc → warn + ignore Deliver), không recall/semantic-cache, diff rỗng = gate fail-closed.
- Door/draft in lệnh gate NGUYÊN VĂN (+ `(AI đề xuất)` nếu infer) + nhãn pre-flight.
- `finish_suspended` edit-mode: worktree mất → từ chối ship kèm thông báo, KHÔNG re-materialize.
- Repo ACTIVE thiếu Gate: giữ nguyên đường pytest hiện tại — zero thay đổi hành vi cũ; suite hiện có (139) pass sau MỖI task.
- Không AI attribution trong commit. Mask qua shield ở mọi emit/persist như hiện tại.

---

### Task 1: `gates.parse_gate_cmd` + `make_cmd_gate`

**Files:**
- Modify: `src/loopkit/gates.py` (sau `parse_deliver`)
- Test: `tests/test_domain_gate.py` (file mới)

**Interfaces:**
- Produces: `parse_gate_cmd(text) -> (cmd|None, text_stripped)` — `Gate: <đến hết dòng>`, first match wins, case-insensitive, strip khỏi text.
- Produces: `make_cmd_gate(cmd: str, workdir: str) -> verifier` — verifier(artifact) BỎ QUA artifact, chạy lệnh, `(rc==0, tail-700 output)`; timeout 300 → `(False, "gate timeout (300s)")`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_domain_gate.py
import subprocess

from loopkit import gates


def test_parse_gate_cmd_extracts_to_end_of_line():
    cmd, rest = gates.parse_gate_cmd(
        "goal Repo: x\nGate: helm template c | grep -q foo && helm lint c\nDoD: WHEN a SHALL b")
    assert cmd == "helm template c | grep -q foo && helm lint c"
    assert "Gate:" not in rest and "DoD: WHEN a SHALL b" in rest
    assert gates.parse_gate_cmd("no gate here") == (None, "no gate here")
    assert gates.parse_gate_cmd(None) == (None, "")


def test_make_cmd_gate_pass_fail_and_ignores_artifact(tmp_path):
    ok, detail = gates.make_cmd_gate("echo hi && true", str(tmp_path))("IGNORED")
    assert ok and "hi" in detail
    ok, detail = gates.make_cmd_gate("echo bad >&2 && false", str(tmp_path))("")
    assert not ok and "bad" in detail


def test_make_cmd_gate_timeout_fails_closed(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("cmd", 300)
    monkeypatch.setattr(gates.subprocess, "run", boom)
    ok, detail = gates.make_cmd_gate("sleep 999", str(tmp_path))("")
    assert not ok and "timeout" in detail
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_domain_gate.py -q` → FAIL `no attribute 'parse_gate_cmd'`

- [ ] **Step 3: Implement** (gates.py, dưới `parse_deliver`)

```python
_GATE_RE = re.compile(r"(?i)\bgate:\s*([^\n]+)")


def parse_gate_cmd(text: str):
    """'Gate: <lệnh đến hết dòng>' -> (cmd, text đã strip). Không có -> (None, text).
    Gate: có mặt = ticket chạy edit-in-place mode (spec 2026-07-13)."""
    m = _GATE_RE.search(text or "")
    if not m:
        return None, text or ""
    return m.group(1).strip(), (text[:m.start()] + text[m.end():]).strip()


def make_cmd_gate(cmd: str, workdir: str):
    """Domain gate: lệnh shell deterministic trong worktree. Artifact bị bỏ qua —
    trạng thái nằm trong worktree (edit-in-place). Lỗi/timeout -> FAIL, không raise."""
    def verifier(artifact: str):
        try:
            r = subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True,
                               text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return False, "gate timeout (300s)"
        except OSError as e:
            return False, f"gate không chạy được: {e}"
        return r.returncode == 0, ((r.stdout + r.stderr).strip() or "(no output)")[-700:]
    return verifier
```

- [ ] **Step 4: Run** focused PASS → full suite `python3 -m pytest tests -q` PASS
- [ ] **Step 5: Commit** `git add src/loopkit/gates.py tests/test_domain_gate.py && git commit -m "gates: Gate: token + command gate (shell, 300s, fail-closed)"`

---

### Task 2: `deliver.infer_gate` + refactor git-tail + `ship_diff`

**Files:**
- Modify: `src/loopkit/deliver.py`
- Test: `tests/test_domain_gate.py` (append; fixtures `make_repo_with_ws`, `_git` import từ test_deliver: thêm `from tests.test_deliver import make_repo_with_ws` KHÔNG được — copy fixture nhỏ vào file này như Step 1 dưới)

**Interfaces:**
- Produces: `infer_gate(goal, dod, repo, ask=None) -> str|None` — lazy import ask_claude; `git ls-files` cap 400 + goal + dod; soul ưu tiên (1) script sẵn có (2) render/validate chuẩn domain (3) grep; reply 1 dòng; junk (rỗng/nhiều dòng/≥300 chars) → None.
- Produces: `ship_diff(workspace, repo, gate_cmd, goal, dod, emit=print, record=...) -> dict` (keys ok/branch/mr_url/error) — re-run gate → `_git_deliver` tail chung.
- Refactor: `_git_deliver(workspace, branch, add_args: list, title, body, emit, record) -> dict` — tail checkout -B → add → commit → push → create_mr, TÁCH từ `ship` hiện tại; `ship` gọi lại nó, **hành vi ship không đổi một byte** (suite deliver cũ phải xanh nguyên).

- [ ] **Step 1: Failing tests** (append vào tests/test_domain_gate.py)

```python
import pathlib

from loopkit import deliver


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def make_edit_repo(tmp_path):
    """Repo + bare origin + worktree đã bị generator sửa 2 file + thêm 1 file mới."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "values.yaml").write_text("a: 1\n")
    (repo / "chart.yaml").write_text("name: x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-qu", "origin", "main")
    ws = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(ws), "-b", "loop/e1")
    (ws / "values.yaml").write_text("a: 2\nb: true\n")
    (ws / "new.yaml").write_text("fresh: yes\n")
    return repo, bare, ws


def test_infer_gate_valid_and_junk(tmp_path):
    repo, _, _ = make_edit_repo(tmp_path)
    ok = deliver.infer_gate("g", "d", str(repo), ask=lambda p, s, model=None: "helm lint chart")
    assert ok == "helm lint chart"
    for junk in ("", "dòng một\ndòng hai", "x" * 400):
        assert deliver.infer_gate("g", "d", str(repo),
                                  ask=lambda p, s, model=None, j=junk: j) is None


def test_infer_gate_prompt_has_tree_goal_dod(tmp_path):
    repo, _, _ = make_edit_repo(tmp_path)
    seen = {}
    deliver.infer_gate("GOAL-X", "DOD-Y", str(repo),
                       ask=lambda p, s, model=None: seen.update(p=p, s=s) or "true")
    assert "values.yaml" in seen["p"] and "GOAL-X" in seen["p"] and "DOD-Y" in seen["p"]


def test_ship_diff_commits_all_changes_and_pushes(tmp_path):
    repo, bare, ws = make_edit_repo(tmp_path)
    events = []
    res = deliver.ship_diff(str(ws), str(repo), "true", "Enable b flag in values",
                            "WHEN x SHALL y", emit=events.append,
                            record=lambda e: events.append(e))
    assert res["ok"], res
    assert res["branch"].startswith("feat/enable-b-flag")
    show = subprocess.run(["git", "-C", str(bare), "show", "--stat", res["branch"]],
                          capture_output=True, text=True).stdout
    assert "values.yaml" in show and "new.yaml" in show     # cả file sửa lẫn file MỚI


def test_ship_diff_gate_red_aborts_no_commit(tmp_path):
    repo, bare, ws = make_edit_repo(tmp_path)
    res = deliver.ship_diff(str(ws), str(repo), "false", "goal", "dod", emit=lambda m: None)
    assert not res["ok"] and res["error"] == "regate"
    assert _git(repo, "log", "--oneline", "feat/goal").returncode != 0


def test_ship_existing_behavior_unchanged(tmp_path):
    # guard refactor: ship cũ vẫn chạy y hệt (fixture kiểu solution.py)
    repo, bare, ws = make_edit_repo(tmp_path)
    (ws / "solution.py").write_text("def f():\n    return 1\n")
    res = deliver.ship(str(ws), str(repo), "pkg/mod.py", "old ship path", "dod")
    assert res["ok"], res
```

- [ ] **Step 2: Run** → FAIL `no attribute 'infer_gate'`

- [ ] **Step 3: Implement** — trong deliver.py:
  (a) TÁCH tail của `ship` (từ `def g(...)` đến return cuối) thành:

```python
def _git_deliver(workspace, branch, add_args, title, body, emit, record) -> dict:
    """Tail chung ship/ship_diff: checkout -B -> add -> commit -> push -> MR."""
    def g(*args):
        return subprocess.run(["git", "-C", workspace, *args],
                              capture_output=True, text=True, timeout=120,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    for args in (("checkout", "-B", branch), ("add", *add_args)):
        r = g(*args)
        if r.returncode != 0:
            emit(f"🚫 deliver abort — git FAIL: {(r.stderr or r.stdout)[-300:]}")
            record({"stage": "delivered", "error": "git_failed"})
            return {"ok": False, "branch": branch, "mr_url": None, "error": "git"}
    c = g("commit", "-m", title)
    if c.returncode != 0:
        emit(f"🚫 deliver abort — commit FAIL: {(c.stderr or c.stdout)[-300:]}")
        record({"stage": "delivered", "error": "commit_failed"})
        return {"ok": False, "branch": branch, "mr_url": None, "error": "commit"}
    p = g("push", "-u", "origin", branch)
    if p.returncode != 0:
        emit(f"🚫 push FAIL: {(p.stderr or p.stdout)[-300:]}\n"
             f"↳ branch `{branch}` còn LOCAL tại {workspace} — push lại khi remote hết lỗi.")
        record({"stage": "delivered", "error": "push_failed", "branch": branch})
        return {"ok": False, "branch": branch, "mr_url": None, "error": "push"}
    url, note = create_mr(workspace, branch, title, body,
                          push_output=(p.stdout or "") + (p.stderr or ""))
    emit(f"🚢 delivered: {url or branch} ({note})")
    record({"stage": "delivered", "branch": branch, "mr_url": url})
    return {"ok": True, "branch": branch, "mr_url": url, "error": None}
```

  `ship` giữ nguyên phần validate/place/paths rồi kết bằng
  `return _git_deliver(workspace, branch, paths, title, guard(dod), emit, record)`
  (bỏ đoạn tail trùng; try/except bọc ngoài của ship GIỮ NGUYÊN).

  (b) Thêm:

```python
_GATE_SOUL = (
    "You are a verification engineer. Given the repo file list, a GOAL and its Definition of "
    "Done, reply with EXACTLY ONE shell command that deterministically verifies the DoD when "
    "run from the repo root. Prefer in order: (1) an EXISTING test/golden script in the repo; "
    "(2) the domain's standard render/validate (helm template + helm lint, terraform validate, "
    "pytest); (3) a targeted grep on rendered output. Command only — no prose, no backticks."
)


def infer_gate(goal, dod, repo, ask=None):
    """Freeze-time: chốt Gate: TRƯỚC generation cho ticket thiếu nó. Junk -> None (fail-closed)."""
    if ask is None:
        from loopkit.engine import ask_claude as ask      # lazy: tránh vòng import
    tree = subprocess.run(["git", "-C", repo, "ls-files"], capture_output=True,
                          text=True, timeout=30).stdout
    files = "\n".join(tree.splitlines()[:400])
    reply = (ask(f"REPO FILES:\n{files}\n\nGOAL:\n{goal}\n\nDoD:\n{dod}", _GATE_SOUL,
                 model=config.ROLE_MODELS.get("orchestrator")) or "").strip()
    cand = reply.strip("`").strip()
    return cand if cand and "\n" not in cand and len(cand) < 300 else None


def ship_diff(workspace, repo, gate_cmd, goal, dod, emit=print, record=lambda e: None) -> dict:
    """Delivery edit-in-place: re-run gate -> commit TOÀN BỘ thay đổi worktree -> push -> MR.
    Không move file, không Deliver:, không cache (spec 2026-07-13 khoá #4)."""
    from loopkit import gates as _gates
    guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
    try:
        ok, detail = _gates.make_cmd_gate(gate_cmd, workspace)("")
        record({"stage": "deliver_gate", "ok": ok, "detail": detail[:200]})
        if not ok:
            emit(f"🚫 deliver abort — gate FAIL sau khi sửa: {detail}")
            return {"ok": False, "branch": None, "mr_url": None, "error": "regate"}
        title = guard(goal.splitlines()[0][:72])
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "edit"
        return _git_deliver(workspace, f"feat/{slug}", ["-A"], title, guard(dod),
                            emit, record)
    except Exception as e:                                # post-approve tail không được nổ
        emit(f"🚫 deliver abort — exception: {e}")
        record({"stage": "delivered", "error": "exception", "detail": str(e)[:200]})
        return {"ok": False, "branch": None, "mr_url": None, "error": "exception"}
```

  Import bổ sung đầu file nếu thiếu: `shield` (`from loopkit import config, shield`).

- [ ] **Step 4: Run** focused PASS → **toàn bộ** `python3 -m pytest tests -q` PASS (đặc biệt tests/test_deliver.py — guard refactor)
- [ ] **Step 5: Commit** `git add src/loopkit/deliver.py tests/test_domain_gate.py && git commit -m "deliver: infer_gate at freeze + ship_diff; shared _git_deliver tail (ship behavior unchanged)"`

---

### Task 3: Engine edit-mode

**Files:**
- Modify: `src/loopkit/engine.py` — `Ticket`, `run_loop` (generator/artifact/reviewer/tail), `finish_suspended`
- Test: `tests/test_domain_gate.py` (append)

**Interfaces:**
- Produces: `Ticket.gate_cmd: str = ""` (truthy = edit-mode). `run_loop` edit-mode: đòi tool_mode (thiếu → return ok=False reason rõ); generator prompt ACT-edit; artifact = `_worktree_diff(ws)` (intent-to-add rồi `git diff HEAD` — file MỚI cũng hiện); diff rỗng → gate fail-closed; reviewer nhận diff (text-style); recall bị skip khi gate_cmd; tail: approved+risky+gate_cmd+repo+ws+DELIVER → `_deliver.ship_diff`. `finish_suspended`: payload `mode=="edit"` → worktree tồn tại ? `ship_diff` : notify từ chối (không re-materialize).

- [ ] **Step 1: Failing tests** (append)

```python
from loopkit.engine import Ticket, run_loop, finish_suspended
import loopkit.deliver as dmod2


def _fake_brain_edit(monkeypatch, tmp_path):
    import loopkit.engine as eng
    monkeypatch.setattr(eng, "route", lambda t, roles: "code")
    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", False)
    monkeypatch.setattr(eng.config, "ENABLE_TOOLS", True)
    # generator (run_agent) "sửa" worktree; reviewer (ask_claude) PASS
    def fake_agent(prompt, soul, workdir, tools, model=None):
        pathlib.Path(workdir, "values.yaml").write_text("a: 2\n")
        return "edited"
    monkeypatch.setattr(eng, "run_agent", fake_agent)
    monkeypatch.setattr(eng, "ask_claude", lambda p, s, model=None: "VERDICT: PASS")
    return eng


def test_run_loop_edit_mode_diff_artifact_and_ship_diff(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")             # reset worktree về sạch
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    shipped = {}
    monkeypatch.setattr(dmod2, "ship_diff",
                        lambda w, r, cmd, g, d, emit=print, record=None:
                        shipped.update(cmd=cmd) or {"ok": True})
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true")
    res = run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                   journal_dir=str(tmp_path), memory=None, workspace=str(ws))
    assert res["ok"] and "values.yaml" in res["artifact"]  # artifact là git diff
    assert shipped["cmd"] == "true"


def test_run_loop_edit_mode_requires_tools(tmp_path, monkeypatch):
    import loopkit.engine as eng
    monkeypatch.setattr(eng.config, "ENABLE_TOOLS", False)
    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", False)
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, ""), gate_cmd="true",
               repo=str(tmp_path))
    res = run_loop(t, notify=lambda m: None, journal_dir=str(tmp_path),
                   memory=None, workspace=str(tmp_path))
    assert not res["ok"] and "ENABLE_TOOLS" in res["reason"]


def test_run_loop_edit_mode_empty_diff_fails_gate(tmp_path, monkeypatch):
    repo, _, ws = make_edit_repo(tmp_path)
    (ws / "values.yaml").write_text("a: 1\n")
    (ws / "new.yaml").unlink()
    eng = _fake_brain_edit(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "run_agent", lambda *a, **k: "did nothing")  # không sửa gì
    t = Ticket(goal="g", dod="d", verifier=gates.make_cmd_gate("true", str(ws)),
               risky=True, repo=str(repo), gate_cmd="true")
    res = run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                   journal_dir=str(tmp_path), memory=None, workspace=str(ws),
                   max_turns=1)
    assert not res["ok"]                                   # diff rỗng không bao giờ PASS


def test_finish_suspended_edit_mode_routes_and_refuses_lost_worktree(tmp_path, monkeypatch):
    class FakeMem:
        def register(self, *a, **k): ...
        def store(self, *a, **k): ...
        def append_event(self, *a, **k): ...
    shipped = []
    monkeypatch.setattr(dmod2, "ship_diff",
                        lambda w, r, cmd, g, d, emit=print, record=None:
                        shipped.append(cmd) or {"ok": True})
    payload = {"artifact": "diff", "goal": "g", "dod": "d", "mode": "edit",
               "gate_cmd": "true", "repo": str(tmp_path), "workspace": str(tmp_path)}
    finish_suspended(FakeMem(), "t", payload, True, lambda m: None)
    assert shipped == ["true"]
    msgs = []
    payload["workspace"] = str(tmp_path / "gone")
    finish_suspended(FakeMem(), "t", payload, True, msgs.append)
    assert shipped == ["true"] and any("worktree" in m for m in msgs)  # từ chối, không ship mù
```

- [ ] **Step 2: Run** → FAIL (`unexpected keyword argument 'gate_cmd'`)

- [ ] **Step 3: Implement** — engine.py, bốn mảnh:

  (a) `Ticket` thêm field cuối: `gate_cmd: str = ""   # Gate: lệnh domain — truthy = edit-in-place mode (spec 2026-07-13)`

  (b) Helper trên `run_loop`:

```python
def _worktree_diff(ws) -> str:
    """Artifact edit-mode: intent-to-add để file MỚI hiện trong diff, không stage nội dung."""
    subprocess.run(["git", "-C", str(ws), "add", "-N", "."], capture_output=True)
    r = subprocess.run(["git", "-C", str(ws), "diff", "HEAD"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout or ""
```

  (c) Trong `run_loop`: ngay sau tính `tool_mode`:

```python
    edit_mode = bool(ticket.gate_cmd)
    if edit_mode and not tool_mode:
        record({"stage": "refused", "reason": "edit-mode without tools"})
        return {"ok": False, "worker": None, "turns": 0,
                "reason": "edit-mode cần LOOPKIT_ENABLE_TOOLS=1 + workspace"}
```

  Recall block đầu hàm đổi điều kiện: `if mem and not ticket.gate_cmd:` (edit-mode không recall).

  Generator: nhánh `if tool_mode:` tách prompt theo mode:

```python
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
```

  Ngay trước `gate_pass, gate_detail = ticket.verifier(artifact)`:

```python
        if edit_mode and not artifact.strip():            # cmd-gate bỏ qua artifact -> phải chặn ở đây
            gate_pass, gate_detail = False, "empty diff — generator không sửa file nào"
        else:
            gate_pass, gate_detail = ticket.verifier(artifact)
```

  (thay dòng gọi verifier cũ; entry/feedback giữ nguyên).

  Reviewer: edit-mode dùng nhánh TEXT với diff (không cần reviewer ACT):

```python
        if tool_mode and not edit_mode:                                         # REVIEWER (acts)
            ...giữ nguyên nhánh cũ...
        else:                                                                   # REVIEWER (text)
            eval_prompt = (f"{ctx}GOAL:\n{ticket.goal}\n\nDEFINITION OF DONE:\n{ticket.dod}\n\n"
                           f"Deterministic gate PASSED: {entry['gate']}\n\n"
                           + (f"ARTIFACT UNDER REVIEW là git diff của thay đổi:\n```\n{artifact}\n```\n"
                              if edit_mode else
                              f"ARTIFACT UNDER REVIEW:\n```\n{artifact}\n```\n")
                           + "Judge the DoD items the gate does NOT cover.")
            reply = ask_claude(eval_prompt, eval_soul, model=config.ROLE_MODELS.get("reviewer"))
```

  Tail (sau record done, cạnh nhánh ship hiện có):

```python
            if (approved and ticket.risky and ticket.gate_cmd and ticket.repo
                    and ws and config.DELIVER):
                from loopkit import deliver as _deliver
                _deliver.ship_diff(str(ws), ticket.repo, ticket.gate_cmd,
                                   ticket.goal, ticket.dod, emit=emit, record=record)
            elif (approved and ticket.risky and ticket.deliver and ticket.repo
                    and ws and config.DELIVER):
                ...nhánh ship cũ giữ nguyên...
```

  (d) `finish_suspended` — trước nhánh deliver hiện có:

```python
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
```

- [ ] **Step 4: Run** focused + full suite PASS
- [ ] **Step 5: Commit** `git add src/loopkit/engine.py tests/test_domain_gate.py && git commit -m "engine: edit-in-place mode — diff artifact, tools required, ship_diff routing, no blind resume"`

---

### Task 4: CLI + Telegram fronts

**Files:**
- Modify: `src/loopkit/fronts/cli.py`, `src/loopkit/fronts/telegram.py`
- Test: `tests/test_domain_gate.py` (append; reuse FakeTgApi/MemStub qua import module test? KHÔNG — copy mini-stub như các task trước nếu cần, hoặc test qua telegram module trực tiếp với stub nhỏ trong file này)

**Interfaces:**
- Produces (cả hai fronts, logic giống nhau — làm helper chung trong deliver.py? KHÔNG: 10 dòng/front, giữ tại chỗ):
  - parse: sau `parse_repo` → `gate_cmd, text = gates.parse_gate_cmd(text)`; nếu `gate_cmd và deliver_path` → warn "Gate: là edit-mode — bỏ qua Deliver:" + deliver_path=None.
  - pending-repo check ĐỔI: `repo_name in config.REPOS_PENDING` và `gate_cmd is None` → `gate_cmd = deliver.infer_gate(goal, dod, repo_path)`; vẫn None → từ chối "repo này cần Gate: — mô tả cách verify trong ticket/idea"; có → emit `🛡 Gate (AI đề xuất): <cmd>`.
  - edit-mode setup: `gate_cmd` truthy → đòi `repo và config.ENABLE_TOOLS` (thiếu → từ chối); `verifier = gates.make_cmd_gate(gate_cmd, wd)`; `frozen_tests = ""`; pre-flight `pre_ok, _ = verifier("")` → `gate_label = "⚠️ gate XANH trước khi sửa — chỉ chống vỡ, không chứng minh DoD" if pre_ok else "🔴 acceptance gate (đỏ trước khi sửa)"`; emit label; skip freeze_deliver (`deliver_path=None`).
  - `Ticket(..., gate_cmd=gate_cmd or "")`; door hiển thị + payload: thêm `mode: "edit" if gate_cmd else "module"`, `gate_cmd`; CLI `terminal_door(artifact, deliver=None, gate=None)` in dòng `🛡 Gate: <cmd>`; cmd_ticket_run in marker `GATE: <cmd>` sau ARTIFACT_END; Telegram door text thêm dòng `🛡 Gate: <cmd>\n<label>`.

- [ ] **Step 1: Failing tests** (append — đại diện đủ nhánh, test qua telegram front cho gọn; CLI test 1 case wiring)

```python
from loopkit.fronts import telegram as tgf
from loopkit.fronts import cli as clif


class TgStub:
    def __init__(self):
        self.sent = []

    def send(self, text, reply_to=None, keyboard=None):
        self.sent.append(text)
        return len(self.sent)


class MStub:
    def __init__(self):
        self.reg, self.doors = {}, {}

    def register(self, t, **f):
        self.reg.setdefault(t, {}).update(f)

    def get_run(self, t):
        return dict(self.reg.get(t, {}))

    def runs(self):
        return {k: dict(v) for k, v in self.reg.items()}

    def door_open(self, t, p):
        self.doors[t] = p

    def door_get(self, t):
        return self.doors.get(t)

    def recall(self, g, d):
        return None

    def append_event(self, *a):
        ...


def test_tg_pending_repo_infer_gate_and_edit_ticket(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(tgf.deliver, "infer_gate", lambda g, d, r: "helm lint c")
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "wt"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("do infra Repo: deploy DoD: WHEN x SHALL y", "tg-1", mem, api)
    assert seen["t"].gate_cmd == "helm lint c"
    assert any("AI đề xuất" in s for s in api.sent)
    assert any("gate" in s.lower() for s in api.sent)      # pre-flight label emitted


def test_tg_pending_repo_no_gate_refused(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {"deploy": str(repo)})
    monkeypatch.setattr(tgf.config, "REPOS_PENDING", {"deploy"})
    monkeypatch.setattr(tgf.deliver, "infer_gate", lambda g, d, r: None)
    called = []
    monkeypatch.setattr(tgf, "run_loop", lambda *a, **k: called.append(1))
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("do infra Repo: deploy DoD: WHEN x SHALL y", "tg-2", mem, api)
    assert not called and any("cần Gate" in s for s in api.sent)


def test_tg_gate_plus_deliver_warns_and_drops_deliver(tmp_path, monkeypatch):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(tgf.config, "REPOS", {})
    monkeypatch.setattr(tgf.config, "TARGET_REPO", str(repo))
    monkeypatch.setattr(tgf.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(tgf, "make_workspace", lambda th, repo=None: (str(tmp_path / "w2"), "worktree"))
    seen = {}
    monkeypatch.setattr(tgf, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": False, "worker": "code", "turns": 1})
    api, mem = TgStub(), MStub()
    tgf.launch_ticket("x Gate: true Deliver: a/b.py DoD: WHEN x SHALL y", "tg-3", mem, api)
    assert seen["t"].gate_cmd == "true" and seen["t"].deliver is None
    assert any("bỏ qua Deliver" in s for s in api.sent)
    door = None  # door payload check qua suspend door
    hd = [kw for kw in []]  # (payload đã test ở nhánh engine; đây chỉ cần Ticket đúng)


def test_cli_gate_ticket_wiring(tmp_path, monkeypatch, capsys):
    repo, _, _ = make_edit_repo(tmp_path)
    monkeypatch.setattr(clif, "_cwd_repo", lambda: str(repo))
    monkeypatch.setattr(clif.config, "ENABLE_TOOLS", True)
    monkeypatch.setattr(clif.config, "REPOS", {})
    monkeypatch.setattr(clif, "make_workspace", lambda th, repo=None: (str(tmp_path / "w3"), "worktree"))
    seen = {}
    monkeypatch.setattr(clif, "run_loop", lambda t, **kw: seen.update(t=t) or
                        {"ok": True, "approved": True, "worker": "code", "turns": 1})
    monkeypatch.setattr(clif.config, "ENABLE_MEMORY", False)
    clif.cmd_run("y Gate: ./tests/run.sh DoD: WHEN a SHALL b")
    assert seen["t"].gate_cmd == "./tests/run.sh"
    assert "Gate" in capsys.readouterr().out
```

- [ ] **Step 2: Run** → FAIL (Ticket chưa nhận gate_cmd từ fronts / thông báo thiếu)

- [ ] **Step 3: Implement** — cả hai fronts theo Interfaces trên. CLI: sửa `cmd_run` + `cmd_ticket_run` + `terminal_door(artifact, deliver=None, gate=None)` (+ dòng `🛡 Gate: {gate}` khi có) + `make_suspend_door(..., gate_cmd="", mode="module", gate_label="")` payload thêm 3 keys + marker `GATE:` in cmd_ticket_run. Telegram: `launch_ticket` thêm parse/infer/label như Interfaces; `make_tg_door(..., gate_cmd="", gate_label="", mode="module")` — door text: nếu edit-mode `🚪 Diff chờ duyệt:\n🛡 Gate: {gate_cmd}\n{gate_label}\n{diff...}`; payload thêm `mode`, `gate_cmd`, `gate_label` (persist = spec 'nhãn ghi lại'). Pre-flight chạy SAU khi build verifier, TRƯỚC run_loop.

- [ ] **Step 4: Run** focused + full suite PASS
- [ ] **Step 5: Commit** `git add src/loopkit/fronts/cli.py src/loopkit/fronts/telegram.py tests/test_domain_gate.py && git commit -m "cli+telegram fronts: Gate: parse/infer at freeze, pre-flight label, edit-mode door + payload"`

---

### Task 5: Slack front + analyst soul + docs

**Files:**
- Modify: `src/loopkit/fronts/slack.py`, `src/loopkit/roles.py` (analyst soul), `src/loopkit/refine.py` (repo_ctx nhắc Gate:), `TICKET_TEMPLATE.md`, `skills/loopkit/SKILL.md`, `README.md`, `BUILD-MAP.md`
- Test: py_compile cho slack.py (không unit test được — token) + 1 test refine prompt

**Interfaces:**
- Slack `launch_ticket`: wiring y hệt Telegram Task 4 (parse → conflict warn → pending-infer → edit-mode setup → Ticket + make_door thêm `gate_cmd`, `gate_label`, `mode` — door message thêm dòng `🛡 Gate: ...` + label).
- `roles.py` analyst soul: thêm một câu — "Với repo pending/hạ tầng, draft PHẢI kèm `Gate: <one shell command>` ưu tiên: script test sẵn có > helm template+lint / terraform validate / pytest > grep có chủ đích."
- `refine.py` `repo_ctx`: thêm dòng liệt kê pending + nhắc "pending repos REQUIRE a Gate: line in the ticket".

- [ ] **Step 1: Failing test** (append — refine nhắc Gate cho analyst)

```python
from loopkit import refine


def test_refine_repo_ctx_mentions_gate_for_pending():
    seen = {}
    refine.refine_turn("idea", [], 0, 5,
                       repos={"active": ["pipeline"], "pending": ["deploy"]},
                       ask=lambda p, s, model=None: seen.update(p=p, s=s) or "QUESTION: q?")
    assert "Gate:" in seen["p"] or "Gate:" in seen["s"]
```

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** — slack.py wiring (mirror Telegram, giữ threading/work() nguyên trạng); roles.py + refine.py câu nhắc Gate; docs:
  - `TICKET_TEMPLATE.md`: mục "Gate (edit-in-place — thường để AI điền): `Gate: <lệnh shell>` — có nó là generator sửa thẳng repo, gate = lệnh, artifact = diff."
  - `skills/loopkit/SKILL.md`: bullet — door có `GATE: <cmd>` → relay lệnh + nhãn pre-flight cho người duyệt; approve = duyệt cả lệnh lẫn diff.
  - `README.md`: bullet trong Delivery/fronts — `Gate: <command>` switches a ticket to edit-in-place mode (multi-file edits verified by YOUR repo's own suite / helm / terraform); resolves issue #1.
  - `BUILD-MAP.md`: §2 helm/kubeconform row ✅ (spec 2026-07-13, Gate: command); §9/§7 note REPOS_PENDING = requires-gate.
- [ ] **Step 4: Run** `python3 -m py_compile src/loopkit/fronts/slack.py` + full suite PASS
- [ ] **Step 5: Commit** `git add -A src skills *.md tests && git commit -m "slack front + analyst: Gate: wiring, pending=requires-gate; docs + BUILD-MAP"`

---

## Live E2E (acceptance — GitHub issue #1, sau merge)

Từ Telegram: (1) ticket helm thật vào `Repo: streaming-deploy` (analyst/infer đề xuất gate) → door diff+gate → Approve → MR trên gitlab.annamglobal.com; (2) ticket multi-file Python vào repo active với `Gate: python3 -m pytest tests -q` (case issue #1). Cả hai pass → đóng issue #1.
