"""引擎侧只读预览桥：由桌面壳用"引擎 Python"调用，stdout 只输出一行 JSON。

职责（全部只读，不写任何文件、不触发保存链路）：
- preview <folder>：扫描项目文件夹 → 文档清单 / 抽取词表（L1 jieba）/ 仓库符号（可选）/ 人物名单；
- check：引擎环境自检。

用法（由 ui 后端自动拼命令，勿手工改路径）：
    <engine_python> engine_bridge.py preview "D:\\项目\\xxx"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_TREE_MAX_NODES = 800
_TREE_MAX_DEPTH = 6


def _build_tree(root: Path):
    """VSCode 风格文件树（含被跳过的非文本文件清单）。深度/节点数封顶。"""
    from meetingvar.memory.files import SKIP_DIRS, read_text_safely

    skipped: list[str] = []
    budget = [_TREE_MAX_NODES]

    def walk(d: Path, depth: int) -> list[dict]:
        if depth > _TREE_MAX_DEPTH or budget[0] <= 0:
            return []
        try:
            entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return []
        nodes = []
        for p in entries:
            if budget[0] <= 0:
                break
            if p.is_dir() and (p.name.lower() in SKIP_DIRS or p.is_symlink()):
                continue
            budget[0] -= 1
            try:
                if p.is_dir():
                    nodes.append({"name": p.name, "dir": True, "children": walk(p, depth + 1)})
                else:
                    node = {"name": p.name, "dir": False, "size": p.stat().st_size}
                    if p.suffix.lower() in {".txt", ".md"} and read_text_safely(p) is None:
                        skipped.append(p.name)
                    nodes.append(node)
            except OSError:
                continue
        return nodes

    return walk(root, 1), skipped


def cmd_preview(folder: Path) -> dict:
    from meetingvar.memory.extract import extract_terms
    from meetingvar.memory.files import project_files

    result: dict = {
        "folder": str(folder),
        "doc_count": 0,
        "terms": {},
        "term_count": 0,
        "persons": [],
        "repo": {"is_repo": False, "terms": 0, "top": {}},
        "tree": [],
        "skipped": [],
        "notes": [],
    }
    if not folder.is_dir():
        result["notes"].append("路径不是文件夹")
        return result

    try:
        result["doc_count"] = sum(
            1 for _ in project_files(folder, {".md", ".txt", ".pdf", ".pptx", ".docx"})
        )
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"文档扫描失败: {type(exc).__name__}")

    try:
        result["tree"], result["skipped"] = _build_tree(folder)
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"文件树构建失败: {type(exc).__name__}")

    try:
        terms = extract_terms(folder)
        result["term_count"] = len(terms)
        result["terms"] = {w: list(srcs)[:2] for w, srcs in list(terms.items())[:40]}
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"词表抽取失败: {type(exc).__name__}")

    try:
        is_repo = (folder / ".git").exists() or any(folder.glob("*.py"))
        if is_repo:
            from meetingvar.memory.repo_extract import repo_terms

            r, _cand = repo_terms(folder)
            result["repo"] = {
                "is_repo": True,
                "terms": len(r),
                "top": {w: list(srcs)[:2] for w, srcs in list(r.items())[:20]},
            }
    except Exception as exc:  # noqa: BLE001
        result["notes"].append(f"仓库扫描失败: {type(exc).__name__}")

    persons_file = folder / "meetingvar" / "persons.json"
    if persons_file.exists():
        try:
            data = json.loads(persons_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                result["persons"] = [str(p.get("name", "")) for p in data if isinstance(p, dict) and p.get("name")]
        except Exception:  # noqa: BLE001
            pass
    return result


def cmd_check() -> dict:
    import platform

    import meetingvar

    return {
        "ok": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "meetingvar": getattr(meetingvar, "__version__", "dev"),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: engine_bridge.py preview <folder> | check"}))
        return
    cmd = sys.argv[1]
    try:
        if cmd == "preview":
            payload = cmd_preview(Path(sys.argv[2]))
        elif cmd == "check":
            payload = cmd_check()
        else:
            payload = {"ok": False, "error": f"unknown command: {cmd}"}
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
