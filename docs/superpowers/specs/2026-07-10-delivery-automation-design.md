# Delivery automation — artifact vào repo sau approve (MR link vào thread)

**Date:** 2026-07-10 · **Status:** approved (design) · **Demand:** 2 lần giao TAY liên tiếp —
normalize_phone (2026-07-08) và bearing_deg (2026-07-09/10, kèm sự cố worktree `/tmp` bay sau
reboot, phải móc artifact từ semantic cache; push dính GitLab group read-only). Chuỗi tay lặp
đúng 4 bước: đặt file theo pattern repo → commit → push `feat/<x>` → MR.

**Locked decisions:**
1. **Placement do AI quyết, user idea-only.** User không bao giờ phải viết path; AI đọc repo
   và đề xuất; người chỉ **thấy-và-phủ-quyết** tại draft/door.
2. **Path chốt LÚC FREEZE (trước generation).** Sau approve, không còn LLM nào đứng giữa
   approve và code-vào-repo — chuỗi deliver deterministic 100%.
3. **MR thật qua `glab`/`gh`** (detect host từ remote URL); CLI thiếu/chưa auth → fallback
   parse link create-MR từ push output (GitLab lẫn GitHub đều in sẵn).
4. **Delivery fail KHÔNG rollback approve** — artifact vẫn cached, branch/file còn local,
   báo lỗi rõ.

## Problem

Sau khi người duyệt bấm approve, `run_loop`/`finish_suspended` chỉ `register done → cache →
post artifact` rồi dừng. File nằm uncommitted trong worktree `/tmp` trên branch `loop/<thread>`
— chưa vào repo, chưa có MR. BUILD-MAP §7 row "Delivery: MR link sau approve ⬜ ❗".

## Design

### 1. Ticket field `Deliver:` — cơ chế lưu quyết định placement

- Cú pháp: `Deliver: flink/bearing.py` (v1: một module `.py`; test tự suy ra
  `test_<module>.py` cùng thư mục).
- **Nguồn điền (không bao giờ là user):**
  - *Idea flow:* analyst repo-mode (đã đọc AGENTS.md + cây repo) đề xuất `Deliver:` ngay
    trong draft — người duyệt draft thấy path, muốn ignore cũng được.
  - *Ticket run thiếu `Deliver:`:* engine thêm 1 brain call nhỏ **lúc freeze** (cùng chỗ
    derive tests): cây repo mức `git ls-files` + Goal → path; ghi vào ticket. Door hiển thị
    `Deliver: <path> (AI đề xuất)`.
  - Degraded (máy quyết được, không cần "đo độ tự tin"): brain trả path không qua nổi
    validate bên dưới, hoặc không repo-mode → skip delivery + warning chỉ rõ artifact nằm
    ở worktree nào (đúng behavior hiện tại, không phá gì).
- **Validate (draft gate + trước deliver):** path tương đối, nằm trong repo, đuôi `.py`;
  path trỏ vào file ĐÃ tồn tại → door phải ghi rõ `(overwrites existing)` — duyệt tức là
  duyệt cả việc đè.

### 2. `deliver.py` — module engine-level, deterministic, không LLM

Gọi tại đúng 2 điểm hiện có: cuối `run_loop` sau door pass, và `finish_suspended` với
`decision=True`. Điều kiện: **human-approved qua door** (khác `mem.store`: non-risky
auto-pass KHÔNG deliver — code vào repo luôn phải qua mắt người) + repo-mode + `Deliver:`
có trong ticket + `LOOPKIT_DELIVER=1` (default bật).

Chuỗi (mỗi bước fail → emit lỗi + journal, giữ nguyên hiện trạng, dừng):

1. **Ensure worktree** — mất (case reboot 2026-07-10) → `make_workspace` lại + ghi artifact
   từ door payload. Door payload từ nay mang thêm `deliver` + `tests` để resume path tự đủ.
2. **Move + rewrite:** `solution.py` → `<Deliver path>`; `test_solution.py` →
   `test_<module>.py` cùng thư mục; trong test, rewrite import `solution` → `<module>`
   (string replace deterministic).
3. **Re-run gate trên file đã move** — import đổi thì phải xanh lại; đỏ → abort, không commit.
   Không giao hàng chưa re-verify.
4. **Branch + commit:** `git checkout -b feat/<module>` từ HEAD worktree; commit 1 dòng từ
   Goal. Không AI attribution. Identity: dùng git config sẵn của repo đích, loopkit không set.
5. **Push** `-u origin feat/<module>`. Fail (case GitLab read-only 403 2026-07-10) → báo
   stderr + branch còn local ở worktree nào.
6. **MR:** remote chứa `gitlab` → `glab mr create` / `github.com` → `gh pr create`
   (title=Goal, body=DoD, target=default branch, mask qua shield). Tool thiếu/chưa auth/lệnh
   fail → fallback: parse link create-MR/PR từ push output; không có nốt → post tên branch +
   lệnh gợi ý.
7. **Emit + journal:** MR link vào thread/terminal; journal
   `{"stage": "delivered", "mr_url"|"branch"|"error": ...}`.

### 3. Knobs (`config.py`)

`LOOPKIT_DELIVER` (default `1`) · `LOOPKIT_MR_TOOL` = `auto|glab|gh|link|off` (default `auto`).

## Verification

- **Unit (tmp git repo fixture + fake remote, fake brain, không LLM):** move+rewrite import
  đúng; re-gate đỏ → abort không commit; push fail → emit lỗi + branch local (tái hiện 403);
  glab absent → fallback parse link từ stderr mẫu GitLab và GitHub; freeze-time inference ghi
  `Deliver:` vào ticket TRƯỚC generation; door payload chứa `deliver`+`tests`; degraded path
  (không `Deliver:`) giữ nguyên behavior cũ + warning.
- **Live E2E:** ticket thật kế tiếp vào `annamgt-streaming-pipeline` (sau khi group hết
  read-only) — pass khi MR link xuất hiện trong thread mà không ai đụng tay vào git.

## Out of scope (YAGNI)

Multi-file artifact (v1 = 1 module + 1 test; ceiling ghi bằng `ponytail:` comment) · template
mô tả MR · auto-merge · delivery cho standalone dir-mode · retry queue cho push fail (chạy
lại = ticket revision hoặc tay) · đổi convention `solution.py` trong loop.
