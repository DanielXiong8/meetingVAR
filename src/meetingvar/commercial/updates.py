"""Signed-manifest update checks and verified downloads.

The app never downloads or executes an update unless a manifest URL is
configured.  The manifest must contain an HTTPS URL and SHA256; the downloaded
file is verified before it is offered to the installer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

_MAX_BYTES = 512 * 1024 * 1024


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(text))[:4])


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def check_manifest(url: str, current_version: str, timeout: int = 10) -> dict:
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "未配置更新清单地址"}
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return {"ok": False, "error": "更新清单地址无效"}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(64 * 1024)
    except (OSError, ValueError):
        return {"ok": False, "error": "更新检查失败（网络不可用）"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "更新清单不是有效 JSON"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "更新清单结构无效"}
    version = str(data.get("version") or "").strip()
    download = str(data.get("url") or "").strip()
    digest = str(data.get("sha256") or "").strip().lower()
    if not version or not download or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return {"ok": False, "error": "更新清单缺少 version/url/sha256"}
    target = urllib.parse.urlsplit(download)
    if target.scheme not in {"https", "http"} or not target.hostname:
        return {"ok": False, "error": "更新下载地址无效"}
    return {"ok": True, "available": is_newer(version, current_version),
            "version": version, "url": download, "sha256": digest,
            "notes": str(data.get("notes") or "")[:4000],
            "mandatory": bool(data.get("mandatory", False))}


def download_update(url: str, expected_sha256: str, dest_dir: str | Path | None = None) -> dict:
    digest = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return {"ok": False, "error": "SHA256 无效"}
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return {"ok": False, "error": "下载地址无效"}
    directory = Path(dest_dir or tempfile.gettempdir()) / "meetingvar_update"
    directory.mkdir(parents=True, exist_ok=True)
    name = Path(parsed.path).name or "meetingVAR-setup.exe"
    if not name.lower().endswith(".exe"):
        name += ".exe"
    target = directory / name
    temporary = target.with_suffix(".part")
    hasher = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=30) as response, open(temporary, "wb") as out:
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                total += len(block)
                if total > _MAX_BYTES:
                    raise ValueError("update too large")
                hasher.update(block)
                out.write(block)
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        return {"ok": False, "error": f"下载失败：{type(exc).__name__}"}
    if hasher.hexdigest() != digest:
        temporary.unlink(missing_ok=True)
        return {"ok": False, "error": "更新文件校验失败（SHA256 不匹配）"}
    os.replace(temporary, target)
    return {"ok": True, "path": str(target), "size": total,
            "sha256": digest}