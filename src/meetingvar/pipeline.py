"""会话编排器：音频进 → 增量转写 → 术语纠错 → 三层监听 → 命中转正 → 片段池。

live（AudioCapture 喂流）与 replay（wav 回放喂流）共用同一套逻辑。
时间轴：内部统一用绝对时间戳（live=真实时钟；replay=模拟基点+偏移），
写入片段池和 transcript.json 时转换为会议内相对秒数，遵守 pool_schema 契约。
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from . import config
from .monitor.hotwords import HotwordMonitor, InterestScorer, ManualVAR
from .pool.store import Pool
from .transcribe.asr import ASR
from .transcribe.correct import correct

LOOKBACK_SECONDS = 120  # 命中回溯窗：命中时刻往前取多少秒


class Session:
    def __init__(
        self,
        session_id: str,
        terms: list[str],
        focus_docs: list[str] | None = None,
        device: str = "cuda",
        screen_capture=None,
        echo: bool = True,
    ):
        self.session_id = session_id
        self.terms = terms
        self.pool = Pool(session_id)
        self.asr = ASR(device)
        self.monitor = HotwordMonitor(terms)
        self.scorer = InterestScorer(focus_docs) if focus_docs else None
        self.manual = ManualVAR()
        self.screen_capture = screen_capture
        self.echo = echo
        self.hd = None  # 高清补录层（--hd 时由 cli 装配）

        self._ring: deque[tuple[float, bytes]] = deque(maxlen=config.RING_SECONDS * 10)
        self._pending: list[tuple[float, bytes]] = []
        self._last_flush_ts: float | None = None
        self._timeline_start_ts: float | None = None
        self.transcript: list[dict] = []
        self.clips: list[dict] = []
        self.corrections: list[tuple[str, str]] = []

    # ---- 音频进 ----
    def ingest_pcm(self, pcm: bytes, start_ts: float) -> None:
        if self._timeline_start_ts is None:
            self._timeline_start_ts = start_ts
        step = len(pcm) / 2 / config.ASR_RATE
        for i in range(0, len(pcm), config.ASR_RATE):  # 1s 一格，ring 粒度
            self._ring.append((start_ts + i / config.ASR_RATE, pcm[i : i + config.ASR_RATE]))
        self._pending.append((start_ts, pcm))
        self._last_flush_ts = start_ts + step
        span = self._last_flush_ts - self._pending[0][0]
        if span >= config.CHUNK_SECONDS:
            self._flush()

    # ---- 转写 + 监听 ----
    def _flush(self, force: bool = False) -> None:
        if not self._pending:
            return
        start_ts = self._pending[0][0]
        pcm = b"".join(b for _, b in self._pending)
        span = len(pcm) / 2 / config.ASR_RATE
        if not force and span < config.CHUNK_SECONDS:
            return
        keep = self._pending[-1] if not force else None
        self._pending = [keep] if keep else []

        segs = self.asr.transcribe_pcm16k(pcm)
        new_lines = []
        for seg in segs:
            fixed, fixes = correct(seg["text"], self.terms)
            self.corrections.extend(fixes)
            line = {"ts": start_ts + seg["start"], "speaker": seg["speaker"], "text": fixed}
            new_lines.append(line)
            self.transcript.append(line)
        if self.echo and new_lines:
            last = new_lines[-1]
            print(f"  [{time.strftime('%H:%M:%S', time.localtime(last['ts']))}] {last['speaker']}: {last['text'][:80]}")

        joined = " ".join(l["text"] for l in new_lines)
        # 触发时刻用会议时间轴（最后进音频的绝对时间戳），不用墙钟——
        # replay 模式下墙钟与模拟时间轴漂移，会造成 clip.at 失真（见 PR #13 review）
        trigger_ts = self._last_flush_ts if self._last_flush_ts is not None else time.time()
        for term in self.monitor.check(joined, trigger_ts):
            self._make_clip("L1", f"热词:{term}", trigger_ts)
        if self.scorer:
            hit, score = self.scorer.check(joined)
            if hit:
                self._make_clip("L2", f"interest_score={score:.2f}", trigger_ts)
        if self.manual.consume():
            self._make_clip("L3", "manual_hotkey", trigger_ts)

    def trigger_manual(self) -> str | None:
        """外部（回车/程序）手动 VAR。"""
        ts = self._last_flush_ts if self._last_flush_ts is not None else time.time()
        self._make_clip("L3", "manual_hotkey", ts)
        return self.clips[-1]["clip_id"] if self.clips else None

    # ---- 命中转正 ----
    def _relative_ts(self, ts: float) -> float:
        """把内部绝对时间戳转换为会议内相对秒数。"""
        base = self._timeline_start_ts if self._timeline_start_ts is not None else ts
        return max(0.0, ts - base)

    def _make_clip(self, layer: str, evidence: str, trigger_ts: float) -> str:
        if self.hd is not None and not self.hd.is_recording():
            self.hd.start()  # 高清层：命中后 90s 的 30fps 补录
        start_ts = trigger_ts - LOOKBACK_SECONDS
        pcm = b"".join(b for t, b in list(self._ring) if start_ts <= t <= trigger_ts)
        lines = [l for l in self.transcript if start_ts <= l["ts"] <= trigger_ts]
        clip_lines = [{**line, "ts": self._relative_ts(line["ts"])} for line in lines]
        # 截屏 manifest 与 JSON 时间语义保持一致：一律会议相对秒
        screens = [
            (self._relative_ts(t), jpg)
            for t, jpg in (self.screen_capture.frames_since(start_ts) if self.screen_capture else [])
        ]
        from .capture.audio import pcm_to_wav_bytes

        clip_id = self.pool.add_clip(
            trigger_layer=layer,
            evidence=evidence,
            audio_wav=pcm_to_wav_bytes(pcm),
            transcript_lines=clip_lines,
            screens=screens,
        )
        self.clips.append(
            {"clip_id": clip_id, "layer": layer, "evidence": evidence, "at": self._relative_ts(trigger_ts)}
        )
        if self.echo:
            print(f"  >>> VAR! [{layer}] {evidence} → clip {clip_id[:8]}（回溯{LOOKBACK_SECONDS}s，{len(lines)}句）")
        return clip_id

    # ---- 收尾 ----
    def finish(self) -> dict:
        self._flush(force=True)
        out = config.POOL_DIR / self.session_id / "transcript.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        transcript = [{**line, "ts": self._relative_ts(line["ts"])} for line in self.transcript]
        out.write_text(
            json.dumps({"session_id": self.session_id, "transcript": transcript, "clips": self.clips}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if self.hd is not None:
            self.hd.stop()
        hd_dir = str(self.hd.session_dir) if self.hd is not None and self.hd.session_dir else None
        summary = {
            "session_id": self.session_id,
            "lines": len(transcript),
            "clips": len(self.clips),
            "corrections": self.corrections,
            "transcript_json": str(out),
            "hd_dir": hd_dir,
        }
        (out.parent / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
