"""
loopkit.refine — idea-refinement stage: ý tưởng thô -> Q&A có giới hạn -> ticket đầy đủ.

Một instance loop framework đúng nghĩa:
  worker = role analyst (mỗi lượt MỘT câu hỏi, hoặc ticket cuối)
  gate   = gates.parse_ticket (goal + DoD + Tests AST-valid) — deterministic, TRƯỚC human
  retry  = gate fail feedback về analyst, tối đa 2 lần
  stop   = max_turns (config.REFINE_MAX_TURNS); chạm trần -> BUỘC draft
  door   = nút Approve & Run (wire ở slack_app)
  memory = STATELESS per turn: caller đưa full history đọc từ disk -> restart-safe by construction

Protocol fail-closed (bài học vụ VERDICT bị chôn): thiếu marker QUESTION:/TICKET: -> coi là
question, KHÔNG BAO GIỜ tự thành draft.
"""
from loopkit import config, gates, roles
from loopkit.engine import ask_claude


def _parse_reply(reply: str):
    """Marker sớm nhất thắng; text từ marker đến hết (question có thể nhiều dòng A/B/C)."""
    text = reply or ""
    up = text.upper()
    found = [(i, kind, m) for i, kind, m in
             ((up.find("TICKET:"), "draft", "TICKET:"),
              (up.find("QUESTION:"), "ask", "QUESTION:")) if i >= 0]
    if not found:
        return "ask", text.strip()
    i, kind, marker = min(found)
    return kind, text[i + len(marker):].strip()


def _valid_draft(draft: str, repos=None) -> bool:
    name, rest = gates.parse_repo(draft)
    _, rest = gates.parse_deliver(rest)
    gate_cmd, rest = gates.parse_gate_cmd(rest)
    if repos is not None and name is not None:
        active = repos.get("active", [])
        pending = repos.get("pending", [])
        if name not in active and not (name in pending and gate_cmd):
            return False                     # pending chỉ hợp lệ khi draft có Gate:
    goal, dod, tests = gates.parse_ticket(rest)
    return bool(goal and dod and (tests or gate_cmd))   # edit-mode draft: Gate thay Tests


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
                    f"  pending (usable ONLY with a `Gate: <one shell command>` line in the "
                    f"ticket): {', '.join(repos.get('pending', [])) or '(none)'}\n")
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
            f"functions; if a 'Repo:' name is present it MUST be an active repo, OR a pending "
            f"repo WITH a `Gate: <shell command>` line (edit-mode drafts need Goal+DoD+Gate, no "
            f"Tests). Output the corrected TICKET.\n\nPREVIOUS DRAFT:\n{text}",
            soul, model=model))
    return ("draft", text) if _valid_draft(text, repos) else ("draft_unvalidated", text)
