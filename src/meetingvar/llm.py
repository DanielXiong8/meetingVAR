"""共享 LLM 客户端（OpenAI 兼容 /chat/completions）。

未配置 MEETINGVAR_LLM_BASE_URL / _MODEL 时返回 None——所有调用方都必须
优雅降级（模板版/规则版），这是"诚实标注哪些环节是模拟"的代码级保证。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


def chat(prompt: str, temperature: float = 0.3, timeout: int = 900) -> str | None:
    if not (config.LLM_BASE_URL and config.LLM_MODEL):
        return None
    try:
        max_tokens = int(config.LLM_MAX_TOKENS)
        if max_tokens <= 0:
            raise ValueError
    except (ValueError, TypeError):
        print("[llm] 输出上限配置无效，回落离线模板/规则层")
        return None
    body = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if config.LLM_THINKING:
        if config.LLM_THINKING not in ("enabled", "disabled"):
            print("[llm] 思考模式配置无效，回落离线模板/规则层")
            return None
        body["thinking"] = {"type": config.LLM_THINKING}
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        choice = data["choices"][0]
        if not isinstance(choice, dict):
            return None
        # A capped response must not be presented as complete meeting minutes.
        if choice.get("finish_reason") == "length":
            print("[llm] 输出达到上限，回落离线模板/规则层")
            return None
        content = choice["message"]["content"]
        if not isinstance(content, str):
            return None
    except (OSError, urllib.error.URLError, ValueError, KeyError, IndexError, TypeError) as exc:
        # Optional enhancement must not prevent local evidence/report delivery.
        # Do not log request headers, prompts, keys or server response bodies.
        print(f"[llm] 请求不可用（{type(exc).__name__}），回落离线模板/规则层")
        return None
    # Qwen3 等思考模型会输出 <think>…</think>；抽取类任务不需要，通用剥离
    import re

    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip() or None
