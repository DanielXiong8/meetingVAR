"""离线回放：把任意 wav 喂进完整管线（转写→纠错→监听→转正），不需要真开会。

这是骨架的主测试路径，也是演示的兜底（现场无网/无会议环境时用它）。
"""
from __future__ import annotations

import audioop
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


def replay(wav_path: Path, session_id: str, terms: list[str], focus_docs: list[str] | None, device: str = "cuda") -> dict:
    pcm = wav_to_pcm16k(wav_path)
    duration = len(pcm) / 2 / config.ASR_RATE
    print(f"[replay] {wav_path} | {duration:.0f}s @16k mono")

    base_ts = time.time() - duration  # 让"命中回溯"语义成立
    session = Session(session_id, terms=terms, focus_docs=focus_docs, device=device, echo=True)

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
    return summary
