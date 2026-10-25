"""Entity linking for graph extraction.

Exact name/alias first, then BGE cosine within the same node kind.  The LLM is
only consulted for the gray zone; ambiguous matches create a new node rather
than silently merging two people/projects.
"""
from __future__ import annotations

import json
import re

from ..agent.embeddings import get_embedding_provider
from .store import GraphStore


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = (sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5)
    return sum(x * y for x, y in zip(a, b)) / denom if denom else 0.0


def _adjudicate(llm, surface: str, kind: str, candidate: dict) -> bool:
    if llm is None:
        return False
    prompt = f"""判断两个名称是否指向同一个{kind}实体。
只输出 JSON：{{"same":true/false}}。不确定时输出 false。

名称 A：{surface}
名称 B：{candidate.get('name', '')}
B 的别名：{', '.join(candidate.get('aliases') or [])}
B 的描述：{str(candidate.get('description', ''))[:300]}
"""
    try:
        reply = llm(prompt)
        if isinstance(reply, dict):
            if not reply.get("ok"):
                return False
            raw = str(reply.get("content", ""))
        else:
            raw = str(reply or "")
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group()) if match else {}
        return data.get("same") is True
    except Exception:
        return False


class EntityLinker:
    def __init__(self, store: GraphStore, llm=None):
        self.store = store
        self.llm = llm
        self.embedder = get_embedding_provider()

    def link(self, *, surface: str, kind: str, aliases: list[str] | None = None,
             description: str = "", confidence: float = 0.7) -> dict:
        surface = str(surface or "").strip()
        aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        if not surface:
            raise ValueError("entity surface is required")
        exact = self.store.resolve(surface, kind=kind)
        if exact:
            return self.store.upsert_node(
                kind=kind, name=exact["name"], aliases=[surface] + aliases,
                description=description or exact.get("description", ""),
                confidence=max(float(exact.get("confidence", 0.0)), float(confidence)),
                status=exact.get("status", "active"),
                metadata=exact.get("metadata", {}))
        for alias in aliases:
            found = self.store.resolve(alias, kind=kind)
            if found:
                return self.store.upsert_node(
                    kind=kind, name=found["name"], aliases=[surface] + aliases,
                    description=description or found.get("description", ""),
                    confidence=max(float(found.get("confidence", 0.0)), float(confidence)),
                    status=found.get("status", "active"),
                    metadata=found.get("metadata", {}))
        candidates = self.store.nodes(kind=kind, limit=2000)
        if candidates and self.embedder.available:
            texts = [surface] + [
                " ".join([node.get("name", "")] + list(node.get("aliases") or [])
                         + [str(node.get("description", ""))[:300]])
                for node in candidates
            ]
            vectors = self.embedder.encode(texts)
            if vectors and len(vectors) == len(texts):
                scored = [(_cosine(vectors[0], vector), node)
                          for node, vector in zip(candidates, vectors[1:])]
                scored.sort(key=lambda pair: -pair[0])
                score, best = scored[0]
                if score >= 0.88 or (score >= 0.65 and _adjudicate(
                        self.llm, surface, kind, best)):
                    return self.store.upsert_node(
                        kind=kind, name=best["name"], aliases=[surface] + aliases,
                        description=description or best.get("description", ""),
                        confidence=max(float(best.get("confidence", 0.0)), float(confidence)),
                        status=best.get("status", "active"),
                        metadata=best.get("metadata", {}))
        return self.store.upsert_node(
            kind=kind, name=surface, aliases=aliases, description=description,
            confidence=confidence, status="active",
            metadata={"provisional": True, "source": "llm_extraction"})