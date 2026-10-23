"""LLM-assisted contradiction check for project facts.

The deterministic detector in FactStore is deliberately conservative.  This
module adds a semantic second opinion: it only returns names of already
retrieved related facts, and never writes anything itself.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .. import config


def _available() -> bool:
    return bool(str(getattr(config, "LLM_BASE_URL", "") or "").strip()
                and str(getattr(config, "LLM_MODEL", "") or "").strip())


def llm_conflicts(new_item: dict, candidates: list[dict],
                  llm: Callable | None = None, timeout: int = 20) -> list[str]:
    if not candidates:
        return []
    names = {str(item.get("name", "")) for item in candidates if item.get("name")}
    if not names:
        return []
    lines = []
    for index, item in enumerate(candidates[:8], 1):
        lines.append(f"[{index}] 名称：{item.get('name', '')}\n"
                     f"类型：{item.get('type', '')}\n"
                     f"描述：{item.get('description', '')}\n"
                     f"内容：{str(item.get('body', ''))[:700]}")
    prompt = f"""你是项目记忆冲突裁判。判断“新事实”是否与下面已有事实在事实上矛盾。
只有同一件事出现不同结论（例如负责人、截止、是否取消/延期、方案选择不同）才算冲突；
补充细节、正常进展、不同主题都不算。只输出 JSON：
{{"conflicts":["已有事实名称"],"reason":"简短理由"}}
没有冲突时 conflicts 为空数组。候选名称必须从下面原样选择，不得编造。

新事实：
类型：{new_item.get('fact_type', '')}
描述：{new_item.get('description', '')}
内容：{str(new_item.get('body', ''))[:1200]}

已有事实：
{chr(10).join(lines)}
"""
    try:
        if llm is not None:
            reply = llm(prompt)
            if isinstance(reply, dict):
                if not reply.get("ok"):
                    return []
                raw = str(reply.get("content", ""))
            else:
                raw = str(reply or "")
        else:
            if not _available():
                return []
            from ..llm import chat_result

            result = chat_result(prompt, temperature=0, timeout=timeout, max_tokens=300)
            if not result.get("ok"):
                return []
            raw = str(result.get("content", ""))
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group()) if match else {}
        conflicts = data.get("conflicts") if isinstance(data, dict) else []
        if not isinstance(conflicts, list):
            return []
        return [str(name) for name in conflicts if str(name) in names][:8]
    except Exception:
        return []