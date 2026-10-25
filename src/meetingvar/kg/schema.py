"""Knowledge-graph vocabulary and SQLite schema (Phase 0).

Small, closed vocabulary is intentional: rule-based extraction and small local
models must not invent arbitrary node/relation names.
"""
from __future__ import annotations

import hashlib
import re

NODE_KINDS = {
    "Person", "Team", "Project", "Meeting", "Decision", "Action",
    "Commitment", "Risk", "OpenQuestion", "Requirement", "Topic",
    "Document", "Artifact", "Date",
}

RELATIONS = {
    "assigned_to", "owned_by", "member_of", "participates_in", "speaks_in",
    "about", "decided_in", "derived_from", "supersedes", "contradicts",
    "depends_on", "blocks", "mitigates", "due_on", "part_of", "references",
    "caused_by", "resolves", "related_to",
}

_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def norm_name(value: str) -> str:
    return _SAFE.sub("", str(value or "").casefold())[:160]


def node_id(kind: str, name: str) -> str:
    digest = hashlib.sha1(f"{kind}:{norm_name(name)}".encode("utf-8")).hexdigest()[:16]
    return f"{kind.casefold()}:{digest}"


def edge_id(src_id: str, relation: str, dst_id: str, valid_from: str = "") -> str:
    raw = f"{src_id}|{relation}|{dst_id}|{valid_from}"
    return "e:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL DEFAULT 0.8,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY,
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    label TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'asserted',
    valid_from TEXT NOT NULL,
    valid_to TEXT DEFAULT '',
    observed_at TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    chunk_id TEXT DEFAULT '',
    turn_ids_json TEXT NOT NULL DEFAULT '[]',
    quote TEXT DEFAULT '',
    extractor TEXT NOT NULL,
    supersedes TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kg_episodes (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    occurred_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kg_aliases (
    alias TEXT NOT NULL,
    node_id TEXT NOT NULL,
    source TEXT DEFAULT '',
    confidence REAL DEFAULT 0.8,
    PRIMARY KEY (alias, node_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON kg_edges(src_id, relation, status);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON kg_edges(dst_id, relation, status);
CREATE INDEX IF NOT EXISTS idx_edges_valid ON kg_edges(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON kg_nodes(kind, status);
CREATE VIRTUAL TABLE IF NOT EXISTS kg_nodes_fts USING fts5(
    node_id UNINDEXED, tokens, name, description, tokenize='unicode61'
);
"""