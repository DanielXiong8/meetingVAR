"""Revision-aware deterministic repository map used for agent-led exploration.

The map records what exists and where it can be read. It does not claim that a
model has semantically verified the repository. Every unit carries a source
reference with the raw byte hash and extraction revision required to reread it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .document_read import read_document
from .files import detect_file_type

MAP_VERSION = 2
_WRITE_LOCK = threading.RLock()
MAP_DB_NAME = "knowledge_map.sqlite3"
_UNIT_STATUSES = {"unseen", "queued", "running", "succeeded", "failed", "stale"}
_CODE_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".go",
    ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".sh", ".sql", ".yaml",
    ".yml", ".toml", ".json",
}
_ENTRY_NAMES = {
    "readme", "readme.md", "readme.rst", "agents.md", "claude.md", "contributing.md",
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "setup.py",
    "requirements.txt", "dockerfile", "makefile", "cmakelists.txt",
    "__main__.py", "main.py", "app.py", "cli.py", "server.py",
    "index.js", "index.ts", "index.tsx", "main.js", "main.ts", "main.tsx",
}
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$", re.M)
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
    re.M,
)


# ---------- paths / serialization ----------

def map_path(root: Path | str) -> Path:
    return Path(root).resolve() / "meetingvar" / "agent_memory" / MAP_DB_NAME


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _source_id(relative: str) -> str:
    return "source:" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]


def unit_id(relative: str, raw_hash: str, extraction_revision: str, kind: str,
            start: int = 0, end: int = 0, label: str = "") -> str:
    return "unit:" + _digest({"path": relative, "bytes_hash": raw_hash,
                               "extraction_revision": extraction_revision,
                               "kind": kind, "start": start, "end": end,
                               "label": label})[:32]


# ---------- connections / schema ----------

def _open_write(root: Path) -> sqlite3.Connection:
    path = map_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    # Do not switch journal mode on every writer: concurrent connections in
    # one process can block on that pragma. The default journal plus a busy
    # timeout is sufficient for short map transactions.
    connection.execute("PRAGMA busy_timeout=10000")
    _ensure(connection)
    return connection


def _open_read(root: Path) -> sqlite3.Connection | None:
    path = map_path(root)
    if not path.is_file():
        return None
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS map_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS map_sources (
            source_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL, bytes_hash TEXT NOT NULL,
            extraction_revision TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0, total_units INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'current', role TEXT NOT NULL DEFAULT 'source',
            imports TEXT NOT NULL DEFAULT '[]', errors TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS map_units (
            unit_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
            kind TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'source',
            start INTEGER NOT NULL DEFAULT 0, end INTEGER NOT NULL DEFAULT 0,
            page_start INTEGER, page_end INTEGER, line_start INTEGER, line_end INTEGER,
            text_excerpt TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            symbols TEXT NOT NULL DEFAULT '[]', source_ref TEXT NOT NULL,
            semantic_summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unseen', updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS map_units_source ON map_units(source_id);
        CREATE INDEX IF NOT EXISTS map_units_status ON map_units(status);
        CREATE INDEX IF NOT EXISTS map_units_path ON map_units(path);
        CREATE TABLE IF NOT EXISTS map_claims (
            claim_id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'observed', semantic_verified INTEGER NOT NULL DEFAULT 0,
            refs TEXT NOT NULL DEFAULT '[]', source_revision TEXT NOT NULL DEFAULT '',
            exploration_id TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS map_explorations (
            exploration_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'onboarding', query TEXT NOT NULL DEFAULT '',
            prompt_version TEXT NOT NULL DEFAULT '', map_revision TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            usage TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL
        );
        """
    )
    _add_column(connection, "map_sources", "role", "TEXT NOT NULL DEFAULT 'source'")
    _add_column(connection, "map_sources", "imports", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(connection, "map_units", "role", "TEXT NOT NULL DEFAULT 'source'")
    _add_column(connection, "map_units", "body", "TEXT NOT NULL DEFAULT ''")
    _add_column(connection, "map_units", "semantic_summary", "TEXT NOT NULL DEFAULT ''")
    connection.commit()


def _add_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


# ---------- extraction / units ----------

def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    return offsets


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _role_for(relative: str) -> str:
    parts = [part.casefold() for part in Path(relative).parts]
    name = parts[-1] if parts else ""
    if name in {"readme", "readme.md", "readme.rst", "agents.md", "claude.md", "contributing.md"}:
        return "rules"
    if name in {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "setup.py",
                "requirements.txt", "dockerfile", "makefile", "cmakelists.txt"}:
        return "manifest"
    if "test" in name or "test" in parts or "tests" in parts:
        return "test"
    if parts and parts[0] in {"docs", "doc", "documentation", "notebooks"}:
        return "docs"
    if name in _ENTRY_NAMES:
        return "entrypoint"
    return "source"


def _code_units(text: str, suffix: str) -> tuple[list[dict], list[dict], list[str]]:
    units: list[dict] = []
    errors: list[dict] = []
    imports: list[str] = []
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError) as exc:
            return [], [{"code": "parse_failed", "message": type(exc).__name__}], []
        offsets = _line_offsets(text)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start_line = max(1, int(getattr(node, "lineno", 1)))
                end_line = max(start_line, int(getattr(node, "end_lineno", start_line)))
                start = offsets[start_line - 1] if start_line - 1 < len(offsets) else 0
                end = offsets[end_line] if end_line < len(offsets) else len(text)
                units.append({"kind": "symbol", "label": node.name, "start": start,
                              "end": end, "line_start": start_line, "line_end": end_line,
                              "body": text[start:end][:200_000], "symbols": [node.name]})
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif node.module:
                    imports.append(node.module)
        return units, errors, sorted(set(imports))
    for match in _IMPORT_RE.finditer(text):
        imports.append(next((group for group in match.groups() if group), ""))
    for match in _JS_SYMBOL_RE.finditer(text):
        name = next((group for group in match.groups() if group), "")
        if not name:
            continue
        start = match.start()
        end = text.find("\n", match.end())
        end = len(text) if end < 0 else end
        units.append({"kind": "symbol", "label": name, "start": start, "end": end,
                      "line_start": _line_at(text, start), "line_end": _line_at(text, end),
                      "body": text[start:end][:200_000], "symbols": [name]})
    return units, errors, sorted({item for item in imports if item})


def _units_for_source(path: Path, relative: str, raw_hash: str) -> tuple[list[dict], dict]:
    suffix = path.suffix.lower()
    extraction = read_document(path)
    text = str(extraction.get("text") or "")
    revision = str(extraction.get("revision") or raw_hash)
    role = _role_for(relative)
    units: list[dict] = []
    errors = list(extraction.get("errors") or [])
    imports: list[str] = []
    if text:
        units.append({"kind": "source", "label": path.name, "role": role,
                      "start": 0, "end": len(text), "line_start": 1,
                      "line_end": max(1, text.count("\n") + 1),
                      "body": text, "symbols": [], "page_start": None, "page_end": None})
        headings = list(_HEADING_RE.finditer(text))
        for index, match in enumerate(headings):
            start = match.start()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            end = min(end, start + 200_000)
            units.append({"kind": "heading", "label": match.group(2).strip()[:160],
                          "role": role, "start": start, "end": end,
                          "line_start": _line_at(text, start), "line_end": _line_at(text, end),
                          "body": text[start:end], "symbols": []})
        if suffix in _CODE_SUFFIXES:
            code_units, parse_errors, imports = _code_units(text, suffix)
            units.extend({**unit, "role": role} for unit in code_units)
            errors.extend(parse_errors)
        if len(text) > 6000:
            for start in range(0, len(text), 6000):
                end = min(len(text), start + 6000)
                units.append({"kind": "window", "label": f"字符 {start}", "role": role,
                              "start": start, "end": end,
                              "line_start": _line_at(text, start), "line_end": _line_at(text, end),
                              "body": text[start:end], "symbols": []})
    kind = detect_file_type(path).get("kind", "text")
    pages = extraction.get("pages") or []
    if kind == "pdf" or len(pages) > 1:
        for page in pages:
            if not isinstance(page, dict) or not page.get("text"):
                continue
            number = int(page.get("number") or 0)
            page_text = str(page.get("text") or "")
            units.append({"kind": "page", "label": f"第 {number} 页", "role": role,
                          "start": 0, "end": len(page_text), "page_start": number,
                          "page_end": number, "line_start": 1,
                          "line_end": max(1, page_text.count("\n") + 1),
                          "body": page_text, "symbols": []})
    source = {"source_id": _source_id(relative), "path": relative,
              "kind": suffix.lstrip(".") or kind, "bytes_hash": raw_hash,
              "extraction_revision": revision, "size": path.stat().st_size,
              "complete": extraction.get("complete") is True, "role": role,
              "errors": errors, "imports": imports, "units": units}
    return units, source


# ---------- build ----------

def build_map(project_root: Path | str, *, force: bool = False, progress=None) -> dict:
    """Build or incrementally update the map. No model call is made."""
    root = Path(project_root).resolve()
    if not root.is_dir():
        return {"ok": False, "error": "项目文件夹无效", "map_status": "failed"}
    from ..agent.workspace import _inventory, revision
    documents = _inventory(root)
    map_revision = revision(documents)
    connection = _open_write(root)
    now = time.time()
    rebuilt = skipped = 0
    try:
        previous = connection.execute("SELECT value FROM map_meta WHERE key='map_revision'").fetchone()
        stored_version = connection.execute("SELECT value FROM map_meta WHERE key='map_version'").fetchone()
        if not stored_version or stored_version[0] != str(MAP_VERSION):
            force = True
        current_paths = set(documents)
        existing = {row["path"]: dict(row) for row in connection.execute("SELECT * FROM map_sources")}
        for removed in set(existing) - current_paths:
            connection.execute("UPDATE map_sources SET status='stale',updated_at=? WHERE path=?", (now, removed))
            connection.execute("UPDATE map_units SET status='stale',updated_at=? WHERE path=?", (now, removed))
            connection.execute("UPDATE map_claims SET status='stale',updated_at=? WHERE status!='stale' AND refs LIKE ?",
                               (now, f'%"{removed}"%'))
        for relative, info in sorted(documents.items()):
            prior = existing.get(relative)
            if (not force and prior and prior["bytes_hash"] == info["sha256"]
                    and prior["status"] == "current" and prior["complete"] and not prior["errors"]):
                skipped += 1
                continue
            path = root / relative
            try:
                units, source = _units_for_source(path, relative, info["sha256"])
            except (OSError, ValueError, UnicodeError) as exc:
                units = []
                source = {"source_id": _source_id(relative), "path": relative,
                          "kind": path.suffix.lstrip(".") or "file", "bytes_hash": info["sha256"],
                          "extraction_revision": info["sha256"], "size": info["size"],
                          "complete": False, "role": _role_for(relative),
                          "errors": [{"code": type(exc).__name__, "message": str(exc)[:200]}],
                          "imports": [], "units": []}
            rebuilt += 1
            connection.execute("""INSERT INTO map_sources
                (source_id,path,kind,bytes_hash,extraction_revision,size,complete,total_units,status,role,imports,errors,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                source_id=excluded.source_id,kind=excluded.kind,bytes_hash=excluded.bytes_hash,
                extraction_revision=excluded.extraction_revision,size=excluded.size,
                complete=excluded.complete,total_units=excluded.total_units,status='current',
                role=excluded.role,imports=excluded.imports,errors=excluded.errors,updated_at=excluded.updated_at""",
                (source["source_id"], relative, source["kind"], source["bytes_hash"],
                 source["extraction_revision"], source["size"], int(source["complete"]),
                 len(units), "current", source["role"], _json(source.get("imports") or []),
                 _json(source["errors"]), now))
            connection.execute("DELETE FROM map_units WHERE path=?", (relative,))
            for unit in units:
                uid = unit_id(relative, source["bytes_hash"], source["extraction_revision"],
                              unit["kind"], unit["start"], unit["end"], unit["label"])
                ref = {"id": uid, "kind": "document", "source_id": source["source_id"],
                       "path": relative, "bytes_hash": source["bytes_hash"],
                       "revision": source["extraction_revision"],
                       "source_revision": source["extraction_revision"],
                       "page": unit.get("page_start"), "page_start": unit.get("page_start"),
                       "page_end": unit.get("page_end"),
                       "char_start": unit["start"], "char_end": unit["end"],
                       "line_start": unit.get("line_start"), "line_end": unit.get("line_end"),
                       "quote": str(unit.get("body") or "")[:400]}
                connection.execute("""INSERT INTO map_units
                    (unit_id,source_id,path,kind,label,role,start,end,page_start,page_end,
                     line_start,line_end,text_excerpt,body,symbols,source_ref,status,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (uid, source["source_id"], relative, unit["kind"], unit["label"],
                     unit.get("role") or source["role"], unit["start"], unit["end"],
                     unit.get("page_start"), unit.get("page_end"), unit.get("line_start"),
                     unit.get("line_end"), str(unit.get("body") or "")[:2400],
                     str(unit.get("body") or ""), _json(unit.get("symbols") or []),
                     _json(ref), "unseen", now))
            if progress:
                try:
                    progress(f"地图：{relative}")
                except Exception:
                    pass
        connection.execute("INSERT INTO map_meta(key,value) VALUES('map_revision',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (map_revision,))
        connection.execute("INSERT INTO map_meta(key,value) VALUES('map_version',?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(MAP_VERSION),))
        connection.commit()
    except sqlite3.DatabaseError:
        connection.rollback()
        raise
    finally:
        connection.close()
    result = read_map(root)
    result["rebuilt_sources"] = rebuilt
    result["skipped_sources"] = skipped
    if previous and previous[0] == map_revision and not force and not rebuilt:
        result["unchanged"] = True
    return result


# ---------- read APIs ----------

def _not_built(root: Path) -> dict:
    return {"ok": True, "map_status": "not_built", "map_revision": "", "project": str(root),
            "sources": [], "units": [], "claims": [], "entrypoints": [],
            "totals": {"sources": 0, "current_sources": 0, "complete_sources": 0, "units": 0,
                       "unseen_units": 0, "explored_units": 0, "failed_units": 0},
            "coverage": {"scope": "deterministic_repository_map", "total": 0, "processed": 0,
                         "failed": 0, "complete": False, "exhaustive": False,
                         "semantic_exhaustive": False},
            "errors": []}


def _decode(row: dict, keys=("errors", "symbols", "source_ref")) -> dict:
    for key in keys:
        if key in row and isinstance(row[key], str):
            try:
                row[key] = json.loads(row[key])
            except (ValueError, TypeError):
                pass
    return row


def read_map(project_root: Path | str, *, limit: int = 20, offset: int = 0,
             query: str = "", include_claims: bool = True) -> dict:
    """Read-only snapshot. Missing database returns not_built without creating it."""
    root = Path(project_root).resolve()
    connection = _open_read(root)
    if connection is None:
        return _not_built(root)
    try:
        meta = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM map_meta")}
        sources = [_decode(dict(row), ("errors", "imports")) for row in connection.execute(
            "SELECT source_id,path,kind,bytes_hash,extraction_revision,size,complete,total_units,status,role,imports,errors "
            "FROM map_sources ORDER BY path")]
        # The map can lag behind the filesystem after a user edit but before the
        # next sync. Do not present stale units or claims as current evidence.
        try:
            from ..agent.workspace import _inventory
            live = _inventory(root)
        except Exception:
            live = None
        freshness_checked = live is not None
        if live is not None:
            for source in sources:
                current = live.get(source["path"])
                if current is None:
                    if source["status"] != "stale":
                        source["status_before_read"] = source["status"]
                        source["status"] = "stale"
                elif current.get("sha256") != source["bytes_hash"]:
                    source["status_before_read"] = source["status"]
                    source["status"] = "changed"
        where, params = "status!='stale'", []
        if query:
            where += " AND (label LIKE ? OR body LIKE ? OR path LIKE ?)"
            params = [f"%{query}%"] * 3
        total_units = connection.execute(f"SELECT COUNT(*) FROM map_units WHERE {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT unit_id,path,kind,label,role,start,end,page_start,page_end,line_start,line_end,"
            f"text_excerpt,symbols,source_ref,status FROM map_units WHERE {where} "
            f"ORDER BY path,start LIMIT ? OFFSET ?",
            params + [max(1, min(2000, int(limit))), max(0, int(offset))]).fetchall()
        units = [_decode(dict(row), ("symbols", "source_ref")) for row in rows]
        current_paths = {row["path"] for row in sources if row["status"] == "current"}
        units = [unit for unit in units if unit.get("path") in current_paths]
        claims = []
        if include_claims:
            current = {row["path"]: row["extraction_revision"] for row in sources if row["status"] == "current"}
            for row in connection.execute("SELECT * FROM map_claims WHERE status!='stale' ORDER BY updated_at DESC"):
                claim = _decode(dict(row), ("refs",))
                refs = claim.get("refs") or []
                fresh = all(ref.get("path") in current
                            and ref.get("source_revision") == current.get(ref.get("path"))
                            for ref in refs if isinstance(ref, dict))
                if fresh:
                    claim["semantic_verified"] = False
                    claims.append(claim)
        active = [row for row in sources if row["status"] != "stale"]
        totals = {
            "sources": len(sources),
            "active_sources": len(active),
            "stale_sources": len(sources) - len(active),
            "current_sources": sum(row["status"] == "current" for row in sources),
            "complete_sources": sum(bool(row["complete"]) and row["status"] == "current" for row in sources),
            "units": connection.execute("SELECT COUNT(*) FROM map_units WHERE status!='stale'").fetchone()[0],
            "unseen_units": connection.execute("SELECT COUNT(*) FROM map_units WHERE status='unseen'").fetchone()[0],
            "explored_units": connection.execute("SELECT COUNT(*) FROM map_units WHERE status='succeeded'").fetchone()[0],
            "failed_units": connection.execute("SELECT COUNT(*) FROM map_units WHERE status='failed'").fetchone()[0],
        }
        latest = connection.execute(
            "SELECT status,result FROM map_explorations ORDER BY updated_at DESC LIMIT 1").fetchone()
        exploration = {}
        if latest:
            try:
                exploration = json.loads(latest["result"] or "{}")
            except (ValueError, TypeError):
                exploration = {}
        entrypoints = [row for row in sources if row["role"] in {"rules", "manifest", "entrypoint"}]
        entrypoints.sort(key=lambda row: ({"rules": 0, "manifest": 1, "entrypoint": 2}.get(row["role"], 3),
                                          len(row["path"]), row["path"]))
        errors = [{"code": "source_incomplete", "path": row["path"], "message": "来源提取未完整"}
                  for row in sources if not row["complete"] and row["status"] == "current"]
        # Deleted/renamed sources remain in history as stale entries. They do
        # not make the current map incomplete; only active sources do.
        complete = (not errors and all(row["status"] == "current" and bool(row["complete"])
                                       for row in active))
        if not complete:
            # A targeted synthesis/overview from an older map revision must not
            # survive a changed/deleted source without a rebuild.
            exploration = {}
        return {"ok": True, "map_status": "complete" if complete else "partial",
                "map_version": int(meta.get("map_version", "0") or 0),
                "map_revision": meta.get("map_revision", ""), "project": str(root),
                "sources": sources, "units": units,
                "next_offset": int(offset) + len(units) if int(offset) + len(units) < total_units else None,
                "unit_total": total_units, "claims": claims, "entrypoints": entrypoints[:30],
                "open_questions": list(exploration.get("open_questions") or [])[:12],
                "overview": str(exploration.get("overview") or "")[:1000],
                "exploration_status": str(latest["status"] if latest else "not_started"),
                "totals": totals,
                "coverage": {"scope": "deterministic_repository_map", "total": len(active),
                             "processed": totals["complete_sources"],
                             "failed": sum(1 for row in active
                                           if row["status"] != "current" or not row["complete"]),
                             "stale": totals["stale_sources"],
                             "complete": complete, "exhaustive": complete,
                             "freshness_checked": freshness_checked,
                             "semantic_exhaustive": False},
                "errors": errors}
    finally:
        connection.close()


def coverage_units(project_root: Path | str) -> list[dict]:
    """All deterministic units needed for exhaustive semantic coverage."""
    root = Path(project_root).resolve()
    connection = _open_read(root)
    if connection is None:
        return []
    try:
        rows = [_decode(dict(row), ("symbols", "source_ref")) for row in connection.execute(
            "SELECT unit_id,path,kind,label,role,start,end,page_start,page_end,line_start,"
            "line_end,text_excerpt,body,symbols,source_ref,semantic_summary,status "
            "FROM map_units WHERE status!='stale' ORDER BY path,start")]
    finally:
        connection.close()
    chosen: dict[str, dict] = {}
    for row in rows:
        path = row["path"]
        kind = row["kind"]
        if kind == "page":
            chosen.setdefault(path + ":page:" + str(row.get("page_start")), row)
        elif kind == "window":
            chosen.setdefault(path + ":window:" + str(row["start"]), row)
        elif kind == "source" and path not in chosen:
            chosen[path] = row
    # Prefer page/window coverage over the duplicate source unit for long files.
    result = []
    by_path: dict[str, list[dict]] = {}
    for key, row in chosen.items():
        by_path.setdefault(row["path"], []).append(row)
    for path, rows in by_path.items():
        if any(row["kind"] == "page" for row in rows):
            result.extend(row for row in rows if row["kind"] == "page")
        elif any(row["kind"] == "window" for row in rows):
            result.extend(row for row in rows if row["kind"] == "window")
        else:
            result.append(next(row for row in rows if row["kind"] == "source"))
    return result


def update_unit_states(project_root: Path | str, *, succeeded=(), failed=(), summaries=None) -> None:
    """Apply one batch's unit results in a single short transaction."""
    with _WRITE_LOCK:
        connection = _open_write(Path(project_root).resolve())
        try:
            now = time.time()
            for uid in succeeded:
                connection.execute("UPDATE map_units SET status='succeeded',updated_at=? WHERE unit_id=?",
                                   (now, uid))
            for uid in failed:
                connection.execute("UPDATE map_units SET status='failed',updated_at=? WHERE unit_id=?",
                                   (now, uid))
            for uid, summary in (summaries or {}).items():
                connection.execute("UPDATE map_units SET semantic_summary=?,updated_at=? WHERE unit_id=?",
                                   (str(summary)[:500], now, uid))
            connection.commit()
        finally:
            connection.close()


def set_unit_summary(project_root: Path | str, unit_id_value: str, summary: str) -> None:
    connection = _open_write(Path(project_root).resolve())
    try:
        connection.execute("UPDATE map_units SET semantic_summary=?,updated_at=? WHERE unit_id=?",
                           (str(summary)[:500], time.time(), unit_id_value))
        connection.commit()
    finally:
        connection.close()


def map_unit(project_root: Path | str, unit_id_value: str) -> dict | None:
    root = Path(project_root).resolve()
    connection = _open_read(root)
    if connection is None:
        return None
    try:
        row = connection.execute("SELECT * FROM map_units WHERE unit_id=?", (unit_id_value,)).fetchone()
        return _decode(dict(row), ("symbols", "source_ref")) if row else None
    finally:
        connection.close()


def read_unit(project_root: Path | str, unit_id_value: str, *, offset: int = 0,
              limit: int = 12000) -> dict:
    root = Path(project_root).resolve()
    unit = map_unit(root, unit_id_value)
    if not unit:
        return {"status": "error", "errors": [{"code": "unit_not_found"}], "items": [],
                "source_refs": [], "coverage": {"complete": False, "exhaustive": False}}
    ref = unit.get("source_ref") or {}
    path = root / str(unit["path"])
    try:
        from .source_policy import excluded
        if excluded(root, path, automatic=True):
            raise ValueError("source_policy")
        path.resolve().relative_to(root)
        extraction = read_document(path)
    except (OSError, ValueError) as exc:
        return {"status": "error", "errors": [{"code": "source_unreadable", "message": type(exc).__name__}],
                "items": [], "source_refs": [], "coverage": {"complete": False, "exhaustive": False}}
    if extraction.get("revision") != ref.get("source_revision"):
        return {"status": "error", "errors": [{"code": "source_stale"}], "items": [],
                "source_refs": [], "coverage": {"complete": False, "exhaustive": False}}
    if unit.get("kind") == "page":
        pages = {int(page.get("number") or 0): page for page in extraction.get("pages") or []
                 if isinstance(page, dict)}
        page = pages.get(int(unit.get("page_start") or 0))
        if not page or page.get("status") not in {"ok", "blank"}:
            return {"status": "error", "errors": [{"code": "page_unavailable"}], "items": [],
                    "source_refs": [], "coverage": {"complete": False, "exhaustive": False}}
        text = str(page.get("text") or "")
    else:
        text = str(extraction.get("text") or "")
        start, end = int(unit["start"]), int(unit["end"])
        if not (0 <= start <= end <= len(text)):
            return {"status": "error", "errors": [{"code": "range_stale"}], "items": [],
                    "source_refs": [], "coverage": {"complete": False, "exhaustive": False}}
        text = text[start:end]
    start_at = max(0, min(len(text), int(offset)))
    window = text[start_at:start_at + max(1, min(120000, int(limit)))]
    base = 0 if unit.get("kind") == "page" else int(unit.get("start") or 0)
    returned_ref = {**ref, "char_start": base + start_at, "char_end": base + start_at + len(window),
                    "quote": window[:400]}
    return {"status": "ok" if window.strip() else "empty",
            "items": [{"text": window, "source_ref_id": unit["unit_id"]}],
            "source_refs": [returned_ref],
            "coverage": {"scope": unit["path"], "total": len(text), "processed": start_at + len(window),
                         "complete": start_at + len(window) >= len(text), "exhaustive": False},
            "next_offset": start_at + len(window) if start_at + len(window) < len(text) else None,
            "errors": []}


def search_units(project_root: Path | str, query: str, *, limit: int = 20,
                 offset: int = 0) -> dict:
    root = Path(project_root).resolve()
    connection = _open_read(root)
    if connection is None:
        return {"status": "empty", "units": [], "total": 0, "next_offset": None,
                "coverage": {"complete": False, "exhaustive": False}, "errors": []}
    try:
        needle = f"%{query}%"
        where = "status!='stale' AND (body LIKE ? OR label LIKE ? OR path LIKE ?)"
        params = [needle, needle, needle]
        total = connection.execute(f"SELECT COUNT(*) FROM map_units WHERE {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT unit_id,path,kind,label,role,start,end,page_start,page_end,line_start,line_end,"
            f"text_excerpt,source_ref,status FROM map_units WHERE {where} ORDER BY path,start LIMIT ? OFFSET ?",
            params + [max(1, min(200, int(limit))), max(0, int(offset))]).fetchall()
        units = [_decode(dict(row), ("source_ref",)) for row in rows]
        return {"status": "ok" if units else "empty", "units": units, "total": total,
                "next_offset": int(offset) + len(units) if int(offset) + len(units) < total else None,
                "coverage": {"scope": "repository_map_search", "total": total,
                             "processed": len(units), "complete": False, "exhaustive": False},
                "errors": []}
    finally:
        connection.close()


# ---------- exploration / claims ----------

def set_unit_status(project_root: Path | str, unit_id_value: str, status: str) -> None:
    if status not in _UNIT_STATUSES:
        raise ValueError("invalid unit status")
    with _WRITE_LOCK:
        connection = _open_write(Path(project_root).resolve())
        try:
            connection.execute("UPDATE map_units SET status=?,updated_at=? WHERE unit_id=?",
                               (status, time.time(), unit_id_value))
            connection.commit()
        finally:
            connection.close()


def record_exploration(project_root: Path | str, exploration_id: str, record: dict) -> dict:
    if not exploration_id:
        raise ValueError("exploration_id is required")
    root = Path(project_root).resolve()
    connection = _open_write(root)
    try:
        status = str(record.get("exploration_status") or record.get("status") or "partial")
        connection.execute("""INSERT INTO map_explorations
            (exploration_id,status,scope,query,prompt_version,map_revision,result,error,usage,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(exploration_id) DO UPDATE SET
            status=excluded.status,scope=excluded.scope,query=excluded.query,
            prompt_version=excluded.prompt_version,map_revision=excluded.map_revision,
            result=excluded.result,error=excluded.error,usage=excluded.usage,updated_at=excluded.updated_at""",
            (exploration_id, status, str(record.get("scope") or "onboarding"),
             str(record.get("query") or ""), str(record.get("prompt_version") or ""),
             str(record.get("map_revision") or ""), _json(record.get("result") or record),
             str(record.get("error") or ""), _json(record.get("usage") or {}), time.time()))
        connection.commit()
    finally:
        connection.close()
    return read_exploration(root, exploration_id) or {}


def read_exploration(project_root: Path | str, exploration_id: str) -> dict | None:
    root = Path(project_root).resolve()
    connection = _open_read(root)
    if connection is None:
        return None
    try:
        row = connection.execute("SELECT * FROM map_explorations WHERE exploration_id=?",
                                 (exploration_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        for key in ("result", "usage"):
            try:
                data[key] = json.loads(data[key])
            except (ValueError, TypeError):
                pass
        return data
    finally:
        connection.close()


def quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """Locate a quote allowing whitespace/newline differences, not paraphrase."""
    text = str(text or "")
    quote = str(quote or "")
    if not quote:
        return None
    at = text.find(quote)
    if at >= 0:
        return at, at + len(quote)
    norm, index_map = [], []
    for index, char in enumerate(text):
        if char.isspace():
            if norm and norm[-1] == " ":
                continue
            norm.append(" ")
            index_map.append(index)
        else:
            norm.append(char)
            index_map.append(index)
    wanted = re.sub(r"\s+", " ", quote).strip()
    if not wanted:
        return None
    position = "".join(norm).find(wanted)
    if position < 0:
        return None
    end_index = position + len(wanted) - 1
    if end_index >= len(index_map):
        return None
    return index_map[position], index_map[end_index] + 1


def _verify_claim_ref(root: Path, ref: dict) -> bool:
    if not isinstance(ref, dict) or not ref.get("path"):
        return False
    unit = map_unit(root, str(ref.get("unit_id") or ref.get("id") or ""))
    if not unit:
        return False
    quote = str(ref.get("quote") or "")
    body = str(unit.get("body") or "")
    if not quote or quote_span(body, quote) is None:
        return False
    source = map_unit(root, unit["unit_id"])
    source_ref = source.get("source_ref") or {}
    return (ref.get("bytes_hash") == source_ref.get("bytes_hash")
            and ref.get("source_revision") == source_ref.get("source_revision"))


def store_claims(project_root: Path | str, claims: list[dict], *, exploration_id: str = "") -> dict:
    root = Path(project_root).resolve()
    saved, rejected = [], []
    with _WRITE_LOCK:
        return _store_claims_locked(root, claims, exploration_id, saved, rejected)


def _store_claims_locked(root: Path, claims: list[dict], exploration_id: str,
                         saved: list, rejected: list) -> dict:
    connection = _open_write(root)
    try:
        for claim in claims if isinstance(claims, list) else []:
            if not isinstance(claim, dict):
                rejected.append({"claim": claim, "reason": "invalid_claim"})
                continue
            text = str(claim.get("text") or "").strip()[:500]
            refs = claim.get("source_refs") or claim.get("refs") or []
            if not text or not isinstance(refs, list) or not refs:
                rejected.append({"claim": claim, "reason": "missing_text_or_refs"})
                continue
            if not all(_verify_claim_ref(root, ref) for ref in refs if isinstance(ref, dict)):
                rejected.append({"claim": claim, "reason": "ref_not_current"})
                continue
            claim_id = "claim:" + hashlib.sha256(
                (text + json.dumps(refs, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()[:32]
            first = next((ref for ref in refs if isinstance(ref, dict)), {})
            connection.execute("""INSERT OR REPLACE INTO map_claims
                (claim_id,text,kind,status,semantic_verified,refs,source_revision,exploration_id,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (claim_id, text, str(claim.get("kind") or "fact")[:40], "observed", 0,
                 _json(refs), str(first.get("source_revision") or ""), exploration_id, time.time()))
            saved.append({"claim_id": claim_id, "text": text,
                          "kind": claim.get("kind") or "fact", "source_refs": refs,
                          "semantic_verified": False})
        connection.commit()
    finally:
        connection.close()
    return {"ok": bool(saved), "claims": saved, "rejected": rejected,
            "semantic_verified": False}