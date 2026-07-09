# Repo: Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ticket chọn repo đích bằng field `Repo: <name>` từ allowlist config — một bot phục vụ nhiều repo, không sửa env, không restart.

**Architecture:** Allowlist `LOOPKIT_REPOS` (name→path) + `LOOPKIT_REPOS_PENDING` trong `config.py`; `gates.parse_repo` tách token khỏi ticket TRƯỚC `parse_ticket` (signature parse_ticket không đổi); routing fail-closed trong `launch_ticket` (unknown/pending từ chối trước mọi LLM call); analyst nhận danh sách repo để hỏi và điền vào draft. Spec: `docs/superpowers/specs/2026-07-09-repo-routing-design.md`.

**Tech Stack:** Python 3 stdlib. Không dependency mới.

## Global Constraints

- Tests xanh trước mọi claim: `python3 -m pytest tests -q`. Baseline hiện tại: **54 passed**.
- Backward compat tuyệt đối: ticket không có `Repo:` → hành vi `TARGET_REPO` hiện tại, `TARGET_REPO` rỗng → standalone tmp-dir.
- Fail-closed: LLM không bao giờ quyết path; tên ngoài allowlist / pending → từ chối với 0 LLM call.
- Không print/commit `SLACK_*_TOKEN`; không AI attribution trong git.
- `ws_key = f"{repo_name}-{thread}"` khi có tên repo (worktree idempotent theo key — đổi repo trong cùng thread không được dính worktree cũ).

---

### Task 1: parsers — `config.REPOS`/`REPOS_PENDING` + `gates.parse_repo` (TDD)

**Files:**
- Modify: `config.py` (thêm sau block agent tool-mode, trước block idea refinement)
- Modify: `gates.py` (thêm sau `parse_ticket`)
- Test: `tests/test_config.py` (append), `tests/test_gates.py` (append; đảm bảo đầu file có `import gates`)

**Interfaces:**
- Produces: `config.REPOS: dict[str, str]`, `config.REPOS_PENDING: set[str]` (reload-friendly như mọi knob); `gates.parse_repo(text: str) -> tuple[str | None, str]` — (tên hoặc None, text đã strip token, first match wins). Task 2 và 3 gọi đúng các tên này.

- [ ] **Step 1: Failing tests.** Append vào `tests/test_config.py`:

```python
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
```

Append vào `tests/test_gates.py`:

```python
def test_parse_repo_extracts_and_strips():
    name, rest = gates.parse_repo("Repo: iac tạo repo mới DoD: WHEN x SHALL y")
    assert name == "iac" and rest == "tạo repo mới DoD: WHEN x SHALL y"


def test_parse_repo_case_insensitive_and_hyphen():
    name, rest = gates.parse_repo("làm X repo: data-deploy DoD: y")
    assert name == "data-deploy" and "repo:" not in rest.lower()
    assert rest == "làm X DoD: y"


def test_parse_repo_absent():
    name, rest = gates.parse_repo("làm X DoD: y")
    assert name is None and rest == "làm X DoD: y"
```

- [ ] **Step 2: Chạy fail.** `python3 -m pytest tests/test_config.py tests/test_gates.py -q` — Expected: 4 test mới FAIL (`AttributeError: ... 'REPOS'` / `'parse_repo'`).

- [ ] **Step 3: Implement.** `config.py`, thêm sau dòng `TARGET_REPO = ...`:

```python
# --- multi-repo allowlist (Repo: routing) ---
def _parse_repos(raw: str) -> dict:
    out = {}
    for entry in (raw or "").split(";"):
        name, sep, path = entry.strip().partition("=")
        if sep and name.strip() and path.strip():
            out[name.strip()] = path.strip()
    return out


REPOS = _parse_repos(_env_str("REPOS", ""))                    # name -> repo path
REPOS_PENDING = {s.strip() for s in _env_str("REPOS_PENDING", "").split(",") if s.strip()}
```

`gates.py`, thêm sau `parse_ticket` (module đã có `import re`):

```python
_REPO_RE = re.compile(r"(?i)\brepo:\s*([\w-]+)\s*")


def parse_repo(text: str):
    """'Repo: <name>' ở bất kỳ đâu trong ticket -> (name, text đã strip token).
    First match wins; không có token -> (None, text nguyên vẹn)."""
    m = _REPO_RE.search(text or "")
    if not m:
        return None, text or ""
    return m.group(1), (text[:m.start()] + text[m.end():]).strip()
```

- [ ] **Step 4: Chạy pass.** `python3 -m pytest tests -q` — Expected: **58 passed**.

- [ ] **Step 5: Commit.**

```bash
git add config.py gates.py tests/test_config.py tests/test_gates.py
git commit -m "repo routing: allowlist config + Repo: token parser"
```

---

### Task 2: `refine.py` — analyst biết danh sách repo, draft gate check tên (TDD)

**Files:**
- Modify: `refine.py` (signature `refine_turn` + `_valid_draft`)
- Test: `tests/test_refine.py` (append)

**Interfaces:**
- Consumes: `gates.parse_repo` (Task 1).
- Produces: `refine_turn(idea, history, turns_used, max_turns, repos=None, ask=ask_claude)` — `repos = {"active": list[str], "pending": list[str]} | None`; `None` → hành vi cũ nguyên vẹn (8 test hiện có không đổi). Task 3 gọi đúng signature này.

- [ ] **Step 1: Failing tests.** Append vào `tests/test_refine.py`:

```python
def test_repos_listed_in_prompt():
    seen = {}
    def fake(p, s, model=None):
        seen["p"] = p
        return "QUESTION: repo nào?"
    refine.refine_turn("idea", [], 0, 5,
                       repos={"active": ["pipeline", "loopkit"], "pending": ["iac"]}, ask=fake)
    assert "pipeline" in seen["p"] and "iac" in seen["p"]


def test_draft_with_unknown_repo_retries_then_ok():
    calls = []
    def fake(p, s, model=None):
        calls.append(p)
        if len(calls) == 1:
            return "TICKET: Repo: sai-ten " + VALID_TICKET
        return "TICKET: Repo: pipeline " + VALID_TICKET
    kind, text = refine.refine_turn("idea", [], 0, 5,
                                    repos={"active": ["pipeline"], "pending": []}, ask=fake)
    assert kind == "draft" and len(calls) == 2                 # 1 lần fail gate vì tên sai
    assert gates.parse_repo(text)[0] == "pipeline"


def test_draft_without_repo_still_valid():
    kind, _ = refine.refine_turn("idea", [], 0, 5,
                                 repos={"active": ["pipeline"], "pending": []},
                                 ask=lambda p, s, model=None: "TICKET: " + VALID_TICKET)
    assert kind == "draft"                                     # không Repo: -> TARGET_REPO default
```

- [ ] **Step 2: Chạy fail.** `python3 -m pytest tests/test_refine.py -q` — Expected: 3 test mới FAIL (`TypeError: refine_turn() got an unexpected keyword argument 'repos'`).

- [ ] **Step 3: Implement.** Trong `refine.py`, thay `_valid_draft` và `refine_turn` bằng:

```python
def _valid_draft(draft: str, repos=None) -> bool:
    name, rest = gates.parse_repo(draft)
    if repos is not None and name is not None and name not in repos.get("active", []):
        return False                                  # tên ngoài allowlist = fail gate
    goal, dod, tests = gates.parse_ticket(rest)
    return bool(goal and dod and tests)


def refine_turn(idea, history, turns_used, max_turns, repos=None, ask=ask_claude):
    """Một lượt analyst. history = [{'role': 'analyst'|'user', 'text': ...}, ...].
    repos = {'active': [...], 'pending': [...]} | None (None -> không nhắc repo).
    -> ('ask', q) | ('draft', ticket) | ('draft_unvalidated', ticket) | ('error', '')."""
    soul = roles.REGISTRY["analyst"].soul
    model = config.ROLE_MODELS.get("analyst")
    convo = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    forced = turns_used >= max_turns
    repo_ctx = ""
    if repos:
        repo_ctx = ("\nAVAILABLE REPOS — the ticket SHOULD include 'Repo: <name>':\n"
                    f"  active: {', '.join(repos.get('active', [])) or '(none)'}\n"
                    f"  pending (registered, NOT usable yet — never pick these): "
                    f"{', '.join(repos.get('pending', [])) or '(none)'}\n")
    prompt = (f"RAW IDEA:\n{idea}\n\nCONVERSATION SO FAR:\n{convo or '(none)'}\n{repo_ctx}\n"
              + ("QUESTION BUDGET EXHAUSTED: output the TICKET now; state assumptions in the goal."
                 if forced else f"Questions used: {turns_used}/{max_turns}."))
    reply = ask(prompt, soul, model=model)
    if not (reply or "").strip():
        reply = ask(prompt, soul, model=model)        # brain trả rỗng: retry đúng 1 lần
        if not (reply or "").strip():
            return "error", ""
    kind, text = _parse_reply(reply)
    if kind == "ask":
        if not forced:
            return "ask", text
        kind, text = _parse_reply(ask(prompt + "\n\nOutput ONLY the TICKET now.",
                                      soul, model=model))
        if kind == "ask":
            return "draft_unvalidated", text          # ponytail: đưa human cái đang có
    for _ in range(2):                                # gate deterministic + bounded retry
        if _valid_draft(text, repos):
            return "draft", text
        _, text = _parse_reply(ask(
            f"{prompt}\n\nYour draft FAILED the format gate. Required: '<goal> DoD: <EARS "
            f"criteria> Tests: ```python ...```' — tests import from `solution`, define test_* "
            f"functions; if a 'Repo:' name is present it MUST be one of the active repos. "
            f"Output the corrected TICKET.\n\nPREVIOUS DRAFT:\n{text}",
            soul, model=model))
    return ("draft", text) if _valid_draft(text, repos) else ("draft_unvalidated", text)
```

- [ ] **Step 4: Chạy pass.** `python3 -m pytest tests -q` — Expected: **61 passed** (11 test refine: 8 cũ nguyên vẹn + 3 mới).

- [ ] **Step 5: Commit.**

```bash
git add refine.py tests/test_refine.py
git commit -m "refine: analyst knows the repo allowlist, draft gate checks Repo: name"
```

---

### Task 3: wire `slack_app.py` + BUILD-MAP + rollout + live E2E

**Files:**
- Modify: `slack_app.py` (xoá `EFFECTIVE_CTX` global lines ~43-45; `launch_ticket`; `_refine_step`)
- Modify: `BUILD-MAP.md` (§7 thêm row)
- Rollout: `~/.loopkit.env`, `git init` analytics-agent, restart bot.

**Interfaces:**
- Consumes: `gates.parse_repo`, `config.REPOS`/`REPOS_PENDING` (Task 1), `refine_turn(..., repos=)` (Task 2), `make_workspace(ws_key, repo=path)` (sẵn có từ P3).

- [ ] **Step 1: Routing trong `launch_ticket`.** Thay phần đầu hàm (đến trước message "Nhận ticket") bằng:

```python
def launch_ticket(client, channel, thread, text, prev_artifact=None) -> bool:
    repo_name, text = gates.parse_repo(text)
    goal, dod, tests_src = gates.parse_ticket(text)
    if not dod:
        return False
    if repo_name and repo_name not in config.REPOS:            # fail-closed: allowlist quyết
        client.chat_postMessage(channel=channel, thread_ts=thread,
            text=f"🙅 Repo `{repo_name}` không có trong allowlist. "
                 f"Hợp lệ: {', '.join(sorted(config.REPOS)) or '(trống)'}")
        return True                                            # đã xử lý — không rơi vào refinement
    if repo_name in config.REPOS_PENDING:
        client.chat_postMessage(channel=channel, thread_ts=thread,
            text=f"⏳ Repo `{repo_name}` đã đăng ký nhưng CHƯA có gate phù hợp "
                 "(terraform/helm) — chờ domain gate.")
        return True
    repo_path = config.REPOS.get(repo_name) if repo_name else config.TARGET_REPO
```

Trong `def work():` sửa 2 chỗ: dòng `wd, kind = make_workspace(str(thread))` thành

```python
            ws_key = f"{repo_name}-{thread}" if repo_name else str(thread)
            wd, kind = make_workspace(ws_key, repo=repo_path)
```

và dòng `project_context=EFFECTIVE_CTX,` trong lời gọi `run_loop` thành `project_context=("" if (repo_path and config.ENABLE_TOOLS) else PROJECT_CTX),`. Xoá block `EFFECTIVE_CTX = ...` (3 dòng comment + gán, lines ~43-45) — không còn ai dùng.

- [ ] **Step 2: `_refine_step` truyền repos.** Dòng gọi `refine.refine_turn(...)` thành:

```python
        repos_info = ({"active": sorted(n for n in config.REPOS if n not in config.REPOS_PENDING),
                       "pending": sorted(config.REPOS_PENDING)} if config.REPOS else None)
        kind, text = refine.refine_turn(run.get("idea", ""), history, turns,
                                        config.REFINE_MAX_TURNS, repos=repos_info)
```

- [ ] **Step 3: Compile + suite.** `python3 -m py_compile slack_app.py && python3 -m pytest tests -q` — Expected: **61 passed**.

- [ ] **Step 4: BUILD-MAP.** §7, thêm sau row "Idea-refinement intake":

```markdown
| `Repo:` routing đa repo | ✅ | spec 2026-07-09: allowlist `LOOPKIT_REPOS` (unknown → từ chối kèm danh sách; `REPOS_PENDING` → từ chối "chờ domain gate" — cả hai TRƯỚC mọi LLM call); ws_key=`<repo>-<thread>` (đổi repo giữa thread không dính worktree cũ); context per-ticket (repo-mode đọc AGENTS.md repo đích); analyst nhận danh sách repo để hỏi & điền; không `Repo:` → `TARGET_REPO` (compat) |
```

- [ ] **Step 5: Commit.**

```bash
git add slack_app.py BUILD-MAP.md
git commit -m "slack: per-ticket Repo: routing over the allowlist"
```

- [ ] **Step 6: Rollout.** (a) Append vào `~/.loopkit.env` (đường dẫn thật):

```bash
export LOOPKIT_REPOS="pipeline=/Users/khanhluong/code/annamgt/annamgt-streaming-pipeline;analytics=/Users/khanhluong/code/annamgt/analytics-agent;loopkit=/Users/khanhluong/code/agents/loopkit;iac=/Users/khanhluong/code/annamgt/annamgt-iac;streaming-deploy=/Users/khanhluong/code/annamgt/annamgt-streaming-deployments;data-deploy=/Users/khanhluong/code/annamgt/annamgt-data-deployments"
export LOOPKIT_REPOS_PENDING="iac,streaming-deploy,data-deploy"
```

(b) `analytics-agent`: `git init -b main` + AGENTS.md ngắn (dự án phase-2 SQL agent: Python, pytest, stdlib-first, module tự chứa) + `.gitignore` (`keys/`, `__pycache__/`, `.DS_Store`) + commit đầu — worktree cần ≥1 commit. **`keys/` PHẢI vào .gitignore trước commit đầu.** (c) Restart bot.

- [ ] **Step 7: Live E2E (3 kịch bản từ Slack).**

1. `@bot Repo: iac tạo repo test DoD: WHEN x SHALL y` → nhận `⏳ ... chờ domain gate`, không có LLM call nào (registry không có run mới).
2. `@bot Repo: sai-ten làm gì đó DoD: WHEN x SHALL y` → nhận `🙅 ... Hợp lệ: analytics, data-deploy, iac, loopkit, pipeline, streaming-deploy`.
3. Dogfood: `@bot Repo: loopkit viết hàm luhn_check(s) kiểm tra số thẻ hợp lệ theo Luhn DoD: WHEN "4532015112830366" SHALL return True; WHEN "1234567812345678" SHALL return False; WHEN chuỗi có ký tự không phải số SHALL return False` → chạy trọn vòng: worktree của loopkit (`git -C .../loopkit worktree list` có `loop/loopkit-<thread>`), gate pass, reviewer, door, Approve → artifact + registry done.
4. Ticket không `Repo:` → vẫn vào pipeline (TARGET_REPO) như cũ.

---

## Self-review (done at write time)

- **Spec coverage:** config 2 env (T1), parse_repo tách trước parse_ticket không đổi signature (T1), routing 3 nhánh fail-closed 0-LLM (T3 S1 — return True để không rơi vào refinement), ws_key repo-thread (T3 S1 + constraint), context per-ticket thay EFFECTIVE_CTX (T3 S1), analyst repos + draft gate tên (T2), rollout env + git init analytics (T3 S6, kèm chốt `keys/` vào .gitignore), E2E 3 kịch bản + compat (T3 S7).
- **Placeholders:** không có.
- **Type consistency:** `parse_repo -> (str|None, str)` dùng thống nhất T1/T2/T3; `refine_turn(..., repos=None, ask=)` khớp T2 def ↔ T3 call; `REPOS: dict`/`REPOS_PENDING: set` khớp T1 def ↔ T2 test ↔ T3 dùng; `make_workspace(ws_key, repo=repo_path)` khớp signature P3 hiện có.
