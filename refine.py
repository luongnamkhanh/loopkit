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
import config, gates, roles
from engine import ask_claude


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


def _valid_draft(draft: str) -> bool:
    goal, dod, tests = gates.parse_ticket(draft)
    return bool(goal and dod and tests)


def refine_turn(idea, history, turns_used, max_turns, ask=ask_claude):
    """Một lượt analyst. history = [{'role': 'analyst'|'user', 'text': ...}, ...].
    -> ('ask', q) | ('draft', ticket) | ('draft_unvalidated', ticket) | ('error', '')."""
    soul = roles.REGISTRY["analyst"].soul
    model = config.ROLE_MODELS.get("analyst")
    convo = "\n".join(f"{h['role']}: {h['text']}" for h in history)
    forced = turns_used >= max_turns
    prompt = (f"RAW IDEA:\n{idea}\n\nCONVERSATION SO FAR:\n{convo or '(none)'}\n\n"
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
        if _valid_draft(text):
            return "draft", text
        _, text = _parse_reply(ask(
            f"{prompt}\n\nYour draft FAILED the format gate. Required: '<goal> DoD: <EARS "
            f"criteria> Tests: ```python ...```' — tests import from `solution`, define test_* "
            f"functions. Output the corrected TICKET.\n\nPREVIOUS DRAFT:\n{text}",
            soul, model=model))
    return ("draft", text) if _valid_draft(text) else ("draft_unvalidated", text)
