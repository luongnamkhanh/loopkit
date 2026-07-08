# Idea-refinement intake — từ ý tưởng thô đến ticket đầy đủ, ngay trong Slack

**Date:** 2026-07-08 · **Status:** approved (design) · **Target:** làm việc remote hoàn toàn
qua Slack (phone-only path phải chạy được trọn vẹn).

**Locked decisions:** (1) trigger = @mention KHÔNG có `DoD:` → tự vào refinement;
(2) draft cuối = Goal + EARS DoD + `Tests:` pytest block (human bấm Approve = duyệt luôn
bộ test làm contract).

## Problem

Loop hiện tại đòi ticket chất lượng (Goal + DoD bắt buộc) ngay từ intake — nhưng người dùng
thường chỉ có ý tưởng thô. Industry giải bằng một plan/triage stage trước vòng code (Devin
`!plan`, GitLab Duo research flow, Copilot task-breakdown). loopkit cần stage đó, chạy như
một loop-framework instance đúng nghĩa.

## Refinement là một vòng loop framework

| Thành phần framework | Trong refinement |
|---|---|
| Worker | role `analyst` (soul mới, không tool) — mỗi lượt: MỘT câu hỏi, ưu tiên A/B/C, hoặc draft ticket |
| Deterministic gate (chạy trước human) | draft phải qua `gates.parse_ticket`: goal + DoD non-empty, `Tests:` qua `_looks_like_tests` (AST) |
| Feedback → retry | gate fail → analyst redraft với mô tả lỗi, tối đa 2 retry |
| Bounded stop | `LOOPKIT_REFINE_MAX_TURNS` (default 5) lượt hỏi; chạm trần → BUỘC draft với giả định nói rõ |
| Human door | nút **[Approve & Run] / [Hủy]** trên draft; reply text = góp ý → redraft (tính vào budget) |
| Memory (durable) | Q&A ghi `Memory.sessions` (masked); registry status `refining` → `ticket_drafted`; analyst STATELESS — mỗi lượt đọc lại toàn bộ history từ disk → **sống sót restart by construction**, không cần cơ chế resume riêng |
| Shield | mọi câu hỏi/draft mask qua `shield.mask` trước khi post/persist |

Đã cân nhắc tái dùng `run_loop` cho refinement (thuần framework nhất trên giấy): loại, vì
`run_loop` chỉ có human door ở cuối còn refinement cần human MỖI lượt — ép vào sẽ bẻ cong
engine. Refinement là vòng nhỏ riêng, cùng nguyên tắc.

## Flow

1. **Intake:** `app_mention` không parse ra DoD (nhánh hiện đang trả lỗi cú pháp) →
   `register(status="refining", idea=<text>, refine_turns=0)` + analyst hỏi câu đầu trong thread.
2. **Q&A:** reply THƯỜNG trong thread `refining` (không cần mention, không cần cú pháp —
   sửa `on_followup`: status `refining` → nhận mọi reply; giữ guard `bot_id`/`subtype`/dedupe).
   Mỗi reply → một lượt `refine_turn` chạy trong worker thread (không block handler).
3. **Draft:** analyst đủ thông tin (hoặc chạm trần) → xuất draft ĐÚNG cú pháp intake hiện có:
   `<goal> DoD: <ears> Tests: ```python ...``` ` → qua gate → post draft + buttons,
   `register(status="ticket_drafted", draft=<text>)`. Gate fail sau 2 retry → vẫn post kèm
   `⚠️ Tests không hợp lệ — approve sẽ rơi về derive-from-DoD`.
4. **Approve & Run:** handler đọc `draft` từ REGISTRY (không phải RAM) → gọi thẳng
   `launch_ticket()` trong CÙNG thread → flow cũ tiếp quản nguyên vẹn (recall → freeze gate →
   worktree → generate → reviewer → door artifact). Buttons event-driven thuần, không có
   in-process wait → **bấm sau restart vẫn chạy, không cần doors.json analog**.
   **Hủy** → `status="refine_cancelled"`.
5. Một thread = trọn vòng đời: ý tưởng → phỏng vấn → contract → code → duyệt → artifact.

## Output protocol của analyst (fail-closed, học từ vụ VERDICT)

Reply bắt đầu bằng `QUESTION:` hoặc `TICKET:` (scan dòng đầu tiên khớp, tolerant như verdict
scan). Không có marker nào → coi là QUESTION (an toàn — human vẫn lái tiếp được, không bao
giờ tự-run một draft không chủ đích).

## Components

- **`refine.py` (mới, Slack-free, unit-test được):** soul analyst;
  `refine_turn(idea, history, turns_used, max_turns, ask=ask_claude) -> ("ask", str) | ("draft", str)`;
  gate + retry nằm trong đây.
- **`roles.py`:** thêm role `analyst` (không tool). **`config.py`:** `REFINE_MAX_TURNS`,
  `ROLE_MODELS["analyst"]` default `sonnet` (`LOOPKIT_MODEL_ANALYST` override).
- **`slack_app.py`:** (a) nhánh intake không-DoD → refinement thay vì báo lỗi; (b) nhánh
  `on_followup` cho thread `refining`; (c) 2 action mới `ticket_approve`/`ticket_reject`.
- **Registry statuses mới:** `refining`, `ticket_drafted`, `refine_cancelled` — không status
  nào là `running` → reaper §8.1 không đụng.

## Edge cases

- Reply có chứa `<@mention>` trong thread refining → Slack đẩy sang `app_mention` (rule cede
  hiện có): nếu kèm DoD → thành ticket trực tiếp trong thread (chấp nhận, document); không
  DoD → khởi động refinement mới đè status cũ (chấp nhận — idea mới nhất thắng).
- Analyst trả rỗng/lỗi → retry 1 lần, vẫn rỗng → post "💥 refinement lỗi, reply để thử lại"
  (turns không tăng).
- `ENABLE_MEMORY=0` → refinement TẮT (cần registry/session làm state); mention không DoD trả
  lại message lỗi cú pháp như cũ.

## Reinforcement — áp loop framework cho CHÍNH việc build feature này

Quá trình build (spec này → plan → thực thi) chạy đúng các thành phần framework:

| Framework | Build process |
|---|---|
| Deterministic gate trước generation | TDD per task: failing test viết & chạy FAIL trước, freeze rồi mới implement |
| Worker / Reviewer tách biệt | fresh subagent per task (writer) + main-session review diff + full suite (reviewer) |
| Bounded stop | mỗi task là đơn vị nhỏ có test riêng; retry có giới hạn, fail thì dừng lại report |
| Human door | user duyệt: design (đã qua) → spec (gate này) → plan → live E2E cuối |
| Memory | spec/plan commit vào git; gap mới phát hiện → ROW trong BUILD-MAP trước khi fix |
| Dogfood (tuỳ chọn, sau khi stable) | `refine.py` là module thuần — ticket cải tiến sau này của chính nó có thể chạy qua bot |

## Verification

- **Unit (fake brain, không Slack/LLM):** `refine_turn` ra QUESTION khi thiếu thông tin; ra
  TICKET draft hợp lệ khi đủ; marker lạ → coi là question; gate fail → retry với feedback;
  chạm trần `max_turns` → buộc draft; draft parse được bởi `gates.parse_ticket` với tests
  AST-valid.
- **Live E2E (acceptance — phone-only):** từ điện thoại: `@bot <ý tưởng thô>` → trả lời 2–3
  câu hỏi → nhận draft → **Approve & Run** → loop chạy → duyệt artifact. Không đụng laptop.
  Bonus: kill bot giữa chừng Q&A, restart, reply tiếp → phiên tiếp tục (stateless resume).

## Out of scope (YAGNI)

Research/best-practice stage (phase riêng, món 2 của roadmap) · `Repo:` routing đa repo
(món 3) · Slack modal/form để sửa draft (reply text là đủ) · similarity recall các phiên
refinement cũ.
