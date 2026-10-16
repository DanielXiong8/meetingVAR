"""全库分层阅读（"让模型读完整个项目文件夹"的生产实现）。

为什么要分层：实测全库体量与速度（2026-10-16，本机 4B CPU）——
- 155 万字中文 ≈ 97 万 token（1.6 字/token）；本地预填充 73 tok/s → 全库一遍 ≈3.7 小时，
  与块大小无关（token 总量决定）；本地上下文拉满 131k 也装不下 97 万 → "一口气全喂"不可能；
- 云端 128k 窗 ≈14 块/十几分钟，1M 窗 1-2 次调用，但单窗仍有中段遗忘。
⇒ 正确形态 = 全库分块逐块读（覆盖率可核算）+ 每块产物本地逐字校验（防幻觉）+ 确定性合并。
模型只经 llm.chat（MEETINGVAR_LLM_* 指向本地或自定义 API）——**用哪个厂商由用户在设置页选**，
本模块与厂商无关；仅"命中片段/块文本"会发给所配置的模型（本地模型=零出网）。

实测教训（都在此固化）：
- 分块正则必须 re.M，否则"第X卷/第X败"一个都匹配不上（曾整库盲切）；
- 重复副本按"50k 前缀包含"去重（逐字节副本可能差几个字符，全文件哈希法会漏）；
- 由模型做"综合长输出"的合并会复读撞输出上限 → 合并必须是确定性计算，模型只写一句话概述。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .files import read_text_safely

CHUNK_CHARS = 24000     # 单块正文上限（32k 上下文：约 15k token 预填充 + 指令/输出余量）
MIN_CHUNK_CHARS = 4000  # 小于此的碎片块与邻块合并（标题切得太碎会拖慢往返）
TERM_LIMIT = 60         # 候选词返回上限（UI 里再勾选确认 ~20 个）
CHARS_PER_TOKEN = 1.6   # 本机 tokenizer 实测（Qwen 系中文）
LOCAL_TOKENS_PER_SEC = 73.0  # 本机 4B CPU 预填充实测

_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零0-9]{1,4}[败章回卷节]|序章|终章|尾声|后记|特典[^\n]{0,20}|SSS[^\n]{0,24})",
    re.M)  # 忘了 re.M 的教训见模块头


def write_terms(folder: Path, terms: list[str]) -> Path:
    """把确认词写入 <folder>/meetingvar/terms.txt（逐行 #!keep：rescan/重扫永不删除）。"""
    import time as _t

    out = Path(folder) / "meetingvar" / "terms.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# meetingVAR 确认词表（全库理解，{_t.strftime('%Y-%m-%d %H:%M')}）",
             "# 格式：#!keep 词 —— 用户确认词，重扫永不删除；go 开会时自动并入监听词表", ""]
    lines += [f"#!keep {w}" for w in dict.fromkeys(w.strip() for w in terms) if w]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _cache_dir(folder: Path) -> Path:
    return Path(folder) / "meetingvar" / "understand" / "cache"


def load_corpus(folder: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """读全库（由 read_text_safely 统一解码），按内容去重。

    必须排除 <项目>/meetingvar/——那是引擎自己的归档区（纪要/确认词表/名册/缓存）。
    实测教训（2026-10-16）：不清除时，上一轮写出的 terms.txt 会被当语料再读一遍，
    形成"自己读自己"的自噬循环（its 内容还会产出'确认词表'这类伪词条）。
    """
    from .files import project_files

    folder = Path(folder).resolve()
    docs: list[tuple[str, str]] = []
    notes: list[str] = []
    for f in project_files(folder, {".md", ".txt", ".docx"}):
        try:
            rel_parts = f.resolve().relative_to(folder).parts
        except ValueError:
            rel_parts = f.parts
        if rel_parts and rel_parts[0].lower() == "meetingvar":
            continue
        t = read_text_safely(f)
        if not t:
            notes.append(f"{f.name}: 跳过（二进制/解码失败）")
            continue
        dup_of = next((name for name, prev in docs
                       if (len(t) > 2000 and t[:20000] == prev[:20000])
                       or t[:50000] in prev or prev[:50000] in t), None)
        if dup_of:
            notes.append(f"{f.name}: 与 {dup_of} 内容重复（{len(t)} 字），只读一份")
            continue
        docs.append((f.name, t))
    return docs, notes


def split_chunks(docs: list[tuple[str, str]], chunk_chars: int = CHUNK_CHARS) -> list[dict]:
    """按卷/章标题切段 → 打包成 ≤chunk_chars 的块 → 合并碎片块（同文档、总长不超上限）。"""
    raw: list[dict] = []
    for name, text in docs:
        marks = [(0, "开头")] + [(m.start(), m.group(1).strip()[:20]) for m in _HEADING_RE.finditer(text)]
        for i, (pos, label) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            seg = text[pos:end]
            for j in range(0, len(seg), chunk_chars):
                part = seg[j:j + chunk_chars]
                if not part.strip():
                    continue
                raw.append({"doc": name, "offset": pos + j,
                            "label": label if j == 0 else f"{label}(续{j // chunk_chars})",
                            "end": pos + j + len(part), "text": part})
    merged: list[dict] = []
    for ch in raw:
        if merged and merged[-1]["doc"] == ch["doc"] \
                and len(merged[-1]["text"]) + len(ch["text"]) <= chunk_chars \
                and (len(merged[-1]["text"]) < MIN_CHUNK_CHARS or len(ch["text"]) < MIN_CHUNK_CHARS):
            merged[-1]["text"] += ch["text"]
            merged[-1]["end"] = ch["end"]
        else:
            merged.append(dict(ch))
    return merged


def plan(folder: Path) -> dict:
    """零 LLM 预估：文件/字数/去重说明/分块清单/本地耗时估算（云端档由 UI 另行提示）。"""
    docs, notes = load_corpus(Path(folder))
    chunks = split_chunks(docs)
    unique = sum(len(t) for _, t in docs)
    tokens = unique / CHARS_PER_TOKEN
    return {
        "folder": str(folder),
        "files": [{"name": n, "chars": len(t)} for n, t in docs],
        "notes": notes,
        "chars": unique,
        "chunks": [{"doc": ch["doc"], "offset": ch["offset"], "end": ch["end"],
                    "label": ch["label"], "chars": len(ch["text"])} for ch in chunks],
        "est_local_minutes": round(tokens / LOCAL_TOKENS_PER_SEC / 60, 1),
    }


def verify_chunk(data: dict, text: str) -> dict:
    """块内逐字校验：不在本块原文出现的项直接丢弃（防幻觉前置，实测抓到过编造词）。"""
    out = {"persons": [], "keywords": [], "events": []}
    for p in data.get("persons") or []:
        nm = str(p.get("name", "")).strip()
        if nm and nm in text:
            out["persons"].append({"name": nm, "role": str(p.get("role", ""))[:80]})
    for k in data.get("keywords") or []:
        tm = str(k.get("term", "")).strip()
        if tm and tm in text:
            out["keywords"].append({"term": tm, "why": str(k.get("why", ""))[:100]})
    for e in data.get("events") or []:
        what = str(e.get("what", "")).strip()
        q = str(e.get("quote", "")).strip()
        if what:
            out["events"].append({"what": what[:100], "quote": q[:40] if q in text else ""})
    return out


_MAP_SYS = """你是严谨的文本分析员。下面是某个项目（可能是文档、代码说明、小说等）的一块内容。请只依据这一块回答。
只输出一个 JSON：
{"persons":[{"name":"原文中出现的人名","role":"这一块里他/她做了什么"}],
 "keywords":[{"term":"原文中出现的词","why":"为什么它对这个项目重要（这一块里的依据）"}],
 "events":[{"what":"这一块里发生了什么关键事","quote":"原文短引（≤30字）"}]}
硬规则：人名/词必须在这块原文里逐字出现（系统会校验，编造会被丢弃）；没有就输出 []。

【块：<<DOC>> 第 <<OFF>> 字起 · <<LABEL>>】
<<TEXT>>
"""

_OVERVIEW_SYS = """下面是一个项目的卷/章/文件标题清单。请用一句话说明这是什么项目（≤50字）。
只输出一句话，不要解释，不要 JSON。

<<LIST>>
"""


def _stable_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    cleaned = re.sub(r"```(?:json)?|```", "", raw)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except ValueError:
        return None


def _chunk_key(ch: dict, model: str) -> str:
    h = hashlib.sha256((ch["doc"] + str(ch["offset"]) + ch["text"]).encode()).hexdigest()[:16]
    return f"{h}_{re.sub(r'[^A-Za-z0-9]', '', model or 'm')[:16]}.json"


def understand(folder: Path, max_chunks: int | None = None, progress=None,
               chunk_chars: int = CHUNK_CHARS, overview: bool = True) -> dict:
    """全库分层阅读主入口。返回候选词/人物/事件 + 覆盖率与缓存统计（不写词表）。"""
    from .. import config
    from ..llm import chat

    folder = Path(folder)
    docs, notes = load_corpus(folder)
    if not docs:
        return {"ok": False, "error": "没有可读文本文件（.md/.txt/.docx）", "notes": notes}
    chunks = split_chunks(docs, chunk_chars=chunk_chars)
    cache = _cache_dir(folder)
    cache.mkdir(parents=True, exist_ok=True)
    model = config.LLM_MODEL or "m"
    full = "\n".join(t for _, t in docs)

    todo = chunks if max_chunks is None else chunks[:max_chunks]
    persons: dict[str, dict] = {}
    keywords: dict[str, dict] = {}
    events: list[dict] = []
    cached_hits = 0
    done_chars = 0
    processed = 0
    error = None
    for i, ch in enumerate(todo):
        key = _chunk_key(ch, model)
        cache_file = cache / key
        if cache_file.exists():
            try:
                result = json.loads(cache_file.read_text(encoding="utf-8"))["result"]
                cached_hits += 1
            except (OSError, ValueError, KeyError):
                result = None
        else:
            result = None
        if result is None:
            if progress:
                progress(f"已读 {i + 1}/{len(todo)} 块：{ch['doc']}@{ch['offset']}（{len(ch['text'])} 字）")
            raw = chat(_MAP_SYS.replace("<<DOC>>", ch["doc"]).replace("<<OFF>>", str(ch["offset"]))
                       .replace("<<LABEL>>", ch["label"]).replace("<<TEXT>>", ch["text"]),
                       temperature=0.2, timeout=1800)
            data = _stable_json(raw)
            if data is None:
                error = f"第 {i + 1} 块（{ch['doc']}@{ch['offset']}）模型无输出：LLM 未配置或输出非法/被截断"
                break
            result = verify_chunk(data, ch["text"])
            try:
                cache_file.write_text(json.dumps(
                    {"chunk": {k: ch[k] for k in ("doc", "offset", "end", "label")}, "result": result},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
        else:
            if progress:
                progress(f"已读 {i + 1}/{len(todo)} 块（缓存）：{ch['doc']}@{ch['offset']}")
        done_chars += len(ch["text"])
        processed = i + 1
        for p in result.get("persons") or []:
            slot = persons.setdefault(p["name"], {"count": full.count(p["name"]), "role": p.get("role", ""), "chunks": []})
            slot["chunks"].append(i)
        for k in result.get("keywords") or []:
            slot = keywords.setdefault(k["term"], {"count": full.count(k["term"]), "why": k.get("why", ""), "chunks": []})
            slot["chunks"].append(i)
        events.extend(result.get("events") or [])

    overview_text = ""
    if overview and not error and keywords:
        names = sorted({ch["doc"] for ch in chunks})
        overview_text = (chat(_OVERVIEW_SYS.replace("<<LIST>>", "、".join(names)),
                              temperature=0.2, timeout=300) or "").strip().splitlines()[0] if names else ""
    # 事件去重（保留先出现的引用）
    seen_events, uniq_events = set(), []
    for e in events:
        if e["what"] not in seen_events:
            seen_events.add(e["what"])
            uniq_events.append(e)

    return {
        "ok": error is None and done_chars > 0,
        "error": error,
        "folder": str(folder),
        "files": [{"name": n, "chars": len(t)} for n, t in docs],
        "notes": notes,
        "unique_chars": sum(len(t) for _, t in docs),
        "chunks_total": len(chunks),
        "chunks_read": processed,
        "covered_chars": done_chars,
        "coverage_pct": round(done_chars / max(1, sum(len(t) for _, t in docs)) * 100, 1),
        "cache_hits": cached_hits,
        "overview": overview_text,
        "terms": sorted(
            [{"term": t, "count": v["count"], "why": v["why"], "chunks": len(v["chunks"])}
             for t, v in keywords.items()],
            key=lambda x: (-x["count"], x["term"]))[:TERM_LIMIT],
        "persons": sorted(
            [{"name": n, "count": v["count"], "role": v["role"], "chunks": len(v["chunks"])}
             for n, v in persons.items()],
            key=lambda x: (-x["count"], x["name"])),
        "events": uniq_events[:20],
    }