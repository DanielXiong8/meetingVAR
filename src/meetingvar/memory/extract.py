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
from .files import project_files

# jieba 懒加载：llm_extract/纯结构化路径不依赖 jieba（CI model-free 环境无此包）；
# 只有人名/拉丁抽取（_persons/_latin）真正用到，在函数内 import。
jieba = None
pseg = None

def _ensure_jieba():
    global jieba, pseg
    if jieba is None:
        import jieba as _j
        import jieba.posseg as _p

        jieba, pseg = _j, _p

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
        if re.search(r"\d{4}-\d{2}|（|（）|\(|略|待办", title) or " " in title or re.fullmatch(r"[一二三四五六七八九十百]+[、.．]?\d*", title):
            continue  # 日期标题/带括号/含空格的叙述性标题不是词条
        if not re.fullmatch(r"(?:实验记录|组会纪要|术语表|README|文献笔记|会议纪要)(?:\s*·.*)?", title):
            _add(terms, title, "结构化")
    for m in re.finditer(r"^[-*]\s*\*{0,2}([^*：:\n]{2,20})\*{0,2}[：:]", doc, re.M):  # "- 词条：定义"
        _add(terms, m.group(1), "结构化")


def _persons(docs: list[str], terms: dict[str, set]) -> None:
    # POS-based person extraction is an enhancement; basic structured and
    # Latin term extraction must still work without optional jieba.
    try:
        _ensure_jieba()
    except ImportError:
        return
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
    _HEADING_STOP = {"决定", "我的进度", "其他", "总结", "背景", "待办", "痛点", "参考", "结果", "说明", "现状", "纪要", "实验记录", "术语表"}
    docs = [
        f.read_text(encoding="utf-8", errors="ignore")
        for f in project_files(memory_dir, {".md", ".txt"})
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


# ---- LLM 特异性层（可选）：语义理解抽取，jieba 层的升级而非替代 ----
# jieba 是统计刀：认得出"李明"像人名，但不知道李明负责 S3、PI 是拍板人。
# 聊天记录/邮件等非结构化来源只有语义层读得懂。配了 MEETINGVAR_LLM_* 才启用，
# 未配置时上层自动回落 jieba 层——离线故事不断。

_LLM_PROMPT = """你是 meetingVAR 的会前分析师。以下是用户的「项目记忆」全部文档（可能包含笔记、聊天记录、邮件）。
请抽取出会议监听所需的信息：
1. terms：监听术语（会议中出现即说明在讨论用户关心的事），8~30 个，中英皆可，来自文档原文；
2. persons：人物名单，每项 {{name, role, why}}——谁是负责人/拍板人/执行人，依据是什么；
3. focus：用户关注焦点，一句话。
只输出 JSON（不要 markdown 代码块）：{{"terms":[…],"persons":[…],"focus":"…"}}。
红线：不得编造文档中不存在的人名或术语。

<项目记忆>
{docs}
</项目记忆>"""


def llm_extract(memory_dir: Path) -> tuple[dict[str, list[str]], list[dict]] | None:
    """LLM 特异性抽取。未配置 LLM 返回 None；解析失败也返回 None（上层回落）。"""
    import json

    from ..llm import chat

    docs = "\n\n---\n\n".join(
        f"### {f.relative_to(memory_dir)}\n{f.read_text(encoding='utf-8', errors='ignore')}"
        for f in project_files(memory_dir, {".md", ".txt"})
    )
    if not docs.strip():
        return None
    # 小模型 JSON 输出不稳：/no_think 关思考、剥代码栅栏、失败重试一次
    prompt = _LLM_PROMPT.format(docs=docs) + "\n/no_think"
    raw = None
    data = None
    for attempt in range(2):
        raw = chat(prompt)
        if raw is None:
            return None
        cleaned = re.sub(r"```(?:json)?|```", "", raw)
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            continue
        try:
            data = json.loads(m.group())
            break
        except json.JSONDecodeError:
            data = None
    if raw is None or not isinstance(data, dict):
        return None
    terms: dict[str, list[str]] = {}
    for t in data.get("terms", []):
        if isinstance(t, str) and 2 <= len(t) <= 20:
            terms.setdefault(t.strip(), []).append("LLM")
    persons = [p for p in data.get("persons", []) if isinstance(p, dict) and p.get("name")]
    try:
        from .. import config
        persons_file = Path(config.DATA_DIR / "persons.json")
        persons_file.parent.mkdir(parents=True, exist_ok=True)
        persons_file.write_text(json.dumps(persons, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    for p in persons:
        terms.setdefault(str(p["name"]).strip(), []).append(f"LLM人名({p.get('role', '?')})")
    if data.get("focus"):
        terms.setdefault(str(data["focus"]).strip(), []).append("LLM焦点")
    return terms, persons
