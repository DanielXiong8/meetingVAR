"""Rule-based graph builder (Phase 0).

Only uses existing FactStore facts plus saved meeting speakers.  No LLM calls.
This is the low-risk baseline that lets us measure whether graph queries add
value over RAG before investing in LLM extraction.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .. import config
from ..agent.memory import FactStore
from ..agent.sessions import SessionCatalog
from .schema import norm_name
from .store import GraphStore

_FACT_KIND = {
    "decision": "Decision",
    "action-item": "Action",
    "commitment": "Commitment",
    "risk": "Risk",
    "open-question": "OpenQuestion",
    "project-fact": "Topic",
    "preference": "Topic",
}
_PREFIX = re.compile(
    r"^(?:action-item|decision|commitment|project-fact|preference|risk|open-question)\s*[:：]\s*",
    re.I)
_SPEAKER = re.compile(r"^spk\d+$", re.I)
_OWNER = re.compile(r"(?:负责人|由|owner)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z]{2,12})")
_DUE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日|周[一二三四五六日天]|下周[一二三四五六日天]?")


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(ts if ts else time.time()))


class GraphBuilder:
    def __init__(self, project_root: Path, data_dir: Path | None = None):
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir) if data_dir else Path(config.DATA_DIR)
        self.fact_store = FactStore(self.project_root)
        self.store = GraphStore(self.project_root)

    @staticmethod
    def _display_name(fact: dict) -> str:
        value = str(fact.get("description") or fact.get("name") or "").strip()
        return _PREFIX.sub("", value).strip() or str(fact.get("name") or "")

    @staticmethod
    def _fact_valid(fact: dict) -> tuple[str, str]:
        meta = fact.get("metadata", {}) or {}
        start = str(meta.get("valid_from") or meta.get("created_at") or "")
        end = str(meta.get("valid_to") or "")
        return start or _iso(), end

    def build(self, *, include_meetings: bool = True, reset: bool = True) -> dict:
        if reset:
            self.store.reset()
        project = self.store.upsert_node(
            kind="Project", name=self.project_root.name or "项目",
            description=f"项目目录：{self.project_root}", confidence=1.0)
        meetings: dict[str, dict] = {}
        if include_meetings:
            try:
                catalog = SessionCatalog(self.data_dir)
                sessions = catalog.list_sessions(limit=200)
            except (OSError, ValueError):
                sessions = []
            for row in sessions:
                try:
                    session = catalog.load(row["session_id"])
                except (OSError, ValueError):
                    continue
                occurred = _iso(row.get("mtime"))
                meeting = self.store.upsert_node(
                    kind="Meeting", name=f"会议 {row['session_id']}",
                    description=f"{occurred} · {len(session.get('turns', []))} 句",
                    aliases=[row["session_id"]], confidence=0.95,
                    metadata={"session_id": row["session_id"], "mtime": row.get("mtime")},
                    observed_at=occurred)
                meetings[row["session_id"]] = meeting
                self.store.upsert_edge(
                    src_id=meeting["id"], dst_id=project["id"], relation="part_of",
                    valid_from=occurred, source_type="meeting", source_id=row["session_id"],
                    session_id=row["session_id"], confidence=0.95, extractor="rule")
                speakers = []
                for turn in session.get("turns", []):
                    speaker = str(turn.get("speaker", "")).strip()
                    if speaker and not _SPEAKER.fullmatch(speaker) and speaker not in speakers:
                        speakers.append(speaker)
                for speaker in speakers:
                    person = self.store.upsert_node(
                        kind="Person", name=speaker, confidence=0.8,
                        metadata={"source": "speaker"}, observed_at=occurred)
                    self.store.upsert_edge(
                        src_id=person["id"], dst_id=meeting["id"], relation="speaks_in",
                        valid_from=occurred, source_type="meeting",
                        source_id=row["session_id"], session_id=row["session_id"],
                        confidence=0.8, extractor="rule")

        facts = self.fact_store.list_facts(limit=5000, status="all", include_inactive=True)
        for fact in facts:
            kind = _FACT_KIND.get(str(fact.get("type", "")), "Topic")
            display = self._display_name(fact)
            aliases = []
            raw_name = str(fact.get("name") or "")
            stripped = _PREFIX.sub("", raw_name).strip()
            if stripped and stripped != display:
                aliases.append(stripped)
            start, end = self._fact_valid(fact)
            origin_id = str(fact.get("origin_meeting") or "")
            meeting = meetings.get(origin_id)
            if meeting and (meeting.get("metadata") or {}).get("mtime"):
                start = _iso(meeting["metadata"]["mtime"])
            fact_status = str(fact.get("status", "active"))
            node_status = "provisional" if fact_status == "pending-conflict" else "active"
            node = self.store.upsert_node(
                kind=kind, name=display,
                description=str(fact.get("body", ""))[:800],
                aliases=aliases, status=node_status,
                confidence=float(fact.get("confidence") or 0.8),
                metadata={"fact_name": raw_name, "fact_type": fact.get("type"),
                          "fact_status": fact_status,
                          "version": fact.get("version"),
                          "owner": fact.get("owner", ""),
                          "due": fact.get("due", "")},
                observed_at=start)
            edge_status = "pending" if fact_status == "pending-conflict" else "asserted"
            self.store.upsert_edge(
                src_id=node["id"], dst_id=project["id"], relation="part_of",
                valid_from=start, valid_to=end, status=edge_status,
                source_type="fact", source_id=raw_name,
                turn_ids=list(fact.get("source_refs") or []),
                quote=str(fact.get("body", ""))[:500],
                confidence=float(fact.get("confidence") or 0.8),
                extractor="rule",
                metadata={"fact_type": fact.get("type"), "fact_status": fact_status})
            if not meeting and origin_id and not origin_id.startswith("chat:"):
                meeting = self.store.upsert_node(
                    kind="Meeting", name=f"会议 {origin_id}",
                    aliases=[origin_id], status="provisional", confidence=0.7,
                    metadata={"session_id": origin_id}, observed_at=start)
                meetings[origin_id] = meeting
                self.store.upsert_edge(
                    src_id=meeting["id"], dst_id=project["id"], relation="part_of",
                    valid_from=start, source_type="fact", source_id=raw_name,
                    session_id=origin_id, confidence=0.7, extractor="rule")
            if meeting:
                self.store.upsert_edge(
                    src_id=node["id"], dst_id=meeting["id"], relation="derived_from",
                    valid_from=start, valid_to=end, status=edge_status,
                    source_type="fact", source_id=raw_name, session_id=origin_id,
                    turn_ids=list(fact.get("source_refs") or []),
                    quote=str(fact.get("body", ""))[:500],
                    confidence=float(fact.get("confidence") or 0.8), extractor="rule")
                if kind == "Decision":
                    self.store.upsert_edge(
                        src_id=node["id"], dst_id=meeting["id"], relation="decided_in",
                        valid_from=start, valid_to=end, status=edge_status,
                        source_type="fact", source_id=raw_name, session_id=origin_id,
                        confidence=float(fact.get("confidence") or 0.8), extractor="rule")
            owner = str(fact.get("owner") or "").strip()
            if not owner:
                match = _OWNER.search(str(fact.get("body", "")))
                owner = match.group(1) if match else ""
            if owner and kind in {"Action", "Commitment", "Decision"}:
                person = self.store.upsert_node(
                    kind="Person", name=owner, confidence=0.8,
                    metadata={"source": "fact_owner"}, observed_at=start)
                self.store.upsert_edge(
                    src_id=node["id"], dst_id=person["id"],
                    relation="assigned_to" if kind != "Decision" else "owned_by",
                    valid_from=start, valid_to=end, status=edge_status,
                    source_type="fact", source_id=raw_name,
                    turn_ids=list(fact.get("source_refs") or []),
                    confidence=float(fact.get("confidence") or 0.8), extractor="rule")
            due = str(fact.get("due") or "").strip()
            if not due:
                match = _DUE.search(str(fact.get("body", "")))
                due = match.group(0) if match else ""
            if due and kind in {"Action", "Commitment", "Decision"}:
                date = self.store.upsert_node(kind="Date", name=due, confidence=0.8)
                self.store.upsert_edge(
                    src_id=node["id"], dst_id=date["id"], relation="due_on",
                    valid_from=start, valid_to=end, status=edge_status,
                    source_type="fact", source_id=raw_name,
                    confidence=float(fact.get("confidence") or 0.8), extractor="rule")
        return {"ok": True, "project": str(self.project_root),
                "facts": len(facts), "meetings": len(meetings),
                **self.store.stats()}