"""Project-scoped facts with provenance, versions, conflict review and FTS.

Markdown files are the source of truth; ``memory.sqlite3`` is a disposable
FTS5 index and ``MEMORY.md`` is a rebuildable human-readable index.  This keeps
the memory portable (copy the folder, keep the facts) while still giving the
Agent BM25-ranked retrieval, filters and lifecycle fields.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .memory_index import MemoryIndex, document_tokens

FACT_TYPES = {"action-item", "decision", "commitment", "project-fact",
              "preference", "risk", "open-question"}
STATUSES = {"active", "superseded", "expired", "pending-conflict", "rejected", "deleted"}
_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_-]+")
_NEGATION = re.compile(r"不|未|没有|取消|终止|不再|延期|失败|搁置")
_DATE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日")


def _slug(name: str) -> str:
    base = _SAFE.sub("-", name.strip()).strip("-")[:48] or "fact"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


_SYNONYMS = (
    ("交付", "上线", "发布", "发版", "交付时间", "release"),
    ("截止", "期限", "deadline", "due"),
    ("负责人", "owner", "负责", "牵头"),
    ("决定", "决议", "拍板", "结论", "定了"),
    ("风险", "阻碍", "阻塞", "隐患", "延期"),
    ("未决", "待定", "待确认", "还没定", "open question"),
    ("测试", "qa", "验证", "用例"),
    ("方案", "计划", "approach", "plan"),
    ("客户", "甲方", "用户", "customer"),
    ("预算", "成本", "费用", "budget"),
)


def _expand_query(query: str) -> str:
    """Small, deterministic business synonym expansion for FTS recall."""
    text = str(query or "")
    additions = []
    for group in _SYNONYMS:
        if any(term in text.casefold() for term in group):
            additions.extend(group)
    return text + (" " + " ".join(dict.fromkeys(additions)) if additions else "")


def _canonical(fact_type: str, description: str) -> str:
    value = re.sub(r"[\s\W_]+", "", str(description or "").casefold())[:100]
    return f"{fact_type}:{value}"


def _feature_tokens(text: str) -> set[str]:
    return {token for token in document_tokens(text) if len(token) >= 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class FactStore:
    def __init__(self, project_root: Path, namespace: str = "agent_memory"):
        self.project_root = Path(project_root)
        self.root = self.project_root / "meetingvar" / namespace
        self.facts_dir = self.root / "facts"
        self.versions_dir = self.root / "versions"
        self.index_path = self.root / "MEMORY.md"
        self.fts = MemoryIndex(self.root)
        self._lock = threading.RLock()
        self._index_ready = False

    def _ensure(self) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._atomic_write(self.index_path, "# Project Agent Memory\n")
        if not self._index_ready:
            try:
                if self.fts.count() == 0 and any(self.facts_dir.glob("*.md")):
                    self.fts.rebuild(self._all_fact_dicts())
            except OSError:
                pass
            self._index_ready = True

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
        target = (self.facts_dir / (_slug(name) + ".md")).resolve()
        if target.parent != self.facts_dir.resolve():
            raise ValueError("fact path escapes the facts directory")
        if target.is_symlink():
            raise ValueError("refusing to write through a symlink")
        return target

    def _compose(self, name: str, description: str, body: str, meta: dict) -> str:
        return ("---\n"
                f"name: {json.dumps(name, ensure_ascii=False)}\n"
                f"description: {json.dumps(description.strip()[:300], ensure_ascii=False)}\n"
                f"metadata: {json.dumps(meta, ensure_ascii=False, sort_keys=True)}\n"
                "---\n\n"
                f"{body.rstrip()}\n")

    def _snapshot(self, path: Path, version: int) -> None:
        if not path.exists():
            return
        directory = self.versions_dir / path.stem
        target = directory / f"v{max(1, int(version))}.md"
        if target.exists():
            return
        try:
            self._atomic_write(target, path.read_text(encoding="utf-8"))
        except OSError:
            pass

    def _conflict_signal(self, new: dict, old: dict) -> bool:
        new_meta, old_meta = new.get("metadata", {}), old.get("metadata", {})
        for field in ("owner", "due"):
            a, b = str(new_meta.get(field, "")).strip(), str(old_meta.get(field, "")).strip()
            if a and b and a.casefold() != b.casefold():
                return True
        new_text = f"{new.get('description', '')} {new.get('body', '')}"
        old_text = f"{old.get('description', '')} {old.get('body', '')}"
        if bool(_NEGATION.search(new_text)) != bool(_NEGATION.search(old_text)):
            return True
        new_dates, old_dates = set(_DATE.findall(new_text)), set(_DATE.findall(old_text))
        if new_dates and old_dates and new_dates != old_dates:
            return True
        return False

    def _related_conflicts(self, fact_type: str, description: str, body: str,
                           exclude: str = "", metadata: dict | None = None) -> list[str]:
        new_text = f"{description} {body}"
        new_tokens = _feature_tokens(new_text)
        if len(new_tokens) < 3:
            return []
        related = []
        for old in self.list_facts(limit=500, status="active"):
            if old.get("name") == exclude or old.get("type") != fact_type:
                continue
            old_text = f"{old.get('description', '')} {old.get('body', '')}"
            if _overlap(new_tokens, _feature_tokens(old_text)) < 0.40:
                continue
            if self._conflict_signal(
                    {"description": description, "body": body,
                     "metadata": dict(metadata or {})}, old):
                related.append(str(old.get("name", "")))
        return [name for name in related if name]

    def write(self, *, name: str, description: str, body: str, fact_type: str,
              source_refs: list | None = None, confidence: float | None = None,
              origin_meeting: str = "", metadata: dict | None = None,
              status: str = "active", valid_from: str = "", valid_to: str = "",
              supersedes: str = "", pinned: bool = False, tags: list | None = None,
              canonical_key: str = "", conflict_policy: str = "auto") -> Path:
        if fact_type not in FACT_TYPES:
            raise ValueError(f"unsupported fact type: {fact_type}")
        if status not in STATUSES:
            raise ValueError(f"unsupported fact status: {status}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description is required")
        if not isinstance(body, str):
            raise ValueError("body must be text")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValueError("confidence must be between 0 and 1")
        with self._lock:
            self._ensure()
            target = self._target(name)
            now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            old = None
            if target.exists():
                try:
                    old = self.read_path(target)
                except (OSError, ValueError, TypeError):
                    old = None
            version = int(old.get("version", 1)) + 1 if old else 1
            created = str(old.get("created_at", now)) if old else now
            old_meta = old.get("metadata", {}) if old else {}
            old_refs = (old.get("source_refs") or []) if old else []
            refs = list(dict.fromkeys(
                [str(x) for x in old_refs]
                + [str(x) for x in (source_refs or [])]))
            old_meetings = (old_meta.get("source_meetings") or []) if old else []
            meetings = list(dict.fromkeys(
                [str(x) for x in old_meetings]
                + ([origin_meeting] if origin_meeting else [])))
            if not valid_from:
                valid_from = str(old_meta.get("valid_from") or created)
            if not valid_to and status == "active":
                valid_to = ""
            if old:
                self._snapshot(target, int(old.get("version", 1)))
            if not supersedes and old and status == "active" and \
                    str(old.get("status", "active")) == "active":
                supersedes = str(old.get("name", ""))
            canonical = canonical_key or _canonical(fact_type, description)
            if not supersedes and not old and status == "active":
                for existing in self.list_facts(limit=500, status="active"):
                    if existing.get("name") == name or existing.get("type") != fact_type:
                        continue
                    if str(existing.get("canonical_key", "")) != canonical:
                        continue
                    candidate = {"description": description, "body": body,
                                 "metadata": dict(metadata or {})}
                    if not self._conflict_signal(candidate, existing):
                        supersedes = str(existing.get("name", ""))
                    break
            conflicts: list[str] = []
            if status == "active" and conflict_policy == "auto" and not supersedes:
                conflicts = self._related_conflicts(fact_type, description, body, exclude=name,
                                                    metadata=metadata)
                if conflicts:
                    status = "pending-conflict"
            meta = {
                "type": fact_type,
                "scope": "project",
                "origin_meeting": origin_meeting,
                "source_refs": refs,
                "source_meetings": meetings,
                "source_count": max(1, len(meetings) or len(refs)),
                "confidence": confidence,
                "version": version,
                "created_at": created,
                "updated_at": now,
                "status": status,
                "canonical_key": canonical,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "supersedes": supersedes,
                "superseded_by": "",
                "conflict_with": conflicts,
                "pinned": bool(pinned),
                "tags": list(dict.fromkeys(str(x) for x in (tags or []) if str(x).strip())),
            }
            if old_meta.get("created_at"):
                meta["created_at"] = old_meta["created_at"]
            if metadata:
                for key, value in metadata.items():
                    if key not in {"name", "description", "metadata", "type"}:
                        meta[str(key)] = value
            self._atomic_write(target, self._compose(name, description, body, meta))
            if supersedes and supersedes != name:
                self._mark_status(supersedes, "superseded", valid_to=now,
                                  superseded_by=name)
            self.fts.upsert(self.read_path(target))
            self.rebuild_index()
            return target

    def _all_fact_dicts(self) -> list[dict]:
        rows = []
        for path in sorted(self.facts_dir.glob("*.md")):
            if path.is_symlink():
                continue
            try:
                rows.append(self.read_path(path))
            except (OSError, ValueError):
                continue
        return rows

    def _mark_status(self, name: str, status: str, *, valid_to: str = "",
                     superseded_by: str = "") -> None:
        try:
            fact = self.read(name)
        except (FileNotFoundError, ValueError):
            return
        meta = dict(fact.get("metadata", {}))
        self._snapshot(Path(fact["path"]), int(fact.get("version", 1)))
        meta["version"] = int(fact.get("version", 1)) + 1
        meta["status"] = status
        if valid_to:
            meta["valid_to"] = valid_to
        if superseded_by:
            meta["superseded_by"] = superseded_by
        meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._atomic_write(Path(fact["path"]),
                           self._compose(fact["name"], fact.get("description", ""),
                                         fact.get("body", ""), meta))
        self.fts.upsert(self.read_path(Path(fact["path"])))

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
        fact = {**values, "metadata": metadata, **metadata, "body": body.strip("\n"),
                "path": str(path)}
        fact.setdefault("type", metadata.get("type", ""))
        fact.setdefault("status", metadata.get("status", "active"))
        fact.setdefault("version", metadata.get("version", 1))
        fact.setdefault("source_refs", metadata.get("source_refs", []))
        return fact

    def read(self, name: str) -> dict:
        target = self._target(name)
        if not target.exists():
            raise FileNotFoundError(name)
        return self.read_path(target)

    def list_facts(self, limit: int = 100, *, status: str = "active",
                   types: list[str] | None = None,
                   include_inactive: bool = False) -> list[dict]:
        if not self.facts_dir.exists():
            return []
        files = sorted(self.facts_dir.glob("*.md"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        rows = []
        for path in files:
            if path.is_symlink():
                continue
            try:
                fact = self.read_path(path)
            except (OSError, ValueError):
                continue
            fact_status = str(fact.get("status", "active"))
            if not include_inactive and status != "all" and fact_status != status:
                continue
            if types and str(fact.get("type", "")) not in types:
                continue
            rows.append(fact)
            if len(rows) >= max(1, int(limit)):
                break
        return rows

    @staticmethod
    def _fallback_score(fact: dict, terms: list[str]) -> tuple[int, list[str]]:
        haystack = "\n".join([str(fact.get("name", "")),
                              str(fact.get("description", "")),
                              str(fact.get("body", ""))]).casefold()
        matched = [term for term in terms if term in haystack]
        return sum(haystack.count(term) for term in matched), matched

    def search(self, query: str, limit: int = 10, *, types: list[str] | None = None,
               status: str = "active", origin_meeting: str | None = None,
               pinned: bool | None = None, include_expired: bool = False) -> list[dict]:
        self._ensure()
        needle = str(query).strip()
        if not needle:
            return self.list_facts(limit=limit, status=status, types=types)
        expanded = _expand_query(needle)
        try:
            rows = self.fts.search(expanded, limit=limit, types=types, status=status,
                                     origin_meeting=origin_meeting, pinned=pinned,
                                     include_expired=include_expired)
        except Exception:
            rows = []
        if rows:
            return rows
        # A fresh project or an unavailable FTS build still gets deterministic
        # substring retrieval; it is a fallback, not the primary path.
        terms = [term for term in re.findall(
            r"[A-Za-z][A-Za-z0-9_.+-]+|[\u4e00-\u9fff]{2,}", needle.casefold())]
        hits = []
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        for fact in self.list_facts(limit=500, status=status, types=types,
                                    include_inactive=(status == "all")):
            if fact.get("status") == "deleted":
                continue
            if origin_meeting and str(fact.get("origin_meeting", "")) != origin_meeting:
                continue
            if pinned is not None and bool(fact.get("pinned")) != bool(pinned):
                continue
            if not include_expired and str(fact.get("valid_to", "")) and                     str(fact.get("valid_to", "")) < now:
                continue
            score, matched = self._fallback_score(fact, terms)
            if score:
                fact["_score"] = score
                fact["_why"] = matched
                hits.append(fact)
        hits.sort(key=lambda item: (-item.get("_score", 0), item.get("name", "")))
        return hits[:max(1, int(limit))]

    def rebuild_index(self) -> Path:
        with self._lock:
            self._ensure()
            facts = self._all_fact_dicts()
            rows = []
            pending = []
            for fact in facts:
                if fact.get("status") == "deleted":
                    continue
                name = str(fact.get("name", "")).replace("[", "(").replace("]", ")")
                description = str(fact.get("description", "")).replace("\n", " ")[:200]
                relative = Path(fact["path"]).relative_to(self.root).as_posix()
                status = fact.get("status", "active")
                suffix = "" if status == "active" else f" `[{status}]`"
                line = f"- [{name}]({relative}) — {description}{suffix}"
                if status == "pending-conflict":
                    pending.append(line)
                else:
                    rows.append(line)
            text = "# Project Agent Memory\n\n" + "\n".join(rows)
            if pending:
                text += "\n\n## 待确认冲突\n\n" + "\n".join(pending)
            text += "\n" if text else ""
            self._atomic_write(self.index_path, text)
            return self.index_path

    def index_text(self) -> str:
        self._ensure()
        return self.index_path.read_text(encoding="utf-8")

    def index(self) -> str:
        """Backward-compatible name used by older CLI/tests."""
        return self.index_text()

    def pending_conflicts(self, limit: int = 50) -> list[dict]:
        return self.list_facts(limit=limit, status="pending-conflict")

    def resolve_conflict(self, name: str, resolution: str) -> dict:
        fact = self.read(name)
        conflicts = [str(x) for x in (fact.get("conflict_with") or [])]
        if fact.get("status") != "pending-conflict":
            raise ValueError("fact is not pending-conflict")
        if resolution not in {"keep_new", "keep_old", "merge"}:
            raise ValueError("resolution must be keep_new, keep_old or merge")
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if resolution == "keep_old":
            self._mark_status(name, "rejected", valid_to=now)
        else:
            body = fact.get("body", "")
            refs = list(fact.get("source_refs") or [])
            meetings = list(fact.get("source_meetings") or [])
            if resolution == "merge" and conflicts:
                try:
                    old = self.read(conflicts[0])
                    body = f"{old.get('body', '')}\n\n---\n\n{body}"
                    refs = list(dict.fromkeys(list(old.get("source_refs") or []) + refs))
                    meetings = list(dict.fromkeys(
                        list(old.get("source_meetings") or []) + meetings))
                except (FileNotFoundError, ValueError):
                    pass
            meta = dict(fact.get("metadata", {}))
            self._snapshot(Path(fact["path"]), int(fact.get("version", 1)))
            meta.update({"version": int(fact.get("version", 1)) + 1,
                         "status": "active", "conflict_with": [], "valid_to": "",
                         "valid_from": now, "updated_at": now,
                         "source_refs": refs, "source_meetings": meetings,
                         "source_count": max(1, len(meetings) or len(refs))})
            self._atomic_write(Path(fact["path"]),
                               self._compose(fact["name"], fact.get("description", ""),
                                             body, meta))
            self.fts.upsert(self.read_path(Path(fact["path"])))
            for other in conflicts:
                self._mark_status(other, "superseded", valid_to=now, superseded_by=name)
        self.rebuild_index()
        return self.read(name)

    def update_fields(self, name: str, *, description: str | None = None,
                      body: str | None = None, fact_type: str | None = None,
                      status: str | None = None, valid_to: str | None = None,
                      pinned: bool | None = None, tags: list | None = None) -> Path:
        fact = self.read(name)
        effective_status = status or str(fact.get("status", "active"))
        effective_valid_to = (valid_to if valid_to is not None else
                              ("" if effective_status == "active"
                               else str(fact.get("valid_to", ""))))
        return self.write(
            name=name,
            description=description if description is not None else str(fact.get("description", "")),
            body=body if body is not None else str(fact.get("body", "")),
            fact_type=fact_type or str(fact.get("type", "project-fact")),
            source_refs=list(fact.get("source_refs") or []),
            confidence=fact.get("confidence"),
            origin_meeting=str(fact.get("origin_meeting", "")),
            status=effective_status,
            valid_from=str(fact.get("valid_from", "")),
            valid_to=effective_valid_to,
            pinned=bool(fact.get("pinned")) if pinned is None else pinned,
            tags=list(fact.get("tags") or []) if tags is None else tags,
            canonical_key=str(fact.get("canonical_key", "")),
            metadata={key: value for key, value in (fact.get("metadata", {}) or {}).items()
                      if key not in {"name", "description", "metadata", "type", "status",
                                     "version", "created_at", "updated_at", "source_refs",
                                     "source_meetings", "source_count", "canonical_key",
                                     "valid_from", "valid_to", "supersedes", "superseded_by",
                                     "conflict_with", "pinned", "tags"}},
            conflict_policy="none",
        )

    def pin(self, name: str, pinned: bool = True) -> Path:
        return self.update_fields(name, pinned=pinned)

    def expire(self, name: str) -> Path:
        return self.update_fields(
            name, status="expired", valid_to=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def delete(self, name: str, *, hard: bool = False) -> bool:
        with self._lock:
            self._ensure()
            target = self._target(name)
            if not target.exists():
                return False
            if hard:
                target.unlink(missing_ok=True)
                shutil.rmtree(self.versions_dir / target.stem, ignore_errors=True)
                self.fts.remove(name)
            else:
                self._mark_status(name, "deleted",
                                  valid_to=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            self.rebuild_index()
            return True

    def stats(self) -> dict:
        facts = self._all_fact_dicts()
        by_status, by_type = {}, {}
        for fact in facts:
            status = str(fact.get("status", "active"))
            kind = str(fact.get("type", "unknown"))
            by_status[status] = by_status.get(status, 0) + 1
            by_type[kind] = by_type.get(kind, 0) + 1
        return {"total": len(facts), "by_status": by_status, "by_type": by_type,
                "index": self.fts.stats()}