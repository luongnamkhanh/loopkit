# BUILD-MAP — design ↔ implementation (single source of truth)

Traceability from the researched design (`../docs/loop-framework-flow.md`, AI Foundation
architecture, ticket-template research) to what is actually built in `loopkit/`.

**Rule: any newly discovered gap gets a ROW here first** — the chat is not the tracker.

Legend: ✅ built & verified · 🟡 partial · ⬜ planned (deliberate defer) · ❗ gap found late

## 1 · Loop engine (flow-level, shared)
| Design item | Status | Where / note |
|---|---|---|
| Orchestrator routing (LLM + deterministic backstop) | ✅ | `engine.route()` — backstop added after a live misroute |
| Worker/generator step | ✅ | `engine.run_loop` |
| Deterministic gate runs FIRST | ✅ | `Ticket.verifier` seam |
| Separate skeptical reviewer | ✅ | reviewer role; **uncalibrated** (→ §8.3); verdict scan tolerant-but-fail-closed (live finding: verdict buried under reasoning); reviewer can't ACT with `claude -p` — live evidence for P3 |
| Feedback → retry | ✅ | observed live (demo2b: gate fail → fix → pass) |
| Bounded stop: `max_turns` | ✅ | `run_loop(max_turns=)` |
| Token/cost budget (run-level) | ⬜ | design §6; rung-4 remainder |
| Human door seam | ✅ | `human_door` — Slack buttons live |
| "Uncertain → halt & ask" semantics | ⬜ | design §6; not modeled |
| Recursion guard | ✅ | fixed role set, no sub-spawn |
| Per-role model tiering (Opus judge / cheap workers) | ✅ | defaults: haiku router / sonnet workers / opus reviewer (≠ workers); `LOOPKIT_MODEL_<ROLE>` overrides; `""` → CLI default |
| Worktree per worker (parallel isolation) | ✅ | `workspace.py`: git worktree per thread on `loop/<thread>` when `LOOPKIT_TARGET_REPO` set (self-healing prune; collision-proof names); tmp-dir fallback otherwise |

## 2 · Verification
| Item | Status | Note |
|---|---|---|
| Starter gates (pytest / py_compile / structural) | ✅ | examples + `slack_app.py` |
| REAL gate: EARS → pytest | ✅ | `gates.py`: explicit `Tests:` (AST-validated) > derived-from-DoD (fresh call BEFORE generation, frozen) > compile-only fallback (warned). helm/kubeconform variants ⬜ (domain) — **real demand 2026-07-09**: ticket savepoint-upgradeMode (mất data khi upgrade) vào repo pending-gate, phải fix TAY (gate tay = `helm template` diff before/after + lint); đủ bằng chứng để build domain gate + diff artifact |
| §8.3 calibration (gold set TPR/TNR), trajectory, pass^k | ⬜ | needs real-run data (scaffold possible) |
| §8.4 continuous eval / golden set | ⬜ | needs real failures |

## 3 · Memory (flow-level, shared) — closes the "stateless agents" weakness (design §7.3)
| Item | Status | Note |
|---|---|---|
| Journal per turn | ✅ | append-only with unique `run_id`+`thread_id`; masked; never wipes |
| Registry: thread → run/state/budget | ✅ | `memory.py` (thread-safe; status running/done/done_cached/exhausted/interrupted) |
| Session: per-thread history on disk | ✅ | `memory.py` sessions/<thread>.jsonl, masked |
| Semantic cache: verified solutions keyed by ticket/DoD | ✅ | `memory.py` — exact-match MVP; stores only verified(+approved); risky recall still passes the door; recall checked BEFORE test-derivation (live finding: was wasting an LLM call) |
| §8.1 durable execution | ✅ | scoped "door+dedupe" (spec 2026-07-08): startup reaper; doors persist (`doors.json`, status `awaiting_approval`, reaper skips) → click-after-restart completes via `engine.finish_suspended`; slack event ids persist (`events.seen`, trimmed 1000 at boot). Full run-resume deliberately out of scope |

## 4 · Shield (flow-level, shared)
| Item | Status | Note |
|---|---|---|
| Output guard: secret/PII mask at Slack + persistence boundaries | ✅ | `shield.py` via engine `emit()`/`record()` + slack direct posts; cache stores raw by design (on-prem), masked at post time |
| Slack `event_id` dedupe (Slack retries ⇒ double-run risk) | ✅ | `shield.seen_event` in mention handler; in-memory per-process (durable dedupe → §8.1) |
| Slack self-loop guard (`bot_id` filter) | ✅ | `slack_app.py` |
| Domain guards (sqlguard-style AST) | ⬜ | only when the domain needs it |

## 5 · Roles (per-agent)
| Item | Status | Note |
|---|---|---|
| Souls ×5 (orchestrator/code/infra/reviewer/analyst) | ✅ | `roles.py` |
| Tool scopes declared (least-privilege) | ✅ | metadata only |
| Skills EXECUTION (real tool use mid-run) | ✅ | `engine.run_agent` (headless Claude Code, `--allowedTools`, cwd=workspace); generator writes `solution.py`, reviewer ACTS (pytest-only bash — no `python3 -c` write escape); **off by default** (`LOOPKIT_ENABLE_TOOLS=1` to enable); repo mode reads target repo's AGENTS.md natively |
| Empty-artifact observability | ✅ ❗ | gap found live (run `r1783483318192` exhausted 4× "empty artifact" with zero clues): no-file turns now journal `agent_reply_tail`+`ts` and emit "tool session said…"; `gates.derive_tests` prints why validation failed. Observability only — root cause of the silent claude session not identified; pinned by `test_p3.py::test_toolmode_empty_artifact_surfaces_agent_reply` |

## 6 · Project layer
| Item | Status | Note |
|---|---|---|
| AGENTS.md (+ injected into every call) | ✅ | double-inject RESOLVED: brain runs in neutral cwd (`config.BRAIN_CWD`) → context enters only via explicit injection; brain-agnostic |
| TICKET_TEMPLATE.md (6-part + EARS) | ✅ | |
| CLAUDE.md | ✅ | points to AGENTS.md; documents the neutral-cwd decision |
| `config.py` — central knobs / feature flags | ✅ | env-overridable (`LOOPKIT_*`), reload-friendly; tested |

## 7 · Slack layer
| Item | Status | Note |
|---|---|---|
| Intake (@mention + mandatory DoD) | ✅ | live, tested end-to-end |
| `Repo:` routing đa repo | ✅ | spec 2026-07-09: allowlist `LOOPKIT_REPOS` (unknown → từ chối kèm danh sách; `REPOS_PENDING` → từ chối "chờ domain gate" — cả hai TRƯỚC mọi LLM call); ws_key=`<repo>-<thread>` (đổi repo giữa thread không dính worktree cũ); context per-ticket (repo-mode đọc AGENTS.md repo đích); analyst nhận danh sách repo để hỏi & điền; không `Repo:` → `TARGET_REPO` (compat) |
| Idea-refinement intake (idea → Q&A → ticket) | ✅ | spec 2026-07-08: mention KHÔNG DoD → analyst hỏi ≤`REFINE_MAX_TURNS` câu (reply thường trong thread), draft Goal+DoD+Tests qua gate `parse_ticket`+AST TRƯỚC khi post; [Approve & Run] đọc draft từ registry (restart-safe, event-driven — không cần doors.json analog); statuses refining/ticket_drafted/ticket_approved/refine_cancelled (reaper không đụng); TẮT khi ENABLE_MEMORY=0 |
| Streamed step updates into thread | ✅ | live |
| Approve/Reject buttons (four-eyes) | ✅ | live; audit persisted via `Memory.audit`; stale clicks never overwrite; door message now SHOWS the artifact (real-ops finding: was a blind approval) |
| Thread follow-ups (`message.channels`) | ✅ ❗ | reply with `DoD:` in an owned thread → new run seeded with previous artifact (revision base). Needs Slack scopes `channels:history`(+groups) + `message.channels` events + reinstall. Known edges: reply containing any `<@mention>` is ceded to app_mention; dead if ENABLE_MEMORY off. **Gap found live (refinement E2E, xác minh bằng socket probe = 0 envelope): channel làm việc là PRIVATE → reply thường = event `message.groups`, chưa từng được subscribe → follow-ups/refinement chưa bao giờ nhận được reply ở đó; mention vẫn tới vì `app_mention` phủ mọi surface. DM cũng chưa dùng được (App Home Messages Tab tắt). Fix config-only: thêm event `message.groups` (scope `groups:history` đã có) + Save + Reinstall; tuỳ chọn bật Messages Tab cho DM. Code không cần đổi (handler channel-type agnostic)** |
| Rate-limit batching / long output as file | 🟡 | one msg per step; artifact truncated 2.5k |
| Delivery: MR link sau approve | ✅ | spec 2026-07-10: `Deliver:` AI điền lúc freeze (`infer_path`; analyst-proposed-in-draft ⬜ defer — freeze-time inference phủ mọi flow), người phủ-quyết tại door; `deliver.ship` sau human door: move→re-gate→`feat/<module>`→push→MR (glab/gh, fallback link từ push output); resume path re-materialize worktree từ door payload; fail = báo rõ + giữ local, không rollback approve. Live E2E 2026-07-11 PASS (ticket T16 idle_logic qua Slack: approve → re-gate 7 passed → commit feat/idle-logic → push fail 403 báo đúng, giữ local; MR chờ group unlock) |
| Socket transport resilience | 🟡 ❗ | ops gap found live (BrokenPipe spam): prefer `websocket_client` adapter, fallback builtin; door restart-safety ✅ (§8.1); mid-generation kills stay `interrupted` (accepted) |
| Recall (cache-hit) re-approve → re-deliver | ⬜ | controller addition — gap discovered in Task 6 review: recall path (cache-hit + re-approve) does NOT re-deliver — deliberate defer until live demand |

## 7b · Telegram layer (front #4 — spec 2026-07-11)
| Item | Status | Note |
|---|---|---|
| Long-poll intake + chat_id trust boundary | ✅ | stdlib urllib; update lạ drop im lặng; dedupe `shield.seen_event(tg-<update_id>)` bền qua restart (`init_dedupe` → `events.seen` lúc boot) |
| Idea refinement Q&A | ✅ | 3 luật routing không state (trần+1 chờ-input = answer; trần+0 = idea mới; ≥2 = từ chối) — thứ chết ở Slack private channel chạy tự nhiên ở đây |
| Door inline keyboard + durable doors | ✅ | suspend door, `finish_suspended` reuse §8.1; click sau restart OK; double-click guard = gỡ nút |
| `/status` command (registry + door marker, zero LLM) | ✅ | lệnh `/` không bao giờ rơi vào idea-routing |
| Sync, không threading | ✅ | ponytail: poll dừng khi generate — single user chấp nhận; thêm thread khi cấn thật |

## 7c · Gap ghi nhận live (2026-07-13)
| Item | Status | Note |
|---|---|---|
| Analyst KHÔNG có tay (text-only) | ⬜ ❗ | live: user nhắn Telegram "repo loopkit có issue mới, resolve đi" → analyst hỏi ngược "issue là gì?" vì không tự `gh issue view` được. Hướng khi có thêm demand: front enrich context (phát hiện từ khoá issue/MR → chạy gh/glab nhét vào idea) hoặc analyst tool-mode (P4 roles/MCP) |

## 8 · Hardening §8 (roof — deliberately after real tickets)
§8.1 durable execution (✅ scoped door+dedupe) · §8.2 risk-classified gating (a `risky` flag
exists; classification is manual) · §8.3 evaluator calibration · §8.4 golden set — rest ⬜ by
design; add when real runs demand them.

## 9 · Product / fronts (Phase 1 productionize — spec 2026-07-09)
| Item | Status | Note |
|---|---|---|
| src-layout package + pyproject | ✅ | hatchling; core deps RỖNG; extras `loopkit[slack]`; entry-points plugin reserved (P3); install từ git URL verify bằng venv sạch |
| CLI front `loopkit run\|idea\|status` | ✅ | cwd = repo đích (worktree per ticket); door terminal y/N fail-closed; `Repo:` bị strip + warning (allowlist là chuyện front slack); live findings: brain subprocess phải `stdin=DEVNULL` (từng nuốt câu trả lời door qua pipe); idea-mode Enter rỗng đốt lượt analyst — polish: re-prompt tại chỗ khi input rỗng (⬜ nhỏ) |
| Slack front = `loopkit-slack` | ✅ | behavior không đổi; AGENTS.md đọc từ cwd; chạy `python -m loopkit.fronts.slack` |
| `.loopkit.*` per-repo config (cascade kiểu aider) | ⬜ | P2 |
| Roles-as-data + MCP + recipes | ⬜ | P3 (CrewAI/Goose patterns đã research) |
| Server / multi-tenant | ⬜ | P4 — chỉ khi có user ngoài |

---

## Build order (the plan of record)

- **Phase 1 — shared foundations:** `config.py` → `memory.py` (registry + session + semantic
  cache; fix journal wipe) → `shield.py` (output guard + Slack event dedupe)
- **Phase 2 — make ✅ real:** EARS→pytest real gate · brain decision + CLAUDE.md (resolve
  double-inject) · audit trail into the journal
- **Phase 3 — agent depth:** brain upgrade (real tool execution) · per-role model tiering ·
  worktree isolation · thread follow-ups
- **Phase 4 — roof (needs real-run data):** §8.1 durable → §8.3 calibration → §8.4 golden set →
  §8.2 automated risk classification
