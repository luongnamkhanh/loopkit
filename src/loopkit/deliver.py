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
                           cwd=dst.parent, capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-700:]
    r = subprocess.run(["python3", "-m", "py_compile", str(dst)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr.strip() or "compiles OK (không có test — gate yếu)")[-300:]


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
