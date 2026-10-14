"""项目记忆库：按项目归档会议纪要 + 跨会话热词库演化。

回答用户三个问题：
1. 同一项目的多场会议纪要自动归档到同一项目目录，供模型全量扫描更新热词；
2. 不同项目不同热词库——词表文件按项目隔离（<项目>/meetingvar/ 或 data/projects/<name>/）；
3. "每扫一次更新记忆"：rescan 命令把归档纪要纳入记忆源，重抽词表（旧词权重保留）。

目录约定：
  <project>/meetingvar/
    minutes/    每场会议的纪要自动归档（go/record 结束时复制一份进来）
    terms.txt   该项目的热词库（rescan 时重建，手动词经 --extra 永久保留）
    profile.json 兴趣画像元数据
"""
import json
import shutil
import time
from pathlib import Path

from .. import config


def project_dir(project_root: Path) -> Path:
    """项目级 meetingvar 目录（词表/纪要归档都在这里）。"""
    d = project_root / "meetingvar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "minutes").mkdir(exist_ok=True)
    return d


def archive_report(project_root: Path, session_id: str) -> Path:
    """digest 后把纪要复制进项目归档。返回归档路径。"""
    src = config.DATA_DIR / "reports" / session_id
    if not src.exists():
        raise FileNotFoundError(f"先跑 digest：{src} 不存在")
    dst_dir = project_dir(project_root) / "minutes" / session_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.md"):
        shutil.copy(f, dst_dir / f.name)
    (dst_dir / "meta.json").write_text(
        json.dumps({"archived_at": time.strftime("%Y-%m-%d %H:%M"), "session_id": session_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    return dst_dir


def scan_minutes(project_root: Path) -> list[Path]:
    return sorted((project_dir(project_root) / "minutes").glob("*/minutes.md"))


def rescan_terms(project_root: Path, extra_terms: list[str] | None = None, use_llm: bool = False) -> tuple[list[str], int]:
    """把「项目文档 + 全部归档纪要」都当记忆源重抽词表；手动词永留。"""
    from .extract import extract_terms, llm_extract
    from .repo_extract import repo_terms

    docs_dir = project_root
    terms: dict[str, list[str]] = {}
    manual_terms = set(extra_terms or [])
    # 旧词表里的手动词永久保留（LLM/工具层词随新扫描刷新）
    old = project_dir(project_root) / "terms.txt"
    if old.exists():
        for line in old.read_text(encoding="utf-8").splitlines():
            if line.startswith("#!keep"):
                parts = line.split(maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    manual_terms.add(parts[1].strip())
    for word in manual_terms:
        terms.setdefault(word, []).append("手动保留")
    for w, s in extract_terms(docs_dir).items():
        terms.setdefault(w, []).extend(s)
    # 归档纪要单独作为记忆源（minutes.md 里的术语=历史会议沉淀）
    minutes_dir = project_dir(project_root) / "minutes"
    if minutes_dir.exists():
        for w, s in extract_terms(minutes_dir).items():
            terms.setdefault(w, []).extend(s)
    r, _ = repo_terms(project_root)
    for w, s in r.items():
        terms.setdefault(w, []).extend(s)
    n_minutes = len(scan_minutes(project_root))
    if use_llm:
        r = llm_extract(project_root)
        if r:
            llm_terms, persons = r
            for w, s in llm_terms.items():
                terms.setdefault(w, []).extend(s)
    out = sorted(terms)
    write = project_dir(project_root) / "terms.txt"
    lines = ["#!", f"# 项目热词库 · {project_root.name} · 更新于 {time.strftime('%Y-%m-%d %H:%M')} · 归档纪要 {n_minutes} 份"]
    lines += ["#!keep " + t for t in sorted(manual_terms)]
    lines += [word for word in out if word not in manual_terms]
    write.write_text("\n".join(lines) + "\n", encoding="utf-8"
    )
    return out, n_minutes
