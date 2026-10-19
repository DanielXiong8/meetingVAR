"""Bounded, content-minimised local audit trace for agent tasks."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_SENSITIVE = ("key", "secret", "token", "password", "authorization", "cookie")


def _safe_value(value, depth=0):
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _safe_value(child, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


class TraceRecorder:
    """Append-only JSONL audit trail, rotated at a fixed size.

    This is an operational audit file on the local machine. It intentionally
    records identifiers and outcomes, not prompts, transcripts or credentials.
    """

    def __init__(self, path: Path, max_bytes: int = 2_000_000):
        self.path = Path(path)
        self.max_bytes = max(64_000, int(max_bytes))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                backup = self.path.with_suffix(self.path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                os.replace(self.path, backup)
        except OSError:
            pass

    def record(self, event: str, **fields) -> None:
        safe_fields = {}
        for key, value in fields.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE):
                safe_fields[str(key)] = "[redacted]"
            else:
                safe_fields[str(key)] = _safe_value(value)
        line = {"ts": round(time.time(), 3), "event": str(event)[:80], **safe_fields}
        payload = json.dumps(line, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload + "\n")

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows[-max(1, limit):]