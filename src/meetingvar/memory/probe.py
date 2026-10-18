"""Budgeted whole-project reading, verified chunk caches and honest coverage.

Planning and execution use the same conservative UTF-8 byte token bound. No
model tokenizer is downloaded and planning makes no network requests. Only
complete, schema-valid chunk results enter the versioned cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .files import read_text_safely

CHUNK_CHARS = 6000
MIN_CHUNK_CHARS = 4000
TERM_LIMIT = 60
LOCAL_TOKENS_PER_SEC = 73.0  # Historical CPU estimate, not a current benchmark.
CACHE_VERSION = 2
PROMPT_VERSION = "reading-v3"
_BUDGET_WARNING = "输入按 UTF-8 字节保守上界估算，含消息开销和安全余量；不是精确 tokenizer 计数，耗时仅供参考。"

_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零0-9]{1,4}[败章回卷节]|序章|终章|尾声|后记|特典[^\n]{0,20}|SSS[^\n]{0,24})",
    re.M)


def write_terms(folder: Path, terms: list[str]) -> Path:
    """把确认词写入 <folder>/meetingvar/terms.txt（逐行 #!keep）。"""
    import time as _t

    out = Path(folder) / "meetingvar" / "terms.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# meetingVAR 确认词表（全库理解，{_t.strftime('%Y-%m-%d %H:%M')}）",
             "# 格式：#!keep 词 —— 用户确认词，重扫永不删除；go 开会时自动并入监听词表", ""]
    lines += [f"#!keep {w}" for w in dict.fromkeys(w.strip() for w in terms) if w]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _cache_dir(folder: Path) -> Path:
    # Legacy cache files are left intact, but never consulted by this version.
    return Path(folder) / "meetingvar" / "understand" / "cache" / "v2"


def atomic_write_json(path: Path, data: dict) -> None:
    """Replace only after a complete same-directory write; keep the old file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_corpus(folder: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Read text, exclude engine archives, and deduplicate near-identical copies."""
    from .files import project_files

    folder = Path(folder).resolve()
    docs: list[tuple[str, str]] = []
    notes: list[str] = []
    for f in project_files(folder, {".md", ".txt", ".docx"}):
        try:
            relative = f.resolve().relative_to(folder)
        except ValueError:
            continue
        if relative.parts and relative.parts[0].lower() == "meetingvar":
            continue
        name = relative.as_posix()
        t = read_text_safely(f)
        if not t:
            notes.append(f"{name}: 跳过（二进制/解码失败）")
            continue
        dup_of = None
        if len(t) >= 200:
            dup_of = next((prev_name for prev_name, prev in docs
                           if (len(t) > 2000 and t[:20000] == prev[:20000])
                           or t[:50000] in prev or prev[:50000] in t), None)
        if dup_of:
            notes.append(f"{name}: 与 {dup_of} 内容重复（{len(t)} 字），只读一份")
            continue
        docs.append((name, t))
    return docs, notes


_MAP_SYS = """你是严谨的文本分析员。下面是某个项目（可能是文档、代码说明、小说等）的一块内容。请只依据这一块回答。
只输出一个 JSON：
{"persons":[{"name":"原文中出现的人名","role":"这一块里他/她做了什么"}],
 "keywords":[{"term":"原文中出现的词","why":"为什么它对这个项目重要（这一块里的依据）"}],
 "events":[{"what":"这一块里发生了什么关键事","quote":"原文短引（≤30字）"}]}
硬规则：人名/词必须在这块原文里逐字出现（系统会校验，编造会被丢弃）；没有就输出 []。
为控制输出长度，persons 最多 8 项、keywords 最多 12 项、events 最多 6 项；只保留最重要的，按重要性排序，不重复。
不要输出推理过程、解释、代码围栏或额外字段。

【块：<<DOC>> 第 <<OFF>> 字起 · <<LABEL>>】
<<TEXT>>
"""

_OVERVIEW_SYS = """下面是一个项目的卷/章/文件标题清单。请用一句话说明这是什么项目（≤50字）。
只输出一句话，不要解释，不要 JSON。

<<LIST>>
"""


def _map_prompt(ch: dict) -> str:
    return (_MAP_SYS.replace("<<DOC>>", ch["doc"]).replace("<<OFF>>", str(ch["offset"]))
            .replace("<<LABEL>>", ch["label"]).replace("<<TEXT>>", ch["text"]))


def _chunk_limit(value: int | None) -> int:
    from ..llm import ClientConfigError, _positive_int

    try:
        return _positive_int(value if value is not None else
                             os.environ.get("MEETINGVAR_LLM_CHUNK_CHARS", str(CHUNK_CHARS)))
    except ClientConfigError:
        raise ClientConfigError() from None


def split_chunks(docs: list[tuple[str, str]], chunk_chars: int = CHUNK_CHARS, *, budget=None) -> list[dict]:
    """Split by headings, then fit the entire rendered request to the token budget."""
    from ..llm import ClientConfigError

    chunk_chars = _chunk_limit(chunk_chars)
    raw: list[dict] = []
    for name, text in docs:
        marks = [(0, "开头")] + [(m.start(), m.group(1).strip()[:20]) for m in _HEADING_RE.finditer(text)]
        for i, (pos, label) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            cursor, part_no = pos, 0
            while cursor < end:
                ch = {"doc": name, "offset": cursor,
                      "label": label if part_no == 0 else f"{label}(续{part_no})"}
                lo, hi = 0, min(chunk_chars, end - cursor)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    candidate = {**ch, "text": text[cursor:cursor + mid]}
                    if budget is None or budget.fits(_map_prompt(candidate)):
                        lo = mid
                    else:
                        hi = mid - 1
                if lo == 0:
                    raise ClientConfigError("context_budget")
                raw.append({**ch, "end": cursor + lo, "text": text[cursor:cursor + lo]})
                cursor += lo
                part_no += 1
    merged: list[dict] = []
    for ch in raw:
        if (merged and merged[-1]["doc"] == ch["doc"] and merged[-1]["end"] == ch["offset"]
                and len(merged[-1]["text"]) + len(ch["text"]) <= chunk_chars
                and (len(merged[-1]["text"]) < MIN_CHUNK_CHARS or len(ch["text"]) < MIN_CHUNK_CHARS)):
            candidate = {**merged[-1], "text": merged[-1]["text"] + ch["text"], "end": ch["end"]}
            if budget is None or budget.fits(_map_prompt(candidate)):
                merged[-1] = candidate
                continue
        merged.append(dict(ch))
    return merged


def plan(folder: Path, chunk_chars: int | None = None) -> dict:
    """Offline plan using exactly the execution splitter and effective budgets."""
    from ..llm import ClientConfigError, token_budget

    docs, notes = load_corpus(Path(folder))
    n_skip = sum(1 for n in notes if "跳过" in n)
    n_dup = sum(1 for n in notes if "重复" in n)
    summary = ([f"跳过 {n_skip} 个不可读文件"] if n_skip else [])
    summary += [f"合并重复 {n_dup} 组"] if n_dup else []
    result = {"ok": False, "error": None, "error_code": None, "complete": False,
              "warnings": [_BUDGET_WARNING], "folder": str(folder),
              "files": [{"name": n, "chars": len(t)} for n, t in docs], "notes": notes,
              "notes_stats": {"skipped": n_skip, "dups": n_dup}, "notes_summary": "；".join(summary),
              "chars": sum(len(t) for _, t in docs), "chunks": [], "est_local_minutes": 0.0}
    try:
        budget, limit = token_budget(), _chunk_limit(chunk_chars)
        chunks = split_chunks(docs, chunk_chars=limit, budget=budget)
    except ClientConfigError as exc:
        result.update(error=str(exc), error_code=exc.error_code)
        return result
    result["budget"] = {**budget.as_dict(), "chunk_chars_limit": limit}
    result["chunks"] = [{k: ch[k] for k in ("doc", "offset", "end", "label")} |
                        {"chars": len(ch["text"])} for ch in chunks]
    tokens = sum(budget.input_tokens(_map_prompt(ch)) for ch in chunks)
    result["est_local_minutes"] = round(tokens / LOCAL_TOKENS_PER_SEC / 60, 1)
    result["ok"] = bool(chunks)
    if not chunks:
        result.update(error="没有可读文本文件（.md/.txt/.docx）", error_code="no_text")
    return result


def _valid_map(data) -> bool:
    fields = {"persons": ("name", "role"), "keywords": ("term", "why"), "events": ("what", "quote")}
    if not isinstance(data, dict) or set(data) != set(fields):
        return False
    return all(isinstance(data[key], list) and all(
        isinstance(item, dict) and set(item) == set(names)
        and all(isinstance(item[name], str) for name in names) for item in data[key])
        for key, names in fields.items())


def verify_chunk(data: dict, text: str) -> dict:
    """Verify exact text evidence after structural validation."""
    out = {"persons": [], "keywords": [], "events": []}
    for p in data.get("persons") or []:
        nm = p["name"].strip()
        if nm and nm in text:
            out["persons"].append({"name": nm, "role": p["role"][:80]})
    for k in data.get("keywords") or []:
        tm = k["term"].strip()
        if tm and tm in text:
            out["keywords"].append({"term": tm, "why": k["why"][:100]})
    for e in data.get("events") or []:
        what, quote = e["what"].strip(), e["quote"].strip()
        if what:
            out["events"].append({"what": what[:100], "quote": quote[:40] if quote in text else ""})
    return out


def _stable_json(raw: str | None) -> dict | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    # Tolerate a single enclosing JSON fence, never arbitrary prose or greedy
    # extraction from multiple unrelated JSON objects.
    cleaned = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1)
    from ..llm import parse_json_object

    data = parse_json_object(cleaned)
    return data if _valid_map(data) else None


def _chunk_key(ch: dict, profile: dict, chunk_chars: int = CHUNK_CHARS) -> str:
    identity = {"version": CACHE_VERSION, "prompt_version": PROMPT_VERSION,
                "prompt_hash": hashlib.sha256(_MAP_SYS.encode("utf-8")).hexdigest(),
                "profile": profile, "temperature": 0.2, "chunk_chars": chunk_chars,
                "chunk": {k: ch[k] for k in ("doc", "offset", "end", "label", "text")}}
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest() + ".json"


def understand(folder: Path, max_chunks: int | None = None, progress=None,
               chunk_chars: int | None = None, overview: bool = True) -> dict:
    """Return verified results and coverage, retaining successful chunks on failure."""
    from ..llm import ClientConfigError, chat_result, client_profile, error_result, token_budget

    folder = Path(folder)
    docs, notes = load_corpus(folder)
    unique = sum(len(t) for _, t in docs)
    result = {"ok": False, "error": None, "error_code": None, "complete": False,
              "warnings": [_BUDGET_WARNING], "folder": str(folder),
              "files": [{"name": n, "chars": len(t)} for n, t in docs], "notes": notes,
              "unique_chars": unique, "chunks_total": 0, "chunks_read": 0,
              "covered_chars": 0, "coverage_pct": 0.0, "cache_hits": 0,
              "overview": "", "terms": [], "persons": [], "events": []}
    if not docs:
        result.update(error="没有可读文本文件（.md/.txt/.docx）", error_code="no_text")
        return result
    try:
        budget, limit = token_budget(), _chunk_limit(chunk_chars)
        profile = client_profile()
        if max_chunks is not None and (type(max_chunks) is not int or max_chunks <= 0):
            raise ClientConfigError()
        chunks = split_chunks(docs, chunk_chars=limit, budget=budget)
    except ClientConfigError as exc:
        result.update(error=str(exc), error_code=exc.error_code)
        return result
    result["budget"] = {**budget.as_dict(), "chunk_chars_limit": limit}
    result["chunks_total"] = len(chunks)
    cache = _cache_dir(folder)
    full = "\n".join(t for _, t in docs)
    todo = chunks if max_chunks is None else chunks[:max_chunks]
    persons: dict[str, dict] = {}
    keywords: dict[str, dict] = {}
    events: list[dict] = []

    def warn(message: str) -> None:
        if message not in result["warnings"]:
            result["warnings"].append(message)

    for i, ch in enumerate(todo):
        if progress:
            progress(f"处理中 {i + 1}/{len(todo)} 块：{ch['doc']}@{ch['offset']}（{len(ch['text'])} 字）")
        key = _chunk_key(ch, profile, limit)
        cache_file = cache / key
        verified, cache_hit = None, False
        try:
            envelope = json.loads(cache_file.read_text(encoding="utf-8"))
            if (isinstance(envelope, dict) and envelope.get("version") == CACHE_VERSION
                    and envelope.get("key") == key and _valid_map(envelope.get("result"))):
                verified = verify_chunk(envelope["result"], ch["text"])
                cache_hit = True
            else:
                warn("忽略结构无效的块缓存，已重新读取。")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            warn("块缓存不可读，已重新读取。")
        if verified is None:
            reply = chat_result(_map_prompt(ch), temperature=0.2, timeout=1800)
            data = _stable_json(reply["content"]) if reply["ok"] else None
            if data is None:
                failure = reply if not reply["ok"] else error_result("invalid_json")
                result.update(error=f"第 {i + 1} 块读取失败：{failure['error']}",
                              error_code=failure["error_code"])
                break
            verified = verify_chunk(data, ch["text"])
            try:
                atomic_write_json(cache_file, {"version": CACHE_VERSION, "key": key,
                                  "chunk": {k: ch[k] for k in ("doc", "offset", "end", "label")},
                                  "result": verified})
            except OSError:
                warn("已完成块未能写入缓存，下次可能需要重新读取。")
        result["cache_hits"] += int(cache_hit)
        result["covered_chars"] += len(ch["text"])
        result["chunks_read"] += 1
        if progress:
            suffix = "（缓存）" if cache_hit else ""
            progress(f"已读 {i + 1}/{len(todo)} 块{suffix}：{ch['doc']}@{ch['offset']}")
        for person in verified["persons"]:
            slot = persons.setdefault(person["name"], {"count": full.count(person["name"]),
                                      "role": person["role"], "chunks": []})
            slot["chunks"].append(i)
        for keyword in verified["keywords"]:
            slot = keywords.setdefault(keyword["term"], {"count": full.count(keyword["term"]),
                                       "why": keyword["why"], "chunks": []})
            slot["chunks"].append(i)
        events.extend(verified["events"])

    if overview and result["error"] is None and keywords:
        names = sorted({ch["doc"] for ch in chunks})
        reply = chat_result(_OVERVIEW_SYS.replace("<<LIST>>", "、".join(names)),
                            temperature=0.2, timeout=300)
        lines = reply["content"].strip().splitlines() if reply["ok"] else []
        if lines:
            result["overview"] = lines[0]
        else:
            failure = reply if not reply["ok"] else error_result("empty_content")
            warn(f"概述未生成（{failure['error_code']}）：{failure['error']}；已读取块仍然有效。")
    seen, unique_events = set(), []
    for event in events:
        if event["what"] not in seen:
            seen.add(event["what"])
            unique_events.append(event)
    result.update(
        ok=result["error"] is None and result["chunks_read"] > 0,
        complete=result["error"] is None and result["chunks_read"] == len(chunks) and bool(chunks),
        coverage_pct=round(result["covered_chars"] / max(1, unique) * 100, 1),
        terms=sorted([{"term": t, "count": v["count"], "why": v["why"], "chunks": len(v["chunks"])}
                      for t, v in keywords.items()], key=lambda x: (-x["count"], x["term"]))[:TERM_LIMIT],
        persons=sorted([{"name": n, "count": v["count"], "role": v["role"], "chunks": len(v["chunks"])}
                        for n, v in persons.items()], key=lambda x: (-x["count"], x["name"])),
        events=unique_events[:20])
    return result
