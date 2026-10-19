"""Bounded, content-minimised local audit trace for agent tasks."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_SENSITIVE = ("key", "secret", "token", "password", "authorization", "cookie")
_SAFE_COUNT_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens",
                    "reasoning_tokens", "input_tokens", "output_tokens",
                    "max_input_tokens", "max_output_tokens"}


def _safe_value(value, depth=0):
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered not in _SAFE_COUNT_KEYS and any(marker in lowered for marker in _SENSITIVE):
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
        self._usage = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "reasoning_tokens": 0, "llm_errors": 0}
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
            if lowered not in _SAFE_COUNT_KEYS and any(marker in lowered for marker in _SENSITIVE):
                safe_fields[str(key)] = "[redacted]"
            else:
                safe_fields[str(key)] = _safe_value(value)
        line = {"ts": round(time.time(), 3), "event": str(event)[:80], **safe_fields}
        payload = json.dumps(line, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload + "\n")

    def record_llm(self, *, model: str, ok: bool, latency_ms: float,
                   usage: dict | None = None, finish_reason: str = "",
                   tool_calls: list[str] | None = None, mode: str = "native") -> None:
        """Record one model turn without prompts, content or credentials."""
        usage = usage or {}
        with self._lock:
            self._usage["llm_calls"] += 1
            self._usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self._usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            self._usage["reasoning_tokens"] += int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
            if not ok:
                self._usage["llm_errors"] += 1
        self.record("llm_call", model=str(model)[:120], ok=bool(ok),
                    latency_ms=round(float(latency_ms), 1),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int((usage.get("completion_tokens_details") or {}).get(
                        "reasoning_tokens") or 0),
                    finish_reason=str(finish_reason)[:40],
                    tool_calls=list(tool_calls or [])[:12], mode=str(mode)[:20])

    def usage_totals(self) -> dict:
        with self._lock:
            return dict(self._usage)

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