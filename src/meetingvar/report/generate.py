"""会后 digest：薄纪要（无 LLM 结构化版）+ 厚纪要（prompt 填充，LLM 可选）。

LLM 通过环境变量 MEETINGVAR_LLM_BASE_URL / _API_KEY / _MODEL 接入
（OpenAI 兼容 /chat/completions）；未配置则输出〔模板版〕并如实标注。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from .. import config
from .prompts import THICK_REPORT, THIN_MINUTES


def _fmt_ts(ts: float) -> str:
    # pool_schema 中的 ts 是会议内相对秒数，不是 Unix 时间戳。
    total = max(0, int(ts))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _load_session(session_id: str) -> tuple[list[dict], list[dict]]:
    base = config.POOL_DIR / session_id
    t = json.loads((base / "transcript.json").read_text(encoding="utf-8"))
    return t["transcript"], t["clips"]


def _chapterize(transcript: list[dict], window: float = 300.0) -> list[tuple[float, list[dict]]]:
    """按会议内相对时间分章，保留原始句子和时间戳，不做语义编造。"""
    if not transcript:
        return []
    chapters: list[tuple[float, list[dict]]] = []
    start = float(transcript[0]["ts"])
    current: list[dict] = []
    for line in transcript:
        if current and float(line["ts"]) - start >= window:
            chapters.append((start, current))
            start = float(line["ts"])
            current = []
        current.append(line)
    if current:
        chapters.append((start, current))
    return chapters


_ACTION_RE = re.compile(
    r"(?:待办|行动项|TODO|会后|下次|负责|请.{0,12}(?:确认|整理|提交|联系|准备|补充|更新|修复|测试)|"
    r"(?:确认|整理|提交|联系|准备|补充|更新|修复|测试).{0,20}(?:一下|完成|跟进))",
    re.IGNORECASE,
)


def _extract_action_items(transcript: list[dict]) -> list[dict]:
    """只提取带明显行动语义的原句，避免离线模板凭空生成任务。"""
    return [line for line in transcript if _ACTION_RE.search(str(line.get("text", "")))]


def _llm_chat(prompt: str) -> str | None:
    if not (config.LLM_BASE_URL and config.LLM_MODEL):
        return None
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": config.LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _thin_no_llm(transcript: list[dict], terms_hit: dict[str, int]) -> str:
    """无 LLM 的薄纪要：结构化速览 + 5 分钟分章 + 明确行动项。"""
    lines = ["# 薄纪要（结构化版·未接 LLM）", ""]
    if not transcript:
        lines.append("（本场无转写内容）")
        return "\n".join(lines)
    first_ts = float(transcript[0]["ts"])
    last_ts = float(transcript[-1]["ts"])
    speakers = sorted({str(l["speaker"]) for l in transcript})
    lines.extend(
        [
            "## 全场速览",
            f"- 时间范围：{_fmt_ts(first_ts)} – {_fmt_ts(last_ts)}",
            f"- 转写句数：{len(transcript)}；说话人：{speakers}",
            f"- 监听词命中：{terms_hit or '无'}",
            f"- 分章数：{len(_chapterize(transcript))}",
            "",
        ]
    )
    lines.append("## 分章记录")
    for start, chapter in _chapterize(transcript):
        lines.append(f"### {_fmt_ts(start)} 起")
        lines.extend(f"- [{_fmt_ts(x['ts'])}] {x['speaker']}: {x['text']}" for x in chapter)
        lines.append("")

    actions = _extract_action_items(transcript)
    lines.append("## 明确行动项")
    if actions:
        lines.extend(f"- [ ] [{_fmt_ts(x['ts'])}] {x['speaker']}: {x['text']}" for x in actions)
    else:
        lines.append("（转写中未发现带明确行动语义的句子）")
    return "\n".join(lines)


def digest(session_id: str, use_llm: bool = False) -> Path:
    transcript, clips = _load_session(session_id)
    terms_hit: dict[str, int] = {}
    for c in clips:
        ev = c["trigger"]["evidence"]
        if ev.startswith("热词:"):
            terms_hit[ev[3:]] = terms_hit.get(ev[3:], 0) + 1

    out_dir = config.DATA_DIR / "reports" / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 薄纪要 ----
    prompt = THIN_MINUTES.format(transcript="\n".join(f"[{_fmt_ts(l['ts'])}] {l['speaker']}: {l['text']}" for l in transcript))
    thin = None
    if use_llm:
        thin = _llm_chat(prompt)
    if thin is None:
        thin = _thin_no_llm(transcript, terms_hit) + "\n\n> 〔模板版〕配置 MEETINGVAR_LLM_* 环境变量后可用 LLM 生成叙述版。"
    (out_dir / "minutes.md").write_text(thin, encoding="utf-8")

    # ---- 厚纪要 ----
    blocks = []
    pool = Path(config.POOL_DIR / session_id)
    for clip_file in sorted(pool.glob("*.json")):
        c = json.loads(clip_file.read_text(encoding="utf-8"))
        if "trigger" not in c:  # 跳过 transcript.json / summary.json 等非片段文件
            continue
        tr = "\n".join(f"[{_fmt_ts(l['ts'])}] {l['speaker']}: {l['text']}" for l in c["transcript"]) or "（回溯窗内无转写句）"
        prompt = THICK_REPORT.format(
            trigger_evidence=c["trigger"]["evidence"],
            trigger_layer=c["trigger"]["layer"],
            start_ts=_fmt_ts(c["audio"]["start_ts"]),
            end_ts=_fmt_ts(c["audio"]["end_ts"]),
            transcript=tr,
            screens_desc=f"{len(c['screen']['keyframes'])} 张关键帧" if c["screen"]["keyframes"] else "无",
            rag_context="（由 B1 RAG 注入，见 spikes/b1_rag）",
            user_focus="（由会前 F1 输入）",
        )
        body = _llm_chat(prompt) if use_llm else None
        if body is None:
            body = prompt + "\n\n> 〔模板版·未接 LLM〕以上为已填充上下文的完整 prompt，LLM 接入后此处为模型输出。"
        blocks.append(f"# VAR 报告 · clip {c['clip_id'][:8]}\n\n{body}")

    (out_dir / "var_reports.md").write_text("\n\n---\n\n".join(blocks) or "# 本场无 VAR 片段", encoding="utf-8")
    print(f"[digest] {out_dir/'minutes.md'}\n[digest] {out_dir/'var_reports.md'}")
    return out_dir
