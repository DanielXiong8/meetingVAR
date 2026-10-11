"""离线回放：把任意 wav 喂进完整管线（转写→纠错→监听→转正），不需要真开会。

这是骨架的主测试路径，也是演示的兜底（现场无网/无会议环境时用它）。
"""
from __future__ import annotations

import audioop
import json
import time
import wave
from pathlib import Path

from . import config
from .pipeline import Session


def wav_to_pcm16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        assert sampwidth == 2, f"仅支持 16bit wav，当前 {sampwidth*8}bit"
        raw = w.readframes(w.getnframes())
    mono, _ = audioop.ratecv(raw, 2, channels, rate, config.ASR_RATE, None)
    return mono


def _fmt_hms(sec: float) -> str:
    total = max(0, int(sec))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def replay(
    wav_path: Path,
    session_id: str,
    terms: list[str],
    focus_docs: list[str] | None,
    device: str = "cuda",
    transcript_out: Path | None = None,
    correction_terms: list[str] | None = None,
) -> dict:
    """回放 wav 走全管线。

    transcript_out：显式转写导出路径（transcribe 模式）。默认 None——
    开会/回放的零落盘承诺不变（未命中连草稿转写都不落盘）；
    用户点名要转写文件时才写这一份，且不影响片段池的 G1a/G1b/G1c 纪律。
    """
    pcm = wav_to_pcm16k(wav_path)
    duration = len(pcm) / 2 / config.ASR_RATE
    print(f"[replay] {wav_path} | {duration:.0f}s @16k mono")

    base_ts = time.time() - duration  # 让"命中回溯"语义成立
    session = Session(session_id, terms=terms, focus_docs=focus_docs, device=device, echo=True,
                      correction_terms=correction_terms)

    step = 5.0  # 5 秒一喂，喂完立即尝试增量转写
    offset = 0.0
    while offset < duration:
        chunk = pcm[int(offset * 2 * config.ASR_RATE) : int((offset + step) * 2 * config.ASR_RATE)]
        session.ingest_pcm(chunk, base_ts + offset)
        offset += step
    summary = session.finish()
    print(f"[done] 转写 {summary['lines']} 句 | 片段 {summary['clips']} 个 | 纠错 {len(summary['corrections'])} 处")
    if summary["corrections"]:
        print("[corrections]", summary["corrections"][:10])

    if transcript_out is not None:
        lines = session.relative_transcript()
        text = "\n".join(f"[{_fmt_hms(l['ts'])}] {l['speaker']}: {l['text']}" for l in lines)
        transcript_out.parent.mkdir(parents=True, exist_ok=True)
        transcript_out.write_text(text + ("\n" if text else ""), encoding="utf-8")
        print(f"[transcribe] 全场转写已显式保存: {transcript_out}（{len(lines)} 句）")
        if session.rewrite_log:
            # 改写与恢复记录随显式导出落盘（显式转写附带的可追溯性清单：原文快照+逐条记录）
            rw = transcript_out.with_name(transcript_out.stem + ".rewrites.json")
            rw.write_text(
                json.dumps(session.rewrite_log, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[transcribe] 改写记录（含原文快照）: {rw}（{len(session.rewrite_log)} 行）")
    return summary
