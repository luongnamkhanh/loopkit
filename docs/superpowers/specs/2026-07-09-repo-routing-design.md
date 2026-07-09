# Repo: routing — per-ticket target repo từ allowlist

**Date:** 2026-07-09 · **Status:** approved (design) · **Target:** một bot phục vụ nhiều repo
cùng lúc, chọn repo ngay trong ticket từ Slack — không sửa env, không restart.

**Locked decision:** đăng ký CẢ 6 repo (pipeline, analytics, loopkit, iac, streaming-deploy,
data-deploy); 3 repo iac/deployments ở trạng thái **pending-gate** — nhận tên nhưng từ chối
ticket tại intake cho tới khi có domain gate (terraform/helm).

## Problem

`LOOPKIT_TARGET_REPO` là biến global: đổi repo = sửa env + restart. Industry giải bằng intake
(issue nằm trong repo); phiên bản Slack-first của loopkit cần field `Repo:` trong ticket.
Fail-closed: LLM không bao giờ quyết định path — allowlist trong config quyết.

## Design

### 1. Config (`config.py`)

- `LOOPKIT_REPOS` — chuỗi `name=path;name=path;...` → `REPOS: dict[str, str]` (parse bỏ qua
  entry rỗng/không có `=`; strip spaces). Default `""` → dict rỗng.
- `LOOPKIT_REPOS_PENDING` — chuỗi `name,name,...` → `REPOS_PENDING: set[str]` (tên đã đăng ký
  nhưng chưa có gate phù hợp). Default `""` → set rỗng.
- Backward compat tuyệt đối: ticket không có `Repo:` → dùng `TARGET_REPO` như hiện tại;
  `TARGET_REPO` rỗng → workspace tmp-dir standalone. Hành vi hiện có không đổi một ly.

### 2. Parse (`gates.py`)

`parse_repo(text) -> (name | None, text_stripped)`:
- Regex `(?i)\brepo:\s*([\w-]+)`, lấy match ĐẦU TIÊN, strip token khỏi text.
- Trả text đã sạch để `parse_ticket` chạy tiếp như cũ — KHÔNG đổi signature `parse_ticket`
  (tránh churn callers + tests hiện có).

### 3. Routing (`slack_app.launch_ticket`) — deterministic, fail-closed, 0 LLM call khi từ chối

Thứ tự sau `parse_repo`:
1. Có tên nhưng **không thuộc** `REPOS` → post từ chối kèm danh sách tên hợp lệ; return False.
2. Tên thuộc `REPOS_PENDING` → post "repo đã đăng ký nhưng chưa có gate cho loại này
   (terraform/helm) — chờ domain gate"; return False.
3. Tên hợp lệ → `repo_path = REPOS[name]`; không có tên → `repo_path = config.TARGET_REPO`
   (có thể rỗng → standalone).
4. `make_workspace(ws_key, repo=repo_path)` với **`ws_key = f"{name}-{thread}"`** khi có tên
   (thread follow-up đổi `Repo:` không được dính worktree repo cũ — `make_workspace` idempotent
   theo ws_key nên key phải chứa repo).
5. Context per-ticket: chuyển logic `EFFECTIVE_CTX` global vào trong `launch_ticket` —
   `project_context = "" if (repo_path and config.ENABLE_TOOLS) else PROJECT_CTX` (repo mode
   đọc AGENTS.md của repo đó natively; single source of context giữ nguyên nguyên tắc).

### 4. Refinement (`refine.py`)

- Prompt `refine_turn` nhận thêm danh sách repo khả dụng (tên active + tên pending đánh dấu
  rõ) từ caller — analyst hỏi "repo nào?" khi chưa rõ (một trong ≤`REFINE_MAX_TURNS` câu) và
  điền `Repo: <name>` vào TICKET draft. Signature mới:
  `refine_turn(idea, history, turns_used, max_turns, repos=None, ask=ask_claude)` —
  `repos = {"active": [...], "pending": [...]}`; `None` → prompt không nhắc repo (compat với
  tests cũ).
- Draft gate mở rộng: draft có `Repo:` mà tên không thuộc active-allowlist → tính là gate
  fail → retry với feedback (tối đa 2, như lỗi format khác).

### 5. Rollout (config, không phải code)

- `~/.loopkit.env`: thêm `LOOPKIT_REPOS` (đủ 6 entry) + `LOOPKIT_REPOS_PENDING=iac,streaming-deploy,data-deploy`.
- `analytics-agent`: `git init` + commit đầu + AGENTS.md (không có commit thì worktree fail).
- Restart bot.

## Verification

- **Unit:** `parse_repo` (có/không token, case-insensitive, strip đúng, tên có gạch ngang);
  config parse `REPOS`/`REPOS_PENDING` (rỗng, entry hỏng, env override); routing 3 nhánh
  unknown/pending/ok (fake client, assert message + return False/True); `ws_key` chứa tên repo;
  `refine_turn` với `repos` — prompt chứa danh sách, draft `Repo:` sai tên → retry.
- **Live E2E:** (1) ticket `Repo: loopkit` dogfood (ví dụ: thêm mask pattern cho shield) →
  chạy trọn vòng đến door; (2) ticket `Repo: iac` → nhận lời từ chối pending-gate; (3) ticket
  không `Repo:` → hành vi cũ (pipeline qua TARGET_REPO).

## Out of scope (YAGNI)

Domain gates terraform/helm (khi build sẽ rút tên khỏi `REPOS_PENDING`) · diff artifact ·
GitLab-issue intake · MR delivery automation (row ❗ riêng trong BUILD-MAP) · per-repo model
tiering hay per-repo config khác (một allowlist là đủ).
