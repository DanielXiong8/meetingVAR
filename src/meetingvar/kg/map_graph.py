"""Deterministic knowledge-graph projection from the repository map.

This is not LLM extraction: it projects already observed sources, headings,
symbols and verified map claims into graph nodes/edges with source revisions.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..memory.repository_map import read_map
from .schema import NODE_KINDS, relation_allowed
from .store import GraphStore

MAX_TOPICS_PER_SOURCE = 12
MAX_TOTAL_TOPICS = 800


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def build_map_graph(project_root: Path | str) -> dict:
    root = Path(project_root).resolve()
    snapshot = read_map(root, limit=2000, include_claims=True)
    if snapshot.get("map_status") == "not_built":
        return {"ok": False, "complete": False, "error_code": "map_not_built",
                "error": "项目地图尚未建立", "nodes": 0, "edges": 0, "errors": []}
    store = GraphStore(root)
    project = store.upsert_node(kind="Project", name=root.name or "项目",
                                description=f"项目目录：{root}", confidence=1.0)
    # Map projection must be idempotent: the edge identity includes valid_from,
    # so a wall-clock second would create duplicate edges on a second build.
    observed = "map:" + str(snapshot.get("map_revision") or "")[:24]
    nodes = 0
    edges = 0
    errors: list[dict] = []
    by_path: dict[str, list[dict]] = {}
    for unit in snapshot.get("units") or []:
        path = str(unit.get("path") or "")
        if not path:
            continue
        by_path.setdefault(path, []).append(unit)
    topics_created = 0
    documents: dict[str, dict] = {}
    for source in snapshot.get("sources") or []:
        if source.get("status") != "current" or not source.get("complete"):
            continue
        relative = str(source.get("path") or "")
        if not relative:
            continue
        document = store.upsert_node(
            kind="Document", name=relative, description=f"{source.get('kind','')} 项目来源",
            aliases=[Path(relative).name], confidence=1.0,
            metadata={"path": relative, "source_revision": source.get("extraction_revision", ""),
                      "bytes_hash": source.get("bytes_hash", "")},
            observed_at=observed)
        documents[relative] = document
        nodes += 1
        try:
            store.upsert_edge(src_id=document["id"], dst_id=project["id"], relation="about",
                              valid_from=observed, source_type="document", source_id=relative,
                              confidence=1.0, extractor="map")
            edges += 1
        except ValueError:
            pass
        units = by_path.get(relative) or []
        candidates = [unit for unit in units if unit.get("kind") in {"heading", "symbol"}]
        candidates.sort(key=lambda unit: ({"heading": 0, "symbol": 1}.get(unit.get("kind"), 2),
                                          unit.get("start", 0)))
        seen = set()
        for unit in candidates:
            if len(seen) >= MAX_TOPICS_PER_SOURCE or topics_created >= MAX_TOTAL_TOPICS:
                break
            label = str(unit.get("label") or "").strip()
            if not label or label in seen or label.startswith("字符 ") or label.startswith("第 "):
                continue
            seen.add(label)
            try:
                topic = store.upsert_node(kind="Topic", name=label[:120], confidence=0.9,
                                          metadata={"source": "map", "path": relative,
                                                    "line_start": unit.get("line_start"),
                                                    "line_end": unit.get("line_end")},
                                          observed_at=observed)
            except ValueError:
                continue
            topics_created += 1
            nodes += 1
            try:
                store.upsert_edge(src_id=document["id"], dst_id=topic["id"], relation="about",
                                  valid_from=observed, source_type="document",
                                  source_id=relative, quote=str(unit.get("text_excerpt") or "")[:400],
                                  chunk_id=unit.get("unit_id", ""),
                                  metadata={"source_refs": [unit.get("source_ref") or {}]},
                                  confidence=0.9, extractor="map")
                edges += 1
            except ValueError as exc:
                errors.append({"stage": "edge", "code": type(exc).__name__, "path": relative})
    # Verified map claims become graph nodes linked to their document source.
    for claim in snapshot.get("claims") or []:
        text = str(claim.get("text") or "").strip()
        refs = claim.get("refs") or []
        first = next((ref for ref in refs if isinstance(ref, dict)), {})
        relative = str(first.get("path") or "")
        if not text:
            continue
        kind = str(claim.get("kind") or "Topic")
        kind = kind if kind in NODE_KINDS else "Topic"
        try:
            node = store.upsert_node(kind=kind, name=text[:100], description=text[:500],
                                     confidence=0.8,
                                     metadata={"source": "map-claim", "refs": refs},
                                     observed_at=observed)
        except ValueError:
            continue
        nodes += 1
        document = documents.get(relative)
        if document:
            try:
                store.upsert_edge(src_id=document["id"], dst_id=node["id"], relation="about",
                                  valid_from=observed, source_type="document", source_id=relative,
                                  quote=str(first.get("quote") or "")[:400],
                                  metadata={"source_refs": refs}, confidence=0.8,
                                  extractor="map-claim")
                edges += 1
            except ValueError:
                pass
    stats = store.stats()
    complete = snapshot.get("coverage", {}).get("complete") is True
    return {"ok": True, "complete": complete, "nodes": nodes, "edges": edges,
            "project": str(root), "map_revision": snapshot.get("map_revision", ""),
            "coverage": {"scope": "deterministic_map_graph",
                         "sources": snapshot.get("totals", {}).get("current_sources", 0),
                         "documents": len(documents), "topics": topics_created,
                         "claims": len(snapshot.get("claims") or []),
                         "complete": complete, "exhaustive": False,
                         "semantic_exhaustive": False},
            "stats": stats, "errors": errors}