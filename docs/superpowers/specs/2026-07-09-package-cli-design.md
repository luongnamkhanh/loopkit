# Phase 1 — Core package + CLI front (productionize nền móng)

**Date:** 2026-07-09 · **Status:** approved (design) · **Vision:** loopkit thành product
portable — cài được ở bất kỳ máy/repo/Claude session nào; Slack chỉ còn là một front.
Roadmap 4 phase đã chốt: **P1 package+CLI (spec này)** → P2 per-repo config (`.loopkit.*`,
cascade kiểu aider) → P3 roles-as-data + MCP + recipes (kiểu CrewAI/Goose) → P4 server/SaaS.

**Research đã fold vào:** aider (pipx/uv tool, config cascade, test-cmd), Goose (MCP
extensions, recipes, local-first cho regulated), CrewAI (agents.yaml), packaging.python.org
(src layout, `[project.scripts]`, entry-points plugin).

## Problem

Loop engine đã Slack-free (engine/gates/refine/memory/shield/workspace) nhưng bị chôn trong
một thư mục flat + một front duy nhất (slack_app) config lằng nhằng. Muốn dùng ở repo khác /
máy khác / Claude session khác phải copy thư mục + env. Cần đóng gói chuẩn + CLI front.

## Design

### 1. Cấu trúc package (src layout)

```
loopkit/                       # repo root (giữ nguyên repo GitHub hiện tại)
├── pyproject.toml
├── src/loopkit/
│   ├── __init__.py            # __version__
│   ├── config.py  engine.py  gates.py  refine.py
│   ├── memory.py  shield.py  workspace.py  roles.py
│   └── fronts/
│       ├── __init__.py
│       ├── cli.py             # MỚI — front terminal
│       └── slack.py           # slack_app.py chuyển vào, bọc main()
├── tests/                     # đổi import: `from loopkit import ...`
└── docs/ AGENTS.md CLAUDE.md BUILD-MAP.md ...
```

- Import nội bộ: tuyệt đối `from loopkit import config` (module lẫn nhau); KHÔNG còn
  `import config` trần — hết ô nhiễm namespace site-packages.
- Diff rộng nhưng thuần cơ học; **gate của restructure = 61 test hiện có phải xanh** sau khi
  đổi import (không sửa logic nào trong bước chuyển).

### 2. pyproject.toml

- `[build-system]` hatchling; `[project]` name `loopkit`, version `0.1.0`,
  `requires-python >= 3.10`; **dependencies core = RỖNG** (engine thuần stdlib — điểm mạnh
  giữ nguyên).
- `[project.optional-dependencies] slack = ["slack-bolt", "slack-sdk"]` — CLI không kéo
  Slack deps; front slack import-guard, thiếu deps → lỗi tử tế "pip install 'loopkit[slack]'".
- `[project.scripts]`: `loopkit = "loopkit.fronts.cli:main"` ·
  `loopkit-slack = "loopkit.fronts.slack:main"`.
- Ghi chú reserved (chưa implement — P3): `[project.entry-points."loopkit.plugins"]` cho
  gate/role bên thứ ba, pattern pytest.
- Phân phối: `uv tool install git+https://github.com/luongnamkhanh/loopkit` (pipx tương
  đương); dev: `pip install -e ".[slack]"`.

### 3. CLI front (`fronts/cli.py`) — v1 ba lệnh

Nguyên tắc: **cwd = repo đích** (giết hẳn nỗi đau config): cwd là git repo → worktree per
ticket (branch `loop/<ts>` như Slack); không phải git repo → workspace tmp-dir. Memory/journal
per-repo: `.loopkit_memory/` + `run_journal.jsonl` trong cwd (hành vi hiện tại giữ nguyên).

- `loopkit run "<goal> DoD: ... [Tests: ...]"` — chạy trọn vòng: parse → recall → freeze gate
  → route → generate → gate → reviewer → **door = terminal**: in artifact (mask) + prompt
  `Approve? [y/N]` → y: register done+approved, cache, in artifact; n: done không approve.
  `Repo:` token nếu có bị strip + warning "CLI dùng cwd" (allowlist là chuyện của front slack).
- `loopkit idea "<ý tưởng thô>"` — refinement Q&A ngay terminal: analyst hỏi từng câu
  (`input()`), đủ thì in `🎫 draft` → `[y] run / [e] góp ý thêm / [n] huỷ`; `e` → nhập
  feedback, analyst redraft (budget `REFINE_MAX_TURNS` như Slack). Sau `y` → chạy như
  `loopkit run` cùng thread.
- `loopkit status` — bảng registry của cwd: thread, status, goal (cắt ngắn), updated_at.
- `thread_id` cho CLI: `cli-<epoch-ms>`. Exit code: 0 = done+approved hoặc lệnh thành công;
  1 = exhausted/lỗi; 130 = user huỷ. Mọi output qua `shield.mask` như Slack.
- Env `LOOPKIT_*` giữ nguyên nghĩa; `TARGET_REPO`/`REPOS*` bị CLI bỏ qua (cwd thắng) — vẫn
  là config của front slack.

### 4. Front Slack — không đổi hành vi

`slack_app.py` → `src/loopkit/fronts/slack.py`, phần `__main__` thành `main()`; `run.sh`
gọi `loopkit-slack` (hoặc `python -m loopkit.fronts.slack`). Mọi behavior giữ nguyên —
E2E Slack sau restructure là một phần acceptance.

## Verification

- **Gate restructure:** toàn bộ 61 test hiện có xanh sau khi đổi import (không đổi logic).
- **Unit CLI mới:** parse args 3 lệnh; door terminal (monkeypatch input) y/N/EOF;
  `idea` loop với fake brain (ask → draft → y chạy tiếp / e redraft / n huỷ); `Repo:` token
  bị strip + warning; exit codes.
- **Acceptance:**
  1. `pip install -e ".[slack]"` → `loopkit --version` chạy từ MỘT REPO KHÁC (vd
     annamgt-streaming-pipeline): `loopkit run "viết hàm ... DoD: ... Tests: ..."` → worktree
     + gate + reviewer + door y/N → artifact.
  2. `loopkit idea "..."` trọn vòng terminal.
  3. `uv tool install` từ git URL → binary trên PATH, chạy ở repo bất kỳ.
  4. Slack E2E một ticket sau restructure — hành vi y hệt trước.

## Out of scope (YAGNI — thuộc phase sau)

`.loopkit.toml/yml` per-repo + cascade (P2) · roles-as-data YAML + MCP pass-through +
recipes/flow (P3) · server/API/multi-tenant (P4) · publish PyPI công khai · TUI/màu mè
(rich/textual) — v1 là plain text sạch sẽ.
