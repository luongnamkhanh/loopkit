# Front #3 — Claude session lái loopkit (P2 mới của roadmap product)

**Date:** 2026-07-09 · **Status:** approved (design) · **Target:** bất kỳ Claude Code session
nào, ở bất kỳ repo nào, lái được trọn vòng loopkit qua bash — không cần Slack, không cần
terminal tương tác. Roadmap reorder theo yêu cầu: **P2 = front này**; per-repo config lùi P3,
roles/MCP P4, server/SaaS P5.

**Locked decisions:** (1) Claude tự trả lời câu hỏi refinement khi context hội thoại đã có
câu trả lời và PHẢI khai báo đã trả lời gì; câu chưa biết → relay người dùng; (2) **door
duyệt cuối LUÔN là người** — Claude chỉ relay, không bao giờ tự `approve`.

## Problem

`loopkit idea/run` hiện tương tác qua `input()` — một agent lái qua Bash tool (non-interactive)
không dùng được. Cần bộ verbs mỗi-lệnh-một-bước, state sống trên disk giữa các lần gọi —
đúng nền móng đã có: refinement stateless-từ-disk + durable doors + `finish_suspended` (§8.1).

## Design

### 1. CLI verbs mới (`fronts/cli.py`) — non-interactive, marker-parseable

| Lệnh | Hành vi | Output (dòng marker) |
|---|---|---|
| `loopkit idea start "<ý tưởng>"` | tạo thread `cli-<ms>`, register `refining`, một lượt analyst | `THREAD: <id>` rồi `QUESTION: <q>` hoặc thẳng `DRAFT: <ticket>` |
| `loopkit idea answer <thread> "<trả lời>"` | append user event + một lượt analyst (state đọc từ disk — y hệt `_refine_step` của Slack) | `QUESTION: <q>` \| `DRAFT: <ticket>` (kèm `DRAFT_UNVALIDATED` warning nếu tests hỏng) |
| `loopkit ticket run <thread>` | chạy loop cho draft đã có (registry `ticket_drafted`) với **suspend door** | log các bước rồi `AWAITING_APPROVAL` + artifact preview; exhausted → `FAILED: <reason>`, exit 1 |
| `loopkit approve <thread>` / `loopkit reject <thread>` | `finish_suspended` từ door trên disk (nguyên hàm §8.1) + `door_close` | `APPROVED` + artifact / `REJECTED`; door không tồn tại → `STALE`, exit 1 |
| `loopkit show <thread>` | status tổng hợp: registry + doors.json + draft/artifact | `STATUS: <...>` (+ `DRAFT:`/`ARTIFACT:` nếu có) |

- **Suspend door** = door factory mới trong cli.py (KHÔNG sửa engine): nhận goal/dod qua
  closure như `make_door` của Slack; khi được gọi: `door_open(thread, {channel: "cli",
  artifact, goal, dod})` + return `False` (không block). Kỹ thuật cần ghi thẳng: sau đó
  `run_loop` register `done, approved=False` TRONG KHI door còn mở trên disk — trạng thái
  thật là "awaiting"; `loopkit show` đọc doors.json để báo đúng; `approve` → `finish_suspended`
  flip `approved=True` + cache + in artifact. Chấp nhận độ lệch status này, không bẻ engine.
- `idea answer` với thread ở `ticket_drafted` = góp ý → về `refining` + redraft (như Slack).
- Marker in ở ĐẦU DÒNG, một marker một dòng: `THREAD:`, `QUESTION:`, `DRAFT:`, `DRAFT_END`,
  `AWAITING_APPROVAL`, `APPROVED`, `REJECTED`, `FAILED:`, `STALE`, `STATUS:`. Draft/artifact
  nhiều dòng nằm giữa `DRAFT:`/`ARTIFACT:` và `DRAFT_END`/`ARTIFACT_END`. Mask như mọi output.
- Exit codes: 0 thành công (kể cả `AWAITING_APPROVAL` — đó là kết thúc đúng của `ticket run`);
  1 lỗi/exhausted/stale; giữ nguyên verbs cũ (`run`, `idea` tương tác, `status`) không đổi.

### 2. Skill (`skills/loopkit/SKILL.md` trong repo; install: copy/symlink vào `~/.claude/skills/loopkit/`)

Nội dung skill dạy mọi Claude session:
- **Trigger:** người dùng muốn build một module/hàm có gate + review + duyệt (một "ticket").
- **Protocol:** `cd <repo đích>` → `loopkit idea start` → vòng lặp: đọc `QUESTION:`, nếu
  context hội thoại đã trả lời được → tự answer và **liệt kê lại các câu đã tự trả lời cho
  người dùng thấy**; chưa biết → AskUserQuestion → `idea answer` → khi `DRAFT:` xuất hiện:
  **đưa nguyên draft cho người dùng**, chỉ chạy `ticket run` sau khi người dùng đồng ý →
  relay `AWAITING_APPROVAL` + artifact → **chỉ chạy `loopkit approve` khi người dùng gõ duyệt
  tường minh trong lượt chat hiện tại; mọi trường hợp khác kể cả "chắc là ok" đều phải hỏi lại**.
- Ba luật cứng in đậm trong skill: tự-trả-lời-phải-khai-báo · draft-phải-qua-mắt-người ·
  approve-chỉ-sau-chữ-duyệt-tường-minh (four-eyes: Claude là relay, không phải approver).
- Skill kiểm tra `loopkit --version` trước; thiếu → hướng dẫn cài
  `pip install "git+https://github.com/luongnamkhanh/loopkit"`.

## Verification

- **Unit (fake brain/run_loop, không LLM):** `idea start` in `THREAD:`+`QUESTION:` và
  registry `refining`; `idea answer` nối history từ disk (không RAM) và in `DRAFT:` khi
  analyst chốt; suspend door persist đúng payload + return False; `ticket run` kết thúc
  `AWAITING_APPROVAL` exit 0 và doors.json có entry; `approve` → registry `approved=True` +
  cache có ticket + in `APPROVED`; `reject` → không cache; `approve` khi không có door →
  `STALE` exit 1; `show` báo "awaiting" khi door mở.
- **Live E2E — dogfood bởi chính Claude session này:** t lái trọn vòng cho một ticket mini
  thật ở repo thật qua bash: idea start → tự trả lời cái đã biết (khai báo) → hỏi người dùng
  cái chưa biết → đưa draft → user OK → ticket run → relay artifact → user gõ duyệt →
  `loopkit approve`. Front này pass khi chính t dùng được nó.

## Out of scope (YAGNI)

MCP server wrapper (P4/SaaS-adjacent) · auto-approve dưới mọi hình thức · nhiều thread song
song từ một session · publish skill lên marketplace · thay đổi hai front hiện có.
