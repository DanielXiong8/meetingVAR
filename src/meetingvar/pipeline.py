"""会话编排器：音频进 → 增量转写 → 术语纠错 → 三层监听 → 命中转正 → 片段池。

live（AudioCapture 喂流）与 replay（wav 回放喂流）共用同一套逻辑。
时间轴：内部统一用绝对时间戳（live=真实时钟；replay=模拟基点+偏移），
写入片段池和 transcript.json 时转换为会议内相对秒数，遵守 pool_schema 契约。
"""
from __future__ import annotations

import hashlib
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
_PCM_BYTES_PER_SECOND = config.ASR_RATE * 2  # 接口约定：16bit 单声道 PCM，1 秒 = 32000 字节


class Session:
    def __init__(
        self,
        session_id: str,
        terms: list[str],
        focus_docs: list[str] | None = None,
        device: str = "cuda",
        screen_capture=None,
        echo: bool = True,
        correction_terms: list[str] | None = None,
    ):
        self.session_id = session_id
        self.terms = terms  # 监听词表（宽进：漏斗全量治理后的 watch 集）
        # 纠错词表（严进：改写转写文本的资格）。缺省回落监听表——手动 --terms 场景
        # 用户给的就是要纠错的词；漏斗自动词必须经 govern 治理后单独传入。
        self.correction_terms = correction_terms if correction_terms is not None else list(terms)
        self.pool = Pool(session_id)
        self.asr = ASR(device)
        self.monitor = HotwordMonitor(terms)
        self.scorer = InterestScorer(focus_docs) if focus_docs else None
        self.manual = ManualVAR()
        self.screen_capture = screen_capture
        self.echo = echo
        self.hd = None  # 高清补录层（--hd 时由 cli 装配）

        # 环形缓冲：固定 1 秒一格，保留 RING_SECONDS 秒（≈10min，需 ≥ LOOKBACK_SECONDS）
        self._ring: deque[tuple[float, bytes]] = deque(maxlen=config.RING_SECONDS)
        self._ring_carry = b""  # 不足 1 秒的尾巴：攒够一格再入环，保证格大小恒定
        self._ring_carry_ts: float | None = None
        self._pending: list[tuple[float, bytes]] = []
        self._last_flush_ts: float | None = None
        self._timeline_start_ts: float | None = None
        self.transcript: list[dict] = []
        self.clips: list[dict] = []
        # 每条纠错记录 = {src,dst,status,reason,rule_id,ts,termset_id}（correct() 产出、此处补 ts/termset_id）
        self.corrections: list[dict] = []
        # 整行原文快照（F1 恢复机制）：纠错发生过的行，逐行记录改写前后全文，
        # 恢复 = 把 text 换回 orig_text（精确），不依赖片段逆替换。局限：行级粒度、随会话内存态。
        self.rewrite_log: list[dict] = []
        # 纠错词表版本标识 = 纠错词表排序去重后的 sha256 前 12 位（内容寻址，可复算）
        joined_terms = "\n".join(sorted(set(self.correction_terms)))
        self.termset_id = (
            hashlib.sha256(joined_terms.encode("utf-8")).hexdigest()[:12] if self.correction_terms else ""
        )

    # ---- 音频进 ----
    def ingest_pcm(self, pcm: bytes, start_ts: float) -> None:
        if self._timeline_start_ts is None:
            self._timeline_start_ts = start_ts
        step = len(pcm) / _PCM_BYTES_PER_SECOND
        self._ring_feed(pcm, start_ts)
        self._pending.append((start_ts, pcm))
        self._last_flush_ts = start_ts + step
        span = self._last_flush_ts - self._pending[0][0]
        if span >= config.CHUNK_SECONDS:
            self._flush()

    def _ring_feed(self, pcm: bytes, start_ts: float) -> None:
        """按固定 1 秒一格进环形缓冲：格时间戳间隔 1.0s ⇔ 格内音频 32000 字节。

        - 上一调用残留的 carry 与本次数据拼接后再切格，格大小与 ingest 块大小无关；
        - 时间轴出现跳变（采集丢帧）时以本次 start_ts 重新锚定，避免时间戳长期漂移。
        """
        data = self._ring_carry + pcm
        if self._ring_carry and self._ring_carry_ts is not None:
            expected = self._ring_carry_ts + len(self._ring_carry) / _PCM_BYTES_PER_SECOND
            ts0 = start_ts if abs(start_ts - expected) > 0.05 else self._ring_carry_ts
        else:
            ts0 = start_ts
        n = len(data) // _PCM_BYTES_PER_SECOND
        for i in range(n):
            lo = i * _PCM_BYTES_PER_SECOND
            self._ring.append((ts0 + i, data[lo : lo + _PCM_BYTES_PER_SECOND]))
        self._ring_carry = data[n * _PCM_BYTES_PER_SECOND :]
        self._ring_carry_ts = ts0 + n if self._ring_carry else None

    # ---- 转写 + 监听 ----
    def _flush(self, force: bool = False) -> None:
        if not self._pending:
            return
        start_ts = self._pending[0][0]
        pcm = b"".join(b for _, b in self._pending)
        span = len(pcm) / 2 / config.ASR_RATE
        if not force and span < config.CHUNK_SECONDS:
            return
        # 滑窗重叠：保留 CHUNK_OVERLAP_SECONDS 尾巴进下一块，防截句
        if not force:
            tail = []
            t_end = self._pending[-1][0] + len(self._pending[-1][1]) / 2 / config.ASR_RATE
            for item in reversed(self._pending):
                if t_end - item[0] <= config.CHUNK_OVERLAP_SECONDS:
                    tail.insert(0, item)
                else:
                    break
            self._pending = tail
        else:
            self._pending = []

        segs = self.asr.transcribe_pcm16k(pcm)
        new_lines = []
        for seg in segs:
            if len(seg["text"].strip()) < config.MIN_TEXT_CHARS:
                # 噪声碎片过滤（阈值可调，见 config.MIN_TEXT_CHARS）：
                # 该过滤同时影响触发判断与证据/报告——边界语义固化在
                # tests/test_short_sentence_filter.py
                continue
            orig_text = seg["text"]
            fixed, fixes = correct(orig_text, self.correction_terms)
            if fixes:
                line_ts = start_ts + seg["start"]
                for r in fixes:
                    r["ts"] = line_ts
                    r["termset_id"] = self.termset_id
                self.corrections.extend(fixes)
                self.rewrite_log.append({
                    "ts": line_ts, "orig_text": orig_text, "text": fixed,
                    "termset_id": self.termset_id, "records": fixes,
                })
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
        # 同一转写块内命中的多个词合并为一次回看（evidence 记录全部命中词）：
        # 各存一份会把同一个 300s 窗口重复保存 N 次——大词表下直接撑爆磁盘（test1 实测 752 片段）
        fired = self.monitor.check(joined, trigger_ts)
        if fired:
            self._make_clip("L1", "热词:" + "+".join(fired), trigger_ts)
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

    def relative_transcript(self) -> list[dict]:
        """全场转写的会议相对秒视图（内存态，供 transcribe 显式导出；默认模式不落盘）。"""
        return [{**line, "ts": self._relative_ts(line["ts"])} for line in self.transcript]

    def _make_clip(self, layer: str, evidence: str, trigger_ts: float) -> str:
        if self.hd is not None and not self.hd.is_recording():
            self.hd.start()  # 高清层：命中后 ≤90s 预算内高帧率补录（帧缩至 1280 宽）
        start_ts = trigger_ts - LOOKBACK_SECONDS
        chunks = [(t, b) for t, b in self._ring if start_ts <= t <= trigger_ts]
        # 环里不足 1s 的尾巴（carry）也要进片段：否则命中前最后一秒的音频可能整体丢失
        if (
            self._ring_carry
            and self._ring_carry_ts is not None
            and start_ts <= self._ring_carry_ts <= trigger_ts
        ):
            chunks.append((self._ring_carry_ts, self._ring_carry))
        pcm = b"".join(b for _, b in chunks)
        lines = [l for l in self.transcript if start_ts <= l["ts"] <= trigger_ts]
        clip_lines = [{**line, "ts": self._relative_ts(line["ts"])} for line in lines]
        # 截屏 manifest 与 JSON 时间语义保持一致：一律会议相对秒
        screens = [
            (self._relative_ts(t), jpg)
            for t, jpg in (self.screen_capture.frames_since(start_ts) if self.screen_capture else [])
        ]
        from .pool.store import pcm_to_wav_bytes

        clip_id = self.pool.add_clip(
            trigger_layer=layer,
            evidence=evidence,
            audio_wav=pcm_to_wav_bytes(pcm),
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
        # 零落盘纪律（审计 G1a/G1b）：未命中（无片段）的会话不创建任何文件/目录，
        # 草稿转写随进程内存一起消失——"没事故就什么都不落盘"（README 设计红线 2）。
        # 命中会话（G1c）才落盘：片段 + 全场转写文本（薄纪要所需）+ summary。
        if not self.clips:
            return {
                "session_id": self.session_id,
                "lines": len(self.transcript),
                "clips": 0,
                "corrections": self.corrections,
                "transcript_json": None,
                "hd_dir": None,
            }
        out = config.POOL_DIR / self.session_id / "transcript.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        transcript = [{**line, "ts": self._relative_ts(line["ts"])} for line in self.transcript]
        out.write_text(
            json.dumps(
                {"session_id": self.session_id, "transcript": transcript, "clips": self.clips,
                 "rewrites": self.rewrite_log},
                ensure_ascii=False, indent=2,
            ),
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
            "rewrite_count": len(self.rewrite_log),
            "transcript_json": str(out),
            "hd_dir": hd_dir,
        }
        (out.parent / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
