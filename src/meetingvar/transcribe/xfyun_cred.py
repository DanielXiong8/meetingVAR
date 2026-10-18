"""讯飞凭证文件定位：把"讯飞文件夹"（控制台下载的目录）解析成具体 .env。

分类两层判定，避免同名 .env 认错服务：
1. 目录名提示（"听写" → IAT；"录音" → LFASR）优先；
2. 无提示时按键位认领：{APPID, APIKey, APISecret} → IAT；{APPID, SecretKey} → LFASR。
只读取键名，不返回/打印键值。
"""
from __future__ import annotations

from pathlib import Path

KEYS_IAT = {"APPID", "APIKey", "APISecret"}
KEYS_LFASR = {"APPID", "SecretKey"}
_MAX_DEPTH = 3


def _env_keys(p: Path) -> set[str]:
    keys: set[str] = set()
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            k = line.strip().partition("=")[0].strip()
            if k in (KEYS_IAT | KEYS_LFASR):
                keys.add(k)
    except OSError:
        pass
    return keys


def _classify(p: Path) -> str | None:
    keys = _env_keys(p)
    hint = " ".join(p.parts).lower()
    if ("听写" in hint or "iat" in hint) and KEYS_IAT <= keys:
        return "iat"
    if ("录音" in hint or "lfasr" in hint or "raasr" in hint) and KEYS_LFASR <= keys:
        return "lfasr"
    if KEYS_IAT <= keys:
        return "iat"
    if KEYS_LFASR <= keys:
        return "lfasr"
    return None


def scan(path: Path) -> list[dict]:
    """列出 path（文件或文件夹）下可认领的凭证：[{path, kind}]，按目录排序。"""
    path = Path(path)
    if path.is_file():
        kind = _classify(path)
        return [{"path": str(path), "kind": kind}] if kind else []
    if not path.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(path.rglob("*.env")):
        try:
            if len(p.relative_to(path).parts) > _MAX_DEPTH:
                continue
        except ValueError:
            continue
        kind = _classify(p)
        if kind:
            out.append({"path": str(p), "kind": kind})
    return out


def _first(path: Path, kind: str) -> Path | None:
    for hit in scan(path):
        if hit["kind"] == kind:
            return Path(hit["path"])
    return None


def find_iat(path: Path) -> Path | None:
    return _first(Path(path), "iat")


def find_lfasr(path: Path) -> Path | None:
    return _first(Path(path), "lfasr")