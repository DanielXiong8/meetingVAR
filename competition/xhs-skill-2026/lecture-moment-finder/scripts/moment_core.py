"""Core library for lecture-moment-finder.

Pure standard library. No network access, no third-party packages, no shell.

The job: given a transcript and a natural-language question, find the complete
stretch where the teacher covers that topic (plus a dense 30-90s core), then
write replayable material: result.json, report.md and optional clips/frames.

Integrity hashes (manifest/SHA256SUMS) are deliberately not produced here; that
is the job of the legal evidence skill (recording-evidence-finder).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TOOL_NAME = "lecture-moment-finder"
TOOL_VERSION = "0.1.0"

# Words that carry no retrieval signal in a lecture question.
STOPWORDS = (
    "老师", "教授", "讲师", "课堂", "课程", "上课", "视频", "录音", "回放",
    "讲的", "讲了", "讲", "说的", "说了", "说", "提到的", "提到", "关于",
    "我想", "想找", "帮我", "麻烦", "请问", "告诉我", "找一下", "找找",
    "那几分钟", "这几分钟", "几分钟", "那段", "这段", "部分", "地方",
    "什么时候", "哪里", "哪儿", "哪个", "哪些", "怎么", "如何", "什么",
    "一下", "内容", "知识点", "考点",
    "的", "了", "吗", "呢", "啊", "吧", "和", "跟", "与", "在", "是",
    "有", "没有", "就", "都", "也", "还", "又", "把", "被", "给", "对",
)

ASCII_TOKEN = re.compile(r"^[A-Za-z0-9_+#.-]+$")
LINE_TS = re.compile(
    r"^\s*\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*(.*)$"
)
SPEAKER_PREFIX = re.compile(r"^([^:：\[\]【】]{1,14})[:：]\s*(.+)$")
SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
EXPLAIN_CUES = ("核心", "表示", "比如", "举例", "定义", "步骤", "也就是",
                "意思是", "注意", "区别", "为什么", "举例来说")
BREAK_CUES = ("休息", "下课", "课间", "稍等", "休息一下")


@dataclass
class Turn:
    start: float
    end: float
    speaker: str
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def norm(text: str) -> str:
    """Lowercase, strip punctuation/whitespace, keep CJK and word characters."""
    text = (text or "").lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff+#.-]+", "", text)
    return text


def fmt_ts(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds or 0))))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def render_window(turns: list["Turn"], start: float, end: float) -> str:
    return "\n".join(
        f"[{fmt_ts(turn.start)}] {turn.speaker + ': ' if turn.speaker else ''}{turn.text}"
        for turn in turns
        if turn.end > start and turn.start < end
    )


def _parse_clock(value: str) -> float:
    value = (value or "").strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _split_speaker(text: str) -> tuple[str, str]:
    match = SPEAKER_PREFIX.match(text.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", text.strip()


def _turns_from_json(path: Path) -> tuple[dict, list[Turn]]:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # JSON Lines: one object per line (used by live_engine transcript.jsonl)
        data = {"turns": [json.loads(line) for line in raw.splitlines() if line.strip()]}
    meta: dict = {}
    rows = data
    if isinstance(data, dict):
        meta = {k: v for k, v in data.items() if k not in {"turns", "segments", "lines"}}
        rows = data.get("turns") or data.get("segments") or data.get("lines") or []
    if not isinstance(rows, list):
        raise ValueError("JSON transcript must be a list or contain a 'turns' list")

    turns: list[Turn] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        start = row.get("start", row.get("start_ts", row.get("ts", 0)))
        end = row.get("end", row.get("end_ts"))
        text = str(row.get("text") or row.get("content") or "").strip()
        speaker = str(row.get("speaker") or row.get("role") or "").strip()
        if not text:
            continue
        try:
            start_f = float(start or 0)
        except (TypeError, ValueError):
            start_f = 0.0
        if end is None:
            nxt = rows[index + 1] if index + 1 < len(rows) else {}
            nxt_start = nxt.get("start", nxt.get("start_ts", nxt.get("ts")))
            try:
                end_f = float(nxt_start) if nxt_start is not None else start_f + 6.0
            except (TypeError, ValueError):
                end_f = start_f + 6.0
        else:
            try:
                end_f = float(end)
            except (TypeError, ValueError):
                end_f = start_f + 6.0
        if end_f < start_f:
            end_f = start_f + 6.0
        turns.append(Turn(start_f, end_f, speaker, text))

    turns.sort(key=lambda turn: turn.start)
    return meta, turns


def _turns_from_subtitle(path: Path) -> tuple[dict, list[Turn]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\s*\r?\n", raw)
    turns: list[Turn] = []
    for block in blocks:
        lines = [line.strip("\ufeff").strip() for line in block.splitlines()]
        lines = [line for line in lines if line and line.upper() != "WEBVTT"]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        left, _, right = lines[timing_index].partition("-->")
        start = _parse_clock(left.strip().split()[0] if left.strip() else "")
        end = _parse_clock(right.strip().split()[0] if right.strip() else "")
        text = " ".join(lines[timing_index + 1:]).strip()
        if not text:
            continue
        speaker, body = _split_speaker(text)
        turns.append(Turn(start, end if end >= start else start + 4.0, speaker, body))
    turns.sort(key=lambda turn: turn.start)
    return {}, turns


def _turns_from_text(path: Path) -> tuple[dict, list[Turn]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = [line for line in raw.splitlines() if line.strip()]
    stamped: list[Turn] = []
    for line in lines:
        match = LINE_TS.match(line)
        if not match:
            continue
        first, second, third, rest = match.groups()
        if third is not None:
            start = int(first) * 3600 + int(second) * 60 + int(third)
        else:
            start = int(first) * 60 + int(second)
        speaker, body = _split_speaker(rest)
        stamped.append(Turn(float(start), float(start) + 6.0, speaker, body))

    if stamped:
        for index, turn in enumerate(stamped[:-1]):
            turn.end = max(turn.start + 1.0, stamped[index + 1].start)
        stamped.sort(key=lambda turn: turn.start)
        return {}, stamped

    paragraphs = [line.strip() for line in lines if line.strip()]
    turns = [Turn(0.0, 6.0 * (i + 1), "", text) for i, text in enumerate(paragraphs)]
    return {}, turns


def parse_transcript(path: str | Path) -> tuple[dict, list[Turn]]:
    """Parse JSON / SRT / VTT / TXT / MD into ordered turns."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"transcript not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        meta, turns = _turns_from_json(path)
    elif suffix in {".srt", ".vtt"}:
        meta, turns = _turns_from_subtitle(path)
    else:
        meta, turns = _turns_from_text(path)
    if not turns:
        raise ValueError(f"no timestamped turns parsed from {path}")
    return meta, turns


def query_terms(query: str) -> tuple[list[tuple[str, float]], str]:
    """Return weighted retrieval terms plus the longest content phrase."""
    cleaned = query or ""
    for word in sorted(STOPWORDS, key=len, reverse=True):
        cleaned = cleaned.replace(word, " ")
    chunks = [chunk for chunk in re.split(r"[^\w\u4e00-\u9fff+#.-]+", cleaned) if chunk]

    terms: dict[str, float] = {}
    longest = ""
    for chunk in chunks:
        chunk = chunk.strip("-_.")
        if not chunk:
            continue
        if ASCII_TOKEN.match(chunk):
            if len(chunk) >= 2:
                terms[chunk.lower()] = max(terms.get(chunk.lower(), 0.0), 2.2)
            continue
        if len(chunk) > len(longest):
            longest = chunk
        if len(chunk) <= 8:
            terms[chunk] = max(terms.get(chunk, 0.0), 3.2)
        for size, weight in ((2, 1.0), (3, 1.6), (4, 2.1), (5, 2.6), (6, 3.0)):
            if len(chunk) >= size:
                for index in range(len(chunk) - size + 1):
                    gram = chunk[index:index + size]
                    terms[gram] = max(terms.get(gram, 0.0), weight)

    ranked = sorted(terms.items(), key=lambda item: (-item[1], -len(item[0])))
    return ranked[:48], longest


def _score_turn(turn: Turn, terms: list[tuple[str, float]], phrase: str) -> tuple[float, list[str]]:
    text = norm(turn.text)
    if not text:
        return 0.0, []
    score = 0.0
    why: list[str] = []
    present = 0
    for term, weight in terms:
        count = text.count(term)
        if not count:
            continue
        present += 1
        score += weight * (1.0 + math.log1p(count))
        if len(why) < 6 and term not in why:
            why.append(term)
    if phrase and len(phrase) >= 3 and phrase in text:
        score += 6.0
        if phrase not in why:
            why.insert(0, phrase)
    if present >= 2:
        score += 1.4 * (present - 1)
    if any(cue in turn.text for cue in EXPLAIN_CUES):
        score += 2.0
    score /= math.sqrt(1.0 + len(text) / 120.0)
    return score, why


def _overlap(left: str, right: str) -> int:
    """Longest suffix/prefix overlap between two short terms."""
    best = 0
    for size in range(1, min(len(left), len(right)) + 1):
        if left[-size:] == right[:size] or right[-size:] == left[:size]:
            best = size
    return best


def _clean_why(terms: list[str]) -> list[str]:
    """Drop noisy fragments that are substrings of or heavily overlap a strong term."""
    kept: list[str] = []
    for term in sorted(terms, key=lambda value: (-len(value), value)):
        if len(term) < 2:
            continue
        skip = False
        for other in kept:
            if term != other and term in other:
                skip = True
                break
            if term != other and _overlap(term, other) >= len(term) - 1:
                skip = True
                break
        if not skip:
            kept.append(term)
    return kept


def _auto_max_span(turns: list[Turn], ratio: float = 0.3, cap: float = 600.0) -> float:
    """Safety cap that scales with recording length, not a fixed target."""
    if not turns:
        return cap
    total = max(1.0, turns[-1].end - turns[0].start)
    return min(cap, max(60.0, total * ratio))


def _gap_between(left: Turn, right: Turn) -> float:
    return max(0.0, right.start - left.end)


def _is_break(turn: Turn) -> bool:
    return any(cue in turn.text for cue in BREAK_CUES)


def _adaptive_span(
    turns: list[Turn],
    scores: list[float],
    hit: int,
    min_span: float,
    max_span: float,
    rel_threshold: float,
    bridge: int,
    max_gap: float,
) -> tuple[int, int, float, float]:
    """Grow a topic span around the hit until relevance or silence ends it.

    This is deliberately not a fixed 30-90 second window. The core clip is
    chosen separately; this span is the full stretch where the topic is still
    being discussed.
    """
    peak = max(scores[hit], 0.001)
    threshold = peak * rel_threshold
    lo = hi = hit
    start, end = turns[hit].start, turns[hit].end

    # 1) Guarantee a minimum context window, balanced around the hit.
    while end - start < min_span:
        can_left = (lo > 0
                    and _gap_between(turns[lo - 1], turns[lo]) <= max_gap
                    and not _is_break(turns[lo - 1]))
        can_right = (hi < len(turns) - 1
                     and _gap_between(turns[hi], turns[hi + 1]) <= max_gap
                     and not _is_break(turns[hi + 1]))
        if not can_left and not can_right:
            break
        before = turns[hit].start - start
        after = end - turns[hit].end
        if can_left and (not can_right or before <= after):
            lo -= 1
            start = turns[lo].start
        else:
            hi += 1
            end = turns[hi].end
        if end - start >= max_span:
            break

    # 2) Grow while the neighbouring turn is still on-topic. A short off-topic
    #    bridge is allowed only when a clearly on-topic turn follows it.
    left_bridge = right_bridge = 0
    while True:
        left_room = lo > 0 and (end - turns[lo - 1].start) <= max_span
        right_room = hi < len(turns) - 1 and (turns[hi + 1].end - start) <= max_span
        left_ok = (left_room
                   and _gap_between(turns[lo - 1], turns[lo]) <= max_gap
                   and not _is_break(turns[lo - 1]))
        right_ok = (right_room
                    and _gap_between(turns[hi], turns[hi + 1]) <= max_gap
                    and not _is_break(turns[hi + 1]))
        left_high = left_ok and scores[lo - 1] >= threshold
        right_high = right_ok and scores[hi + 1] >= threshold

        if left_high and (not right_high or scores[lo - 1] >= scores[hi + 1]):
            lo -= 1
            start = turns[lo].start
            left_bridge = 0
            continue
        if right_high:
            hi += 1
            end = turns[hi].end
            right_bridge = 0
            continue

        if (left_ok and left_bridge < bridge and lo >= 2
                and scores[lo - 2] >= threshold
                and _gap_between(turns[lo - 2], turns[lo - 1]) <= max_gap):
            lo -= 1
            start = turns[lo].start
            left_bridge += 1
            continue
        if (right_ok and right_bridge < bridge and hi + 2 < len(turns)
                and scores[hi + 2] >= threshold
                and _gap_between(turns[hi + 1], turns[hi + 2]) <= max_gap):
            hi += 1
            end = turns[hi].end
            right_bridge += 1
            continue
        break

    return lo, hi, start, max(start + 1.0, end)


def _dense_core(
    turns: list[Turn],
    scores: list[float],
    lo: int,
    hi: int,
    hit: int,
    core_min: float,
    core_max: float,
) -> tuple[int, int]:
    """Pick the densest 30-90s window inside the full span for replay."""
    best = (lo, hi)
    best_density = -1.0
    for start in range(lo, hit + 1):
        total = 0.0
        for end in range(start, hi + 1):
            total += scores[end]
            if end < hit:
                continue
            duration = turns[end].end - turns[start].start
            if duration < core_min:
                continue
            if duration > core_max:
                break
            density = total / max(1.0, duration)
            if density > best_density:
                best_density = density
                best = (start, end)
    if best_density < 0:
        return lo, hi
    return best


def search(
    turns: list[Turn],
    query: str,
    top: int = 3,
    min_span: float = 45.0,
    max_span: float = 0.0,
    core_min: float = 30.0,
    core_max: float = 90.0,
    rel_threshold: float = 0.15,
    bridge: int = 2,
    max_gap: float = 20.0,
) -> list[dict]:
    """Return ranked moments, each with a full topic span and a dense core."""
    terms, phrase = query_terms(query)
    if not terms:
        return []

    scored: list[tuple[float, int, list[str]]] = []
    for index, turn in enumerate(turns):
        score, why = _score_turn(turn, terms, phrase)
        if score > 0:
            scored.append((score, index, why))
    if not scored:
        return []

    scores = [0.0] * len(turns)
    for score, index, _why in scored:
        scores[index] = score

    scored.sort(key=lambda item: -item[0])
    best_score = scored[0][0] or 1.0
    effective_max = max_span if max_span and max_span > 0 else _auto_max_span(turns)

    candidates: list[dict] = []
    for score, index, why in scored[: max(10, top * 4)]:
        lo, hi, start, end = _adaptive_span(
            turns, scores, index, min_span, effective_max, rel_threshold, bridge, max_gap
        )
        core_lo, core_hi = _dense_core(
            turns, scores, lo, hi, index, core_min, core_max
        )
        core_start = turns[core_lo].start
        core_end = turns[core_hi].end
        candidates.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(end - start, 2),
            "start_text": fmt_ts(start),
            "end_text": fmt_ts(end),
            "core_start": round(core_start, 2),
            "core_end": round(core_end, 2),
            "core_duration": round(core_end - core_start, 2),
            "core_start_text": fmt_ts(core_start),
            "core_end_text": fmt_ts(core_end),
            "turn_range": [lo, hi],
            "core_turn_range": [core_lo, core_hi],
            "speaker": turns[index].speaker,
            "text": render_window(turns, start, end),
            "core_text": render_window(turns, core_start, core_end),
            "quote": turns[index].text,
            "score": round(float(score) / best_score, 4),
            "raw_score": round(float(score), 4),
            "why": _clean_why(why)[:4],
        })

    candidates.sort(key=lambda item: (item["start"], -item["score"]))
    merged: list[dict] = []
    for item in candidates:
        same_moment = bool(merged) and (
            min(merged[-1]["core_end"], item["core_end"])
            - max(merged[-1]["core_start"], item["core_start"]) > 0
        )
        if same_moment:
            previous = merged[-1]
            previous["end"] = max(previous["end"], item["end"])
            previous["end_text"] = fmt_ts(previous["end"])
            previous["duration"] = round(previous["end"] - previous["start"], 2)
            if item["score"] > previous["score"]:
                for key in ("score", "raw_score", "why", "quote",
                            "core_start", "core_end", "core_duration",
                            "core_start_text", "core_end_text",
                            "core_turn_range", "core_text"):
                    previous[key] = item[key]
            continue
        merged.append(item)

    for item in merged:
        item["text"] = render_window(turns, item["start"], item["end"])
        selected = [
            index for index, turn in enumerate(turns)
            if turn.end > item["start"] and turn.start < item["end"]
        ]
        if selected:
            item["turn_range"] = [selected[0], selected[-1]]

    merged.sort(key=lambda item: (-item["score"], item["start"]))
    if merged:
        best_raw = max(item["raw_score"] for item in merged)
        merged = [item for item in merged if item["raw_score"] >= best_raw * 0.2]
    return merged[: max(1, int(top))]


def _which_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _extract_clip(ffmpeg: str, media: Path, start: float, end: float, target: Path) -> bool:
    base = [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(media)]
    attempts = [
        base + ["-c", "copy", str(target)],
        base + ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(target)],
        base + ["-vn", "-c:a", "aac", str(target.with_suffix(".m4a"))],
    ]
    for command in attempts:
        try:
            subprocess.run(command, capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        produced = Path(command[-1])
        if produced.is_file() and produced.stat().st_size > 0:
            if produced != target:
                produced.replace(target)
            return True
    return False


def _extract_frame(ffmpeg: str, media: Path, at: float, target: Path) -> bool:
    command = [
        ffmpeg, "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}",
        "-i", str(media), "-frames:v", "1", "-q:v", "2", str(target),
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return target.is_file() and target.stat().st_size > 0


def render_report(query: str, source: str, segments: list[dict], notes: list[str]) -> str:
    lines = [
        "# 课堂回放定位结果",
        "",
        f"- **问题**：{query}",
        f"- **来源**：`{source}`",
        f"- **命中片段**：{len(segments)} 段",
        "",
    ]
    for index, segment in enumerate(segments, 1):
        lines += [
            f"## {index}. 完整讨论 {segment['start_text']} – {segment['end_text']}"
            f"（{int(segment['duration'])} 秒）",
            "",
            f"**核心片段**：{segment['core_start_text']} – {segment['core_end_text']}"
            f"（{int(segment['core_duration'])} 秒）",
            "",
            f"**命中理由**：{'、'.join(segment.get('why') or []) or '关键词匹配'}",
            "",
            "> " + segment["quote"],
            "",
            "完整讨论：",
            "",
            "```text",
            segment["text"],
            "```",
            "",
        ]
        if segment.get("core_text") and segment["core_duration"] < segment["duration"] - 1:
            lines += [
                "核心片段原文：",
                "",
                "```text",
                segment["core_text"],
                "```",
                "",
            ]
    lines += [
        "## 如何回放",
        "",
        "1. 打开原始录音/录屏，先跳到“核心片段”的开始时间；",
        "2. 需要完整语境时，再按“完整讨论”的起止时间回看；",
        "3. 对照 `transcript_excerpt.txt` 中的原话；",
        "4. 有 `clip_core.mp4` / `clip_full.mp4` 时可以直接播放。",
        "",
    ]
    for note in notes:
        lines.append(f"> 注意：{note}")
    return "\n".join(lines).rstrip() + "\n"


def build_outputs(
    out_dir: str | Path,
    *,
    query: str,
    transcript_path: str | Path,
    segments: list[dict],
    media_path: str | Path | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Write result.json, report.md, transcript excerpt and optional clips."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    notes = list(notes or [])
    transcript_path = Path(transcript_path)

    result = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "query": query,
        "source": str(transcript_path),
        "segments": segments,
        "notes": notes,
    }
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "report.md").write_text(
        render_report(query, str(transcript_path), segments, notes), encoding="utf-8"
    )
    excerpt = "\n\n".join(
        f"# 片段 {i + 1} · 完整讨论 {segment['start_text']} – {segment['end_text']}\n"
        f"核心片段：{segment['core_start_text']} – {segment['core_end_text']}\n\n{segment['text']}"
        for i, segment in enumerate(segments)
    )
    (out / "transcript_excerpt.txt").write_text(excerpt + "\n", encoding="utf-8")

    media = Path(media_path) if media_path else None
    if media and media.is_file():
        ffmpeg = _which_ffmpeg()
        if ffmpeg:
            first = segments[0] if segments else None
            if first:
                if _extract_clip(ffmpeg, media, first["core_start"], first["core_end"],
                                 out / "clip_core.mp4"):
                    notes.append("已生成核心片段 clip_core.mp4。")
                else:
                    notes.append("ffmpeg 无法截取核心片段，未生成 clip_core.mp4。")
                if first["duration"] <= 600:
                    if _extract_clip(ffmpeg, media, first["start"], first["end"],
                                     out / "clip_full.mp4"):
                        notes.append("已生成完整讨论 clip_full.mp4。")
                    else:
                        notes.append("ffmpeg 无法截取完整讨论，未生成 clip_full.mp4。")
                else:
                    notes.append("完整讨论超过 10 分钟，未生成 clip_full.mp4；核心片段仍可用。")
                frames_dir = out / "frames"
                frames_dir.mkdir(exist_ok=True)
                core_span = max(1.0, first["core_end"] - first["core_start"])
                for i, ratio in enumerate((0.15, 0.5, 0.85), 1):
                    _extract_frame(ffmpeg, media, first["core_start"] + core_span * ratio,
                                   frames_dir / f"frame_{i:02d}.jpg")
        else:
            notes.append("未找到 ffmpeg，未生成片段；转录与时间戳仍然可用。")

    if notes:
        result["notes"] = notes
        (out / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "report.md").write_text(
            render_report(query, str(transcript_path), segments, notes),
            encoding="utf-8",
        )

    return {
        "ok": True,
        "out_dir": str(out),
        "result": str(out / "result.json"),
        "report": str(out / "report.md"),
        "excerpt": str(out / "transcript_excerpt.txt"),
        "notes": notes,
    }
