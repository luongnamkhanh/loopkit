"""
loopkit.workspace — per-ticket isolated workspaces (flow-level, build once).

Standalone mode (TARGET_REPO unset): a plain per-thread dir under /tmp/loopkit_runs (P2 behavior).
Repo mode (TARGET_REPO = path to a git repo): one git WORKTREE per thread on branch
loop/<thread>, so parallel tickets can edit the same repository without clobbering each other
(design §2②). Worktrees are kept after the run for inspection; prune manually with
`git worktree prune` / `git worktree remove`.
"""
import hashlib, pathlib, subprocess
from typing import Optional
import config
from memory import _safe

RUNS_BASE = pathlib.Path("/tmp/loopkit_runs")
WT_BASE = pathlib.Path("/tmp/loopkit_worktrees")


def _ws_name(thread_id: str) -> str:
    """Collision-proof dir name: if sanitization changed the id ('a/b' and 'a b' both -> 'a_b'),
    suffix a short hash of the raw id so two threads can never share a workspace."""
    name = _safe(thread_id)
    if name != thread_id:
        name += "-" + hashlib.sha1(thread_id.encode()).hexdigest()[:6]
    return name


def make_workspace(thread_id: str, repo: Optional[str] = None):
    """-> (path, kind) with kind in {'dir', 'worktree'}. Idempotent per thread."""
    repo = config.TARGET_REPO if repo is None else repo
    name = _ws_name(str(thread_id))
    if not repo:
        wd = RUNS_BASE / name
        wd.mkdir(parents=True, exist_ok=True)
        return str(wd), "dir"
    wt = WT_BASE / name
    if wt.exists():
        return str(wt), "worktree"
    WT_BASE.mkdir(parents=True, exist_ok=True)
    branch = f"loop/{name}"
    r = subprocess.run(["git", "-C", repo, "worktree", "add", "-b", branch, str(wt)],
                       capture_output=True, text=True)
    if r.returncode != 0:   # branch exists, or a worktree was rm -rf'd without prune:
        subprocess.run(["git", "-C", repo, "worktree", "prune"],       # self-heal stale entries
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", repo, "worktree", "add", str(wt), branch],
                       check=True, capture_output=True, text=True)
    return str(wt), "worktree"
