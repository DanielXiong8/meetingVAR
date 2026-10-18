"""会话编排器：音频进 → 增量转写 → 术语纠错 → 三层监听 → 命中转正 → 片段池。

live（AudioCapture 喂流）与 replay（wav 回放喂流）共用同一套逻辑。
时间轴：内部统一用绝对时间戳（live=真实时钟；replay=模拟基点+偏移），
写入片段池和 transcript.json 时转换为会议内相对秒数，遵守 pool_schema 契约。
说话人：句级 CAM++ 声纹经会话级在线聚类（transcribe/speaker.py）映射为
全场稳定的 spk0/spk1/…；无声纹时退回块内编号（FunASR 局部聚类，仅兜底）。
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from pathlib import Path

from . import config
from .events import emit
from .monitor.hotwords import HotwordMonitor, InterestScorer, ManualVAR
from .pool.store import Pool
from .transcribe.asr import ASR
from .transcribe.correct import correct
from .transcribe.speaker import OnlineSpeakerCluster

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
        l2_threshold: float | None = None,
        speaker_book=None,
        asr_engine: str = "local",
        xfyun_cred: str | None = None,
        auto_triggers: bool = True,
    ):
        self.session_id = session_id
        self.terms = terms  # 监听词表（宽进：漏斗全量治理后的 watch 集）
        # 纠错词表（严进：改写转写文本的资格）。缺省回落监听表——手动 --terms 场景
        # 用户给的就是要纠错的词；漏斗自动词必须经 govern 治理后单独传入。
        self.correction_terms = correction_terms if correction_terms is not None else list(terms)
        self.pool = Pool(session_id)
        self.auto_triggers = auto_triggers
        self.streaming = asr_engine == "xfyun_rtasr"
        self._final_ids: set[str] = set()
        self.interest_turns: list[dict] = []
        if self.streaming:
            self.asr = None
        elif asr_engine == "xfyun":
            # 云端识别：一句话一条 DB，无说话人声纹（with_embeddings=False），
            # 聚类降级为"整场 spk?"——UI 侧已就该限制提示用户。
            from .transcribe.xfyun_iat import XfyunASR
            if not xfyun_cred:
                raise ValueError("asr_engine=xfyun 需要提供 --xfyun-cred 凭证文件")
            self.asr = XfyunASR(xfyun_cred)
        else:
            self.asr = ASR(device)
        self.monitor = HotwordMonitor(terms)
        self.scorer = InterestScorer(focus_docs, threshold=l2_threshold) if focus_docs and auto_triggers else None
        self.manual = ManualVAR()
        # 会话级说话人聚类：跨块维持声纹质心 → 全场稳定的 spk0/spk1/…
        self.speaker_cluster = OnlineSpeakerCluster(threshold=config.SPEAKER_SIM_THRESHOLD)
        # 说话人点名：新说话人先与声纹名册比对（认得出自动命名），认不出排入点名队列，
        # 用户在终端输入名字后追溯改名本场转写与已存片段，并更新名册（跨会话）。
        self.speaker_book = speaker_book
        self.speaker_names: dict[str, str] = {}
        self._questions: deque[dict] = deque()
        self._question_current: dict | None = None
        self.screen_capture = screen_capture
        self.echo = echo
        self.hd = None  # 高清补录层（--hd 时由 cli 装配）

        # 连续音频按 1s 分格；按时间淘汰，断流边界的短格不缩短保留时长。
        self._ring: deque[tuple[float, bytes]] = deque()
        self._ring_carry = b""  # 不足 1 秒的尾巴：攒够一格再入环，保证格大小恒定
        self._ring_carry_ts: float | None = None
        self._pending: list[tuple[float, bytes]] = []
        self._last_flush_ts: float | None = None
        self._timeline_start_ts: float | None = None
        self._processed_until: float | None = None
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
        if not pcm:
            return
        if len(pcm) % 2:
            raise ValueError("16-bit PCM 必须包含完整采样点")
        if self._timeline_start_ts is None:
            self._timeline_start_ts = start_ts
        step = len(pcm) / _PCM_BYTES_PER_SECOND
        if self._last_flush_ts is not None:
            gap = start_ts - self._last_flush_ts
            if gap < -0.5 / config.ASR_RATE:
                raise ValueError("音频时间戳不得重叠或倒退")
            if gap > 0.5 / config.ASR_RATE:
                self._flush(force=True)  # 断流两侧不能拼成连续的 ASR 输入
        self._ring_feed(pcm, start_ts)
        cutoff = start_ts + step - config.RING_SECONDS
        while self._ring and self._ring[0][0] + len(self._ring[0][1]) / _PCM_BYTES_PER_SECOND <= cutoff:
            self._ring.popleft()
        self._last_flush_ts = start_ts + step
        if self.streaming:
            return
        self._pending.append((start_ts, pcm))
        span = self._last_flush_ts - self._pending[0][0]
        if span >= config.CHUNK_SECONDS:
            self._flush()

    def _ring_feed(self, pcm: bytes, start_ts: float) -> None:
        """按固定 1 秒一格进环形缓冲：格时间戳间隔 1.0s ⇔ 格内音频 32000 字节。

        - 上一调用残留的 carry 与本次数据拼接后再切格，格大小与 ingest 块大小无关；
        - 时间轴出现跳变（采集丢帧）时以本次 start_ts 重新锚定，避免时间戳长期漂移。
        """
        if self._ring_carry and self._ring_carry_ts is not None:
            expected = self._ring_carry_ts + len(self._ring_carry) / _PCM_BYTES_PER_SECOND
            if abs(start_ts - expected) > 0.5 / config.ASR_RATE:
                self._ring.append((self._ring_carry_ts, self._ring_carry))
                self._ring_carry = b""
                ts0 = start_ts
            else:
                ts0 = self._ring_carry_ts
        else:
            ts0 = start_ts
        data = self._ring_carry + pcm
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
        end_ts = start_ts + span
        if self._processed_until is not None and end_ts <= self._processed_until:
            self._pending = []
            return
        if not force and span < config.CHUNK_SECONDS:
            return
        # 滑窗重叠：保留 CHUNK_OVERLAP_SECONDS 尾巴进下一块，防截句。
        # 但重叠块会重复上一句内容，全靠下面"同时间戳同文本"去重挡掉——该去重
        # 依赖句级时间戳（同一句话在相邻块里落在同一绝对时刻）。云引擎（讯飞 IAT）
        # 不给句级时间戳，去重必然失手 → 对它直接关掉重叠（实网实测的病根）。
        if not force and getattr(self.asr, "sentence_timestamps", True):
            tail_size = min(len(pcm), int(config.CHUNK_OVERLAP_SECONDS * 2 * config.ASR_RATE))
            self._pending = [(end_ts - tail_size / 2 / config.ASR_RATE, pcm[-tail_size:])] if tail_size else []
        else:
            self._pending = []

        asr_started = time.monotonic()
        if self.echo:
            emit("asr_state", processing=True, seconds=round(span, 2))
        try:
            segs = self.asr.transcribe_pcm16k(pcm)
        except Exception:
            if self.echo:
                emit("asr_state", processing=False, error="语音识别失败，请检查引擎日志")
            raise
        if self.echo:
            emit("asr_state", processing=False, latency=round(time.monotonic() - asr_started, 2),
                 lag=max(0, round(time.time() - end_ts, 2)))
        self._processed_until = end_ts
        self.accept_segments(segs, start_ts)

    def accept_segments(self, segs: list[dict], start_ts: float) -> None:
        """Accept finalized ASR segments; streaming partials never enter this path."""
        new_lines = []
        for seg in segs:
            event_id = seg.get("id")
            if event_id and event_id in self._final_ids:
                continue
            if len(seg["text"].strip()) < config.MIN_TEXT_CHARS:
                # 噪声碎片过滤（阈值可调，见 config.MIN_TEXT_CHARS）：
                # 该过滤同时影响触发判断与证据/报告——边界语义固化在
                # tests/test_short_sentence_filter.py
                continue
            orig_text = seg["text"]
            fixed, fixes = correct(orig_text, self.correction_terms)
            line_ts = start_ts + seg["start"]
            # Overlapping ASR windows can repeat a sentence. Do not duplicate
            # that sentence (or its triggers); later occurrences remain valid.
            if any(abs(old["ts"] - line_ts) <= 0.5 and old["text"] == fixed
                   for old in reversed(self.transcript)
                   if old["ts"] >= start_ts - 0.5):
                continue
            # 说话人：有句级声纹（emb）走会话级聚类——跨块全局编号 spk0/spk1/…；
            # 声纹模式开启但该句太短（<0.25s 不给声纹下结论）→ 标 spk?（宁标未知，
            # 不得退回块内局部编号——那正是"全场 spk0"的病根）；
            # 声纹整体未启用（降级模式）→ 沿用块内编号。
            # 分配放在去重之后：被去重丢弃的句子不参与声纹质心更新。
            emb = seg.get("emb")
            if emb is not None:
                n_before = self.speaker_cluster.n_speakers
                speaker = self.speaker_cluster.assign(emb, float(seg["end"]) - float(seg["start"]))
                if self.speaker_cluster.n_speakers > n_before:
                    self._on_new_speaker(speaker, fixed, line_ts)
                speaker = self.speaker_names.get(speaker, speaker)  # 已点名的直接显示名字
            elif getattr(self.asr, "with_embeddings", False):
                speaker = "spk?"
            else:
                speaker = seg.get("speaker", "spk?")
            line = {"ts": line_ts, "speaker": speaker, "text": fixed}
            # 改写记录在去重检查之后：被去重丢弃的行不得进纠错清单/改写日志
            if fixes:
                for r in fixes:
                    r["ts"] = line_ts
                    r["termset_id"] = self.termset_id
                self.corrections.extend(fixes)
                self.rewrite_log.append({
                    "ts": line_ts, "orig_text": orig_text, "text": fixed,
                    "termset_id": self.termset_id, "records": fixes,
                })
            new_lines.append(line)
            self.transcript.append(line)
            if event_id:
                self._final_ids.add(event_id)
            self.interest_turns.append({"id": event_id or f"turn_{len(self.interest_turns) + 1}",
                                       "ts": line_ts, "end_ts": start_ts + float(seg.get("end", seg["start"])),
                                       "text": orig_text, "corrected_text": fixed})
        self.transcript.sort(key=lambda line: line["ts"])
        if self.echo:
            for line in new_lines:
                clock = time.strftime('%H:%M:%S', time.localtime(line['ts']))
                if not emit("line", clock=clock, spk=line["speaker"], text=line["text"],
                            ts=self._relative_ts(line["ts"])):
                    print(f"  [{clock}] {line['speaker']}: {line['text']}", flush=True)

        joined = " ".join(l["text"] for l in new_lines)
        # 触发时刻用会议时间轴（最后进音频的绝对时间戳），不用墙钟——
        # replay 模式下墙钟与模拟时间轴漂移，会造成 clip.at 失真（见 PR #13 review）
        trigger_ts = self._last_flush_ts if self._last_flush_ts is not None else time.time()
        # 同一转写块内命中的多个词合并为一次回看（evidence 记录全部命中词）：
        # 各存一份会把同一个 300s 窗口重复保存 N 次——大词表下直接撑爆磁盘（test1 实测 752 片段）
        fired = self.monitor.check(joined, trigger_ts) if self.auto_triggers else []
        if fired:
            self._make_clip("L1", "热词:" + "+".join(fired), trigger_ts)
        if self.auto_triggers and self.scorer:
            hit, score = self.scorer.check(joined)
            if hit:
                self._make_clip("L2", f"interest_score={score:.2f}", trigger_ts)
        if self.manual.consume():
            self._make_clip("L3", "manual_hotkey", trigger_ts)

    def capture_interest(self, event: dict) -> str | None:
        known = {turn["id"]: turn for turn in self.interest_turns}
        ids = event.get("evidence_ids") or []
        if not ids or any(key not in known for key in ids):
            return None
        evidence = [known[key] for key in ids]
        end = max(turn["end_ts"] for turn in evidence)
        start = min(turn["ts"] for turn in evidence)
        chunks = list(self._ring)
        if self._ring_carry and self._ring_carry_ts is not None:
            chunks.append((self._ring_carry_ts, self._ring_carry))
        if not chunks or start < chunks[0][0] or self._last_flush_ts is None or end > self._last_flush_ts + 0.1:
            emit("interest_state", state="error", message="API返回时证据已不在音频缓冲内，本次未保存")
            return None
        reason = f"API兴趣/{event['event_type']}: {event['reason']}"
        quotes = "；".join(f"{turn['id']}: {turn['text']}" for turn in evidence)
        return self._make_clip("L3", reason, min(end, self._last_flush_ts), explanation=quotes)

    def trigger_manual(self) -> str | None:
        """外部（回车/程序）手动 VAR。"""
        ts = self._last_flush_ts if self._last_flush_ts is not None else time.time()
        self._make_clip("L3", "manual_hotkey", ts)
        return self.clips[-1]["clip_id"] if self.clips else None

    # ---- 说话人点名 ----
    def _on_new_speaker(self, spk_id: str, sample: str, ts: float) -> None:
        """新声纹簇诞生：先试声纹名册自动识别；认不出才排入点名队列。"""
        auto, sim = None, -1.0
        idx = int(spk_id[3:]) if spk_id[3:].isdigit() else -1
        if self.speaker_book is not None and idx >= 0 and idx < self.speaker_cluster.n_speakers:
            auto, sim = self.speaker_book.match(
                self.speaker_cluster.centroid_of(idx), config.SPEAKER_BOOK_SIM
            )
        if auto:
            self.speaker_names[spk_id] = auto
            # 自动识别成功也把本场质心并进名册：声音随时间/设备漂移时质心跟着走
            if self.speaker_book is not None and idx >= 0:
                self.speaker_book.add(auto, self.speaker_cluster.centroid_of(idx))
            if self.echo:
                print(f"  [spk] 声纹自动识别：{spk_id} ↔「{auto}」（相似度 {sim:.2f}，来自声纹名册）")
            return
        self._questions.append({"spk": spk_id, "sample": sample, "ts": ts, "printed": False})

    def next_question_prompt(self) -> str | None:
        """主循环调用：有新说话人待点名时返回提示文本（每个只返回一次），否则 None。"""
        if self._question_current is None and self._questions:
            self._question_current = self._questions.popleft()
        q = self._question_current
        if q is None or q.get("printed"):
            return None
        q["printed"] = True
        when = time.strftime("%H:%M:%S", time.localtime(q["ts"]))
        return (
            f"[spk] 检测到新说话人 {q['spk']}（{when} 起）——刚才说：「{q['sample'][:40]}」\n"
            "      他是谁？输入名字并回车（直接回车＝跳过；名字会记入声纹名册供下次自动识别）: "
        )

    def pending_questions_count(self) -> int:
        """待点名 + 正在等待回答的说话人数。"""
        return len(self._questions) + (1 if self._question_current is not None else 0)

    def submit_answer(self, line: str) -> str | None:
        """终端输入行归属：有点名待答 → 作为名字；否则返回 None（调用方按手动 VAR 处理）。"""
        q = self._question_current
        if q is None:
            return None
        self._question_current = None
        name = (line or "").strip()
        if not name:
            return f"[spk] {q['spk']} 跳过命名"
        self.name_speaker(q["spk"], name)
        return f"[spk] {q['spk']} → {name}（已写入本场转写与声纹名册）"

    def name_speaker(self, spk_id: str, name: str) -> None:
        """把名字绑定到说话人：追溯改名本场转写与已落盘片段，并更新声纹名册。"""
        name = (name or "").strip()[:24]
        if not spk_id or not name:
            return
        self.speaker_names[spk_id] = name
        for line in self.transcript:
            if line.get("speaker") == spk_id:
                line["speaker"] = name
        idx = int(spk_id[3:]) if spk_id[3:].isdigit() else -1
        if self.speaker_book is not None and 0 <= idx < self.speaker_cluster.n_speakers:
            self.speaker_book.add(name, self.speaker_cluster.centroid_of(idx))
        self._rename_in_stored_clips(spk_id, name)

    def _rename_in_stored_clips(self, spk_id: str, name: str) -> None:
        """已创建的片段 JSON 同步改名（薄/厚纪要与证据保持一致）。"""
        for clip in self.clips:
            path = config.POOL_DIR / self.session_id / f"{clip['clip_id']}.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                changed = False
                for line in data.get("transcript", []):
                    if line.get("speaker") == spk_id:
                        line["speaker"] = name
                        changed = True
                if changed:
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                continue  # 改名是增强项，单个片段损坏不中断

    # ---- 命中转正 ----
    def _relative_ts(self, ts: float) -> float:
        """把内部绝对时间戳转换为会议内相对秒数。"""
        base = self._timeline_start_ts if self._timeline_start_ts is not None else ts
        return max(0.0, ts - base)

    def relative_transcript(self) -> list[dict]:
        """全场转写的会议相对秒视图（内存态，供 transcribe 显式导出；默认模式不落盘）。"""
        return [{**line, "ts": self._relative_ts(line["ts"])} for line in self.transcript]

    def _make_clip(self, layer: str, evidence: str, trigger_ts: float, explanation: str = "") -> str:
        if self.hd is not None and not self.hd.is_recording():
            self.hd.start()  # 高清层：命中后 ≤90s 预算内高帧率补录（帧缩至 1280 宽）
        start_ts = trigger_ts - LOOKBACK_SECONDS
        pieces = []
        media_start = None
        media_end = trigger_ts
        chunks = list(self._ring)
        if self._ring_carry and self._ring_carry_ts is not None:
            chunks.append((self._ring_carry_ts, self._ring_carry))
        for t, b in chunks:
            lo = max(0, round((start_ts - t) * config.ASR_RATE))
            hi = min(len(b) // 2, round((trigger_ts - t) * config.ASR_RATE))
            if hi > lo:
                if media_start is None:
                    media_start = t + lo / config.ASR_RATE
                elif t + lo / config.ASR_RATE > media_end + 0.5 / config.ASR_RATE:
                    # Keep real timestamps across a capture gap; silence marks
                    # missing samples instead of moving later evidence earlier.
                    pieces.append(bytes(round((t + lo / config.ASR_RATE - media_end) * config.ASR_RATE) * 2))
                media_end = t + hi / config.ASR_RATE
                pieces.append(b[lo * 2:hi * 2])
        pcm = b"".join(pieces)
        lines = [l for l in self.transcript if start_ts <= l["ts"] <= trigger_ts]
        clip_lines = [{**line, "ts": self._relative_ts(line["ts"])} for line in lines]
        # 截屏 manifest 与 JSON 时间语义保持一致：一律会议相对秒
        screens = [
            (self._relative_ts(max(t, media_start if media_start is not None else start_ts)), jpg)
            for t, jpg in (self.screen_capture.frames_since(start_ts) if self.screen_capture else [])
            if t <= trigger_ts
        ]
        from .pool.store import pcm_to_wav_bytes

        clip_id = self.pool.add_clip(
            trigger_layer=layer,
            evidence=evidence,
            explanation=explanation,
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
        try:
            self._flush(force=True)
        finally:
            if self.hd is not None:
                self.hd.stop()
        # 零落盘纪律（审计 G1a/G1b）：未命中（无片段）的会话不创建任何文件/目录，
        # 草稿转写随进程内存一起消失——"没事故就什么都不落盘"（README 设计红线 2）。
        # 命中会话（G1c）才落盘：片段 + 全场转写文本（薄纪要所需）+ summary。
        if not self.clips:
            # 声纹名册是用户主动提供的数据（点名应答才写入），不受"零落盘"约束——
            # 零命中会话也照常保存名册，下次开会才能自动识别。
            if self.speaker_book is not None:
                self.speaker_book.save()
            return {
                "session_id": self.session_id,
                "lines": len(self.transcript),
                "clips": 0,
                "corrections": self.corrections,
                "transcript_json": None,
                "hd_dir": None,
                "speakers": dict(self.speaker_names),
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
        hd_dir = str(self.hd.session_dir) if self.hd is not None and self.hd.session_dir else None
        if self.speaker_book is not None:
            self.speaker_book.save()
        summary = {
            "session_id": self.session_id,
            "lines": len(transcript),
            "clips": len(self.clips),
            "corrections": self.corrections,
            "rewrite_count": len(self.rewrite_log),
            "transcript_json": str(out),
            "hd_dir": hd_dir,
            "speakers": dict(self.speaker_names),
        }
        (out.parent / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
