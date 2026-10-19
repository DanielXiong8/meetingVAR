"""Safe, read-only session catalog for the agent tools.

Only session ids matching a strict pattern are accepted; all paths stay under
``data/pool``. Transcript text is treated as untrusted data by callers.
"""
from __future__ import annotations

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

    def __init__(self, data_dir: Path, cache_limit: int = 8):
        self.pool = Path(data_dir) / "pool"
        self.cache_limit = max(1, int(cache_limit))
        self._cache: dict[str, tuple[float, int, dict]] = {}

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SAFE_SESSION.fullmatch(session_id):
            raise ValueError("invalid session id")
        if ".." in session_id:
            raise ValueError("invalid session id")
        target = (self.pool / session_id / "transcript.json").resolve()
        if self.pool.resolve() not in target.parents:
            raise ValueError("session path escapes pool")
        return target

    def load(self, session_id: str) -> dict:
        path = self._path(session_id)
        stat = path.stat()
        cached = self._cache.get(session_id)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        data = json.loads(path.read_text(encoding="utf-8"))
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
                          "speaker": str(row.get("speaker", ""))[:80], "text": text})
        session = {"session_id": session_id, "turns": turns,
                   "clips": len(data.get("clips", [])) if isinstance(data, dict) else 0,
                   "mtime": stat.st_mtime}
        self._cache[session_id] = (stat.st_mtime, stat.st_size, session)
        if len(self._cache) > self.cache_limit:
            self._cache.pop(next(iter(self._cache)))
        return session

    def list_sessions(self, limit: int = 20, *, nonempty_only: bool = True) -> list[dict]:
        if not self.pool.exists():
            return []
        rows = []
        for directory in self.pool.iterdir():
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
        rows.sort(key=lambda row: row["mtime"], reverse=True)
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

    def window(self, session_id: str, turn_ids: list[str], *, max_turns: int = 12) -> list[dict]:
        session = self.load(session_id)
        by_id = {turn["id"]: turn for turn in session["turns"]}
        selected = [by_id[tid] for tid in turn_ids if tid in by_id]
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