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
