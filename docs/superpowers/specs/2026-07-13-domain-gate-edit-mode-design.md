# Domain gate + edit-in-place mode — mở khoá repo helm/terraform (`Gate:` AI-điền)

**Date:** 2026-07-13 · **Status:** approved (design) · **Demand:** BUILD-MAP 2026-07-09 (ticket
savepoint fix TAY, gate tay = `helm template` diff + lint) + bộ 21 tickets 2026-07-11: 8 ticket
"good" có gate helm/grep/golden phải đi vòng qua subagent vì loop chỉ biết pytest. 4 repo
(`iac`, `streaming-deploy`, `data-deploy`, `biz`) đang PENDING chỉ vì thiếu gate này.

**Locked decisions:**
1. **`Gate: <lệnh shell>` = cơ chế, AI = người điền** (đúng bài `Deliver:`): analyst đề xuất
   trong draft (idea flow), hoặc `infer_gate` lúc freeze (ticket thiếu Gate:). Người không bao
   giờ phải viết lệnh — chỉ thấy-và-phủ-quyết tại draft/door.
2. **Lệnh tự do, KHÔNG allowlist** (lựa chọn của chủ tool): chạy `shell=True` trong worktree,
   quyền user, như tự gõ terminal. Hàng rào = mắt người: lệnh in NGUYÊN VĂN ở draft và door.
3. **Gate chốt TRƯỚC generation, không ngoại lệ** — kể cả gate do AI infer. Gate sinh sau
   generation = generator tự chấm bài mình (anti-Goodhart, như derive_tests frozen).
4. **`Gate:` có mặt = edit-in-place mode**: generator SỬA THẲNG file trong worktree (tool-mode
   bắt buộc), artifact = `git diff`, delivery = commit tại chỗ. Không solution.py, không
   `Deliver:` (xuất hiện cùng → ignore + warn), không semantic cache/recall (diff không
   re-apply an toàn).
5. **REPOS_PENDING đổi nghĩa**: từ "cấm" thành "bắt buộc có `Gate:`" — có gate (người ghi hoặc
   AI infer được) là chạy; không infer nổi → từ chối kèm lý do. Mở khoá per-ticket, không đổi env.

## Problem

Loop hiện chỉ có gate pytest/py_compile và artifact model một-module-python. Ticket hạ tầng
(helm values/templates, terraform, YAML) sửa file CÓ SẴN và cần gate dạng lệnh
(`helm template|lint`, `terraform validate`, golden diff script, grep). Kết quả: 4 repo bị
pending, 8/21 ticket thật phải làm ngoài loop.

## Design

### 1. Token `Gate:` (`gates.parse_gate_cmd`)

- Cú pháp: `Gate: <lệnh đến hết dòng>` — một dòng, ở bất kỳ đâu trong ticket (như `Repo:`);
  strip khỏi text trước `parse_ticket`. Ví dụ: `Gate: ./charts/pipeline/tests/run-golden-tests.sh`,
  `Gate: helm template charts/pipeline | grep -q 'upgradeMode: savepoint' && helm lint charts/pipeline`.
- `make_cmd_gate(cmd, workdir)` (gates.py): verifier chạy `subprocess.run(cmd, shell=True,
  cwd=workdir, timeout=300, capture_output)` → `(rc == 0, tail 700 chars)`. Artifact arg bị
  bỏ qua — trạng thái nằm trong worktree.

### 2. Ai điền `Gate:`

- **Analyst (idea flow, repo-mode):** soul dạy thứ tự ưu tiên khi đề xuất: (1) test/golden
  script CÓ SẴN trong repo → dùng lại; (2) render+lint chuẩn domain (`helm template`+`helm lint`,
  `terraform validate`); (3) grep có chủ đích theo DoD. Draft hiển thị lệnh nguyên văn.
- **`infer_gate(goal, dod, repo, ask)` (deliver.py hoặc gates.py):** brain call nhỏ lúc FREEZE
  cho ticket thiếu `Gate:` vào repo pending — input `git ls-files` (cap 400) + goal + dod,
  cùng thứ tự ưu tiên trên; reply MỘT dòng lệnh. Validate tối thiểu: không rỗng, một dòng.
  Không đoán nổi → từ chối ticket (fail-closed, KHÔNG có gate-yếu-tạm cho YAML). Repo ACTIVE
  thiếu `Gate:` → giữ nguyên đường pytest hiện tại (Tests:/derive/compile) — không đổi gì.
- Repo active + ticket có `Gate:` → Gate override pytest derivation (dùng được mọi nơi).

### 3. Pre-flight gate run (lưới rẻ)

Vì gate chốt trước generation: chạy lệnh MỘT lần trên worktree sạch ngay sau freeze.
- **Đỏ** → gate đúng nghĩa acceptance (DoD chưa đạt thì phải đỏ — RED của TDD). Nhãn: `gate
  pre-flight: ĐỎ (acceptance)`.
- **Xanh sẵn** → gate chỉ chống-vỡ (regression), không chứng minh DoD. Nhãn cảnh báo lên door:
  `⚠️ gate xanh trước khi sửa — chỉ chống vỡ, không chứng minh DoD`.
- Nhãn ghi journal + hiện ở door. Không chặn — chỉ thông tin cho người duyệt.

### 4. Engine: edit-in-place mode

- Điều kiện vào mode: ticket có `gate_cmd` + repo-mode + `ENABLE_TOOLS=1`. Thiếu tools →
  front từ chối từ intake ("edit-mode cần LOOPKIT_ENABLE_TOOLS=1").
- Generator prompt variant: "ACT: sửa các file trong repo (worktree hiện tại) để đạt GOAL/DoD.
  KHÔNG tạo solution.py. Trả lời một dòng tóm tắt."
- Artifact sau mỗi turn = `git -C ws diff` (+ `--stat`) — fail-closed: diff rỗng = gate fail
  với thông báo rõ (tương tự empty-artifact hiện có).
- Reviewer prompt variant: "Artifact là git diff dưới đây — judge theo DoD" (text-mode đủ,
  reviewer ACT không bắt buộc v1).
- Recall/semantic-cache: SKIP hoàn toàn khi edit-mode (quyết định khoá #4).

### 5. Door & delivery (edit-mode)

- Door hiển thị: **Gate command nguyên văn** (kèm `(AI đề xuất)` nếu inferred) + nhãn pre-flight
  + `git diff --stat` + diff (cắt 2500, mask). Approve = duyệt cả lệnh gate lẫn diff.
- Delivery sau approve (nhánh MỎNG, `deliver.ship_diff`): re-run gate cmd lần cuối → đỏ = abort
  báo rõ → `git add -A` → commit (dòng đầu Goal ≤72, mask, không attribution) → branch
  `feat/<slug-goal>` (checkout -B) → push → MR (reuse `create_mr` nguyên si — fallback link đã có).
  Fail bước nào báo bước đó, giữ local, không rollback approve (như ship hiện tại).
- Door payload thêm `gate_cmd` + `mode: "edit"` — `finish_suspended` route sang `ship_diff`
  khi mode edit (resume path + re-materialize: edit-mode KHÔNG re-materialize được từ payload
  (diff không apply lại) → door sau reboot mà worktree mất = báo "worktree mất, chạy lại ticket",
  không ship mù).

### 6. Fronts

- Cả 3 fronts: `parse_gate_cmd` sau `parse_repo`; pending-repo check đổi: có gate_cmd (parse
  hoặc infer thành công) → cho chạy; không → thông báo mới "repo này cần Gate: — mô tả cách
  test trong idea để analyst tự đề xuất".
- Telegram/Slack: draft + door in Gate command đậm, nguyên văn.

## Verification

- Unit (không LLM, git fixture): parse_gate_cmd; make_cmd_gate pass/fail/timeout; infer_gate
  (fake ask) valid/junk→refuse; pre-flight hai nhãn; edit-mode artifact=diff + diff rỗng
  fail-closed; ship_diff happy/gate-đỏ-abort/push-fail (pre-receive hook fixture cũ); resume
  edit-mode mất worktree → từ chối ship mù; Deliver:+Gate: → warn + ignore Deliver.
- **Live E2E (nghiệm thu):** một ticket thật vào `streaming-deploy` từ Telegram — ví dụ T-series
  còn lại — analyst đề xuất gate helm, draft → Run → door hiện diff+gate → Approve → MR trên
  gitlab.annamglobal.com. Pass khi trọn vòng từ phone.

## Out of scope (YAGNI)

Allowlist lệnh (chủ tool đã chọn bỏ — thêm knob khi hối hận) · semantic cache cho edit-mode ·
reviewer ACT trong edit-mode · multi-gate/chuỗi lệnh phức tạp (dùng script trong repo) ·
terraform plan với credentials cloud (validate-only trước) · re-materialize worktree edit-mode.

## Trust note

Lệnh gate (kể cả AI đề xuất) chạy `shell=True` quyền user trên máy user — ngang bro tự gõ
terminal. Hàng rào duy nhất là mắt người tại draft và door, nơi lệnh LUÔN in nguyên văn.
Đây là lựa chọn có ý thức cho tool cá nhân local-first; nếu loopkit có người dùng thứ hai,
allowlist là việc đầu tiên phải thêm.
