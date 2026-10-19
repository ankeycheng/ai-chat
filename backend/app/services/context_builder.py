"""History context building rules (spec v1.2 §5.9).

* Only *completed* turns (user message + its assistant reply) are included; user
  messages of failed/interrupted runs are skipped.
* Assistant ``reasoning_content`` is NEVER sent back (no ``tools`` are used, so per
  DeepSeek thinking-mode docs it would be ignored anyway, and it wastes budget).
* Search results are injected only for the current turn, as a system message placed
  directly before the current user message; historical search material is not re-sent.
* Budget: estimated tokens (system + search + current user + history) ≤
  CONTEXT_MAX_INPUT_TOKENS; older turns are dropped whole (oldest first), never cut
  mid-message. If the mandatory part does not fit, the search block is shrunk first;
  if it still does not fit → ContextTooLong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.errors import ContextTooLong
from app.models.entities import MessageRow

PER_MESSAGE_OVERHEAD = 4


def estimate_tokens(text: str) -> int:
    """Conservative estimate following DeepSeek's guide (≈0.3 token/ASCII char, ≈0.6/CJK char) ×1.2."""
    ascii_n = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - ascii_n
    return math.ceil((ascii_n * 0.3 + other * 0.6) * 1.2)


def msg_tokens(m: dict) -> int:
    return estimate_tokens(m.get("content") or "") + PER_MESSAGE_OVERHEAD


@dataclass
class ContextStats:
    included_turns: int
    dropped_turns: int
    estimated_tokens: int
    search_chars: int


def completed_turns(history: list[tuple[MessageRow, str | None]]) -> list[tuple[MessageRow, MessageRow]]:
    users: dict[str, MessageRow] = {}
    turns: list[tuple[MessageRow, MessageRow]] = []
    for msg, run_status in history:
        if msg.role == "user":
            users[msg.request_id] = msg
        elif msg.role == "assistant":
            u = users.get(msg.request_id)
            if u is not None and run_status in (None, "completed") and msg.content.strip():
                turns.append((u, msg))
    turns.sort(key=lambda t: t[0].sequence_no)
    return turns


def build_messages(
    *,
    system_prompt: str,
    history: list[tuple[MessageRow, str | None]],
    current_user: str,
    search_block: str | None,
    max_input_tokens: int,
    max_history_messages: int,
) -> tuple[list[dict], ContextStats]:
    system = {"role": "system", "content": system_prompt}
    current = {"role": "user", "content": current_user}
    search_msg = {"role": "system", "content": search_block} if search_block else None

    fixed = msg_tokens(system) + msg_tokens(current)
    if search_msg:
        budget_left = max_input_tokens - fixed
        if msg_tokens(search_msg) > budget_left:
            # shrink search material proportionally (keep the head which holds S1..Sn order)
            keep = max(0, int(len(search_block) * max(budget_left, 0) / max(msg_tokens(search_msg), 1)) - 50)
            search_msg = {"role": "system", "content": search_block[:keep]} if keep > 200 else None
        if search_msg:
            fixed += msg_tokens(search_msg)
    if fixed > max_input_tokens:
        raise ContextTooLong("当前问题超出上下文预算（CONTEXT_MAX_INPUT_TOKENS）")

    turns = completed_turns(history)
    chosen: list[tuple[MessageRow, MessageRow]] = []
    total = fixed
    max_turns = max_history_messages // 2
    for u, a in reversed(turns):
        if len(chosen) >= max_turns:
            break
        cost = estimate_tokens(u.content) + estimate_tokens(a.content) + 2 * PER_MESSAGE_OVERHEAD
        if total + cost > max_input_tokens:
            break
        chosen.append((u, a))
        total += cost
    chosen.reverse()

    messages: list[dict] = [system]
    for u, a in chosen:
        messages.append({"role": "user", "content": u.content})
        messages.append({"role": "assistant", "content": a.content})  # reasoning_content deliberately omitted
    if search_msg:
        messages.append(search_msg)
    messages.append(current)
    stats = ContextStats(
        included_turns=len(chosen),
        dropped_turns=len(turns) - len(chosen),
        estimated_tokens=total,
        search_chars=len(search_msg["content"]) if search_msg else 0,
    )
    return messages, stats
