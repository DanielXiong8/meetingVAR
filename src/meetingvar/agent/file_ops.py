"""Project-scoped file reads; writes remain approval-only application actions."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from ..memory.source_policy import excluded
from ..memory.files import SUPPORTED_TEXT_SUFFIXES, detect_file_type, read_text_safely

MAX_READ_BYTES = 12 * 1024 * 1024
MAX_READ_CHARS = 120_000  # Explicit maximum page, never a whole-source cut-off.
MAX_WRITE_CHARS = 100_000
_TEXT_SUFFIXES = {
    "", ".md", ".markdown", ".txt", ".rst", ".log", ".csv", ".tsv", ".json",
    ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml",
    ".html", ".htm", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".py",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".sh", ".bat", ".ps1", ".sql", ".tex", ".bib",
}
_SEARCH_SUFFIXES = _TEXT_SUFFIXES | set(SUPPORTED_TEXT_SUFFIXES)
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".next", ".venv", "venv"}
_INTERNAL_DIR = "meetingvar"
_BLOCKED_COMMANDS = re.compile(
    r"(?:rm\s+-[a-z]*r|rd\s+/s|rmdir\s+/s|del\s+/[a-z]*[sf]|format\s+[a-z]:|"
    r"diskpart|mkfs|shutdown|reboot|stop-computer|restart-computer|"
    r"remove-item\s+.*-recurse|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f)", re.I)


def validate_command(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        raise ValueError("命令不能为空")
    if len(text) > 800 or "\x00" in text:
        raise ValueError("命令过长或包含非法字符")
    if _BLOCKED_COMMANDS.search(text):
        raise ValueError("该命令可能删除或破坏数据，已被拒绝")
    return text


def _resolve(project_root, relative, *, directory=False):
    root = Path(project_root).resolve()
    raw = str(relative or "").strip().replace("\\", "/")
    if "\x00" in raw or len(raw) > 1000 or (not raw and not directory):
        raise ValueError("文件路径无效")
    rel = Path(raw or ".")
    if ".." in rel.parts:
        raise ValueError("文件路径越界")
    lexical = rel if rel.is_absolute() else root / rel
    target = lexical.resolve()
    try:
        inside = target.relative_to(root)
    except ValueError:
        raise ValueError("文件路径越界") from None
    if not inside.parts and not directory:
        raise ValueError("文件路径无效")
    if excluded(root, lexical, automatic=False):
        raise ValueError("文件被项目隐私策略或排除规则保护")
    return target, inside.as_posix() if inside.parts else ""


def _safe_target(project_root: Path | str, relative: str) -> tuple[Path, str]:
    return _resolve(project_root, relative)


def _safe_dir(project_root: Path | str, relative: str) -> tuple[Path, str]:
    target, rel = _resolve(project_root, relative, directory=True)
    if not target.is_dir() or target.is_symlink():
        raise ValueError("目录不存在或不可读")
    return target, rel


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _revision(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cursor(binding: dict, position) -> str:
    data = json.dumps({"v": 1, "binding": _digest(binding), "position": position},
                      separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _position(cursor, binding, default):
    if not cursor:
        return default
    try:
        if not isinstance(cursor, str) or len(cursor) > 16384:
            raise ValueError()
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4),
                                          altchars=b"-_", validate=True))
        if data.get("v") != 1 or data.get("binding") != _digest(binding):
            raise ValueError()
        return data["position"]
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("cursor_invalid: 项目、来源版本或查询参数已变化，请重新读取") from None


def _integer(value, default, *, minimum=0, maximum=None):
    value = default if value is None else value
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("分页参数必须是有效整数")
    if maximum is not None and value > maximum:
        raise ValueError("分页参数超过上限")
    return value


def _coverage(scope, total, processed, failed=0, truncated=False, complete=None):
    return {"scope": scope, "total": total, "processed": processed,
            "failed": failed, "truncated": bool(truncated),
            "complete": not (truncated or failed) if complete is None else bool(complete)}


def _status(items, errors, truncated=False, complete=True):
    if errors and not items and not truncated:
        return "error"
    if errors or truncated or not complete:
        return "partial"
    return "ok" if items else "empty"


def _source(root, rel, revision, *, text="", kind="file",
            line_start=None, line_end=None, page_start=None, page_end=None):
    identity = [str(Path(root).resolve()), rel, revision, line_start, line_end, page_start, page_end]
    return {"source_id": "source_" + _digest(identity), "revision": revision,
            "path": rel, "page_start": page_start, "page_end": page_end,
            "line_start": line_start, "line_end": line_end,
            "quote": text[:400], "kind": kind}


def _document(path):
    """Use the page-aware extractor when installed; never invent PDF coverage."""
    kind = detect_file_type(path).get("kind")
    try:
        from ..memory.files import read_document
    except ImportError:
        read_document = None
    if read_document is not None:
        result = read_document(path, max_bytes=MAX_READ_BYTES)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("文档提取结果无效")
        return {**result, "kind": kind}
    if path.stat().st_size == 0:
        text = ""
    else:
        text = read_text_safely(path, max_bytes=MAX_READ_BYTES)
    if text is None:
        raise ValueError("该文件不是可读文本或提取失败")
    errors = ([{"code": "page_coverage_unavailable",
                "message": "PDF提取器未提供逐页覆盖，不能声明整份文档已读完"}]
              if kind == "pdf" else [])
    return {"text": text, "kind": kind, "pages": [], "errors": errors,
            "complete": not errors, "total_pages": None}


def list_directory(project_root: Path | str, relative: str = "", limit: int = 300,
                   *, offset: int = 0, cursor: str | None = None) -> dict:
    """Stable one-level listing. Continue with identical parameters and cursor."""
    root = Path(project_root).resolve()
    target, rel = _safe_dir(root, relative)
    cap = _integer(limit, 300, minimum=1, maximum=1000)
    start = _integer(offset, 0)
    rows, errors, stamps = [], [], []
    for entry in target.iterdir():
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS or excluded(root, entry, automatic=True):
            continue
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
            rows.append({"name": entry.name, "type": "dir" if is_dir else "file",
                         "size": 0 if is_dir else stat.st_size,
                         "path": entry.relative_to(root).as_posix()})
            stamps.append((entry.name, stat.st_size, stat.st_mtime_ns))
        except OSError as exc:
            errors.append({"path": entry.relative_to(root).as_posix(), "code": type(exc).__name__})
    rows.sort(key=lambda r: (r["type"] != "dir", r["name"].casefold(), r["name"]))
    revision = _digest(sorted(stamps))
    binding = {"tool": "list_files", "project": str(root), "path": rel,
               "revision": revision, "limit": cap, "offset": offset}
    start = _integer(_position(cursor, binding, start), 0)
    if start > len(rows):
        raise ValueError("offset超出目录范围")
    selected = rows[start:start + cap]
    end = start + len(selected)
    more = end < len(rows)
    return {"path": rel or ".", "source_path": str(target), "entries": selected,
            "revision": revision, "total": len(rows), "scanned": end,
            "failed": len(errors), "errors": errors, "truncated": more,
            "next_cursor": _cursor(binding, end) if more else None,
            "status": _status(selected, errors, more),
            "coverage": _coverage("directory_entries", len(rows), end, len(errors), more)}


def _inventory(root, target):
    files, errors = [], []
    def failed(exc):
        errors.append({"code": type(exc).__name__, "message": "目录扫描失败"})
    for directory, dirs, names in os.walk(target, followlinks=False, onerror=failed):
        folder = Path(directory)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS
                         and not excluded(root, folder / d, automatic=True))
        for name in names:
            path = folder / name
            if name.startswith(".") or excluded(root, path, automatic=True):
                continue
            if path.suffix.lower() in _SEARCH_SUFFIXES:
                files.append(path)
    files.sort(key=lambda p: (p.relative_to(root).as_posix().casefold(), p.as_posix()))
    stamps = []
    for path in files:
        try:
            stat = path.stat()
            revision = _revision(path) if stat.st_size <= MAX_READ_BYTES else str(stat.st_mtime_ns)
            stamps.append((path.relative_to(root).as_posix(), stat.st_size, revision))
        except OSError as exc:
            stamps.append((path.relative_to(root).as_posix(), type(exc).__name__))
    return files, _digest(stamps), errors


def search_text(project_root: Path | str, query: str, relative: str = "",
                limit: int = 30, max_files: int = 200, *,
                offset: int = 0, cursor: str | None = None) -> dict:
    """Literal search; cursor resumes within a file without dropping matches."""
    needle = str(query or "").strip()
    if not needle or len(needle) > 300:
        raise ValueError("搜索关键词为空或过长")
    root = Path(project_root).resolve()
    target, rel = _safe_dir(root, relative)
    hit_cap = _integer(limit, 30, minimum=1, maximum=200)
    file_cap = _integer(max_files, 200, minimum=1, maximum=1000)
    offset = _integer(offset, 0)
    files, revision, errors = _inventory(root, target)
    binding = {"tool": "search_files", "project": str(root), "path": rel,
               "revision": revision, "query": needle, "limit": hit_cap,
               "max_files": file_cap, "offset": offset}
    position = _position(cursor, binding, [offset, 0, 0])
    if not isinstance(position, list) or len(position) != 3:
        raise ValueError("cursor_invalid")
    index, line_offset, previous_failed = [_integer(v, 0) for v in position]
    if index > len(files):
        raise ValueError("offset超出文件范围")
    hits, refs = [], []
    scanned = 0
    failures = 0
    lowered = needle.casefold()
    while index < len(files) and scanned < file_cap and len(hits) < hit_cap:
        path = files[index]
        path_rel = path.relative_to(root).as_posix()
        scanned += 1
        try:
            if excluded(root, path, automatic=True):
                raise ValueError("来源策略已变化")
            if path.stat().st_size > MAX_READ_BYTES:
                raise ValueError("文件超过读取大小上限")
            source_revision = _revision(path)
            document = _document(path)
            if source_revision != _revision(path):
                raise ValueError("来源在读取期间变化")
            source_text = document["text"]
            lines = source_text.splitlines(keepends=True)
            page_ranges, page_at = [], 0
            for page in document.get("pages") or []:
                page_text = str(page.get("text") or "")
                at = source_text.find(page_text, page_at) if page_text else page_at
                if at >= 0:
                    page_ranges.append((at, at + len(page_text), page["number"]))
                    page_at = at + len(page_text)
            char_at = sum(len(line) for line in lines[:line_offset])
            doc_errors = document.get("errors") or []
            if doc_errors or document.get("complete") is False:
                if line_offset == 0:
                    failures += 1
                errors.append({"path": path_rel, "code": "incomplete_extraction"})
        except (OSError, ValueError) as exc:
            failures += 1
            errors.append({"path": path_rel, "code": type(exc).__name__,
                           "message": str(exc) if isinstance(exc, ValueError) else "文件读取失败"})
            index += 1
            line_offset = 0
            continue
        while line_offset < len(lines) and len(hits) < hit_cap:
            raw_line = lines[line_offset]
            line = raw_line.rstrip(chr(13) + chr(10))
            char_end = char_at + len(raw_line)
            line_offset += 1
            if lowered in line.casefold():
                located = [p[2] for p in page_ranges if p[0] < char_end and p[1] > char_at]
                ref = _source(root, path_rel, source_revision, text=line,
                              page_start=min(located) if located else None,
                              page_end=max(located) if located else None,
                              line_start=line_offset, line_end=line_offset)
                hits.append({"path": path_rel, "source_path": str(path),
                             "line": line_offset, "line_start": line_offset,
                             "line_end": line_offset, "text": line[:400],
                             "page_start": ref["page_start"], "page_end": ref["page_end"],
                             "excerpt_truncated": len(line) > 400,
                             "revision": source_revision, "source_id": ref["source_id"]})
                refs.append(ref)
            char_at = char_end
        if line_offset >= len(lines):
            index += 1
            line_offset = 0
    more = index < len(files)
    failed = previous_failed + failures + len([e for e in errors if "path" not in e])
    coverage = _coverage("eligible_files", len(files), index, failed, more)
    return {"query": needle, "path": rel or ".", "source_path": str(target),
            "scanned": scanned, "processed": index, "total": len(files),
            "failed": failed, "skipped": failures, "hits": hits,
            "source_refs": refs, "revision": revision, "errors": errors,
            "status": _status(hits, errors, more, coverage["complete"]),
            "coverage": coverage, "truncated": more,
            "next_cursor": _cursor(binding, [index, line_offset, failed]) if more else None}


def read_text_file(project_root: Path | str, relative: str, *,
                   line_start: int | None = None, line_end: int | None = None,
                   offset: int = 0, limit: int = 16000, cursor: str | None = None,
                   page_start: int | None = None, page_end: int | None = None) -> dict:
    """Read exact text. Lines/pages are 1-based inclusive; offset/limit are characters.

    Keep query parameters unchanged when following next_cursor. Coverage describes
    the selected range, not unseen pages or the entire project.
    """
    target, rel = _safe_target(project_root, relative)
    if not target.is_file() or target.is_symlink():
        raise ValueError("文件不存在或不可读")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError("文件过大，无法直接读取")
    revision = _revision(target)
    document = _document(target)
    if revision != _revision(target):
        raise ValueError("source_changed: 来源在读取期间变化")
    text = document["text"]
    cap = _integer(limit, 16000, minimum=1, maximum=MAX_READ_CHARS)
    offset = _integer(offset, 0)
    pages = document.get("pages") or []
    page_ranges = []
    # Locate page text in extractor text; unknown alignment stays unknown.
    search_at = 0
    for page in pages:
        page_text = str(page.get("text") or "")
        found = text.find(page_text, search_at) if page_text else search_at
        if found >= 0:
            page_ranges.append((found, found + len(page_text), int(page["number"])))
            search_at = found + len(page_text)
    lo, hi = 0, len(text)
    if page_start is not None or page_end is not None:
        first = _integer(page_start, 1, minimum=1)
        last = _integer(page_end, first, minimum=first)
        selected = [p for p in page_ranges if first <= p[2] <= last]
        if not selected:
            raise ValueError("请求页码不存在或提取器未提供页码定位")
        lo, hi = selected[0][0], selected[-1][1]
    line_parts = text.splitlines(keepends=True)
    starts = [0]
    for part in line_parts:
        starts.append(starts[-1] + len(part))
    if line_start is not None or line_end is not None:
        first = _integer(line_start, 1, minimum=1)
        last = _integer(line_end, max(first, len(line_parts)), minimum=first)
        lo = max(lo, starts[min(first - 1, len(line_parts))])
        hi = min(hi, starts[min(last, len(line_parts))])
        hi = max(lo, hi)
    binding = {"tool": "read_file", "project": str(Path(project_root).resolve()),
               "path": rel, "revision": revision, "text_revision": _digest(text),
               "line_start": line_start, "line_end": line_end,
               "page_start": page_start, "page_end": page_end,
               "offset": offset, "limit": cap}
    base = min(hi, lo + offset)
    if lo + offset > hi:
        raise ValueError("offset超出请求范围")
    start = _integer(_position(cursor, binding, base), 0)
    if start < lo or start > hi:
        raise ValueError("offset超出请求范围")
    end = min(hi, start + cap)
    excerpt = text[start:end]
    more = end < hi
    import bisect
    first_line = bisect.bisect_right(starts, start) if excerpt else None
    last_line = bisect.bisect_right(starts, end - 1) if excerpt else None
    located = [p[2] for p in page_ranges if p[0] < end and p[1] > start]
    errors = list(document.get("errors") or [])
    complete = bool(document.get("complete", False)) and not more and not errors
    ref = _source(project_root, rel, revision, text=excerpt,
                  kind="document" if document.get("kind") == "pdf" else "file",
                  line_start=first_line, line_end=last_line,
                  page_start=min(located) if located else None,
                  page_end=max(located) if located else None)
    ref["source_id"] = "source_" + _digest([ref["source_id"], start, end])
    ref.update(offset=start, end=end)
    return {"path": rel, "source_path": str(target), "size": size,
            "kind": document.get("kind"), "text": excerpt, "revision": revision,
            "source_id": ref["source_id"], "source_refs": [ref] if excerpt else [],
            "line_start": first_line, "line_end": last_line,
            "page_start": ref["page_start"], "page_end": ref["page_end"],
            "offset": start, "end_offset": end, "total_chars": len(text),
            "total_lines": len(line_parts), "total_pages": document.get("total_pages"),
            "pages": [{k: p.get(k) for k in ("number", "status", "method")}
                      for p in pages if p.get("number") in located],
            "errors": errors, "complete": complete, "truncated": more,
            "status": _status(excerpt, errors, more, complete),
            "coverage": _coverage("requested_characters", hi - base, end - base,
                                    len(errors), more, complete),
            "next_cursor": _cursor(binding, end) if more else None}


def validate_write(project_root: Path | str, relative: str, content: str) -> tuple[Path, str, str]:
    target, rel = _safe_target(project_root, relative)
    if target.suffix.lower() not in _TEXT_SUFFIXES:
        raise ValueError("只允许写入文本/代码类文件")
    text = str(content if content is not None else "")
    if "\x00" in text:
        raise ValueError("内容包含二进制字符")
    if len(text) > MAX_WRITE_CHARS:
        raise ValueError(f"内容超过 {MAX_WRITE_CHARS} 字符上限")
    if target.exists() and target.is_symlink():
        raise ValueError("拒绝写入符号链接")
    return target, rel, text


def write_text_file(project_root: Path | str, relative: str, content: str) -> dict:
    target, rel, text = validate_write(project_root, relative, content)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = ""
    if target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = Path(project_root) / "meetingvar" / "agent_memory" / "file_backups" / stamp
        backup_target = backup_dir / rel
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_target)
        backup = f"meetingvar/agent_memory/file_backups/{stamp}/{rel}"
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
    return {"path": rel, "bytes": len(text.encode("utf-8")), "backup": backup}
