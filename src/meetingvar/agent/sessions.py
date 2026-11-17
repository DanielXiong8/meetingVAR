"""Safe, read-only session catalog for the agent tools.

Only session ids matching a strict pattern are accepted; all paths stay under
``data/pool``. Transcript text is treated as untrusted data by callers.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterable

_SAFE_SESSION = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _terms(query: str) -> list[str]:
    words = re.findall(r"[A-Za-z]\s*[一二三四五六七八九十0-9]+"
                       r"|[A-Za-z][A-Za-z0-9_.+-]+|[\u4e00-\u9fff]+",
                       str(query or ""))
    out = []
    for word in words:
        word = re.sub(r"\s+", "", word)
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            if len(word) <= 4:
                out.append(word)
            out.extend(word[i:i + 2] for i in range(max(0, len(word) - 1)))
        else:
            out.append(word.lower())
    return list(dict.fromkeys(term for term in out if term))


def _score(text: str, terms: Iterable[str]) -> int:
    folded = re.sub(r"\s+", "", str(text).casefold())
    return sum(folded.count(re.sub(r"\s+", "", str(term).casefold())) for term in terms)


class SessionCatalog:
    """Local transcript catalog with bounded reads and a small mtime cache."""

    def __init__(self, data_dir: Path, cache_limit: int = 8, *, project_root: Path | None = None):
        self.pool = Path(data_dir) / "pool"
        self.project_root = Path(project_root).resolve() if project_root is not None else None
        self.cache_limit = max(1, int(cache_limit))
        self._cache: dict[str, tuple[str, int, dict]] = {}

    def _allowed_sessions(self) -> set[str] | None:
        if self.project_root is None:
            return None
        from ..memory.project_sessions import session_ids_for_project
        return session_ids_for_project(self.project_root, strict=True)

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SAFE_SESSION.fullmatch(session_id):
            raise ValueError("invalid session id")
        if ".." in session_id:
            raise ValueError("invalid session id")
        allowed = self._allowed_sessions()
        if allowed is not None and session_id not in allowed:
            raise ValueError("session does not belong to this project")
        lexical = self.pool / session_id / "transcript.json"
        from ..memory.source_policy import excluded
        if excluded(self.pool.resolve(), lexical, automatic=False) or lexical.parent.is_symlink():
            raise ValueError("session source excluded")
        target = lexical.resolve()
        if self.pool.resolve() not in target.parents:
            raise ValueError("session path escapes pool")
        return target

    def load(self, session_id: str) -> dict:
        path = self._path(session_id)
        stat = path.stat()
        raw = path.read_bytes()
        revision = hashlib.sha256(raw).hexdigest()
        cached = self._cache.get(session_id)
        if cached and cached[0] == revision and cached[1] == stat.st_size:
            return cached[2]
        data = json.loads(raw.decode("utf-8"))
        rows = data.get("transcript") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("transcript JSON must contain a transcript array")
        turns = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                continue
            text = row["text"].strip()
            if not text:
                continue
            turns.append({"id": f"turn_{index}", "ts": row.get("ts", 0),
                          "speaker": str(row.get("speaker", ""))[:80], "text": text,
                          "session_id": session_id, "revision": revision,
                          "path": str(path), "kind": "turn",
                          "source_id": f"{session_id}:turn_{index}:{revision}"})
        session = {"session_id": session_id, "turns": turns,
                   "clips": len(data.get("clips", [])) if isinstance(data, dict) else 0,
                   "mtime": stat.st_mtime, "revision": revision, "path": str(path)}
        self._cache[session_id] = (revision, stat.st_size, session)
        if len(self._cache) > self.cache_limit:
            self._cache.pop(next(iter(self._cache)))
        return session

    def list_sessions(self, limit: int = 20, *, nonempty_only: bool = True) -> list[dict]:
        if not self.pool.exists():
            return []
        rows = []
        allowed = self._allowed_sessions()
        for directory in self.pool.iterdir():
            if allowed is not None and directory.name not in allowed:
                continue
            if not directory.is_dir():
                continue
            path = directory / "transcript.json"
            if not path.exists():
                continue
            try:
                stat = path.stat()
                if nonempty_only and stat.st_size < 8:
                    continue
                rows.append({"session_id": directory.name, "mtime": stat.st_mtime,
                             "bytes": stat.st_size})
            except OSError:
                continue
        rows.sort(key=lambda row: (-row["mtime"], row["session_id"]))
        return rows[:max(1, min(100, int(limit)))]

    def search(self, query: str, *, limit: int = 5, session_id: str | None = None,
               per_session: int = 4) -> list[dict]:
        terms = _terms(query)
        if not terms:
            return []
        sessions = [session_id] if session_id else [
            row["session_id"] for row in self.list_sessions(limit=100)]
        results = []
        for sid in sessions:
            try:
                session = self.load(sid)
            except (OSError, ValueError):
                continue
            matches = []
            for turn in session["turns"]:
                score = _score(turn["text"], terms)
                if score:
                    matches.append({**turn, "score": score})
            if not matches:
                continue
            matches.sort(key=lambda row: (-row["score"], row["ts"]))
            results.append({"session_id": sid, "total_hits": len(matches),
                            "matches": matches[:max(1, int(per_session))]})
        results.sort(key=lambda row: (-row["total_hits"],
                                       -max(m["score"] for m in row["matches"])))
        return results[:max(1, min(20, int(limit)))]

    def _snapshot(self):
        from .file_ops import _digest
        rows, errors = [], []
        if not self.pool.exists():
            return rows, _digest([]), errors
        allowed = self._allowed_sessions()
        for directory in self.pool.iterdir():
            if allowed is not None and directory.name not in allowed:
                continue
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                path = self._path(directory.name)
                if not path.exists():
                    continue
                raw = path.read_bytes()
                stat = path.stat()
                rows.append({"session_id": directory.name, "mtime": stat.st_mtime,
                             "bytes": len(raw), "revision": hashlib.sha256(raw).hexdigest()})
            except (OSError, ValueError) as exc:
                errors.append({"session_id": directory.name, "code": type(exc).__name__})
        rows.sort(key=lambda row: (-row["mtime"], row["session_id"]))
        return rows, _digest(rows), errors

    def list_sessions_page(self, limit=20, *, offset=0, cursor=None):
        from .file_ops import _cursor, _position, _integer, _coverage, _status
        cap = _integer(limit, 20, minimum=1, maximum=100)
        offset = _integer(offset, 0)
        rows, revision, errors = self._snapshot()
        binding = {"tool": "list_sessions", "pool": str(self.pool.resolve()),
                   "project": str(self.project_root), "revision": revision,
                   "limit": cap, "offset": offset}
        start = _integer(_position(cursor, binding, offset), 0)
        if start > len(rows):
            raise ValueError("offset beyond sessions")
        selected = rows[start:start + cap]
        end = start + len(selected)
        more = end < len(rows)
        return {"sessions": selected, "scanned": end, "total": len(rows),
                "errors": errors, "truncated": more,
                "status": _status(selected, errors, more),
                "coverage": _coverage("project_sessions", len(rows), end, len(errors), more),
                "next_cursor": _cursor(binding, end) if more else None}

    def search_page(self, query, *, limit=5, session_id=None, per_session=8, offset=0, cursor=None):
        from .file_ops import _cursor, _position, _integer, _coverage, _status, _digest
        cap = _integer(limit, 5, minimum=1, maximum=20)
        per_session = _integer(per_session, 8, minimum=1, maximum=100)
        offset = _integer(offset, 0)
        rows, revision, errors = self._snapshot()
        if session_id:
            self._path(session_id)
            rows = [row for row in rows if row["session_id"] == session_id]
        terms = _terms(query)
        results = []
        for row in rows:
            try:
                session = self.load(row["session_id"])
                if session["revision"] != row["revision"]:
                    raise ValueError("source_changed")
            except (OSError, ValueError) as exc:
                errors.append({"session_id": row["session_id"], "code": type(exc).__name__})
                continue
            matches = [{**turn, "score": _score(turn["text"], terms)} for turn in session["turns"]]
            matches = [m for m in matches if m["score"]]
            matches.sort(key=lambda m: (-m["score"], str(m["ts"]), m["id"]))
            if matches:
                results.append({"session_id": row["session_id"], "total_hits": len(matches), "matches": matches})
        results.sort(key=lambda r: (-r["total_hits"], -r["matches"][0]["score"], r["session_id"]))
        binding = {"tool": "search_sessions", "pool": str(self.pool.resolve()),
                   "project": str(self.project_root), "revision": revision, "errors": _digest(errors),
                   "query": query, "session_id": session_id, "limit": cap,
                   "per_session": per_session, "offset": offset}
        position = _position(cursor, binding, [offset, 0])
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("cursor_invalid")
        index, match_start = [_integer(v, 0) for v in position]
        if index > len(results):
            raise ValueError("cursor_invalid")
        selected = []
        while index < len(results) and len(selected) < cap:
            row = results[index]
            matches = row["matches"][match_start:match_start + per_session]
            selected.append({**row, "matches": matches})
            match_start += len(matches)
            if match_start < len(row["matches"]):
                break
            index += 1
            match_start = 0
        more = index < len(results)
        total = sum(r["total_hits"] for r in results)
        processed = sum(r["total_hits"] for r in results[:index]) + match_start
        return {"query": query, "results": selected, "scanned": len(rows), "total": len(rows),
                "errors": errors, "truncated": more, "status": _status(selected, errors, more),
                "coverage": _coverage("matching_transcript_turns", total, processed, len(errors), more),
                "next_cursor": _cursor(binding, [index, match_start]) if more else None}

    def window(self, session_id: str, turn_ids: list[str], *, max_turns: int = 12) -> list[dict]:
        session = self.load(session_id)
        by_id = {turn["id"]: turn for turn in session["turns"]}
        selected = [dict(by_id[tid]) for tid in turn_ids if tid in by_id]
        return selected[:max(1, min(30, int(max_turns)))]

    def compare(self, session_a: str, session_b: str, query: str, *,
                limit: int = 4) -> dict:
        left = self.search(query, session_id=session_a, limit=1, per_session=limit)
        right = self.search(query, session_id=session_b, limit=1, per_session=limit)
        return {"query": query,
                "session_a": left[0] if left else {"session_id": session_a, "matches": []},
                "session_b": right[0] if right else {"session_id": session_b, "matches": []}}

    def stats(self) -> dict:
        rows = self.list_sessions(limit=100)
        return {"sessions": len(rows), "pool": str(self.pool),
                "latest_mtime": rows[0]["mtime"] if rows else 0,
                "checked_at": round(time.time(), 3)}