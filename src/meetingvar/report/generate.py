"""会后 digest：薄纪要（无 LLM 结构化版）+ 厚纪要（prompt 填充，LLM 可选）。

LLM 通过环境变量 MEETINGVAR_LLM_BASE_URL / _API_KEY / _MODEL 接入
（OpenAI 兼容 /chat/completions）；未配置则输出〔模板版〕并如实标注。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from .. import config
from .prompts import THICK_REPORT, THIN_MINUTES


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _load_session(session_id: str) -> tuple[list[dict], list[dict]]:
    base = config.POOL_DIR / session_id
    t = json.loads((base / "transcript.json").read_text(encoding="utf-8"))
    return t["transcript"], t["clips"]


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
    """无 LLM 的薄纪要：按 5 分钟窗归组转写 + 命中统计（结构化、零编造）。"""
    lines = ["# 薄纪要（结构化版·未接 LLM）", ""]
    if not transcript:
        lines.append("（本场无转写内容）")
        return "\n".join(lines)
    lines.append(f"- 转写句数：{len(transcript)}；说话人：{sorted({l['speaker'] for l in transcript})}")
    lines.append(f"- 监听词命中：{terms_hit or '无'}")
    lines.append("")
    window, start = 300.0, transcript[0]["ts"]
    cur: list[dict] = []
    for l in transcript:
        if l["ts"] - start >= window:
            if cur:
                lines.append(f"## {_fmt_ts(cur[0]['ts'])} 起的片段")
                lines.extend(f"- [{_fmt_ts(x['ts'])}] {x['speaker']}: {x['text']}" for x in cur)
                lines.append("")
            cur = []
        cur.append(l)
    if cur:
        lines.append(f"## {_fmt_ts(cur[0]['ts'])} 起的片段")
        lines.extend(f"- [{_fmt_ts(x['ts'])}] {x['speaker']}: {x['text']}" for x in cur)
    return "\n".join(lines)


def digest(session_id: str, use_llm: bool = False) -> Path:
    transcript, clips = _load_session(session_id)
    terms_hit: dict[str, int] = {}
    # transcript.json 的 clips 条目是 {clip_id, layer, evidence, at}（无 trigger 键）；
    # 片段池 JSON 是完整 Clip（有 trigger.evidence）。两种形态都兼容。
    for c in clips:
        ev = c.get("evidence") or c.get("trigger", {}).get("evidence", "")
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
