"""F1 三级漏斗抽取（v2）：仓库工具层 → 文档小模型层 → 仓库语义 API 层。

背景（2026-10-05 用户讨论）：会议 agent 面对的开发者项目 = 代码 + 文档/聊天记录。
- 代码关键词不该用 LLM"读懂"——符号名就是精确热词（函数/类/命令行工具名），
  grep 词频就是权重。零 LLM、秒级、任意仓库大小。
- 文档/纪要/聊天记录才有"谁负责什么"的语义 → 小模型（本地，离线）。
- 全仓库级语义（"这个模块是干嘛的"）→ API 大模型，但只喂工具层筛出的
  候选摘要（每文件 ≤40 行），几 KB 输入，不爆炸。

漏斗：L0 工具层永远跑；L1 文档层有本地小模型就跑；L2 仓库层配了 API 才跑。
输出统一汇入 terms 字典 {词: [来源]}，与现有 HotwordMonitor/纠错模块无缝衔接。
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

# ---------- L0 工具层：代码仓库符号/词频（零 LLM，任意大小） ----------

_CODE_EXT = {".py", ".js", ".ts", ".java", ".cpp", ".h", ".go", ".rs", ".sh", ".sql", ".yaml", ".yml", ".toml"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".zcode"}
_NAME_RE = {
    "py_def": re.compile(r"^\s*def\s+([a-zA-Z_][\w]{2,40})", re.M),
    "py_class": re.compile(r"^\s*class\s+([A-Z][\w]{1,40})", re.M),
    "func_any": re.compile(r"\b(?:function|func|fn)\s+([a-zA-Z_][\w]{2,40})"),
}
_IDENT_RE = re.compile(r"\b[a-z][a-z0-9_]{4,24}\b")


def repo_terms(repo_dir: Path, max_files: int = 2000) -> tuple[dict[str, list[str]], list[dict]]:
    """返回 (术语, 关键文件候选[(path, 摘要行, 分数)])。"""
    terms: dict[str, set] = {}
    files: list[Path] = []
    for f in repo_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in _CODE_EXT and not any(d in f.parts for d in _SKIP_DIRS):
            files.append(f)
            if len(files) >= max_files:
                break
    idents: Counter = Counter()
    candidates: list[dict] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        hits: list[str] = []
        for kind, rx in _NAME_RE.items():
            for m in rx.finditer(text):
                name = m.group(1)
                if 3 <= len(name) <= 20:
                    idents[name.lower()] += 3
                    hits.append(name)
        idents.update(w.lower() for w in _IDENT_RE.findall(text))
        if hits:
            for h in set(hits[:5]):
                terms.setdefault(h, set()).add("工具层")
        # 关键文件候选：符号密度最高的前若干文件，取头 40 行当摘要
        density = len(hits) / max(len(lines), 1)
        if len(hits) >= 3:
            candidates.append({"path": str(f.relative_to(repo_dir)), "lines": len(lines), "density": density, "head": "\n".join(lines[:40])})
    candidates.sort(key=lambda c: -c["density"])
    stop = {"self", "none", "true", "false", "import", "return", "def", "class", "str", "int", "float", "list", "dict", "path", "file", "data", "text", "name", "type", "model", "args", "kwargs", "print", "open", "test", "time"}
    terms = {w: sorted(s) for w, s in terms.items() if w not in stop}
    return terms, candidates[:8]


# ---------- L2 仓库语义层：API 大模型只读候选摘要 ----------

_REPO_PROMPT = """你是会议监听系统的分析师。下面是一个代码仓库中「符号密度最高的文件」的开头片段。
请概括每个文件实现了什么功能（一句话），并给出 5~15 个"会议中一旦提到就说明在讨论这个项目"的领域热词
（可以是功能名/模块名/业务概念，中英皆可，须来自给定内容）。
只输出 JSON：{{"files":[{{"path":"…","function":"…"}}],"terms":["…"]}}

{candidates}
"""


def repo_semantic(candidates: list[dict]) -> tuple[dict[str, list[str]], list[dict]]:
    from ..llm import chat

    blocks = "\n\n".join(f"### {c['path']} ({c['lines']} 行)\n```\n{c['head']}\n```" for c in candidates)
    raw = chat(_REPO_PROMPT.format(candidates=blocks))
    if raw is None:
        return {}, []
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}, []
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return {}, []
    terms: dict[str, list[str]] = {}
    for t in data.get("terms", []):
        if isinstance(t, str) and 2 <= len(t) <= 20:
            terms.setdefault(t.strip(), []).append("仓库API")
    files = data.get("files", [])
    return terms, files if isinstance(files, list) else []
