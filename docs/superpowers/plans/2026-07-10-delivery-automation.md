# Delivery Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sau khi người duyệt approve, loopkit tự giao artifact vào repo đích: move file theo `Deliver:` path → re-run gate → commit trên `feat/<module>` → push → tạo MR (glab/gh, fallback link) → post link vào thread/terminal.

**Architecture:** Module mới `src/loopkit/deliver.py` chứa toàn bộ chuỗi deterministic (không LLM lúc giao hàng). Path chốt LÚC FREEZE: parse token `Deliver:` từ ticket (gates), thiếu thì 1 brain call nhỏ (`infer_path`) trước generation. Engine gọi `deliver.ship()` tại 2 điểm accepted: cuối `run_loop` (chỉ khi `ticket.risky` và door pass — non-risky KHÔNG deliver) và `finish_suspended(decision=True)` (door payload mang đủ deliver/repo/tests/workspace để tự re-materialize sau reboot).

**Tech Stack:** Python 3 stdlib only (subprocess git/glab/gh/pytest). Spec: `docs/superpowers/specs/2026-07-10-delivery-automation-design.md`.

## Global Constraints

- Core dependencies RỖNG (pyproject) — không thêm dependency mới; stdlib + subprocess.
- Toàn bộ 81 test hiện có phải pass sau MỖI task: `python3 -m pytest tests -q`.
- Không AI attribution trong bất kỳ commit nào.
- `deliver.ship()` chạy CHỈ KHI: human-approved qua door + repo-mode + `Deliver:` có + `LOOPKIT_DELIVER=1` (default bật). Non-risky auto-pass không bao giờ deliver.
- Delivery fail KHÔNG rollback approve; mỗi bước fail → emit lỗi rõ + journal + dừng, giữ nguyên hiện trạng.
- Engine import deliver LAZY (trong hàm) — deliver import `engine.ask_claude` nên import module-level sẽ tạo vòng.
- Mọi text ra ngoài đi qua `emit`/`notify` sẵn có (shield mask ở đó rồi).

---

### Task 1: `gates.parse_deliver` — token `Deliver:` trong ticket

**Files:**
- Modify: `src/loopkit/gates.py` (thêm sau `parse_repo`, ~dòng 74)
- Test: `tests/test_deliver.py` (file mới)

**Interfaces:**
- Produces: `gates.parse_deliver(text: str) -> tuple[str | None, str]` — `(path, text_đã_strip_token)`; token dạng `Deliver: <path>.py` ở bất kỳ đâu, first match wins, không có → `(None, text nguyên vẹn)`. Case-insensitive như `parse_repo`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_deliver.py
from loopkit import gates


def test_parse_deliver_extracts_and_strips():
    path, rest = gates.parse_deliver(
        "Tinh bearing Deliver: flink/bearing.py DoD: WHEN x SHALL y")
    assert path == "flink/bearing.py"
    assert "Deliver:" not in rest
    assert rest.startswith("Tinh bearing") and "DoD: WHEN x SHALL y" in rest


def test_parse_deliver_absent():
    path, rest = gates.parse_deliver("goal DoD: WHEN x SHALL y")
    assert path is None and rest == "goal DoD: WHEN x SHALL y"


def test_parse_deliver_case_insensitive_and_none_input():
    path, _ = gates.parse_deliver("x deliver: a/b_c.py DoD: y")
    assert path == "a/b_c.py"
    assert gates.parse_deliver(None) == (None, "")
```

- [ ] **Step 2: Run to verify FAIL**

Run: `python3 -m pytest tests/test_deliver.py -q`
Expected: FAIL — `AttributeError: module 'loopkit.gates' has no attribute 'parse_deliver'`

- [ ] **Step 3: Implement in `gates.py`** (đặt ngay dưới `parse_repo`)

```python
_DELIVER_RE = re.compile(r"(?i)\bdeliver:\s*([\w./-]+\.py)\s*")


def parse_deliver(text: str):
    """'Deliver: <path>.py' ở bất kỳ đâu trong ticket -> (path, text đã strip token).
    First match wins; không có token -> (None, text nguyên vẹn)."""
    m = _DELIVER_RE.search(text or "")
    if not m:
        return None, text or ""
    return m.group(1), (text[:m.start()] + " " + text[m.end():]).strip()
```

- [ ] **Step 4: Run tests** — `python3 -m pytest tests/test_deliver.py tests/test_gates.py -q` → PASS (cả test_gates cũ)

- [ ] **Step 5: Commit**

```bash
git add src/loopkit/gates.py tests/test_deliver.py
git commit -m "gates: parse Deliver: token (placement declared in ticket)"
```

---

### Task 2: `deliver.py` — `validate_path` + `place_and_verify` (move → rewrite import → re-gate)

**Files:**
- Create: `src/loopkit/deliver.py`
- Test: `tests/test_deliver.py` (append)

**Interfaces:**
- Produces: `deliver.validate_path(path: str, repo: str) -> bool` — tương đối, không `..`, không tuyệt đối, đuôi `.py`, resolve nằm trong repo.
- Produces: `deliver.place_and_verify(workspace: str, deliver_path: str) -> tuple[bool, str]` — move `solution.py` → path, `test_ticket.py` (nếu có) → `test_<module>.py` cùng dir + rewrite import `solution`→`<module>`, rồi re-run pytest (hoặc py_compile nếu không có test) — `(ok, detail)`.

- [ ] **Step 1: Write the failing tests** (append vào `tests/test_deliver.py`)

```python
import pathlib
import subprocess

from loopkit import deliver


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def make_ws(tmp_path):
    """Workspace giả lập worktree: có solution.py + test_ticket.py xanh."""
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_ticket.py").write_text(
        "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return ws


def test_validate_path(tmp_path):
    repo = tmp_path
    assert deliver.validate_path("pkg/mod.py", str(repo))
    assert not deliver.validate_path("/abs/mod.py", str(repo))
    assert not deliver.validate_path("../out.py", str(repo))
    assert not deliver.validate_path("pkg/mod.txt", str(repo))
    assert not deliver.validate_path("", str(repo))


def test_place_and_verify_moves_rewrites_and_regates(tmp_path):
    ws = make_ws(tmp_path)
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert ok, detail
    assert (ws / "pkg" / "adder.py").exists()
    assert not (ws / "solution.py").exists()
    tsrc = (ws / "pkg" / "test_adder.py").read_text()
    assert "from adder import add" in tsrc and "solution" not in tsrc


def test_place_and_verify_regate_fails_on_broken_artifact(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "solution.py").write_text("def add(a, b):\n    return a - b\n")  # sai
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert not ok


def test_place_and_verify_compile_only_when_no_tests(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "test_ticket.py").unlink()
    ok, detail = deliver.place_and_verify(str(ws), "pkg/adder.py")
    assert ok
```

- [ ] **Step 2: Run to verify FAIL** — `python3 -m pytest tests/test_deliver.py -q` → FAIL `No module named 'loopkit.deliver'` (import error)

- [ ] **Step 3: Create `src/loopkit/deliver.py`**

```python
"""
loopkit.deliver — giao artifact vào repo sau HUMAN approve (spec 2026-07-10).

Chuỗi deterministic, KHÔNG LLM lúc giao hàng (path đã chốt lúc freeze):
  place (move solution.py -> Deliver: path, test theo cùng, rewrite import)
  -> re-run gate trên file đã move (import đổi thì phải xanh lại)
  -> branch feat/<module> -> commit (1 dòng từ goal, không attribution) -> push
  -> MR qua glab/gh (detect từ remote URL) | fallback: link create-MR parse từ push output.

Delivery fail KHÔNG rollback approve — báo rõ, giữ branch/file local.
"""
import pathlib, re, shutil, subprocess
from typing import Optional
from loopkit import config
from loopkit.workspace import make_workspace


def validate_path(path: str, repo: str) -> bool:
    if not path or not path.endswith(".py"):
        return False
    p = pathlib.PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        return False
    root = pathlib.Path(repo).resolve()
    return str((root / path).resolve()).startswith(str(root))


def place_and_verify(workspace: str, deliver_path: str):
    """Move + rewrite import + re-gate. -> (ok, detail). Fail -> file giữ nguyên vị trí mới
    để inspect, KHÔNG commit (caller dừng chuỗi)."""
    ws = pathlib.Path(workspace)
    sol = ws / "solution.py"
    if not sol.exists():
        return False, "solution.py không tồn tại trong workspace"
    dst = ws / deliver_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    module = dst.stem
    sol.rename(dst)
    tsrc_f = ws / "test_ticket.py"
    if tsrc_f.exists():
        src = (tsrc_f.read_text()
               .replace("from solution import", f"from {module} import")
               .replace("import solution", f"import {module}")
               .replace("solution.", f"{module}."))
        tdst = dst.parent / f"test_{module}.py"
        tdst.write_text(src)
        tsrc_f.unlink()
        r = subprocess.run(["python3", "-m", "pytest", "-q", tdst.name],
                           cwd=dst.parent, capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-700:]
    r = subprocess.run(["python3", "-m", "py_compile", str(dst)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr.strip() or "compiles OK (không có test — gate yếu)")[-300:]
```

- [ ] **Step 4: Run tests** — `python3 -m pytest tests/test_deliver.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/loopkit/deliver.py tests/test_deliver.py
git commit -m "deliver: place artifact at Deliver: path + re-gate after move"
```

---

### Task 3: `deliver.ship` — branch → commit → push (fail graceful)

**Files:**
- Modify: `src/loopkit/deliver.py`
- Test: `tests/test_deliver.py` (append)

**Interfaces:**
- Consumes: `place_and_verify`, `create_mr` (Task 4 — trong task này stub bằng monkeypatch-able call; ship gọi `create_mr` đã định nghĩa ở Task 4, nên **Task 3 viết ship với `create_mr` stub tạm** trả `(None, "MR: chưa wire")` rồi Task 4 thay thân thật).
- Produces: `deliver.ship(workspace: str, repo: str, deliver_path: str, goal: str, dod: str, emit=print, record=lambda e: None) -> dict` — `{"ok": bool, "branch": str, "mr_url": str|None, "error": str|None}`. Journal stages: `deliver_gate`, `delivered`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def make_repo_with_ws(tmp_path):
    """Repo thật + bare origin + worktree có artifact xanh (mô phỏng sau approve)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-qu", "origin", "main")
    ws = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(ws), "-b", "loop/t1")
    (ws / "solution.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_ticket.py").write_text(
        "from solution import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return repo, bare, ws


def test_ship_commits_and_pushes(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    events = []
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add two numbers",
                       "WHEN ints SHALL sum", emit=events.append,
                       record=lambda e: events.append(e))
    assert res["ok"], res
    assert res["branch"] == "feat/adder"
    r = subprocess.run(["git", "-C", str(bare), "log", "--oneline", "feat/adder"],
                       capture_output=True, text=True)
    assert "add two numbers" in r.stdout          # commit lên remote, message từ goal


def test_ship_push_fail_keeps_branch_and_reports(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    hook = pathlib.Path(bare) / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'read-only group' >&2\nexit 1\n")
    hook.chmod(0o755)
    msgs = []
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add two numbers",
                       "WHEN ints SHALL sum", emit=msgs.append)
    assert not res["ok"] and res["error"] == "push"
    assert res["branch"] == "feat/adder"
    local = _git(repo, "log", "--oneline", "feat/adder")
    assert "add two numbers" in local.stdout      # commit còn local
    assert any("push FAIL" in m for m in msgs)    # báo rõ, có stderr


def test_ship_aborts_on_regate_fail_no_commit(tmp_path):
    repo, bare, ws = make_repo_with_ws(tmp_path)
    (ws / "solution.py").write_text("def add(a, b):\n    return a - b\n")
    res = deliver.ship(str(ws), str(repo), "pkg/adder.py", "add", "dod")
    assert not res["ok"] and res["error"] == "regate"
    assert _git(repo, "log", "--oneline", "feat/adder").returncode != 0  # branch không tồn tại
```

- [ ] **Step 2: Run to verify FAIL** — `python3 -m pytest tests/test_deliver.py -q` → FAIL `no attribute 'ship'`

- [ ] **Step 3: Implement `ship` (+ stub `create_mr`)** — append vào `deliver.py`

```python
def create_mr(workspace: str, branch: str, title: str, body: str,
              push_output: str = "", remote_url: Optional[str] = None):
    return None, "MR: chưa wire"          # Task 4 thay thân thật


def ship(workspace: str, repo: str, deliver_path: str, goal: str, dod: str,
         emit=print, record=lambda e: None) -> dict:
    """Chuỗi giao hàng sau approve. Mỗi bước fail -> emit + journal + DỪNG, không rollback."""
    ok, detail = place_and_verify(workspace, deliver_path)
    record({"stage": "deliver_gate", "ok": ok, "detail": detail[:200]})
    if not ok:
        emit(f"🚫 deliver abort — re-gate FAIL sau move: {detail}")
        return {"ok": False, "branch": None, "mr_url": None, "error": "regate"}
    module = pathlib.Path(deliver_path).stem
    branch = f"feat/{module.replace('_', '-')}"
    test_rel = str(pathlib.PurePosixPath(deliver_path).parent / f"test_{module}.py")

    def g(*args):
        return subprocess.run(["git", "-C", workspace, *args],
                              capture_output=True, text=True)

    g("checkout", "-B", branch)                     # -B: revision re-run dùng lại branch
    g("add", deliver_path, test_rel)
    c = g("commit", "-m", goal.splitlines()[0][:72])
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
    url, note = create_mr(workspace, branch, goal.splitlines()[0][:72], dod,
                          push_output=(p.stdout or "") + (p.stderr or ""))
    emit(f"🚢 delivered: {url or branch} ({note})")
    record({"stage": "delivered", "branch": branch, "mr_url": url})
    return {"ok": True, "branch": branch, "mr_url": url, "error": None}
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_deliver.py -q` → PASS
- [ ] **Step 5: Full suite** — `python3 -m pytest tests -q` → PASS (81 cũ + mới)
- [ ] **Step 6: Commit**

```bash
git add src/loopkit/deliver.py tests/test_deliver.py
git commit -m "deliver: ship chain — feat branch, commit from goal, push with graceful fail"
```

---

### Task 4: `deliver.create_mr` — glab/gh theo remote, fallback link từ push output

**Files:**
- Modify: `src/loopkit/deliver.py` (thay thân stub `create_mr`)
- Test: `tests/test_deliver.py` (append)

**Interfaces:**
- Produces: `create_mr(workspace, branch, title, body, push_output="", remote_url=None) -> tuple[str | None, str]` — `(url, note)`. Tool chọn theo `config.MR_TOOL` (`auto|glab|gh|link|off`); `auto`: remote chứa `github.com` → `gh`, ngược lại → `glab`. Tool thiếu (`shutil.which` None) hoặc lệnh fail → parse `https://…(merge_requests/new|pull/new)…` từ push output; không có nốt → `(None, hướng dẫn tay)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
import loopkit.deliver as dmod
from loopkit import config


GITLAB_PUSH = ("remote:\nremote: To create a merge request for feat/adder, visit:\n"
               "remote:   https://gitlab.com/g/p/-/merge_requests/new?"
               "merge_request%5Bsource_branch%5D=feat%2Fadder\nremote:\n")
GITHUB_PUSH = ("remote:\nremote: Create a pull request for 'feat/adder' on GitHub by visiting:\n"
               "remote:      https://github.com/o/r/pull/new/feat/adder\nremote:\n")


def test_create_mr_fallback_link_gitlab(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda _: None)
    url, note = deliver.create_mr(str(tmp_path), "feat/adder", "t", "b",
                                  push_output=GITLAB_PUSH,
                                  remote_url="https://gitlab.com/g/p.git")
    assert url and "merge_requests/new" in url


def test_create_mr_fallback_link_github(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda _: None)
    url, note = deliver.create_mr(str(tmp_path), "feat/adder", "t", "b",
                                  push_output=GITHUB_PUSH,
                                  remote_url="git@github.com:o/r.git")
    assert url and "pull/new" in url


def test_create_mr_via_glab(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod.shutil, "which", lambda name: "/bin/" + name)
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class R:  # noqa: N801
            returncode = 0
            stdout = "https://gitlab.com/g/p/-/merge_requests/7\n"
            stderr = ""
        return R()

    monkeypatch.setattr(dmod.subprocess, "run", fake_run)
    url, note = deliver.create_mr(str(tmp_path), "feat/adder", "tiêu đề", "dod",
                                  remote_url="https://gitlab.com/g/p.git")
    assert url == "https://gitlab.com/g/p/-/merge_requests/7"
    assert calls["cmd"][0] == "glab"


def test_create_mr_off(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPKIT_MR_TOOL", "off")
    import importlib
    importlib.reload(config)
    try:
        url, note = deliver.create_mr(str(tmp_path), "b", "t", "d",
                                      remote_url="https://gitlab.com/g/p.git")
        assert url is None and "off" in note
    finally:
        monkeypatch.delenv("LOOPKIT_MR_TOOL")
        importlib.reload(config)
```

- [ ] **Step 2: Run to verify FAIL** — 3/4 test fail (stub trả `None, "MR: chưa wire"`); riêng knob `MR_TOOL` chưa có trong config → test `off` fail vì reload không đổi gì. Task này thêm luôn knob.

- [ ] **Step 3: Add knobs to `config.py`** (sau block agent tool-mode, ~dòng 50)

```python
# --- delivery (post-approve ship) — spec 2026-07-10 ---
DELIVER = _env_bool("DELIVER", True)              # tắt = behavior cũ (artifact nằm worktree)
MR_TOOL = _env_str("MR_TOOL", "auto")             # auto|glab|gh|link|off
```

- [ ] **Step 4: Replace `create_mr` body in `deliver.py`**

```python
_MR_LINK_RE = re.compile(r"https://\S*(?:merge_requests/new|pull/new)\S*")


def _remote_url(workspace: str) -> str:
    r = subprocess.run(["git", "-C", workspace, "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def create_mr(workspace: str, branch: str, title: str, body: str,
              push_output: str = "", remote_url: Optional[str] = None):
    """-> (mr_url|None, note). Không raise — mọi nhánh fail đều rơi về fallback."""
    tool = config.MR_TOOL
    if tool == "off":
        return None, "MR skipped (LOOPKIT_MR_TOOL=off)"
    if tool != "link":
        url = remote_url if remote_url is not None else _remote_url(workspace)
        use = tool if tool in ("glab", "gh") else ("gh" if "github.com" in url else "glab")
        cmd = {"glab": ["glab", "mr", "create", "--title", title, "--description",
                        body, "--source-branch", branch, "--yes"],
               "gh": ["gh", "pr", "create", "--title", title, "--body", body,
                      "--head", branch]}[use]
        if shutil.which(use):
            r = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True,
                               timeout=60)
            m = re.search(r"https://\S+", r.stdout or "")
            if r.returncode == 0 and m:
                return m.group(0), f"MR tạo qua {use}"
    m = _MR_LINK_RE.search(push_output or "")
    if m:
        return m.group(0), "link create-MR từ push output (bấm để tạo)"
    return None, f"tạo MR tay từ branch {branch}"
```

- [ ] **Step 5: Run** — `python3 -m pytest tests/test_deliver.py tests/test_config.py -q` → PASS
- [ ] **Step 6: Full suite** — `python3 -m pytest tests -q` → PASS
- [ ] **Step 7: Commit**

```bash
git add src/loopkit/deliver.py src/loopkit/config.py tests/test_deliver.py
git commit -m "deliver: real MR via glab/gh by remote host, fallback push-output link; knobs DELIVER/MR_TOOL"
```

---

### Task 5: `deliver.infer_path` (freeze-time) + `ensure_workspace` (re-materialize)

**Files:**
- Modify: `src/loopkit/deliver.py`
- Test: `tests/test_deliver.py` (append)

**Interfaces:**
- Produces: `infer_path(goal: str, repo: str, ask=None) -> str | None` — brain call nhỏ (model tier orchestrator/haiku) với `git ls-files` (cap 400 dòng) + goal; reply 1 dòng path; qua `validate_path` mới nhận, không thì `None` (degraded → caller skip delivery + warning).
- Produces: `ensure_workspace(thread_id: str, repo: str, artifact: str, tests_src: str = "", workspace: str = "") -> str` — workspace còn thì dùng; mất (reboot) → `make_workspace` lại + ghi `solution.py`/`test_ticket.py` từ payload.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_infer_path_valid_reply(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    got = deliver.infer_path("thêm hàm cộng", str(repo),
                             ask=lambda p, s, model=None: "pkg/adder.py")
    assert got == "pkg/adder.py"


def test_infer_path_junk_reply_degrades_to_none(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    for junk in ("/etc/x.py", "../up.py", "tôi nghĩ là pkg nhé", ""):
        assert deliver.infer_path("g", str(repo),
                                  ask=lambda p, s, model=None, j=junk: j) is None


def test_infer_path_prompt_contains_tree_and_goal(tmp_path):
    repo, _, _ = make_repo_with_ws(tmp_path)
    seen = {}

    def fake_ask(prompt, soul, model=None):
        seen["prompt"] = prompt
        return "pkg/adder.py"

    deliver.infer_path("thêm hàm cộng", str(repo), ask=fake_ask)
    assert "pkg/__init__.py" in seen["prompt"] and "thêm hàm cộng" in seen["prompt"]


def test_ensure_workspace_rematerializes(tmp_path, monkeypatch):
    repo, _, ws = make_repo_with_ws(tmp_path)
    import shutil as _sh
    _git(repo, "worktree", "remove", "--force", str(ws))   # mô phỏng /tmp bay sau reboot
    monkeypatch.setattr(dmod, "make_workspace",
                        lambda t, repo=None: (str(tmp_path / "re"), "worktree"))
    (tmp_path / "re").mkdir()
    out = deliver.ensure_workspace("t1", str(repo), "ART", "TESTS", workspace=str(ws))
    assert out == str(tmp_path / "re")
    assert (tmp_path / "re" / "solution.py").read_text() == "ART"
    assert (tmp_path / "re" / "test_ticket.py").read_text() == "TESTS"


def test_ensure_workspace_keeps_existing(tmp_path):
    _, _, ws = make_repo_with_ws(tmp_path)
    assert deliver.ensure_workspace("t1", "unused", "X", workspace=str(ws)) == str(ws)
    assert (ws / "solution.py").read_text() != "X"          # không ghi đè file đang có
```

- [ ] **Step 2: Run to verify FAIL** — `no attribute 'infer_path'`

- [ ] **Step 3: Implement** (append vào `deliver.py`)

```python
_PLACER_SOUL = (
    "You are a code-placement assistant. Given the repo file list and a goal, reply with "
    "EXACTLY ONE relative path (ending in .py) for the NEW module, following the repo's "
    "existing layout and naming. Path only — no prose, no backticks."
)


def infer_path(goal: str, repo: str, ask=None) -> Optional[str]:
    """Freeze-time: chốt Deliver: TRƯỚC generation để door hiện được path.
    Reply không validate được -> None (degraded: caller skip delivery + warning)."""
    if ask is None:
        from loopkit.engine import ask_claude as ask      # lazy: tránh vòng import
    tree = subprocess.run(["git", "-C", repo, "ls-files"],
                          capture_output=True, text=True).stdout
    files = "\n".join(tree.splitlines()[:400])
    reply = ask(f"REPO FILES:\n{files}\n\nGOAL:\n{goal}", _PLACER_SOUL,
                model=config.ROLE_MODELS.get("orchestrator"))
    cand = (reply or "").strip().splitlines()[-1].strip().strip("`").strip()
    return cand if validate_path(cand, repo) else None


def ensure_workspace(thread_id: str, repo: str, artifact: str,
                     tests_src: str = "", workspace: str = "") -> str:
    """Resume path (§8.1): worktree /tmp có thể bay sau reboot — dựng lại từ door payload."""
    p = pathlib.Path(workspace) if workspace else None
    if p is None or not p.exists():
        ws, _ = make_workspace(thread_id, repo=repo)
        p = pathlib.Path(ws)
    if not (p / "solution.py").exists():
        (p / "solution.py").write_text(artifact)
    if tests_src and not (p / "test_ticket.py").exists():
        (p / "test_ticket.py").write_text(tests_src)
    return str(p)
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_deliver.py -q` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/loopkit/deliver.py tests/test_deliver.py
git commit -m "deliver: freeze-time path inference + workspace re-materialize from door payload"
```

---

### Task 6: Engine wiring — `Ticket` fields, `run_loop` tail, `finish_suspended`

**Files:**
- Modify: `src/loopkit/engine.py` — `Ticket` (~dòng 60), tail của `run_loop` (~dòng 187-196), `finish_suspended` (~dòng 205)
- Test: `tests/test_deliver.py` (append)

**Interfaces:**
- Consumes: `deliver.ship`, `deliver.ensure_workspace` (import LAZY trong hàm).
- Produces: `Ticket` thêm `deliver: Optional[str] = None`, `repo: str = ""`, `tests_src: str = ""` (default giữ nguyên — mọi caller cũ không đổi). `run_loop` tail gọi ship khi `approved and ticket.risky and ticket.deliver and ticket.repo and ws and config.DELIVER`. `finish_suspended` gọi ship khi `decision and payload["deliver"] and config.DELIVER` (payload keys: `deliver`, `repo`, `tests`, `workspace` — Task 7/8 wire).

- [ ] **Step 1: Write the failing tests** (append — fake toàn bộ brain, không LLM)

```python
from loopkit.engine import Ticket, run_loop, finish_suspended


def _fake_brain(monkeypatch):
    """route -> code; generator trả code block; reviewer trả VERDICT: PASS.
    ENABLE_MEMORY tắt để run_loop không tự tạo Memory ghi .loopkit_memory vào cwd."""
    import loopkit.engine as eng
    replies = iter(["```python\ndef f():\n    return 1\n```", "VERDICT: PASS"])
    monkeypatch.setattr(eng, "route", lambda t, roles: "code")
    monkeypatch.setattr(eng, "ask_claude", lambda p, s, model=None: next(replies))
    monkeypatch.setattr(eng.config, "ENABLE_MEMORY", False)
    return eng


def test_run_loop_ships_after_human_approve(tmp_path, monkeypatch):
    eng = _fake_brain(monkeypatch)
    shipped = {}
    monkeypatch.setattr(dmod, "ship",
                        lambda ws, repo, path, goal, dod, emit=print, record=None:
                        shipped.update(ws=ws, repo=repo, path=path) or
                        {"ok": True, "branch": "feat/x", "mr_url": "u", "error": None})
    t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"), risky=True,
               deliver="pkg/x.py", repo=str(tmp_path))
    res = run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                   journal_dir=str(tmp_path), memory=None,
                   workspace=str(tmp_path))
    assert res["approved"] and shipped["path"] == "pkg/x.py"


def test_run_loop_no_ship_when_not_risky_or_no_deliver(tmp_path, monkeypatch):
    for kwargs in ({"risky": False, "deliver": "pkg/x.py"},   # auto-pass: cấm deliver
                   {"risky": True, "deliver": None}):          # không có path
        eng = _fake_brain(monkeypatch)
        called = []
        monkeypatch.setattr(dmod, "ship",
                            lambda *a, **k: called.append(1) or {"ok": True})
        t = Ticket(goal="g", dod="d", verifier=lambda a: (True, "ok"),
                   repo=str(tmp_path), **kwargs)
        run_loop(t, human_door=lambda a: True, notify=lambda m: None,
                 journal_dir=str(tmp_path), memory=None, workspace=str(tmp_path))
        assert not called


def test_finish_suspended_ships_from_payload(tmp_path, monkeypatch):
    class FakeMem:
        def register(self, *a, **k): ...
        def store(self, *a, **k): ...
    shipped = {}
    monkeypatch.setattr(dmod, "ensure_workspace",
                        lambda th, repo, art, tests_src="", workspace="": str(tmp_path))
    monkeypatch.setattr(dmod, "ship",
                        lambda ws, repo, path, goal, dod, emit=print, record=None:
                        shipped.update(path=path) or
                        {"ok": True, "branch": "b", "mr_url": None, "error": None})
    payload = {"artifact": "A", "goal": "g", "dod": "d", "deliver": "pkg/x.py",
               "repo": str(tmp_path), "tests": "T", "workspace": "/gone"}
    finish_suspended(FakeMem(), "t1", payload, True, lambda m: None)
    assert shipped["path"] == "pkg/x.py"
    shipped.clear()
    finish_suspended(FakeMem(), "t1", payload, False, lambda m: None)  # reject: không ship
    assert not shipped
```

- [ ] **Step 2: Run to verify FAIL** — `TypeError: Ticket.__init__() got an unexpected keyword argument 'deliver'`

- [ ] **Step 3: Modify `engine.py`** — ba mảnh:

`Ticket` (dòng 60-65) thành:

```python
@dataclass
class Ticket:
    goal: str
    dod: str                                    # Definition of Done (the loop's stop condition)
    verifier: Callable[[str], tuple]            # deterministic gate: artifact -> (passed, detail)
    risky: bool = False                         # True -> require human_door before "done"
    deliver: Optional[str] = None               # Deliver: path (chốt lúc freeze) — spec 2026-07-10
    repo: str = ""                              # repo đích (worktree gốc) cho delivery
    tests_src: str = ""                         # frozen tests (cho door payload re-materialize)
```

Tail của `run_loop` — sau `record({"stage": "done", ...})` (dòng 194), TRƯỚC `return`:

```python
            if (approved and ticket.risky and ticket.deliver and ticket.repo
                    and ws and config.DELIVER):
                from loopkit import deliver as _deliver       # lazy: deliver imports engine
                _deliver.ship(str(ws), ticket.repo, ticket.deliver,
                              ticket.goal, ticket.dod, emit=emit, record=record)
```

`finish_suspended` — sau nhánh `if decision:` hiện có (sau dòng 216 `notify(f"📦 artifact:...")`):

```python
        if payload.get("deliver") and config.DELIVER:
            from loopkit import deliver as _deliver           # lazy: tránh vòng import
            ws = _deliver.ensure_workspace(thread_id, payload.get("repo", ""), artifact,
                                           tests_src=payload.get("tests", ""),
                                           workspace=payload.get("workspace", ""))
            _deliver.ship(ws, payload.get("repo", ""), payload["deliver"],
                          payload.get("goal", ""), payload.get("dod", ""), emit=notify)
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_deliver.py tests/test_engine.py -q` → PASS
- [ ] **Step 5: Full suite** — `python3 -m pytest tests -q` → PASS
- [ ] **Step 6: Commit**

```bash
git add src/loopkit/engine.py tests/test_deliver.py
git commit -m "engine: ship after human door (run_loop tail + finish_suspended resume path)"
```

---

### Task 7: CLI front wiring — parse/infer lúc freeze, door hiện path, payload đủ resume

**Files:**
- Modify: `src/loopkit/fronts/cli.py` — `terminal_door` (~22), `_build_verifier` (~37), `cmd_run` (~51), `make_suspend_door` (~176), `cmd_ticket_run` (~185)
- Test: `tests/test_deliver.py` (append; pattern fake giống `tests/test_cli_agent.py`)

**Interfaces:**
- Consumes: `gates.parse_deliver`, `deliver.infer_path`, `Ticket(deliver=, repo=, tests_src=)`.
- Produces: `_build_verifier(...) -> tuple[verifier, frozen_tests_src]` (đổi từ trả 1 giá trị — 2 call sites trong file này cập nhật theo). `make_suspend_door(mem, thread, goal, dod, deliver="", repo="", ws="", tests="")` — payload door thêm 4 keys. `terminal_door(artifact, deliver=None)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from loopkit.fronts import cli as clif


def test_cli_run_passes_deliver_into_ticket(tmp_path, monkeypatch):
    repo, _, _ = make_repo_with_ws(tmp_path)
    seen = {}
    monkeypatch.setattr(clif, "_cwd_repo", lambda: str(repo))
    monkeypatch.setattr(clif, "_build_verifier",
                        lambda mem, g, d, t, wd: (lambda a: (True, "ok"), "TESTS"))
    monkeypatch.setattr(clif, "make_workspace",
                        lambda th, repo=None: (str(tmp_path / "ws2"), "worktree"))
    (tmp_path / "ws2").mkdir(exist_ok=True)
    monkeypatch.setattr(clif, "run_loop",
                        lambda t, **kw: seen.update(ticket=t) or
                        {"ok": True, "approved": True, "worker": "code", "turns": 1})
    monkeypatch.setenv("LOOPKIT_ENABLE_MEMORY", "0")
    import importlib
    importlib.reload(config)
    try:
        clif.cmd_run("goal Deliver: pkg/adder.py DoD: WHEN x SHALL y")
    finally:
        monkeypatch.delenv("LOOPKIT_ENABLE_MEMORY")
        importlib.reload(config)
    t = seen["ticket"]
    assert t.deliver == "pkg/adder.py" and t.repo == str(repo)
    assert t.tests_src == "TESTS"


def test_cli_run_infers_when_missing(tmp_path, monkeypatch, capsys):
    repo, _, _ = make_repo_with_ws(tmp_path)
    monkeypatch.setattr(clif, "_cwd_repo", lambda: str(repo))
    monkeypatch.setattr(clif, "_build_verifier",
                        lambda mem, g, d, t, wd: (lambda a: (True, "ok"), ""))
    monkeypatch.setattr(clif, "make_workspace",
                        lambda th, repo=None: (str(tmp_path / "ws3"), "worktree"))
    (tmp_path / "ws3").mkdir(exist_ok=True)
    monkeypatch.setattr(clif.deliver, "infer_path", lambda g, r: "pkg/adder.py")
    seen = {}
    monkeypatch.setattr(clif, "run_loop",
                        lambda t, **kw: seen.update(ticket=t) or
                        {"ok": True, "approved": True, "worker": "code", "turns": 1})
    monkeypatch.setenv("LOOPKIT_ENABLE_MEMORY", "0")
    import importlib
    importlib.reload(config)
    try:
        clif.cmd_run("goal DoD: WHEN x SHALL y")
    finally:
        monkeypatch.delenv("LOOPKIT_ENABLE_MEMORY")
        importlib.reload(config)
    assert seen["ticket"].deliver == "pkg/adder.py"
    assert "AI đề xuất" in capsys.readouterr().out


def test_suspend_door_payload_carries_delivery(tmp_path):
    opened = {}

    class FakeMem:
        def door_open(self, th, payload):
            opened.update(payload)
    door = clif.make_suspend_door(FakeMem(), "t", "g", "d",
                                  deliver="pkg/x.py", repo="/r", ws="/w", tests="T")
    assert door("ART") is False
    assert (opened["deliver"], opened["repo"], opened["workspace"], opened["tests"]) == \
           ("pkg/x.py", "/r", "/w", "T")
```

- [ ] **Step 2: Run to verify FAIL** — `_build_verifier` trả 1 giá trị / `make_suspend_door` không nhận kwargs mới.

- [ ] **Step 3: Modify `cli.py`** — các mảnh:

Import (dòng 8): thêm `deliver` vào `from loopkit import __version__, config, deliver, gates, refine, shield`.

`terminal_door`:

```python
def terminal_door(artifact: str, deliver: str = None) -> bool:
    print("\n🚪 HUMAN DOOR — artifact chờ duyệt:\n")
    print(_mask((artifact or "")[:2500]))
    if deliver:
        print(f"\n📦 Deliver: {deliver}")        # duyệt = duyệt cả chỗ đặt
    try:
        return input("\nApprove? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:                                 # non-interactive: fail-closed
        return False
```

`_build_verifier` — trả thêm frozen tests (dòng cuối mỗi nhánh):

```python
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
```

Helper mới (đặt trên `cmd_run`) — dùng chung cho `cmd_run` và `cmd_ticket_run`:

```python
def _freeze_deliver(deliver_path, goal, repo):
    """Chốt Deliver: lúc freeze. Token có sẵn > infer > degraded (None + warning)."""
    if not (repo and config.DELIVER):
        return None
    if deliver_path is None:
        deliver_path = deliver.infer_path(goal, repo)
        if deliver_path:
            exists = (pathlib.Path(repo) / deliver_path).exists()
            print(f"📦 Deliver: {deliver_path} (AI đề xuất)"
                  + (" (overwrites existing)" if exists else ""))
        else:
            print("⚠️ Không chốt được Deliver: — sẽ KHÔNG auto-deliver "
                  "(artifact nằm ở worktree).")
        return deliver_path
    if not deliver.validate_path(deliver_path, repo):
        print(f"⚠️ Deliver: {deliver_path} không hợp lệ — sẽ KHÔNG auto-deliver.")
        return None
    exists = (pathlib.Path(repo) / deliver_path).exists()
    print(f"📦 Deliver: {deliver_path}" + (" (overwrites existing)" if exists else ""))
    return deliver_path
```

(thêm `import pathlib` vào đầu file cùng dòng `import argparse, subprocess, time` → `import argparse, pathlib, subprocess, time`)

`cmd_run` — sau `parse_repo`, thay đoạn dựng verifier/ticket/run_loop:

```python
    deliver_path, text = gates.parse_deliver(text)
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
    verifier, frozen_tests = _build_verifier(mem, goal, dod, tests_src, wd)
    deliver_path = _freeze_deliver(deliver_path, goal, repo)     # chốt TRƯỚC generation
    ctx = "" if (repo and config.ENABLE_TOOLS) else read_agents_md(".")
    t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True,
               deliver=deliver_path, repo=repo, tests_src=frozen_tests)
    res = run_loop(t, human_door=lambda a: terminal_door(a, deliver=deliver_path),
                   notify=print, project_context=ctx,
                   memory=mem, thread_id=str(thread), workspace=wd)
```

`make_suspend_door`:

```python
def make_suspend_door(mem, thread, goal, dod, deliver="", repo="", ws="", tests=""):
    """Door không chặn cho agent-mode: persist rồi trả False — approve là lệnh riêng."""
    def door(artifact: str) -> bool:
        mem.door_open(thread, {"channel": "cli", "artifact": artifact,
                               "goal": goal, "dod": dod, "deliver": deliver,
                               "repo": repo, "workspace": ws, "tests": tests})
        return False
    return door
```

`cmd_ticket_run` — tương tự cmd_run: sau `parse_repo` thêm `deliver_path, text = gates.parse_deliver(text)`; `verifier, frozen_tests = _build_verifier(...)`; `deliver_path = _freeze_deliver(deliver_path, goal, repo)`; Ticket thêm 3 fields; door:

```python
    res = run_loop(t, human_door=make_suspend_door(mem, thread, goal, dod,
                                                   deliver=deliver_path or "", repo=repo,
                                                   ws=wd, tests=frozen_tests),
                   notify=print, project_context=ctx, memory=mem,
                   thread_id=str(thread), workspace=wd)
```

và trong output `AWAITING_APPROVAL` thêm 1 dòng sau `ARTIFACT_END`: `if deliver_path: print(f"DELIVER: {deliver_path}")` — Claude-session front relay được path cho người duyệt.

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_deliver.py tests/test_cli.py tests/test_cli_agent.py -q` → PASS
- [ ] **Step 5: Full suite** — `python3 -m pytest tests -q` → PASS
- [ ] **Step 6: Commit**

```bash
git add src/loopkit/fronts/cli.py tests/test_deliver.py
git commit -m "cli front: Deliver frozen before generation, door shows path, resume payload complete"
```

---

### Task 8: Slack front wiring + docs + BUILD-MAP

**Files:**
- Modify: `src/loopkit/fronts/slack.py` — `make_door` (~54), `launch_ticket` (~78-134)
- Modify: `TICKET_TEMPLATE.md`, `skills/loopkit/SKILL.md`, `BUILD-MAP.md`
- Test: py_compile (slack.py không có unit test — import cần token; giữ nguyên hiện trạng repo)

**Interfaces:**
- Consumes: `gates.parse_deliver`, `deliver.infer_path`, `Ticket(deliver=, repo=, tests_src=)`, `make_door(..., deliver="", repo="", ws="", tests="")`.

- [ ] **Step 1: Modify `make_door`** — signature + payload + hiển thị:

```python
def make_door(thread_ts, client, channel, goal, dod, deliver="", repo="", ws="", tests=""):
    def door(artifact: str) -> bool:
        ev = threading.Event(); _pending[thread_ts] = {"event": ev, "approved": False}
        if MEM:                                  # §8.1: persist so a restart can resume it
            MEM.door_open(thread_ts, {"channel": channel, "artifact": artifact,
                                      "goal": goal, "dod": dod, "deliver": deliver,
                                      "repo": repo, "workspace": ws, "tests": tests})
            MEM.register(thread_ts, status="awaiting_approval")
        preview = _guard((artifact or "")[:1500])    # never ask a blind approval
        deliver_line = f"\n📦 Deliver: `{deliver}`" if deliver else ""
        client.chat_postMessage(channel=channel, thread_ts=thread_ts,
            text="Reviewer PASS — approve this change?",
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                     "text": f"Reviewer PASS — artifact chờ duyệt:{deliver_line}\n```{preview}```"}},
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
```

- [ ] **Step 2: Modify `launch_ticket`** — dòng 79 thêm parse; trong `work()` sau khi verifier dựng xong (sau nhánh derive, trước `t = Ticket(...)`), chốt path và giữ frozen tests:

Đầu hàm (sau `repo_name, text = gates.parse_repo(text)`):

```python
    deliver_path, text = gates.parse_deliver(text)
```

Trong `work()` — các nhánh verifier gán thêm `frozen_tests` (`frozen_tests = tests_src` / `= derived` / `= ""` theo nhánh, mirror Task 7), rồi trước `t = Ticket(...)`:

```python
            dpath = deliver_path
            if repo_path and config.DELIVER:
                if dpath is None:
                    dpath = deliver.infer_path(goal, repo_path)
                    if dpath:
                        notify(f"📦 Deliver: `{dpath}` (AI đề xuất)")
                    else:
                        notify("⚠️ Không chốt được Deliver: — sẽ KHÔNG auto-deliver.")
                elif not deliver.validate_path(dpath, repo_path):
                    notify(f"⚠️ Deliver: `{dpath}` không hợp lệ — sẽ KHÔNG auto-deliver.")
                    dpath = None
                else:
                    notify(f"📦 Deliver: `{dpath}`")
            t = Ticket(goal=goal, dod=dod, verifier=verifier, risky=True,
                       deliver=dpath, repo=repo_path or "", tests_src=frozen_tests)
            res = run_loop(t, human_door=make_door(thread, client, channel, goal, dod,
                                                   deliver=dpath or "", repo=repo_path or "",
                                                   ws=wd, tests=frozen_tests),
                           ...)   # phần còn lại giữ nguyên
```

Import đầu file: thêm `deliver` vào dòng `from loopkit import ...` hiện có.

- [ ] **Step 3: Verify compile + suite**

Run: `python3 -m py_compile src/loopkit/fronts/slack.py && python3 -m pytest tests -q`
Expected: compile OK, toàn bộ test PASS.

- [ ] **Step 4: Docs** — 3 file:

`TICKET_TEMPLATE.md`: thêm mục sau phần Tests:

```markdown
## Deliver (tuỳ chọn — thường để AI điền)
`Deliver: <path/to/module.py>` — chỗ đặt file trong repo đích. Thiếu thì loopkit tự đề xuất
lúc freeze (đọc cây repo); path hiện ở door — approve là duyệt cả chỗ đặt.
```

`skills/loopkit/SKILL.md`: trong phần relay door, thêm bullet:

```markdown
- Door có dòng `DELIVER: <path>` → relay path đó cho người duyệt cùng artifact —
  approve nghĩa là duyệt cả chỗ đặt file; sau approve loopkit tự commit/push/tạo MR.
```

`BUILD-MAP.md` — flip row §7 Delivery:

```markdown
| Delivery: MR link sau approve | ✅ | spec 2026-07-10: `Deliver:` AI điền lúc freeze (analyst/`infer_path`), người phủ-quyết tại door; `deliver.ship` sau human door: move→re-gate→`feat/<module>`→push→MR (glab/gh, fallback link từ push output); resume path re-materialize worktree từ door payload; fail = báo rõ + giữ local, không rollback approve. Live E2E còn chờ (GitLab group hết read-only) |
```

- [ ] **Step 5: Commit**

```bash
git add src/loopkit/fronts/slack.py TICKET_TEMPLATE.md skills/loopkit/SKILL.md BUILD-MAP.md
git commit -m "slack front: Deliver wiring + door shows path; docs + BUILD-MAP flip delivery row"
```

---

## Deferred có chủ đích

Analyst đề xuất `Deliver:` ngay trong draft (spec §1, nhánh idea-flow): freeze-time
`infer_path` đã phủ MỌI flow (draft thiếu path → infer trước generation, door vẫn hiện path)
nên nudge prompt trong `refine.py` chỉ là tối ưu — thêm khi live runs cho thấy inference
lúc freeze chọn path kém. Hành vi normative của spec (user không viết path, người thấy path
trước approve) giữ nguyên.

## Live E2E (sau khi merge, ngoài scope plan)

Ticket thật kế tiếp vào `annamgt-streaming-pipeline` khi GitLab group hết read-only — pass khi MR link xuất hiện trong thread mà không ai đụng tay vào git. Push-fail path đã có unit test (pre-receive hook) mô phỏng đúng vụ 403 hôm 2026-07-10.
