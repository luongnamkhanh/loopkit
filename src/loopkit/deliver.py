"""
loopkit.deliver — giao artifact vào repo sau HUMAN approve (spec 2026-07-10).

Chuỗi deterministic, KHÔNG LLM lúc giao hàng (path đã chốt lúc freeze):
  place (move solution.py -> Deliver: path, test theo cùng, rewrite import)
  -> re-run gate trên file đã move (import đổi thì phải xanh lại)
  -> branch feat/<module> -> commit (1 dòng từ goal, không attribution) -> push
  -> MR qua glab/gh (detect từ remote URL) | fallback: link create-MR parse từ push output.

Delivery fail KHÔNG rollback approve — báo rõ, giữ branch/file local.
"""
import os, pathlib, re, shutil, subprocess
from typing import Optional
from loopkit import config
from loopkit import shield
from loopkit.workspace import make_workspace


def validate_path(path: str, repo: str) -> bool:
    if not path or not path.endswith(".py"):
        return False
    p = pathlib.PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        return False
    root = pathlib.Path(repo).resolve()
    return (root / path).resolve().is_relative_to(root)  # không dùng startswith: dính repo_evil/symlink


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
        # ponytail: \b-rewrite đổi cả chữ "solution" trong string literal của test —
        # re-gate fail-closed bắt được nếu vỡ; nâng cấp AST rewrite khi có ca thật.
        src = re.sub(r"\bsolution\b", module, tsrc_f.read_text())
        tdst = dst.parent / f"test_{module}.py"
        tdst.write_text(src)
        tsrc_f.unlink()
        r = subprocess.run(["python3", "-m", "pytest", "-q", tdst.name],
                           cwd=dst.parent, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "LOOPKIT_NO_BRAIN": "1"})
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-700:]
    r = subprocess.run(["python3", "-m", "py_compile", str(dst)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr.strip() or "compiles OK (không có test — gate yếu)")[-300:]


_MR_LINK_RE = re.compile(r"https://\S*(?:merge_requests/new|pull/new)\S*")


def _remote_url(workspace: str) -> str:
    r = subprocess.run(["git", "-C", workspace, "remote", "get-url", "origin"],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def create_mr(workspace: str, branch: str, title: str, body: str,
              push_output: str = "", remote_url: Optional[str] = None):
    """-> (mr_url|None, note). Không raise — mọi nhánh fail đều rơi về fallback."""
    tool = config.MR_TOOL
    if tool == "off":
        return None, "MR skipped (LOOPKIT_MR_TOOL=off)"
    if tool != "link":
        use = tool
        try:
            if tool not in ("glab", "gh"):           # auto: chỉ lúc này mới cần remote URL
                url = remote_url if remote_url is not None else _remote_url(workspace)
                use = "gh" if "github.com" in url else "glab"
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
        except (OSError, subprocess.SubprocessError):
            pass                                      # contract: KHÔNG raise — rơi về fallback
    m = _MR_LINK_RE.search(push_output or "")
    if m:
        return m.group(0), "link create-MR từ push output (bấm để tạo)"
    return None, f"tạo MR tay từ branch {branch}"


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
                          capture_output=True, text=True, timeout=30).stdout
    files = "\n".join(tree.splitlines()[:400])
    reply = ask(f"REPO FILES:\n{files}\n\nGOAL:\n{goal}", _PLACER_SOUL,
                model=config.ROLE_MODELS.get("orchestrator"))
    lines = (reply or "").strip().splitlines()
    if not lines:
        return None
    cand = lines[-1].strip().strip("`").strip()
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


def freeze_deliver(deliver_path, goal, repo, emit=print):
    """Chốt Deliver: lúc freeze — helper chung cho mọi front (CLI print / Slack notify).
    Token có sẵn > infer > degraded (None + warning). Guard false (không repo-mode hoặc
    DELIVER=0) -> LUÔN None: door không bao giờ hứa một delivery sẽ không xảy ra."""
    if not (repo and config.DELIVER):
        if deliver_path:
            emit(f"⚠️ Deliver: {deliver_path} bị bỏ qua (không repo-mode hoặc LOOPKIT_DELIVER=0).")
        return None
    if deliver_path is None:
        deliver_path = infer_path(goal, repo)
        if deliver_path:
            exists = (pathlib.Path(repo) / deliver_path).exists()
            emit(f"📦 Deliver: {deliver_path} (AI đề xuất)"
                 + (" (overwrites existing)" if exists else ""))
        else:
            emit("⚠️ Không chốt được Deliver: — sẽ KHÔNG auto-deliver "
                 "(artifact nằm ở worktree).")
        return deliver_path
    if not validate_path(deliver_path, repo):
        emit(f"⚠️ Deliver: {deliver_path} không hợp lệ — sẽ KHÔNG auto-deliver.")
        return None
    exists = (pathlib.Path(repo) / deliver_path).exists()
    emit(f"📦 Deliver: {deliver_path}" + (" (overwrites existing)" if exists else ""))
    return deliver_path


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


def ship(workspace: str, repo: str, deliver_path: str, goal: str, dod: str,
         emit=print, record=lambda e: None) -> dict:
    """Chuỗi giao hàng sau approve. Mỗi bước fail -> emit + journal + DỪNG, không rollback."""
    if not validate_path(deliver_path, repo):
        emit(f"🚫 deliver abort — Deliver path không hợp lệ: {deliver_path}")
        record({"stage": "delivered", "error": "bad_path"})
        return {"ok": False, "branch": None, "mr_url": None, "error": "bad_path"}
    try:
        ok, detail = place_and_verify(workspace, deliver_path)
        record({"stage": "deliver_gate", "ok": ok, "detail": detail[:200]})
        if not ok:
            emit(f"🚫 deliver abort — re-gate FAIL sau move: {detail}")
            return {"ok": False, "branch": None, "mr_url": None, "error": "regate"}
        module = pathlib.Path(deliver_path).stem
        branch = f"feat/{module.replace('_', '-')}"
        guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
        title = guard(goal.splitlines()[0][:72])

        paths = [deliver_path]
        test_rel = str(pathlib.PurePosixPath(deliver_path).parent / f"test_{module}.py")
        if (pathlib.Path(workspace) / test_rel).exists():   # compile-only mode không có test file
            paths.append(test_rel)
        return _git_deliver(workspace, branch, paths, title, guard(dod), emit, record)
    except Exception as e:                       # fail-closed: post-approve tail không được nổ
        emit(f"🚫 deliver abort — exception: {e}")
        record({"stage": "delivered", "error": "exception", "detail": str(e)[:200]})
        return {"ok": False, "branch": None, "mr_url": None, "error": "exception"}


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
    try:
        from loopkit import gates as _gates
        guard = shield.mask if config.ENABLE_SHIELD else (lambda s: s)
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
