"""Self-contained project file preview used by the desktop shell and bridge.

The desktop executable must be able to show a file even when no engine Python
has been configured.  This module therefore depends only on the standard
library plus the package's own pure-Python readers; PyMuPDF/pypdf are optional
enhancements for PDF text.
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from .files import (SKIP_DIRS, SUPPORTED_TEXT_SUFFIXES, detect_file_type,
                    project_files, read_text_safely, zip_entries)

_TREE_MAX_NODES = 800
_TREE_MAX_DEPTH = 6

_LANGUAGE = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx", ".vue": "vue", ".svelte": "svelte",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".swift": "swift",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ps1": "powershell",
    ".bat": "bat", ".cmd": "bat", ".sql": "sql", ".css": "css", ".scss": "scss",
    ".less": "less", ".html": "html", ".htm": "html", ".xml": "xml", ".svg": "xml",
    ".json": "json", ".jsonl": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".md": "markdown",
    ".tsv": "tsv", ".csv": "csv", ".tex": "latex", ".rst": "rst",
}


def build_tree(root: str | Path) -> tuple[list[dict], list[str]]:
    """VS Code-style tree with content-detected kinds, bounded by depth/nodes."""
    skipped: list[str] = []
    budget = [_TREE_MAX_NODES]
    root = Path(root).resolve()

    def walk(directory: Path, depth: int) -> list[dict]:
        if depth > _TREE_MAX_DEPTH or budget[0] <= 0:
            return []
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return []
        nodes = []
        for entry in entries:
            if budget[0] <= 0:
                break
            if entry.is_dir() and (entry.name.lower() in SKIP_DIRS or entry.is_symlink()):
                continue
            budget[0] -= 1
            try:
                rel = entry.relative_to(root).as_posix()
                if entry.is_dir():
                    nodes.append({"name": entry.name, "dir": True, "rel": rel,
                                  "children": walk(entry, depth + 1)})
                else:
                    suffix = entry.suffix.lower()
                    info = detect_file_type(entry, suffix)
                    nodes.append({"name": entry.name, "dir": False,
                                  "size": entry.stat().st_size, "rel": rel,
                                  "suffix": suffix, "actual_suffix": info["actual_suffix"],
                                  "kind": info["kind"], "kind_label": info["label"],
                                  "kind_short": info["short"], "mismatch": info["mismatch"],
                                  "readable": info["readable"],
                                  "image": info["kind"] == "image",
                                  "video": info["kind"] == "video",
                                  "audio": info["kind"] == "audio"})
                    if suffix in {".txt", ".md"} and not info["readable"]:
                        skipped.append(entry.name)
            except OSError:
                continue
        return nodes

    return walk(root, 1), skipped


def scan_project_folder(folder: str | Path) -> dict:
    """Tree/preview available without any engine Python or model runtime."""
    root = Path(folder)
    result: dict = {
        "ok": True, "folder": str(root), "doc_count": 0,
        "terms": {}, "term_count": 0, "persons": [],
        "repo": {"is_repo": False, "terms": 0, "top": {}},
        "tree": [], "skipped": [],
        "notes": ["本地文件预览模式：词表、人物与代码符号需要引擎运行时"],
    }
    if not root.is_dir():
        return {"ok": False, "error": "路径不是文件夹"}
    try:
        result["tree"], result["skipped"] = build_tree(root)
    except Exception:  # noqa: BLE001
        result["notes"].append("文件树构建失败")
    count = 0
    try:
        for path in project_files(root, set(SUPPORTED_TEXT_SUFFIXES)):
            if detect_file_type(path)["readable"]:
                count += 1
    except Exception:  # noqa: BLE001
        count = 0
    result["doc_count"] = count
    result["repo"]["is_repo"] = (root / ".git").exists() or any(root.glob("*.py"))
    persons = root / "meetingvar" / "persons.json"
    if persons.is_file():
        try:
            import json

            data = json.loads(persons.read_text(encoding="utf-8"))
            if isinstance(data, list):
                result["persons"] = [str(p.get("name", "")) for p in data
                                     if isinstance(p, dict) and p.get("name")]
        except (OSError, ValueError):
            pass
    return result


def safe_project_path(folder: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or "\x00" in relative:
        raise ValueError("invalid path")
    if len(relative) > 1000:
        raise ValueError("path too long")
    root = Path(folder).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path outside project")
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("not a readable file")
    return candidate


def read_project_file(folder: str | Path, relative: str) -> dict:
    try:
        path = safe_project_path(Path(folder), relative)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    suffix = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return {"ok": False, "error": "文件不可读"}
    info = detect_file_type(path, suffix)
    kind = info["kind"]
    mime = (mimetypes.guess_type("preview" + info["actual_suffix"])[0]
            or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
    result = {"ok": True, "path": relative, "name": path.name, "suffix": suffix,
              "actual_suffix": info["actual_suffix"], "kind": kind,
              "kind_label": info["label"], "kind_short": info["short"],
              "mismatch": info["mismatch"], "size": size, "mime": mime,
              "language": _LANGUAGE.get(suffix if kind == "text" else info["actual_suffix"], "")}
    if kind == "image":
        if size > 15 * 1024 * 1024:
            result["too_large"] = True
            return result
        try:
            result["data_url"] = "data:" + mime + ";base64," + base64.b64encode(
                path.read_bytes()).decode("ascii")
        except OSError:
            return {"ok": False, "error": "图片读取失败"}
        return result
    if kind in {"video", "audio"}:
        if size <= 15 * 1024 * 1024:
            try:
                result["data_url"] = "data:" + mime + ";base64," + base64.b64encode(
                    path.read_bytes()).decode("ascii")
            except OSError:
                pass
        return result
    if kind in {"zip", "xlsx"}:
        listing = zip_entries(path)
        result.update(entry_count=listing["count"], entries=listing["entries"])
        return result
    if kind == "pdf" or kind in {"docx", "pptx", "doc", "ppt", "xls", "rtf"}:
        text = read_text_safely(path)
        result.update(text=(text or "")[:200_000],
                      truncated=bool(text and len(text) > 200_000),
                      has_text=bool(text))
        return result
    if kind == "text":
        text = read_text_safely(path)
        if text is not None:
            result.update(text=text[:200_000], truncated=len(text) > 200_000)
            return result
        result.update(kind="binary", kind_label="无法解码的文本", kind_short="BIN")
    elif kind == "empty":
        result.update(kind="binary", kind_label="空文件", kind_short="EMPTY")
    elif kind == "ole":
        result["kind_label"] = "OLE 复合文档（可能是旧版 Office）"
    return result