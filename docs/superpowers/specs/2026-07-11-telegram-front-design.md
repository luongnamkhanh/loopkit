# Front #4 — Telegram (personal-use mobile front, thay Slack làm front hàng ngày)

**Date:** 2026-07-11 · **Status:** approved (design) · **Why:** Slack front friction thật:
scope hell (message.groups chưa subscribe → Q&A refinement chết trong private channel,
im:write thiếu → không DM được), workspace enterprise không phải của mình. Telegram bot:
một token BotFather, long-poll không cần public URL — đúng local-first, đúng "tối ưu cho
một người dùng trước, scale sau".

**Locked decisions:**
1. **Stdlib thuần** — `urllib` long-poll, ZERO dependency; front nằm trong core, không extra.
2. **`slack.py` giữ nguyên** — "chuyển" nghĩa là đổi lệnh chạy (`loopkit-telegram`), hai front
   sống song song, không migration.
3. **Một chat_id duy nhất, bắt buộc** — `LOOPKIT_TG_CHAT_ID`; update lạ drop im lặng
   (bot public theo username — đây là trust boundary, không phải option).
4. **Import sạch, testable** — không như slack.py (import đòi token): token check trong
   `main()`, API bọc `TgApi` mỏng mock được. Unit test là hạng nhất cho front này.

## Problem

Front mobile hiện tại (Slack) hỏng một nửa (Q&A refinement không nhận reply trong private
channel) và nằm trong workspace không kiểm soát được. Cần front điện thoại mà: intake ticket
+ idea Q&A + door bấm nút + delivery report — full parity, ít friction, chạy từ laptop.

## Design

### 1. `fronts/telegram.py` + entry point `loopkit-telegram` (pyproject scripts)

- Env: `LOOPKIT_TG_TOKEN` + `LOOPKIT_TG_CHAT_ID` — thiếu cái nào `main()` exit 1 kèm hướng
  dẫn 2 dòng (BotFather lấy token; `curl getUpdates` lấy chat_id sau khi nhắn bot 1 câu).
- `TgApi` class mỏng (urllib): `get_updates(offset, timeout=50)`, `send(text, reply_to=None,
  keyboard=None) -> message_id`, `answer_callback(id)`, `edit_reply_markup(message_id, None)`.
  Mọi call có timeout; lỗi mạng → log + backoff ngắn + tiếp tục poll (bot không chết vì một
  request hỏng).

### 2. Vòng chính: long-poll → dispatch

- `getUpdates` offset-ack; update chưa ack được Telegram gửi lại sau restart → dedupe qua
  `Memory.events.seen` (`tg-<update_id>`, cơ chế sẵn có của Slack event ids).
- Update từ chat ≠ `TG_CHAT_ID` → drop im lặng (không reply, không log nội dung).
- `message` → intake; `callback_query` → route theo prefix của `callback_data`:
  `door:<thread>` (approve/reject — §4) | `draft:<run|edit|cancel>:<thread>` (§3).

### 3. Intake & threading (chat tuyến tính — không có thread thật)

- Message có `DoD:` (parse_ticket) → launch ticket: `Repo:` routing theo allowlist
  `LOOPKIT_REPOS` (fail-closed như Slack), `Deliver:` + freeze qua `deliver.freeze_deliver`
  (emit = send), worktree per ticket, `run_loop` với suspend-style door (xem §4).
- Không `DoD:` → idea refinement: analyst hỏi từng câu (mỗi câu một message).
- **Thread id = `tg-<message_id của intake>`.** Routing câu trả lời:
  - reply vào BẤT KỲ message nào thuộc chuỗi một run → route run đó (map message_id→thread
    giữ trong registry qua `mem.register(thread, tg_msgs=[...])`);
  - message trần (không DoD, không reply) khi có đúng MỘT thread `refining` → là ANSWER cho
    thread đó (muốn mở idea mới trong lúc này: reply vô message intake cũ để answer, còn
    message trần LUÔN ưu tiên làm answer — luật đơn giản cho một người dùng);
  - message trần khi KHÔNG có thread `refining` nào → idea MỚI;
  - mơ hồ (≥2 thread đang hỏi) → bot liệt kê thread + yêu cầu reply trực tiếp.
- Draft flow như Slack: draft qua gate `parse_ticket`+AST trước khi post, nút
  `[▶️ Run] [✏️ Góp ý] [🚫 Huỷ]` (inline keyboard thay cho button Slack).

### 4. Door — inline keyboard + durable doors reuse nguyên

- Reviewer PASS → send artifact preview (2500 chars, mask) + dòng `📦 Deliver: <path>`
  (+`(overwrites existing)`) + keyboard `[✅ Approve] [🚫 Reject]`, `callback_data=<thread>`.
- Door kiểu suspend (như CLI agent-mode): `door_open` persist payload đầy đủ
  (artifact/goal/dod/deliver/repo/workspace/tests + `channel: "telegram"`) rồi trả False —
  KHÔNG block thread poll. `callback_query` → `audit` + `finish_suspended` (delivery chạy
  ở đó, engine-level sẵn) + `door_close` + `answer_callback` + `edit_reply_markup` gỡ nút
  (chống double-click; click stale → answer_callback "door không còn mở").
- Bấm sau restart: doors.json + reaper reuse nguyên si — §8.1 không đổi một dòng.

### 5. Output

- notify → `send()` plain text (KHÔNG parse_mode — MarkdownV2 escaping là bug factory;
  đẹp để sau). Mask qua shield trước khi gửi (guard như Slack). Artifact cắt 2500 chars.

### 6. Testability (khác biệt chủ đích với slack.py)

Import module không side-effect; token check trong `main()`. Unit tests với `FakeTgApi` +
fake engine/refine: chat_id lạ bị drop; intake DoD → run_loop được gọi đúng tham số;
intake không DoD → refine_turn; reply routing 3 nhánh (reply-to, trần-1-thread, mơ hồ);
callback approve → finish_suspended + door_close + gỡ nút; callback stale → không nổ;
update_id dedupe sau "restart".

## Verification

- Unit như §6 (không token, không mạng). Toàn bộ suite hiện có pass.
- **Live E2E:** tạo bot BotFather thật → `loopkit-telegram` → nhắn idea từ điện thoại →
  Q&A → draft → Run → door → Approve → delivery chain chạy (push fail 403 nếu GitLab còn
  khoá — chấp nhận, như T16). Pass khi trọn vòng không đụng bàn phím máy tính.

## Out of scope (YAGNI)

Group chat / multi-user · webhook mode · media/file gửi kèm · edit-message streaming ·
parse_mode formatting · migration state từ Slack · tắt/gỡ slack.py.
