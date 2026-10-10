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

LOOKBACK_SECONDS = config.LOOKBACK_SECONDS  # 命中回溯窗（config 可调，env MEETINGVAR_LOOKBACK）


class Session:
    def __init__(
        self,
        session_id: str,
        terms: list[str],
        focus_docs: list[str] | None = None,
        device: str = "cuda",
        screen_capture=None,
        echo: bool = True,
        l2_threshold: float | None = None,
    ):
        self.session_id = session_id
        self.terms = terms
        self.pool = Pool(session_id)
        self.asr = ASR(device)
        self.monitor = HotwordMonitor(terms)
        self.scorer = InterestScorer(focus_docs, threshold=l2_threshold) if focus_docs else None
        self.manual = ManualVAR()
        self.screen_capture = screen_capture
        self.echo = echo
        self.hd = None  # 高清补录层（--hd 时由 cli 装配）

        self._ring: deque[tuple[float, bytes]] = deque()
        self._pending: list[tuple[float, bytes]] = []
        self._last_flush_ts: float | None = None
        self._timeline_start_ts: float | None = None
        self._processed_until: float | None = None
        self.transcript: list[dict] = []
        self.clips: list[dict] = []
        self.corrections: list[tuple[str, str]] = []

    # ---- 音频进 ----
    def ingest_pcm(self, pcm: bytes, start_ts: float) -> None:
        if not pcm:
            return
        if len(pcm) % 2:
            raise ValueError("16-bit PCM 必须包含完整采样点")
        if self._timeline_start_ts is None:
            self._timeline_start_ts = start_ts
        step = len(pcm) / 2 / config.ASR_RATE
        bytes_per_second = 2 * config.ASR_RATE
        for i in range(0, len(pcm), bytes_per_second):
            self._ring.append((start_ts + i / bytes_per_second, pcm[i : i + bytes_per_second]))
        cutoff = start_ts + step - config.RING_SECONDS
        while self._ring and self._ring[0][0] + len(self._ring[0][1]) / bytes_per_second <= cutoff:
            self._ring.popleft()
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
        end_ts = start_ts + span
        if self._processed_until is not None and end_ts <= self._processed_until:
            self._pending = []
            return
        if not force and span < config.CHUNK_SECONDS:
            return
        # 滑窗重叠：保留 CHUNK_OVERLAP_SECONDS 尾巴进下一块，防截句
        if not force:
            tail_size = min(len(pcm), int(config.CHUNK_OVERLAP_SECONDS * 2 * config.ASR_RATE))
            self._pending = [(end_ts - tail_size / 2 / config.ASR_RATE, pcm[-tail_size:])] if tail_size else []
        else:
            self._pending = []

        segs = self.asr.transcribe_pcm16k(pcm)
        new_lines = []
        for seg in segs:
            if len(seg["text"].strip()) < 2:
                continue  # 噪声碎片（单字/空转写）不进证据流水
            fixed, fixes = correct(seg["text"], self.terms)
            self.corrections.extend(fixes)
            line = {"ts": start_ts + seg["start"], "speaker": seg["speaker"], "text": fixed}
            # Overlapping ASR windows can repeat a sentence. Do not duplicate
            # that sentence (or its triggers); later occurrences remain valid.
            if any(abs(old["ts"] - line["ts"]) <= 0.5 and old["text"] == fixed
                   for old in reversed(self.transcript)
                   if old["ts"] >= start_ts - 0.5):
                continue
            new_lines.append(line)
            self.transcript.append(line)
        self.transcript.sort(key=lambda line: line["ts"])
        self._processed_until = end_ts
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
        pieces = []
        media_start = None
        media_end = trigger_ts
        for t, b in self._ring:
            lo = max(0, round((start_ts - t) * config.ASR_RATE))
            hi = min(len(b) // 2, round((trigger_ts - t) * config.ASR_RATE))
            if hi > lo:
                if media_start is None:
                    media_start = t + lo / config.ASR_RATE
                media_end = t + hi / config.ASR_RATE
                pieces.append(b[lo * 2:hi * 2])
        pcm = b"".join(pieces)
        lines = [l for l in self.transcript if start_ts <= l["ts"] <= trigger_ts]
        clip_lines = [{**line, "ts": self._relative_ts(line["ts"])} for line in lines]
        # 截屏 manifest 与 JSON 时间语义保持一致：一律会议相对秒
        screens = [
            (self._relative_ts(t), jpg)
            for t, jpg in (self.screen_capture.frames_since(start_ts) if self.screen_capture else [])
            if t <= trigger_ts
        ]
        from .capture.audio import pcm_to_wav_bytes

        clip_id = self.pool.add_clip(
            trigger_layer=layer,
            evidence=evidence,
            audio_wav=pcm_to_wav_bytes(pcm),
            audio_start_ts=self._relative_ts(media_start if media_start is not None else trigger_ts),
            audio_end_ts=self._relative_ts(media_end),
            transcript_lines=clip_lines,
            screens=screens,
            hd_dir=str(self.hd.session_dir) if self.hd is not None and self.hd.session_dir else None,
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
        # 零落盘纪律（审计 G1）：没有任何转写与片段时不创建任何文件
        if not self.transcript and not self.clips:
            return {
                "session_id": self.session_id, "lines": 0, "clips": 0,
                "corrections": [], "transcript_json": None,
            }
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
