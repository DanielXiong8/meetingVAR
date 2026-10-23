"""Reranking for memory/RAG results.

Primary path is a listwise LLM reranker (small local model or configured API).
If no model is configured/reachable, it falls back to BGE cosine similarity and
then to the original hybrid order.  Reranking never invents new candidates.
"""
from __future__ import annotations

import json
import re

from .. import config
from .embeddings import get_embedding_provider


def _text(item: dict, key: str = "text") -> str:
    return str(item.get(key) or item.get("body") or item.get("description") or item.get("name") or "")


def embedding_rerank(query: str, candidates: list[dict], *, top_k: int = 8,
                     text_key: str = "text") -> list[dict]:
    if len(candidates) <= 1:
        return candidates[:top_k]
    embedder = get_embedding_provider()
    vectors = embedder.encode([query] + [_text(item, text_key)[:1800] for item in candidates])
    if not vectors or len(vectors) != len(candidates) + 1:
        return candidates[:top_k]
    q = vectors[0]
    scored = []
    for item, vector in zip(candidates, vectors[1:]):
        denom = (sum(x * x for x in q) ** 0.5) * (sum(x * x for x in vector) ** 0.5)
        cosine = sum(a * b for a, b in zip(q, vector)) / denom if denom else 0.0
        row = dict(item)
        row["_rerank"] = round(cosine, 4)
        scored.append((cosine, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for _, row in scored[:top_k]]


def _llm_available() -> bool:
    return bool(str(getattr(config, "LLM_BASE_URL", "") or "").strip()
                and str(getattr(config, "LLM_MODEL", "") or "").strip())


def llm_rerank(query: str, candidates: list[dict], *, top_k: int = 8,
               text_key: str = "text", timeout: int = 20) -> list[dict]:
    if len(candidates) <= 1:
        return candidates[:top_k]
    if not _llm_available():
        return embedding_rerank(query, candidates, top_k=top_k, text_key=text_key)
    from ..llm import chat_result

    lines = []
    for index, item in enumerate(candidates[:20], 1):
        title = item.get("name") or item.get("title") or item.get("source_id") or ""
        lines.append(f"[{index}] {title}\n{_text(item, text_key)[:700]}")
    prompt = f"""你是检索重排器。根据问题，对候选资料按“能否直接回答问题”排序。
只输出 JSON：{{"ranking":[序号,...]}}，只包含真正相关的候选；不相关的不填。
问题：{query}
候选：
{chr(10).join(lines)}
"""
    result = chat_result(prompt, temperature=0, timeout=timeout, max_tokens=300)
    if not result.get("ok"):
        return embedding_rerank(query, candidates, top_k=top_k, text_key=text_key)
    try:
        match = re.search(r"\{.*\}", str(result.get("content", "")), re.S)
        data = json.loads(match.group()) if match else {}
        ranking = data.get("ranking") if isinstance(data, dict) else None
        order = []
        for value in ranking or []:
            try:
                index = int(value) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(candidates) and index not in order:
                order.append(index)
        if not order:
            return embedding_rerank(query, candidates, top_k=top_k, text_key=text_key)
        order.extend(index for index in range(len(candidates)) if index not in order)
        return [candidates[index] for index in order[:top_k]]
    except Exception:
        return embedding_rerank(query, candidates, top_k=top_k, text_key=text_key)