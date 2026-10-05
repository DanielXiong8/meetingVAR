"""共享 LLM 客户端（OpenAI 兼容 /chat/completions）。

未配置 MEETINGVAR_LLM_BASE_URL / _MODEL 时返回 None——所有调用方都必须
优雅降级（模板版/规则版），这是"诚实标注哪些环节是模拟"的代码级保证。
"""
from __future__ import annotations

import json
import urllib.request

from . import config


def chat(prompt: str, temperature: float = 0.3, timeout: int = 180) -> str | None:
    if not (config.LLM_BASE_URL and config.LLM_MODEL):
        return None
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]
