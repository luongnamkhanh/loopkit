"""
loopkit.config — central knobs & feature flags (flow-level, shared).

Every scalar has a default and an env override `LOOPKIT_<NAME>`.
Reference values as `config.X` at call time (not `from config import X`) so tests/ops can
reload this module after changing the environment.
"""
import os


def _env_str(name: str, default: str) -> str:
    return os.environ.get(f"LOOPKIT_{name}", default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[f"LOOPKIT_{name}"])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(f"LOOPKIT_{name}")
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


# --- loop bounds ---
MAX_TURNS = _env_int("MAX_TURNS", 4)
CLAUDE_TIMEOUT = _env_int("CLAUDE_TIMEOUT", 180)      # seconds per brain call

# --- feature flags ---
ENABLE_SHIELD = _env_bool("ENABLE_SHIELD", True)      # mask secrets/PII at boundaries
ENABLE_MEMORY = _env_bool("ENABLE_MEMORY", True)      # registry + session + semantic cache

# --- storage ---
JOURNAL_DIR = _env_str("JOURNAL_DIR", ".")
MEMORY_DIR = _env_str("MEMORY_DIR", ".loopkit_memory")

# --- brain (text mode) ---
# ask_claude runs in a NEUTRAL cwd: repo context reaches it ONLY via the explicit AGENTS.md
# injection (project_context) — one source of context, no double-inject; brain-agnostic.
BRAIN_CWD = _env_str("BRAIN_CWD", "/tmp/loopkit_brain")

# --- agent tool-mode (P3) ---
ENABLE_TOOLS = _env_bool("ENABLE_TOOLS", False)   # off by default: text mode = P2 behavior
AGENT_TIMEOUT = _env_int("AGENT_TIMEOUT", 600)    # tool sessions run much longer than one-shot
TARGET_REPO = _env_str("TARGET_REPO", "")         # git repo for worktree workspaces ("" = standalone)


# --- per-role model tiering (design §1: cheap router, mid workers, strong separate judge) ---
def _env_model(role: str, default: str):
    v = os.environ.get(f"LOOPKIT_MODEL_{role.upper()}", default)
    return v or None                               # set env to "" to fall back to CLI default


ROLE_MODELS = {"orchestrator": _env_model("orchestrator", "haiku"),
               "code": _env_model("code", "sonnet"),
               "infra": _env_model("infra", "sonnet"),
               "reviewer": _env_model("reviewer", "opus")}
