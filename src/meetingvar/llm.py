"""共享 LLM 客户端（OpenAI 兼容 /chat/completions）。

未配置 MEETINGVAR_LLM_BASE_URL / _MODEL 时返回 None——所有调用方都必须
优雅降级（模板版/规则版），这是"诚实标注哪些环节是模拟"的代码级保证。
"""
from __future__ import annotations

import json
import urllib.request

from . import config


def chat(prompt: str, temperature: float = 0.3, timeout: int = 900) -> str | None:
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
    # 本机 LLM 服务绝不走系统代理（SETUP 坑：HTTP_PROXY 劫持全部请求，
    # 代理进程不在时连 127.0.0.1 的 llama.cpp 都会被拒绝）
    host = config.LLM_BASE_URL.split("//")[-1].split(":")[0].lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        resp = opener.open(req, timeout=timeout)
    else:
        resp = urllib.request.urlopen(req, timeout=timeout)
    with resp:
        data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    # Qwen3 等思考模型会输出 <think>…</think>；抽取类任务不需要，通用剥离
    import re

    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
