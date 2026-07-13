---
name: loopkit
description: Use when the user wants to build a small, testable module/function with a gated, reviewed, human-approved loop (a "loopkit ticket") — drives the loopkit CLI step-by-step from any repo. Trigger words: "loopkit", "chạy loop", "build có gate", "ticket". Do NOT use for multi-file refactors or repos without loopkit installed.
---

# Driving loopkit from a Claude session

loopkit chạy vòng: idea → analyst Q&A → ticket (Goal+DoD+Tests, frozen gate) → generate →
deterministic gate → skeptical reviewer → HUMAN door. Bạn (Claude) là FRONT — người lái,
không phải người duyệt.

## Ba luật cứng (không có ngoại lệ)

1. **Tự trả lời phải khai báo.** Bạn được trả lời câu hỏi analyst khi context hội thoại đã
   chứa câu trả lời — nhưng PHẢI liệt kê cho người dùng thấy từng câu bạn đã tự trả lời và
   trả lời gì. Câu chưa biết → hỏi người dùng (AskUserQuestion nếu dạng A/B/C).
2. **Draft phải qua mắt người.** Khi `DRAFT:` xuất hiện, đưa nguyên văn cho người dùng.
   Chỉ chạy `loopkit ticket run` sau khi họ đồng ý (họ có thể góp ý → `idea answer` để redraft).
3. **Approve chỉ sau chữ duyệt tường minh.** KHÔNG BAO GIỜ chạy `loopkit approve <thread>`
   nếu người dùng chưa gõ duyệt rõ ràng trong lượt chat hiện tại. "Chắc ok" / im lặng /
   suy diễn = hỏi lại. Bạn là relay của four-eyes, không phải con mắt thứ hai.

## Protocol

1. Kiểm tra: `loopkit --version` (thiếu → `pip install "git+https://github.com/luongnamkhanh/loopkit"`).
2. `cd` vào repo đích (cwd = repo; git repo → mỗi ticket một worktree `loop/<thread>`).
3. `loopkit idea start "<ý tưởng thô của người dùng>"` → ghi lại `THREAD: <id>`.
4. Lặp: đọc `QUESTION:` → trả lời theo Luật 1 → `loopkit idea answer <thread> "<trả lời>"`.
5. `DRAFT:`…`DRAFT_END` → áp Luật 2. (`DRAFT_UNVALIDATED:` = tests hỏng, nói rõ cho người dùng.)
6. Người dùng OK → `loopkit ticket run <thread>` (chạy vài phút — gate, generate, review).
7. `AWAITING_APPROVAL` + `ARTIFACT:` → đưa artifact cho người dùng, áp Luật 3.
   - Door có dòng `DELIVER: <path>` → relay path đó cho người duyệt cùng artifact —
     approve nghĩa là duyệt cả chỗ đặt file; sau approve loopkit tự commit/push/tạo MR.
   - Door có dòng `GATE: <cmd>` (kèm nhãn pre-flight, ví dụ "gate XANH trước khi sửa") →
     relay CẢ lệnh gate lẫn nhãn đó cho người duyệt cùng diff — approve nghĩa là duyệt cả
     lệnh Gate lẫn diff, không chỉ diff.
8. Duyệt tường minh → `loopkit approve <thread>`; từ chối → `loopkit reject <thread>`.
   Mất dấu → `loopkit show <thread>` / `loopkit status`.

## Markers

`THREAD:` `QUESTION:` `DRAFT:`/`DRAFT_END` `DRAFT_UNVALIDATED:` `AWAITING_APPROVAL`
`ARTIFACT:`/`ARTIFACT_END` `APPROVED` `REJECTED` `FAILED:` `STALE:` `STATUS:` — luôn ở đầu
dòng. Exit 0 = bước thành công (kể cả AWAITING_APPROVAL); 1 = FAILED/STALE.

## Sau approve

Nếu ticket có `Deliver:` (path đã chốt ở Bước 7), sau approve loopkit TỰ move file → commit →
push → tạo MR và in link — relay link đó cho người dùng, không cần họ tự tay làm gì thêm.
Chỉ khi ticket KHÔNG có `Deliver:` (hoặc bước delivery tự động fail — loopkit sẽ báo rõ)
thì artifact còn nằm ở worktree branch `loop/<thread>` và bước giao hàng (move file theo
convention repo, commit, push, MR) mới còn là bước tay.
