"""Incremental meeting-to-graph ingestion (Phase 1)."""
from __future__ import annotations

import time
from pathlib import Path

from .. import config
from ..agent.sessions import SessionCatalog
from .build import GraphBuilder
from .extract import chunk_turns, extract_chunk
from .link import EntityLinker
from .schema import relation_allowed
from .store import GraphStore


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(ts if ts else time.time()))


class GraphIngestor:
    def __init__(self, project_root: Path, data_dir: Path | None = None, llm=None):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.llm = llm
        self.catalog = SessionCatalog(self.data_dir)
        self.store = GraphStore(self.project_root)

    def ingest_session(self, session_id: str, *, force: bool = False,
                       max_chars: int = 900) -> dict:
        session = self.catalog.load(session_id)
        turns = session.get("turns") or []
        report = {"ok": True, "session_id": session_id, "chunks": 0,
                  "entities": 0, "relations": 0, "skipped": 0, "errors": []}
        if not turns:
            report.update(ok=False, errors=[{"stage": "load", "code": "empty_transcript"}])
            return report
        # Rules first so the LLM can link to existing people/meetings/facts.
        GraphBuilder(self.project_root, self.data_dir).build(
            include_meetings=True, reset=False)
        linker = EntityLinker(self.store, self.llm)
        meeting_node = self.store.resolve(f"会议 {session_id}", kind="Meeting")
        project_node = self.store.resolve(self.project_root.name, kind="Project")
        special = {}
        if meeting_node:
            special.update({"本次会议": meeting_node, "会议": meeting_node,
                            "Meeting": meeting_node})
        if project_node:
            special.update({"本项目": project_node, "项目": project_node,
                            "Project": project_node})
        for index, chunk in enumerate(chunk_turns(turns, max_chars=max_chars)):
            report["chunks"] += 1
            episode_id = f"meeting:{session_id}:{index}"
            if not force and self.store.episode_hash(episode_id) == chunk["hash"]:
                report["skipped"] += 1
                continue
            try:
                parsed = extract_chunk(chunk, self.llm)
            except Exception as exc:
                report["errors"].append({"stage": "extract", "chunk": index,
                                         "code": type(exc).__name__})
                continue
            linked = {}
            for entity in parsed["entities"]:
                if entity["surface"] in special:
                    linked[entity["surface"]] = special[entity["surface"]]
                    continue
                try:
                    node = linker.link(
                        surface=entity["surface"], kind=entity["kind"],
                        aliases=entity["aliases"], confidence=entity["confidence"],
                        description=entity.get("evidence_text", "")[:500])
                except (ValueError, OSError):
                    report["errors"].append({"stage": "link", "chunk": index,
                                             "surface": entity["surface"]})
                    continue
                linked[entity["surface"]] = node
                for alias in entity["aliases"]:
                    linked.setdefault(alias, node)
                report["entities"] += 1
            valid_from = _iso(chunk.get("start_ts"))
            for relation in parsed["relations"]:
                src = linked.get(relation["head"])
                dst = linked.get(relation["tail"])
                if not src or not dst or src["id"] == dst["id"]:
                    report["errors"].append({"stage": "relation", "chunk": index,
                                             "relation": relation["relation"]})
                    continue
                if not relation_allowed(src["kind"], relation["relation"], dst["kind"]):
                    report["errors"].append({"stage": "relation_type", "chunk": index,
                                             "relation": relation["relation"]})
                    continue
                try:
                    self.store.upsert_edge(
                        src_id=src["id"], dst_id=dst["id"],
                        relation=relation["relation"], valid_from=valid_from,
                        source_type="meeting", source_id=session_id,
                        session_id=session_id, chunk_id=episode_id,
                        turn_ids=relation["evidence_turn_ids"],
                        quote=relation["evidence_text"],
                        confidence=relation["confidence"], extractor="llm",
                        metadata={"chunk": index})
                    report["relations"] += 1
                except (ValueError, OSError) as exc:
                    report["errors"].append({"stage": "edge", "chunk": index,
                                             "code": type(exc).__name__})
            self.store.upsert_episode(
                episode_id=episode_id, source_type="meeting", source_id=session_id,
                session_id=session_id, occurred_at=valid_from,
                content_hash=chunk["hash"], text=chunk["text"])
        return report

    def ingest_all(self, *, limit: int = 20, force: bool = False) -> dict:
        reports = []
        for row in self.catalog.list_sessions(limit=limit):
            reports.append(self.ingest_session(row["session_id"], force=force))
        return {"ok": all(row.get("ok") for row in reports), "sessions": len(reports),
                "entities": sum(row.get("entities", 0) for row in reports),
                "relations": sum(row.get("relations", 0) for row in reports),
                "reports": reports}