"""
loopkit.gates — build a REAL deterministic gate from a ticket (flow-level, build once).

Trust order:
  1. Explicit `Tests:` block in the ticket (human-authored) — highest trust.
  2. Tests DERIVED from the EARS DoD by a fresh LLM call BEFORE any generation turn,
     then FROZEN for the whole loop — the generator can never influence its own gate.
  3. Caller falls back to a compile-only gate (and should say so loudly).

The gate writes each artifact to `solution.py` and runs pytest against the frozen
`test_ticket.py` in an isolated per-ticket workdir. If the generator names things
differently, the gate fails with an ImportError and the feedback loop corrects it.
"""
import ast, os, pathlib, re, subprocess
from loopkit import config
from loopkit.engine import ask_claude, extract_code


def _looks_like_tests(src: str) -> bool:
    """Real tests must PARSE, IMPORT module `solution`, and define >=1 test_* function.
    AST-based (reviewer finding: substring checks let a vacuous always-pass gate through)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    imports_solution = any(
        (isinstance(n, ast.ImportFrom) and n.module == "solution")
        or (isinstance(n, ast.Import) and any(a.name == "solution" for a in n.names))
        for n in ast.walk(tree))
    has_test = any(isinstance(n, ast.FunctionDef) and n.name.startswith("test")
                   for n in ast.walk(tree))
    return imports_solution and has_test

_TESTWRITER_SOUL = (
    "You are a test writer. From the GOAL and the EARS Definition of Done, output ONLY a pytest "
    "file in a ```python fenced block. Import the implementation from the module `solution` "
    "(e.g. `from solution import <name>`). Write one test per EARS criterion — no tests beyond "
    "the DoD, no network or filesystem access, deterministic asserts only."
)


def parse_ticket(text: str):
    """'<goal> DoD: <ears> [Tests: <pytest code>]' -> (goal, dod, tests_or_None).

    DoD is split FIRST; a `Tests:` tail is accepted only if it actually LOOKS like tests
    (parses + imports `solution` + defines test_*). Otherwise it stays part of the DoD —
    so DoD prose containing the word "tests:" no longer truncates the DoD
    (reviewer-reproduced bug)."""
    text = re.sub(r"<@[^>]+>", "", text or "").strip()      # strip the bot mention
    parts = re.split(r"(?i)\bdod:\s*", text, maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return None, None, None
    goal, tail = parts[0].strip(), parts[1].strip()
    dod, tests = tail, None
    tparts = re.split(r"(?i)\btests:\s*", tail, maxsplit=1)
    if len(tparts) == 2 and tparts[1].strip():
        cand = extract_code(tparts[1].strip())
        if _looks_like_tests(cand):
            dod, tests = tparts[0].strip(), cand
    if not dod:
        return None, None, None
    return goal, dod, tests


_REPO_RE = re.compile(r"(?i)\brepo:\s*([\w-]+)\s*")


def parse_repo(text: str, repos: dict = None):
    """'Repo: <name>' ở bất kỳ đâu trong ticket -> (name, text đã strip token); token thắng,
    prose bị bỏ qua. Không có token -> quét text tìm tên khớp WHOLE-WORD với key trong `repos`
    (nếu có) và dùng làm repo đã resolve — substring của từ khác (vd 'core' trong 'score')
    không khớp. Không tên nào khớp -> (None, text nguyên vẹn) để caller fallback TARGET_REPO."""
    m = _REPO_RE.search(text or "")
    if m:
        return m.group(1), (text[:m.start()] + text[m.end():]).strip()
    best = None
    for name in (repos or {}):
        hit = re.search(r"\b" + re.escape(name) + r"\b", text or "")
        if hit and (best is None or hit.start() < best[0]):
            best = (hit.start(), name)
    return (best[1] if best else None), text or ""


_DELIVER_RE = re.compile(r"(?i)\bdeliver:\s*([\w./-]+\.py)\s*")


def parse_deliver(text: str):
    """'Deliver: <path>.py' ở bất kỳ đâu trong ticket -> (path, text đã strip token).
    First match wins; không có token -> (None, text nguyên vẹn)."""
    m = _DELIVER_RE.search(text or "")
    if not m:
        return None, text or ""
    return m.group(1), (text[:m.start()] + text[m.end():]).strip()


_GATE_RE = re.compile(r"(?i)\bgate:\s*([^\n]+)")


def parse_gate_cmd(text: str):
    """'Gate: <lệnh đến hết dòng>' -> (cmd, text đã strip). Không có -> (None, text).
    Chỉ tìm trong phần TRƯỚC 'DoD:' — chữ "gate:" trong văn xuôi DoD không bị nuốt
    (cùng lớp hazard với Tests:, xem _looks_like_tests). Gate: có mặt = edit-in-place mode."""
    text = text or ""
    d = re.search(r"(?i)\bdod:", text)
    scope = text[:d.start()] if d else text
    m = _GATE_RE.search(scope)
    if not m:
        return None, text
    return m.group(1).strip(), (text[:m.start()] + text[m.end():]).strip()


def _gate_env() -> dict:
    """Env TRUNG TÍNH cho mọi gate subprocess: strip config runtime + secrets của loopkit
    (suite repo đích không được phụ thuộc LOOPKIT_*; lệnh gate không được thấy token) và
    PYTHONPATH (repo tự khai trong lệnh nếu cần); LUÔN cấm brain — chống loop lồng nhau."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("LOOPKIT_") or k.startswith("SLACK_") or k == "PYTHONPATH")}
    env["LOOPKIT_NO_BRAIN"] = "1"
    return env


def make_cmd_gate(cmd: str, workdir: str):
    """Domain gate: lệnh shell deterministic trong worktree. Artifact bị bỏ qua —
    trạng thái nằm trong worktree (edit-in-place). Lỗi/timeout -> FAIL, không raise."""
    def verifier(artifact: str):
        try:
            r = subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True,
                               text=True, timeout=300, env=_gate_env())
        except subprocess.TimeoutExpired:
            return False, "gate timeout (300s)"
        except OSError as e:
            return False, f"gate không chạy được: {e}"
        return r.returncode == 0, ((r.stdout + r.stderr).strip() or "(no output)")[-700:]
    return verifier


def derive_tests(goal: str, dod: str, ask=ask_claude):
    """EARS DoD -> frozen pytest source, or None if the reply isn't usable as tests."""
    reply = ask(f"GOAL:\n{goal}\n\nEARS DEFINITION OF DONE:\n{dod}", _TESTWRITER_SOUL,
                model=config.ROLE_MODELS.get("reviewer"))
    src = extract_code(reply)
    if _looks_like_tests(src):
        return src
    # observability (live gap): say WHY derivation failed — visible in the bot terminal
    print(f"[loopkit][derive] validation failed; raw reply tail: {(reply or 'EMPTY')[-200:]!r}")
    return None


def make_pytest_gate(tests_src: str, workdir: str):
    """Freeze the tests once; return verifier(artifact) -> (passed, detail)."""
    wd = pathlib.Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "test_ticket.py").write_text(tests_src)
    def verifier(artifact: str):
        (wd / "solution.py").write_text(artifact)
        r = subprocess.run(["python3", "-m", "pytest", "-q", "test_ticket.py"],
                           cwd=wd, capture_output=True, text=True, timeout=120,
                           env=_gate_env())
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-700:]
    return verifier


def make_compile_gate(workdir: str):
    """Weak fallback: artifact merely has to compile. Callers must WARN when using this."""
    wd = pathlib.Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    def verifier(artifact: str):
        if not (artifact or "").strip():         # reviewer finding: py_compile passes "" —
            return False, "empty artifact"       # tool agent writing nothing must fail closed
        f = wd / "artifact.py"
        f.write_text(artifact)
        r = subprocess.run(["python3", "-m", "py_compile", str(f)],
                           capture_output=True, text=True)
        return r.returncode == 0, (r.stderr.strip() or "compiles OK")[-300:]
    return verifier
