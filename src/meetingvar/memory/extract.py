"""F1 · 会前兴趣注入：从项目记忆自动抽取监听词表（术语 + 人名）。

设计立场（用户定案）：meetingVAR 是会议 agent——有记忆、有项目文件访问权限，
**词表应该自己读出来**；用户主动输入的热词是覆盖项，不是必选项。

抽取三路：
  a) 结构化标记：markdown 标题、**加粗**、`- 词条：定义` 形态（术语表通常长这样）；
  b) 人名：jieba 词性标注（nr）频次 ≥2 + 称谓模式（PI/师兄A/张老师…）；
  c) 拉丁术语：字母词频次 ≥2（kraken2、UMI、Q30）。
用户手动词表（--extra）永远并入且去重。输出一行一词的监听词表。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import jieba
import jieba.posseg as pseg

_ROLE_RE = re.compile(r"(?:PI|导师|老板|教授|老师|师兄|师姐|师弟|师妹)[A-Za-z]?")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")
_CN_WORD_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")

# nr 标注的常见误报（品牌/机构/地名在会议语境通常不是"要监听的人名"）
_NAME_BLOCKLIST = {"华为", "腾讯", "阿里", "百度", "微信", "讯飞", "谷歌", "苹果", "北京", "上海", "深圳", "中国"}
_LATIN_BLOCKLIST = {"data", "readme", "true", "false", "none", "wav", "mp4", "jpg", "md", "txt", "api", "ok", "todo"}


def _add(terms: dict[str, set], word: str, source: str) -> None:
    word = word.strip().strip("*：:，。、 ")
    if not (2 <= len(word) <= 20):
        return
    if "、" in word or "。" in word:  # 顿号串（S1、S2…）不是单一词条
        return
    if re.fullmatch(r"[A-Za-z0-9\-]+", word) and word.lower() in _LATIN_BLOCKLIST:
        return
    terms.setdefault(word, set()).add(source)


def _structured(doc: str, terms: dict[str, set]) -> None:
    for m in re.finditer(r"\*\*([^*\n]{2,20})\*\*", doc):  # 加粗
        _add(terms, m.group(1), "结构化")
    for m in re.finditer(r"^#{1,4}\s*(.+?)\s*$", doc, re.M):  # 标题
        title = m.group(1)
        if re.search(r"\d{4}-\d{2}|（|（）|\(|略|待办", title) or " " in title:
            continue  # 日期标题/带括号/含空格的叙述性标题不是词条
        if not re.fullmatch(r"(?:实验记录|组会纪要|术语表|README|文献笔记|会议纪要)(?:\s*·.*)?", title):
            _add(terms, title, "结构化")
    for m in re.finditer(r"^[-*]\s*\*{0,2}([^*：:\n]{2,20})\*{0,2}[：:]", doc, re.M):  # "- 词条：定义"
        _add(terms, m.group(1), "结构化")


def _persons(docs: list[str], terms: dict[str, set]) -> None:
    counts: Counter = Counter()
    for doc in docs:
        for w, flag in pseg.cut(doc):
            if flag == "nr" and 1 < len(w) <= 6:
                counts[w] += 1
    for w, c in counts.items():
        if c >= 2 and w not in _NAME_BLOCKLIST and not re.search(r"[a-zA-Z0-9]", w):
            if w.endswith(("的", "了", "汇", "报", "说", "在", "和", "与")):
                continue  # jieba nr 碎片误标（"师兄汇""师弟的"）
            _add(terms, w, "人名")
    # 称谓模式：PI / 师兄A / 张老师（出现即收）
    joined = "\n".join(docs)
    for m in set(_ROLE_RE.findall(joined)):
        _add(terms, m, "人名")


def _latin(docs: list[str], terms: dict[str, set]) -> None:
    counts: Counter = Counter()
    for doc in docs:
        for w in _LATIN_RE.findall(doc):
            counts[w] += 1
    for w, c in counts.items():
        if c >= 2:
            _add(terms, w, "拉丁")


def extract_terms(memory_dir: Path, extra_terms: list[str] | None = None) -> dict[str, list[str]]:
    """返回 {词: [来源, …]}。docs = memory_dir 下全部 md/txt。"""
    _HEADING_STOP = {"决定", "我的进度", "其他", "总结", "背景", "待办", "痛点", "参考", "结果", "说明", "现状"}
    docs = [
        f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted(memory_dir.rglob("*"))
        if f.suffix.lower() in {".md", ".txt"}
    ]
    terms: dict[str, set] = {}
    for doc in docs:
        _structured(doc, terms)
    _persons(docs, terms)
    _latin(docs, terms)
    for t in extra_terms or []:
        _add(terms, t, "手动")

    # 过滤：纯数字、单字、日期形态、通用标题残留
    out = {
        w: sorted(s)
        for w, s in terms.items()
        if not re.fullmatch(r"[\d\s\-./:]+", w) and w not in _HEADING_STOP
    }
    # 拉丁单词若是已抽取短语的一部分（index ⊂ index hopping），去重
    phrases = {w.lower().replace(" ", "") for w in out if " " in w}
    out = {w: s for w, s in out.items() if not (re.fullmatch(r"[A-Za-z\-]+", w) and any(w.lower() in ph for ph in phrases))}
    return dict(sorted(out.items(), key=lambda kv: (0 if "手动" in kv[1] else 1, -len(kv[1]), kv[0])))


def write_terms_file(terms: dict[str, list[str]], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 自动生成 meetingvar 监听词表（{time_tag()}）", f"# 来源：项目记忆自动抽取 + 手动热词", ""]
    lines += [w for w in terms]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(terms)


def time_tag() -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M")
