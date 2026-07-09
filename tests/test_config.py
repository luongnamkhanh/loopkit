"""T1 verifier — config.py DoD made runnable."""
import importlib
from loopkit import config


def test_defaults():
    importlib.reload(config)
    assert config.MAX_TURNS == 4
    assert config.CLAUDE_TIMEOUT == 180
    assert config.ENABLE_SHIELD is True
    assert config.ENABLE_MEMORY is True
    assert isinstance(config.JOURNAL_DIR, str) and isinstance(config.MEMORY_DIR, str)
    assert isinstance(config.BRAIN_CWD, str)          # neutral brain cwd (no double-inject)
    assert set(config.ROLE_MODELS) == {"orchestrator", "code", "infra", "reviewer", "analyst"}


def test_env_override_int(monkeypatch):
    monkeypatch.setenv("LOOPKIT_MAX_TURNS", "7")
    importlib.reload(config)
    assert config.MAX_TURNS == 7
    monkeypatch.delenv("LOOPKIT_MAX_TURNS")
    importlib.reload(config)
    assert config.MAX_TURNS == 4


def test_env_override_bool(monkeypatch):
    monkeypatch.setenv("LOOPKIT_ENABLE_SHIELD", "false")
    importlib.reload(config)
    assert config.ENABLE_SHIELD is False
    monkeypatch.setenv("LOOPKIT_ENABLE_SHIELD", "1")
    importlib.reload(config)
    assert config.ENABLE_SHIELD is True
    monkeypatch.delenv("LOOPKIT_ENABLE_SHIELD")
    importlib.reload(config)


def test_bad_int_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LOOPKIT_MAX_TURNS", "seven")
    importlib.reload(config)
    assert config.MAX_TURNS == 4
    monkeypatch.delenv("LOOPKIT_MAX_TURNS")
    importlib.reload(config)


def test_repos_allowlist_parsing(monkeypatch):
    import importlib
    monkeypatch.setenv("LOOPKIT_REPOS", "pipeline=/a/b; iac=/c ;;bad;=x;x=")
    monkeypatch.setenv("LOOPKIT_REPOS_PENDING", "iac, ,deploy")
    importlib.reload(config)
    assert config.REPOS == {"pipeline": "/a/b", "iac": "/c"}   # entry hỏng bị bỏ qua
    assert config.REPOS_PENDING == {"iac", "deploy"}
    monkeypatch.delenv("LOOPKIT_REPOS")
    monkeypatch.delenv("LOOPKIT_REPOS_PENDING")
    importlib.reload(config)
    assert config.REPOS == {} and config.REPOS_PENDING == set()
