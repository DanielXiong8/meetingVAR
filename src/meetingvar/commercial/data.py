"""Data inventory, export and deliberate deletion for compliance controls."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import zipfile
from pathlib import Path

CATEGORIES = {
    "agent": ("对话、事实与 Trace", ("meetingvar/agent_memory",)),
    "minutes": ("会议纪要与归档", ("meetingvar/minutes",)),
    "understanding": ("全库理解缓存", ("meetingvar/understand", "meetingvar/interest_context.json")),
    "terms": ("词表、人物与声纹名册", ("meetingvar/terms.txt", "meetingvar/persons.json",
                                      "meetingvar/speakers.json")),
}


def _parts(root: Path, category: str) -> list[Path]:
    return [root / rel for rel in CATEGORIES.get(category, ("",))[1]]


def _size(path: Path) -> tuple[int, int]:
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 0, 0
    if path.is_dir():
        count = total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    count += 1
                    total += item.stat().st_size
                except OSError:
                    continue
        return count, total
    return 0, 0


def inventory(project_root: str | Path, data_dir: str | Path) -> dict:
    root = Path(project_root)
    data = Path(data_dir)
    rows = []
    for key, (label, _rels) in CATEGORIES.items():
        count = total = 0
        for path in _parts(root, key):
            c, s = _size(path)
            count += c
            total += s
        rows.append({"category": key, "label": label, "files": count, "bytes": total})
    for key, label in (("clips", "全局片段与录屏"), ("pool", "全局会话池"), ("reports", "全局纪要")):
        count, total = _size(data / key)
        rows.append({"category": key, "label": label, "files": count, "bytes": total})
    return {"ok": True, "project": str(root), "data_dir": str(data), "categories": rows}


def export_project(project_root: str | Path, out_path: str | Path) -> dict:
    root = Path(project_root)
    out = Path(out_path)
    if not root.is_dir():
        return {"ok": False, "error": "项目目录无效"}
    out.parent.mkdir(parents=True, exist_ok=True)
    include = [root / "meetingvar"]
    written = 0
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("meetingvar-export.json", json.dumps({
                "project": str(root),
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False))
            for base in include:
                if not base.is_dir():
                    continue
                for path in base.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(root).as_posix())
                        written += 1
    except (OSError, ValueError):
        return {"ok": False, "error": "导出失败"}
    return {"ok": True, "path": str(out), "files": written,
            "bytes": out.stat().st_size if out.is_file() else 0}


def delete_categories(project_root: str | Path, data_dir: str | Path,
                      categories: list[str], confirm: str) -> dict:
    if str(confirm or "").strip() != "DELETE":
        return {"ok": False, "error": "请输入 DELETE 以确认删除"}
    root = Path(project_root)
    data = Path(data_dir)
    removed = []
    for key in categories:
        if key in {"clips", "pool", "reports"}:
            target = data / key
        elif key in CATEGORIES:
            for path in _parts(root, key):
                if path.exists():
                    try:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                        removed.append(str(path))
                    except OSError:
                        pass
            continue
        else:
            continue
        if target.exists():
            try:
                shutil.rmtree(target) if target.is_dir() else target.unlink()
                removed.append(str(target))
            except OSError:
                pass
    return {"ok": True, "removed": removed, "count": len(removed)}