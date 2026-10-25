"""SQLite property-graph store for Phase 0/1.

Markdown facts and transcripts remain the source of truth.  This graph is a
rebuildable projection with stable node IDs, temporal edges and provenance.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import deque
from pathlib import Path

from ..agent.memory_index import document_tokens, query_tokens
from .schema import NODE_KINDS, RELATIONS, SCHEMA_SQL, edge_id, node_id, norm_name

_EDGE_STATUSES = {"asserted", "superseded", "retracted", "pending"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class GraphStore:
    def __init__(self, project_root: Path, path: Path | None = None):
        self.project_root = Path(project_root)
        self.root = self.project_root / "meetingvar" / "kg"
        self.db_path = Path(path) if path else self.root / "graph.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA_SQL)
        return connection

    @staticmethod
    def _node(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "kind": row["kind"], "name": row["name"],
            "canonical_name": row["canonical_name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "description": row["description"], "status": row["status"],
            "confidence": row["confidence"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _edge(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "src_id": row["src_id"], "dst_id": row["dst_id"],
            "relation": row["relation"], "label": row["label"],
            "status": row["status"], "valid_from": row["valid_from"],
            "valid_to": row["valid_to"], "observed_at": row["observed_at"],
            "confidence": row["confidence"], "source_type": row["source_type"],
            "source_id": row["source_id"], "session_id": row["session_id"],
            "chunk_id": row["chunk_id"],
            "turn_ids": json.loads(row["turn_ids_json"] or "[]"),
            "quote": row["quote"], "extractor": row["extractor"],
            "supersedes": row["supersedes"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def reset(self) -> None:
        with self._connect() as connection:
            for table in ("kg_edges", "kg_nodes", "kg_episodes", "kg_aliases", "kg_nodes_fts"):
                connection.execute(f"DELETE FROM {table}")

    def upsert_node(self, *, kind: str, name: str, description: str = "",
                    aliases: list[str] | None = None, status: str = "active",
                    confidence: float = 0.8, metadata: dict | None = None,
                    observed_at: str = "") -> dict:
        if kind not in NODE_KINDS:
            raise ValueError(f"unsupported node kind: {kind}")
        display = str(name or "").strip()
        if not display:
            raise ValueError("node name is required")
        canonical = norm_name(display)
        if not canonical:
            raise ValueError("node name has no searchable characters")
        identifier = node_id(kind, display)
        now = observed_at or _now()
        aliases = list(dict.fromkeys(str(a).strip() for a in (aliases or []) if str(a).strip()))
        with self._connect() as connection:
            old = connection.execute("SELECT * FROM kg_nodes WHERE id = ?", (identifier,)).fetchone()
            merged_aliases = aliases
            created = now
            if old:
                old_aliases = json.loads(old["aliases_json"] or "[]")
                merged_aliases = list(dict.fromkeys(old_aliases + aliases))
                created = old["created_at"]
            connection.execute("""
                INSERT INTO kg_nodes(id,kind,name,canonical_name,aliases_json,description,
                                     status,confidence,created_at,updated_at,metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, canonical_name=excluded.canonical_name,
                  aliases_json=excluded.aliases_json,
                  description=CASE WHEN excluded.description != '' THEN excluded.description
                                   ELSE kg_nodes.description END,
                  status=excluded.status,
                  confidence=MAX(kg_nodes.confidence, excluded.confidence),
                  updated_at=excluded.updated_at,
                  metadata_json=excluded.metadata_json
            """, (identifier, kind, display, canonical,
                  json.dumps(merged_aliases, ensure_ascii=False), description,
                  status, float(confidence), created, now,
                  json.dumps(metadata or {}, ensure_ascii=False)))
            for alias in merged_aliases:
                connection.execute(
                    "INSERT OR REPLACE INTO kg_aliases(alias,node_id,source,confidence) "
                    "VALUES (?,?,?,?)", (norm_name(alias), identifier, "extractor",
                                         float(confidence)))
            connection.execute("DELETE FROM kg_nodes_fts WHERE node_id = ?", (identifier,))
            tokens = " ".join(document_tokens(
                " ".join([display] + merged_aliases + [description])))
            connection.execute(
                "INSERT INTO kg_nodes_fts(node_id,tokens,name,description) VALUES (?,?,?,?)",
                (identifier, tokens, display, description))
            row = connection.execute("SELECT * FROM kg_nodes WHERE id = ?", (identifier,)).fetchone()
        return self._node(row)

    def get_node(self, identifier: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM kg_nodes WHERE id = ?",
                                     (identifier,)).fetchone()
        return self._node(row) if row else None

    def find_nodes(self, query: str, *, kind: str | None = None,
                   status: str = "active", limit: int = 10) -> list[dict]:
        tokens = query_tokens(query)
        clauses = []
        params: list = []
        if status and status != "all":
            clauses.append("n.status = ?")
            params.append(status)
        if kind:
            clauses.append("n.kind = ?")
            params.append(kind)
        if not tokens:
            return []
        match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        sql = ("SELECT n.*, bm25(kg_nodes_fts) AS rank FROM kg_nodes_fts "
               "JOIN kg_nodes n ON n.id = kg_nodes_fts.node_id "
               "WHERE kg_nodes_fts MATCH ?" + where +
               " ORDER BY rank LIMIT ?")
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, [match] + params + [max(1, limit)]).fetchall()
        except sqlite3.DatabaseError:
            rows = []
        if not rows:
            like = f"%{str(query).strip()}%"
            sql2 = "SELECT n.*, 0 AS rank FROM kg_nodes n WHERE " + " AND ".join(clauses) + \
                   " AND (n.name LIKE ? OR n.description LIKE ?) LIMIT ?"
            with self._connect() as connection:
                rows = connection.execute(sql2, params + [like, like, max(1, limit)]).fetchall()
        return [self._node(row) for row in rows]

    def nodes(self, *, kind: str | None = None, status: str = "active",
              limit: int = 1000) -> list[dict]:
        clauses = []
        params: list = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM kg_nodes" + where + " ORDER BY updated_at DESC LIMIT ?",
                params + [max(1, min(5000, int(limit)))]).fetchall()
        return [self._node(row) for row in rows]

    def resolve(self, query: str, *, kind: str | None = None) -> dict | None:
        normalized = norm_name(query)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT n.* FROM kg_nodes n LEFT JOIN kg_aliases a ON a.node_id = n.id "
                "WHERE (n.canonical_name = ? OR a.alias = ?)"
                + (" AND n.kind = ?" if kind else "") +
                " ORDER BY n.confidence DESC LIMIT 1",
                [normalized, normalized] + ([kind] if kind else [])).fetchone()
        if row:
            return self._node(row)
        found = self.find_nodes(query, kind=kind, limit=1)
        return found[0] if found else None

    def upsert_edge(self, *, src_id: str, dst_id: str, relation: str,
                    valid_from: str, source_type: str, source_id: str,
                    valid_to: str = "", observed_at: str = "",
                    status: str = "asserted", confidence: float = 0.8,
                    session_id: str = "", chunk_id: str = "",
                    turn_ids: list | None = None, quote: str = "",
                    extractor: str = "rule", supersedes: str = "",
                    metadata: dict | None = None, label: str = "") -> dict:
        if relation not in RELATIONS:
            raise ValueError(f"unsupported relation: {relation}")
        if status not in _EDGE_STATUSES:
            raise ValueError(f"unsupported edge status: {status}")
        if not self.get_node(src_id) or not self.get_node(dst_id):
            raise ValueError("edge endpoints must exist")
        identifier = edge_id(src_id, relation, dst_id, valid_from)
        observed = observed_at or _now()
        turns = [str(x) for x in (turn_ids or []) if str(x)]
        with self._connect() as connection:
            old = connection.execute("SELECT * FROM kg_edges WHERE id = ?", (identifier,)).fetchone()
            if old:
                old_turns = json.loads(old["turn_ids_json"] or "[]")
                turns = list(dict.fromkeys(old_turns + turns))
            connection.execute("""
                INSERT INTO kg_edges(id,src_id,dst_id,relation,label,status,valid_from,valid_to,
                                     observed_at,confidence,source_type,source_id,session_id,
                                     chunk_id,turn_ids_json,quote,extractor,supersedes,metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  label=excluded.label, status=excluded.status,
                  valid_to=CASE WHEN excluded.valid_to != '' THEN excluded.valid_to
                                ELSE kg_edges.valid_to END,
                  confidence=MAX(kg_edges.confidence, excluded.confidence),
                  turn_ids_json=excluded.turn_ids_json,
                  quote=CASE WHEN excluded.quote != '' THEN excluded.quote ELSE kg_edges.quote END,
                  supersedes=CASE WHEN excluded.supersedes != '' THEN excluded.supersedes
                                  ELSE kg_edges.supersedes END,
                  metadata_json=excluded.metadata_json
            """, (identifier, src_id, dst_id, relation, label, status, valid_from, valid_to,
                  observed, float(confidence), source_type, source_id, session_id, chunk_id,
                  json.dumps(turns, ensure_ascii=False), quote, extractor, supersedes,
                  json.dumps(metadata or {}, ensure_ascii=False)))
            row = connection.execute("SELECT * FROM kg_edges WHERE id = ?",
                                     (identifier,)).fetchone()
        return self._edge(row)

    @staticmethod
    def _active_sql(as_of: str) -> tuple[str, list]:
        return ("e.status = 'asserted' AND e.valid_from <= ? AND "
                "(e.valid_to = '' OR e.valid_to > ?)", [as_of, as_of])

    def _incident(self, node_ids: list[str], *, direction: str = "both",
                  relation: str | None = None, as_of: str = "") -> list[dict]:
        if not node_ids:
            return []
        as_of = as_of or _now()
        active, params = self._active_sql(as_of)
        marks = ",".join("?" for _ in node_ids)
        clauses = []
        if direction in {"out", "both"}:
            clauses.append(f"e.src_id IN ({marks})")
        if direction in {"in", "both"}:
            clauses.append(f"e.dst_id IN ({marks})")
        where = "(" + " OR ".join(clauses) + ")"
        if relation:
            where += " AND e.relation = ?"
        sql = f"SELECT e.* FROM kg_edges e WHERE {where} AND {active}"
        args = list(node_ids) * len(clauses)
        if relation:
            args.append(relation)
        args.extend(params)
        with self._connect() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self._edge(row) for row in rows]

    def neighbors(self, start_id: str, *, depth: int = 1, direction: str = "both",
                  relation: str | None = None, as_of: str = "") -> dict:
        start = self.get_node(start_id)
        if not start:
            return {"ok": False, "error": "node_not_found", "start": None,
                    "nodes": [], "edges": []}
        depth = max(1, min(3, int(depth)))
        frontier = [start_id]
        seen = {start_id}
        nodes = [start]
        edges: dict[str, dict] = {}
        for _ in range(depth):
            found = self._incident(frontier, direction=direction, relation=relation, as_of=as_of)
            next_frontier = []
            for edge in found:
                edges[edge["id"]] = edge
                other = edge["dst_id"] if edge["src_id"] in frontier else edge["src_id"]
                if other not in seen:
                    seen.add(other)
                    node = self.get_node(other)
                    if node:
                        nodes.append(node)
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break
        return {"ok": True, "start": start, "nodes": nodes,
                "edges": list(edges.values()), "depth": depth}

    def timeline(self, identifier: str, *, limit: int = 100) -> dict:
        node = self.get_node(identifier)
        if not node:
            return {"ok": False, "error": "node_not_found", "node": None, "edges": []}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM kg_edges WHERE src_id = ? OR dst_id = ? "
                "ORDER BY valid_from ASC LIMIT ?",
                (identifier, identifier, max(1, min(500, int(limit))))).fetchall()
        edges = [self._edge(row) for row in rows]
        return {"ok": True, "node": node, "edges": edges}

    def path(self, src_id: str, dst_id: str, *, max_depth: int = 3,
             relation: str | None = None, as_of: str = "") -> dict:
        if not self.get_node(src_id) or not self.get_node(dst_id):
            return {"ok": False, "error": "node_not_found", "found": False,
                    "nodes": [], "edges": []}
        max_depth = max(1, min(4, int(max_depth)))
        queue = deque([(src_id, [src_id], [])])
        seen = {src_id}
        while queue:
            current, node_path, edge_path = queue.popleft()
            if current == dst_id:
                return {"ok": True, "found": True,
                        "nodes": [self.get_node(node) for node in node_path],
                        "edges": edge_path}
            if len(edge_path) >= max_depth:
                continue
            for edge in self._incident([current], direction="both", relation=relation,
                                       as_of=as_of):
                other = edge["dst_id"] if edge["src_id"] == current else edge["src_id"]
                if other in seen:
                    continue
                seen.add(other)
                queue.append((other, node_path + [other], edge_path + [edge]))
        return {"ok": True, "found": False, "nodes": [], "edges": []}

    def upsert_node_vector(self, node_id: str, vector: list[float], model: str) -> None:
        import array

        values = array.array("f", [float(x) for x in vector])
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO kg_node_vectors(node_id,model,dim,vector) "
                "VALUES (?,?,?,?)", (node_id, model, len(values), values.tobytes()))

    def node_vectors(self, model: str | None = None) -> dict[str, list[float]]:
        import array

        try:
            with self._connect() as connection:
                if model:
                    rows = connection.execute(
                        "SELECT node_id,vector FROM kg_node_vectors WHERE model = ?",
                        (model,)).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT node_id,vector FROM kg_node_vectors").fetchall()
        except sqlite3.DatabaseError:
            return {}
        result = {}
        for row in rows:
            values = array.array("f")
            try:
                values.frombytes(row["vector"])
            except (ValueError, TypeError):
                continue
            result[str(row["node_id"])] = list(values)
        return result

    def episode_hash(self, episode_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_hash FROM kg_episodes WHERE id = ?", (episode_id,)).fetchone()
        return str(row["content_hash"]) if row else ""

    def upsert_episode(self, *, episode_id: str, source_type: str, source_id: str,
                       session_id: str, occurred_at: str, content_hash: str,
                       text: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO kg_episodes(id,source_type,source_id,session_id,occurred_at,"
                "content_hash,text) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET source_type=excluded.source_type, "
                "source_id=excluded.source_id, session_id=excluded.session_id, "
                "occurred_at=excluded.occurred_at, content_hash=excluded.content_hash, "
                "text=excluded.text",
                (episode_id, source_type, source_id, session_id, occurred_at,
                 content_hash, text))

    def stats(self) -> dict:
        with self._connect() as connection:
            nodes = {row["kind"]: row["n"] for row in connection.execute(
                "SELECT kind, COUNT(*) n FROM kg_nodes GROUP BY kind")}
            edges = {row["relation"]: row["n"] for row in connection.execute(
                "SELECT relation, COUNT(*) n FROM kg_edges GROUP BY relation")}
            statuses = {row["status"]: row["n"] for row in connection.execute(
                "SELECT status, COUNT(*) n FROM kg_edges GROUP BY status")}
            total_nodes = connection.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
            total_edges = connection.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        return {"nodes": int(total_nodes), "edges": int(total_edges),
                "by_kind": nodes, "by_relation": edges, "edge_status": statuses}

    def node_count(self) -> int:
        return self.stats()["nodes"]

    def edge_count(self) -> int:
        return self.stats()["edges"]