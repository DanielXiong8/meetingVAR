"""Opt-in crash/telemetry handling with local-first storage and redaction."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import traceback
import urllib.request
from pathlib import Path

_SECRET_KEYS = re.compile(r"(key|token|secret|password|credential|authorization|cookie)", re.I)
_MAX_REPORT = 64 * 1024


def _redact_text(value: str) -> str:
    text = str(value)
    text = re.sub(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[=:]\s*\S+",
                  r"\1=<redacted>", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", text)
    return text


def _redact(value, depth: int = 0):
    if depth > 5:
        return "<deep>"
    if isinstance(value, dict):
        return {str(k): ("<redacted>" if _SECRET_KEYS.search(str(k)) else _redact(v, depth + 1))
                for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in list(value)[:80]]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", value)
        return value[:2000]
    return value


def write_crash(exc_type, exc, tb, *, app_version: str, logs_dir: Path,
                context: dict | None = None) -> Path | None:
    """Write one redacted local crash report; never includes prompts or keys."""
    try:
        directory = Path(logs_dir) / "crashes"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"crash-{stamp}-{os.getpid()}.json"
        report = {
            "app": "meetingVAR", "version": app_version,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "python": os.sys.version.split()[0], "platform": os.name,
            "exception": getattr(exc_type, "__name__", "Exception"),
            "message": _redact_text(exc)[:500],
            "traceback": _redact_text("".join(traceback.format_exception(exc_type, exc, tb)))[-12000:],
            "context": _redact(context or {}),
        }
        raw = json.dumps(report, ensure_ascii=False, indent=2)[:_MAX_REPORT]
        path.write_text(raw, encoding="utf-8")
        return path
    except OSError:
        return None


def upload_crash(path: Path, endpoint: str, timeout: int = 15) -> dict:
    endpoint = str(endpoint or "").strip()
    if not endpoint.startswith(("https://", "http://")):
        return {"ok": False, "error": "未配置崩溃上报地址"}
    try:
        data = Path(path).read_bytes()[:_MAX_REPORT]
        request = urllib.request.Request(endpoint, data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(2048)
        return {"ok": True}
    except (OSError, ValueError):
        return {"ok": False, "error": "崩溃上报失败"}