"""Transcript-only lecture focus search with small-window LLM judging.

The old pipeline scored an entire 10-minute excerpt with TF-IDF/keywords, which
finds the topic but not the moment.  This module builds 60-120 second windows,
prefilters candidates with the user's focus terms, then asks an LLM whether each
window actually explains the concept.  The output is a list of timestamped
evidence segments, not a whole-excerpt hit.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_LINE = re.compile(r"\[(?:(\d+):)?(\d+):(\d+)\]\s+([^:]+):\s+(.*)")


@dataclass
class LectureTurn:
    id: str
    ts: float
    end_ts: float
    speaker: str
    text: str


def parse_transcript(path: str | Path) -> list[LectureTurn]:
    rows = []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()):
        match = _LINE.match(line)
        if not match:
            continue
        ts = int(match.group(1) or 0) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
        rows.append(LectureTurn(f"turn_{index}", float(ts), float(ts + 2),
                                match.group(4), match.group(5)))
    for index, row in enumerate(rows[:-1]):
        row.end_ts = max(row.ts + 0.5, rows[index + 1].ts)
    return rows


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


_GLOSSARY: list[str] | None = None


def _glossary_terms() -> list[str]:
    global _GLOSSARY
    if _GLOSSARY is None:
        path = Path(__file__).resolve().parents[2] / "configs" / "lecture_math_terms.txt"
        try:
            _GLOSSARY = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                         if line.strip() and not line.strip().startswith("#")]
        except OSError:
            _GLOSSARY = []
    return _GLOSSARY


def _terms(question: str, keywords: list[str] | None) -> list[str]:
    words = [str(x).strip() for x in (keywords or []) if str(x).strip()]
    if words:
        return list(dict.fromkeys(words))
    chunks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_.+-]+", question)
    # Without caller-supplied keywords, a whole Chinese question is useless as
    # a literal search term.  Recover known domain terms first
    # ("多元复合函数链式法则" -> 链式法则), then split the residue on
    # interrogative/functional words.
    stop = re.compile(r"怎么|如何|为什么|什么|哪些|定义|计算|求|推导|解释|讲解|请|一下|"
                      r"与|和|及|的|是|吗|呢|了")
    glossary = sorted(_glossary_terms(), key=len, reverse=True)
    terms = []
    for chunk in chunks:
        residual = chunk
        matched = [term for term in glossary if term in chunk]
        for term in matched:
            if term not in terms:
                terms.append(term)
            residual = residual.replace(term, "")
        parts = [part for part in stop.split(residual) if 2 <= len(part) <= 8]
        if parts:
            terms.extend(parts)
        elif residual and not stop.search(residual) and 2 <= len(residual) <= 8:
            terms.append(residual)
    return list(dict.fromkeys(terms))


def build_windows(turns: list[LectureTurn], window: float = 90.0,
                  stride: float = 45.0) -> list[dict]:
    if not turns:
        return []
    end = turns[-1].end_ts
    rows = []
    start = 0.0
    while start < end:
        selected = [turn for turn in turns if turn.end_ts > start and turn.ts < start + window]
        if selected:
            text = " ".join(turn.text for turn in selected)
            rows.append({"start": round(start, 2), "end": round(start + window, 2),
                         "turn_ids": [turn.id for turn in selected], "text": text,
                         "lines": [{"id": turn.id, "ts": turn.ts, "text": turn.text}
                                   for turn in selected]})
        start += stride
    return rows


def keyword_hits(text: str, terms: list[str]) -> list[str]:
    normalized = _normalize(text)
    return [term for term in terms if _normalize(term) in normalized]


def _bigrams(text: str) -> set[str]:
    text = _normalize(text)
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))}


def _judge(chat_fn: Callable, question: str, window: dict, timeout: int = 120) -> dict:
    # The judge must see the same evidence IDs it is asked to cite.  Without
    # the prefixes a small model invents "turn_1", which then fails validation.
    lines = window.get("lines") or []
    rendered, used = [], 0
    for row in lines:
        line = f"[{row['id']}] {row['text']}"
        if used + len(line) + 1 > 2400:
            break
        rendered.append(line)
        used += len(line) + 1
    transcript = "\n".join(rendered) or window["text"][:2400]
    prompt = (
        "你是课堂内容裁判。判断下面这段课堂转写是否在实质讲解或回答关注问题。\n"
        "同义表达、举例、推导、板书口述都算命中；不要要求关注问题里的原词逐字出现。\n"
        "只有纯闲聊、课程安排、只出现一个词但没有解释时才算不命中。\n"
        "只输出 JSON：{\"hit\":true/false,\"reason\":\"简短理由\",\"evidence_ids\":[\"turn_1\"]}；"
        "没有命中时 evidence_ids 为空数组。evidence_ids 只能从下列方括号里的 ID 中选择。\n\n"
        f"关注问题：{question}\n转写：\n{transcript}"
    )
    raw = chat_fn(prompt, temperature=0, timeout=timeout)
    if raw is None:
        return {"hit": False, "error": "LLM unavailable"}
    try:
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group()) if match else None
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("hit"), bool):
        return {"hit": False, "error": "invalid JSON"}
    ids = data.get("evidence_ids")
    if data["hit"]:
        if not isinstance(ids, list) or not ids:
            return {"hit": False, "error": "missing evidence"}
        allowed = set(window["turn_ids"])
        if not {str(x) for x in ids} <= allowed:
            return {"hit": False, "error": "evidence outside window"}
    return {"hit": bool(data["hit"]), "reason": str(data.get("reason", ""))[:300],
            "evidence_ids": [str(x) for x in (ids or [])]}


def find_focus(turns: list[LectureTurn], question: str, *,
               keywords: list[str] | None = None, chat_fn: Callable | None = None,
               window: float = 90.0, stride: float = 45.0,
               max_candidates: int = 6, timeout: int = 120) -> dict:
    terms = _terms(question, keywords)
    windows = build_windows(turns, window, stride)
    query_bigrams = _bigrams(question)
    scored = []
    for row in windows:
        hits = keyword_hits(row["text"], terms)
        overlap = _bigrams(row["text"])
        # Frequency matters: the window that defines 方向导数 says it six
        # times, while a passing mention says it once.
        occurrences = sum(_normalize(row["text"]).count(_normalize(term)) for term in hits)
        lexical = occurrences * 6 + len(hits) * 4 + len(query_bigrams & overlap)
        scored.append((lexical, hits, row))
    scored.sort(key=lambda item: (-item[0], item[2]["start"]))
    # If there are keyword-bearing windows, judge those first; otherwise take
    # the most lexically overlapping windows.  Always retain one early and one
    # late window so the judge is not trapped by a bad lexical top-k.
    with_hits = [item for item in scored if item[1]]
    candidates = (with_hits or scored)[:max_candidates]
    if windows and all(item[2]["start"] != windows[0]["start"] for item in candidates):
        candidates.append((0, [], windows[0]))
    if windows and all(item[2]["start"] != windows[-1]["start"] for item in candidates):
        candidates.append((0, [], windows[-1]))
    results = []
    for _, hits, row in sorted(candidates, key=lambda item: item[2]["start"]):
        judged = {"hit": bool(hits), "reason": "关键词命中" if hits else "候选窗口",
                  "evidence_ids": row["turn_ids"][:1]}
        if chat_fn is not None:
            judged = _judge(chat_fn, question, row, timeout=timeout)
        # A window with two or more explicit focus terms is evidence-bearing
        # even when a small local judge refuses it; one term alone still needs
        # the semantic judge.
        hit = bool(judged.get("hit")) or len(hits) >= 2
        if hit:
            reasons = [x for x in (judged.get("reason", ""), "关键词:" + "+".join(hits) if hits else "") if x]
            evidence = [str(x) for x in (judged.get("evidence_ids") or [])]
            lines = row["lines"]
            first = next((i for i, line in enumerate(lines) if line["id"] in set(evidence)), 0)
            lines = lines[first:]
            start = lines[0]["ts"] if lines else row["start"]
            results.append({"start": start, "end": row["end"],
                            "text": " ".join(line["text"] for line in lines),
                            "turn_ids": [line["id"] for line in lines],
                            "lines": lines,
                            "keywords": hits, "reason": "；".join(reasons),
                            "evidence_ids": evidence or [lines[0]["id"]] if lines else []})
    # Merge overlapping/touching hit windows into segments.  Windows overlap by
    # design, so rebuild the text from de-duplicated turn lines instead of
    # concatenating the same 60 seconds twice.
    merged = []
    for row in results:
        if merged and row["start"] <= merged[-1]["end"] + 1:
            merged[-1]["end"] = max(merged[-1]["end"], row["end"])
            seen = {line["id"] for line in merged[-1]["lines"]}
            merged[-1]["lines"].extend(line for line in row["lines"] if line["id"] not in seen)
            merged[-1]["text"] = " ".join(line["text"] for line in merged[-1]["lines"])
            merged[-1]["turn_ids"] = list(dict.fromkeys(merged[-1]["turn_ids"] + row["turn_ids"]))
            merged[-1]["keywords"] = list(dict.fromkeys(merged[-1]["keywords"] + row["keywords"]))
        else:
            merged.append(dict(row))
    return {"question": question, "terms": terms, "windows": len(windows),
            "candidates": len(candidates), "segments": merged,
            "hits": len(merged)}
