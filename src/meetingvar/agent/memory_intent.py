"""Turn an explicit chat instruction into a reviewed project-memory candidate.

No model is required: the user's sentence is the evidence.  The chat action is
only a proposal; ``ChatAgent.execute_action`` writes it after confirmation.
"""
from __future__ import annotations

import re
import time

_TRIGGER = re.compile(
    r"(?:请|帮我|给我)?(?:记住|记一下|记下|别忘了|不要忘记|存到(?:项目)?记忆|"
    r"加入(?:项目)?记忆|记到(?:项目)?记忆)[：:，,\s]*(.*)", re.S)
_PRONOUN = re.compile(r"^(?:这件事|这个|刚才|上面|前面|这些|它)[。！？!?.\s]*$")
_PREFERENCE = re.compile(r"喜欢|不喜欢|偏好|习惯|以后都|默认|别再|不要再用|更倾向")
_DECISION = re.compile(r"决定|定了|确定|拍板|结论|采用|改成|改为")
_COMMITMENT = re.compile(r"承诺|答应|保证|一定|保证完成")
_RISK = re.compile(r"风险|担心|可能延期|阻塞|依赖|问题很大")
_QUESTION = re.compile(r"待定|未决|还没定|需要确认|待确认|问题")
_ACTION = re.compile(r"负责|交付|截止|完成|跟进|安排|排期|下周|明天|周五|月底")


def is_memory_request(text: str) -> bool:
    return bool(_TRIGGER.search(str(text or "").strip()))


def _clip(text: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _classify(text: str) -> str:
    if _PREFERENCE.search(text):
        return "preference"
    if _COMMITMENT.search(text):
        return "commitment"
    if _RISK.search(text):
        return "risk"
    if _DECISION.search(text):
        return "decision"
    if _QUESTION.search(text):
        return "open-question"
    if _ACTION.search(text):
        return "action-item"
    return "project-fact"


def propose_memory(text: str, history: list[dict] | None = None,
                   conversation_id: str = "") -> list[dict]:
    message = str(text or "").strip()
    match = _TRIGGER.search(message)
    if not match:
        return []
    content = match.group(1).strip()
    context = ""
    if _PRONOUN.match(content) or len(content) < 4:
        recent = [m for m in (history or []) if m.get("content")][-4:]
        if recent:
            context = "\n".join(
                f"{'用户' if m.get('role') == 'user' else '助手'}：{str(m.get('content'))[:500]}"
                for m in recent)
            content = context
        else:
            return []
    content = content.strip(" ：:，,。\n")
    if not content:
        return []
    fact_type = _classify(content)
    title = _clip(content.splitlines()[0], 72)
    description = title
    body = content
    if context and title in {"用户", "助手"}:
        title = _clip(context.splitlines()[0], 72)
    body = (f"{body}\n\n"
            f"- 来源：对话 {conversation_id or '当前对话'}\n"
            f"- 记录时间：{time.strftime('%Y-%m-%d %H:%M')}\n"
            f"- 类型：{fact_type}（由用户显式要求记住）")
    ref = f"conversation:{conversation_id}" if conversation_id else "conversation:current"
    return [{
        "fact_type": fact_type,
        "title": title,
        "description": description,
        "body": body,
        "confidence": 0.9,
        "source_refs": [ref],
        "origin_meeting": f"chat:{conversation_id}" if conversation_id else "chat:current",
    }]