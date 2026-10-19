"""Project-scoped facts with provenance, atomic writes and a compact index.

One fact per Markdown file. ``MEMORY.md`` is a rebuildable index, never the
source of truth. This mirrors the useful part of ZCode's memory design without
copying its session-specific code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

FACT_TYPES = {"action-item", "decision", "commitment", "project-fact",
              "preference", "risk", "open-question"}
_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_-]+")


def _slug(name: str) -> str:
    base = _SAFE.sub("-", name.strip()).strip("-")[:48] or "fact"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


class FactStore:
    def __init__(self, project_root: Path, namespace: str = "agent_memory"):
        self.project_root = Path(project_root)
        self.root = self.project_root / "meetingvar" / namespace
        self.facts_dir = self.root / "facts"
        self.index_path = self.root / "MEMORY.md"
        self._lock = threading.RLock()

    def _ensure(self) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._atomic_write(self.index_path, "# Project Agent Memory\n")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    def _target(self, name: str) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("fact name is required")
        if "\x00" in name:
            raise ValueError("fact name contains NUL")
        # The filename is derived from a sanitized slug; separators in the
        # display name (for example "CI/CD") must not become path components.
        target = (self.facts_dir / (_slug(name) + ".md")).resolve()
        if target.parent != self.facts_dir.resolve():
            raise ValueError("fact path escapes the facts directory")
        if target.is_symlink():
            raise ValueError("refusing to write through a symlink")
        return target

    def write(self, *, name: str, description: str, body: str, fact_type: str,
              source_refs: list | None = None, confidence: float | None = None,
              origin_meeting: str = "", metadata: dict | None = None) -> Path:
        if fact_type not in FACT_TYPES:
            raise ValueError(f"unsupported fact type: {fact_type}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description is required")
        if not isinstance(body, str):
            raise ValueError("body must be text")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        with self._lock:
            self._ensure()
            target = self._target(name)
            created = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            version = 1
            if target.exists():
                try:
                    old = self.read_path(target)
                    created = old.get("created_at", created)
                    version = int(old.get("version", 1)) + 1
                except (OSError, ValueError, TypeError):
                    pass
            meta = {
                "type": fact_type,
                "scope": "project",
                "origin_meeting": origin_meeting,
                "source_refs": list(source_refs or []),
                "confidence": confidence,
                "version": version,
                "created_at": created,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            if metadata:
                for key, value in metadata.items():
                    if key not in {"name", "description", "metadata", "type"}:
                        meta[str(key)] = value
            front = (
                "---\n"
                f"name: {json.dumps(name, ensure_ascii=False)}\n"
                f"description: {json.dumps(description.strip()[:300], ensure_ascii=False)}\n"
                f"metadata: {json.dumps(meta, ensure_ascii=False, sort_keys=True)}\n"
                "---\n\n"
                f"{body.rstrip()}\n"
            )
            self._atomic_write(target, front)
            self.rebuild_index()
            return target

    @staticmethod
    def _split(front: str) -> tuple[dict, str]:
        if not front.startswith("---\n"):
            raise ValueError("missing frontmatter")
        end = front.find("\n---\n", 4)
        if end < 0:
            raise ValueError("unterminated frontmatter")
        header = front[4:end]
        body = front[end + 5:]
        values = {}
        for line in header.splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            try:
                values[key.strip()] = json.loads(raw.strip())
            except ValueError:
                values[key.strip()] = raw.strip()
        return values, body

    def read_path(self, path: Path) -> dict:
        text = Path(path).read_text(encoding="utf-8")
        values, body = self._split(text)
        metadata = values.get("metadata") if isinstance(values.get("metadata"), dict) else {}
        return {**values, "metadata": metadata, **metadata, "body": body,
                "path": str(path)}

    def read(self, name: str) -> dict:
        target = self._target(name)
        if not target.exists():
            raise FileNotFoundError(name)
        return self.read_path(target)

    def list_facts(self, limit: int = 100) -> list[dict]:
        if not self.facts_dir.exists():
            return []
        files = sorted(self.facts_dir.glob("*.md"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        rows = []
        for path in files[:max(1, limit)]:
            if path.is_symlink():
                continue
            try:
                rows.append(self.read_path(path))
            except (OSError, ValueError):
                continue
        return rows

    def search(self, query: str, limit: int = 10) -> list[dict]:
        needle = str(query).strip().lower()
        if not needle:
            return []
        hits = []
        for fact in self.list_facts(limit=500):
            haystack = "\n".join([str(fact.get("name", "")), str(fact.get("description", "")),
                                  str(fact.get("body", ""))]).lower()
            if needle in haystack:
                hits.append(fact)
                if len(hits) >= limit:
                    break
        return hits

    def rebuild_index(self) -> Path:
        with self._lock:
            self._ensure()
            rows = []
            for fact in self.list_facts(limit=1000):
                name = str(fact.get("name", "")).replace("[", "(").replace("]", ")")
                description = str(fact.get("description", "")).replace("\n", " ")[:200]
                relative = Path(fact["path"]).relative_to(self.root).as_posix()
                rows.append(f"- [{name}]({relative}) — {description}")
            text = "# Project Agent Memory\n\n" + "\n".join(rows) + ("\n" if rows else "")
            self._atomic_write(self.index_path, text)
            return self.index_path

    def index(self) -> str:
        self._ensure()
        return self.index_path.read_text(encoding="utf-8")