"""SQLite FTS5 index for project facts.

The Markdown fact files remain the source of truth.  This index is disposable:
it can be rebuilt from ``facts/*.md`` at any time.  FTS5 is used because it is
bundled with CPython's SQLite on Windows and gives us BM25 ranking without an
extra service or model download.

Chinese text is indexed as characters + bigrams (SQLite's unicode61 tokenizer
does not segment CJK), which makes "交付时间" match a fact containing "交付".
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
_GENERIC = {"流程", "安排", "时间", "问题", "情况", "工作", "内容", "相关", "项目", "会议"}
_TYPE_WEIGHT = {
    "decision": 0.22, "commitment": 0.20, "action-item": 0.16,
    "risk": 0.14, "open-question": 0.12, "project-fact": 0.08,
    "preference": 0.12,
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def document_tokens(text: str) -> list[str]:
    """Tokens stored in the index: CJK chars + bigrams, Latin words."""
    value = _norm(text)
    tokens: list[str] = []
    for word in _LATIN.findall(value):
        if len(word) >= 2:
            tokens.append(word)
    for run in _CJK.findall(value):
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
        if len(run) <= 8:
            tokens.append(run)
    return list(dict.fromkeys(tokens))


def query_tokens(text: str) -> list[str]:
    """Query tokens avoid single CJK chars unless the query itself is one char."""
    value = _norm(text)
    tokens: list[str] = []
    for word in _LATIN.findall(value):
        if len(word) >= 2:
            tokens.append(word)
    for run in _CJK.findall(value):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
            if len(run) <= 8:
                tokens.append(run)
    return list(dict.fromkeys(tokens))


def _quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


class MemoryIndex:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "memory.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                name TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                type TEXT,
                description TEXT,
                body TEXT,
                origin_meeting TEXT,
                confidence REAL,
                version INTEGER,
                source_count INTEGER,
                pinned INTEGER,
                status TEXT,
                canonical_key TEXT,
                updated_at TEXT,
                valid_from TEXT,
                valid_to TEXT,
                tokens TEXT
            )
        """)
        connection.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                name UNINDEXED, tokens, tokenize='unicode61'
            )
        """)
        return connection

    @staticmethod
    def _fact_row(fact: dict) -> tuple:
        meta = fact.get("metadata") if isinstance(fact.get("metadata"), dict) else {}
        meta = {**meta, **{k: fact[k] for k in (
            "type", "origin_meeting", "confidence", "version", "source_count",
            "pinned", "status", "canonical_key", "updated_at", "valid_from", "valid_to")
            if k in fact}}
        text = "\n".join(str(fact.get(key, "")) for key in
                         ("name", "description", "body"))
        return (
            str(fact.get("name", "")),
            str(fact.get("path", "")),
            str(meta.get("type", "")),
            str(fact.get("description", "")),
            str(fact.get("body", "")),
            str(meta.get("origin_meeting", "")),
            float(meta.get("confidence") or 0.0),
            int(meta.get("version") or 1),
            int(meta.get("source_count") or 1),
            1 if meta.get("pinned") else 0,
            str(meta.get("status") or "active"),
            str(meta.get("canonical_key", "")),
            str(meta.get("updated_at", "")),
            str(meta.get("valid_from", "")),
            str(meta.get("valid_to", "")),
            " ".join(document_tokens(text)),
        )

    def upsert(self, fact: dict) -> None:
        row = self._fact_row(fact)
        if not row[0]:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            connection.execute("DELETE FROM facts_fts WHERE name = ?", (row[0],))
            connection.execute("INSERT INTO facts_fts(name, tokens) VALUES (?,?)",
                               (row[0], row[-1]))

    def remove(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM facts WHERE name = ?", (name,))
            connection.execute("DELETE FROM facts_fts WHERE name = ?", (name,))

    def rebuild(self, facts: Iterable[dict]) -> int:
        with self._connect() as connection:
            connection.execute("DELETE FROM facts")
            connection.execute("DELETE FROM facts_fts")
            count = 0
            for fact in facts:
                row = self._fact_row(fact)
                if not row[0]:
                    continue
                connection.execute(
                    "INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                connection.execute("INSERT INTO facts_fts(name, tokens) VALUES (?,?)",
                                   (row[0], row[-1]))
                count += 1
            return count

    def count(self) -> int:
        try:
            with self._connect() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        except sqlite3.DatabaseError:
            return 0

    @staticmethod
    def _recency(updated_at: str) -> float:
        try:
            stamp = time.mktime(time.strptime(updated_at[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError, OverflowError):
            return 0.0
        age_days = max(0.0, (time.time() - stamp) / 86400.0)
        return max(0.0, 1.0 - age_days / 365.0) * 0.35

    @staticmethod
    def _score(row: sqlite3.Row, rank: float, matched: list[str]) -> float:
        base = -float(rank)
        if base <= 0:
            base = 1.0 / (1.0 + max(0.0, float(rank)))
        meta_type = str(row["type"] or "")
        confidence = float(row["confidence"] or 0.0)
        sources = min(5, int(row["source_count"] or 1))
        score = (base * 2.0
                 + _TYPE_WEIGHT.get(meta_type, 0.05)
                 + MemoryIndex._recency(str(row["updated_at"] or ""))
                 + confidence * 0.12
                 + sources * 0.04
                 + (0.45 if int(row["pinned"] or 0) else 0.0)
                 + len(matched) * 0.02)
        return round(score, 6)

    def search(self, query: str, limit: int = 10, *, types: list[str] | None = None,
               status: str = "active", origin_meeting: str | None = None,
               pinned: bool | None = None, include_expired: bool = False) -> list[dict]:
        tokens = query_tokens(query)
        if not tokens:
            return []
        match = " OR ".join(_quote(token) for token in tokens[:32])
        clauses = ["f.status != 'deleted'"]
        params: list = [match]
        if status and status != "all":
            clauses.append("f.status = ?")
            params.append(status)
        elif status == "all":
            pass
        if types:
            clauses.append("f.type IN (" + ",".join("?" for _ in types) + ")")
            params.extend(types)
        if origin_meeting:
            clauses.append("f.origin_meeting = ?")
            params.append(origin_meeting)
        if pinned is not None:
            clauses.append("f.pinned = ?")
            params.append(1 if pinned else 0)
        if not include_expired:
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            clauses.append("(f.valid_to = '' OR f.valid_to IS NULL OR f.valid_to >= ?)")
            params.append(now)
        sql = ("SELECT f.*, bm25(facts_fts) AS rank FROM facts_fts "
               "JOIN facts f ON f.name = facts_fts.name "
               "WHERE facts_fts MATCH ? AND " + " AND ".join(clauses) +
               " ORDER BY rank LIMIT ?")
        params.append(max(1, min(100, int(limit))) * 4)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, params).fetchall()
        except sqlite3.DatabaseError:
            return []
        output = []
        for row in rows:
            fact = {
                "name": row["name"], "description": row["description"],
                "body": row["body"], "path": row["path"],
                "metadata": {
                    "type": row["type"], "origin_meeting": row["origin_meeting"],
                    "confidence": row["confidence"], "version": row["version"],
                    "source_count": row["source_count"], "pinned": bool(row["pinned"]),
                    "status": row["status"], "canonical_key": row["canonical_key"],
                    "updated_at": row["updated_at"], "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                },
            }
            haystack = " ".join([str(row["name"] or ""), str(row["description"] or ""),
                                 str(row["body"] or "")]).casefold()
            matched = [token for token in tokens if token in haystack][:6]
            if matched and all(token in _GENERIC for token in matched):
                continue
            query_dates = re.findall(r"\d{1,2}月|\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
                                     r"\d{1,2}月\d{1,2}日", query)
            if query_dates and not all(date in haystack for date in query_dates):
                continue
            fact["_score"] = self._score(row, row["rank"], matched)
            fact["_why"] = matched
            output.append(fact)
        output.sort(key=lambda item: (-item["_score"], item["name"]))
        return output[:max(1, min(100, int(limit)))]

    def stats(self) -> dict:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT status, COUNT(*) n FROM facts GROUP BY status").fetchall()
                return {str(row["status"]): int(row["n"]) for row in rows}
        except sqlite3.DatabaseError:
            return {}