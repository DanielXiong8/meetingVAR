#!/usr/bin/env python3
"""Foreground live engine: capture -> ASR -> interest trigger -> topic clip.

The engine opens a topic when the matcher fires, keeps capturing the whole
stretch while the lecture is still on that topic, and closes it when the topic
ends. That is how a 01:30-06:30 explanation is saved as 01:30-06:30, not as a
fixed 90-second lookback.

Usually started by live_ctl.py as a detached process. It can also run in the
foreground for debugging, or replay a transcript with --simulate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_core import (  # noqa: E402
    ASR_RATE,
    TOOL_NAME,
    TOOL_VERSION,
    Interest,
    LiveAudio,
    LiveMatcher,
    NullASR,
    OpenTopic,
    ScreenRing,
    ScreenVideoRecorder,
    WhisperASR,
    append_jsonl,
    atomic_write_json,
    load_config,
    video_available,
    write_hit_output,
)
from moment_core import (  # noqa: E402
    BREAK_CUES,
    Turn,
    _adaptive_span,
    _dense_core,
    _score_turn,
    fmt_ts,
    render_window,
)


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _public_config(config: dict) -> dict:
    return {key: value for key, value in config.items() if not key.startswith("_")}


def _stitch(previous: str, current: str) -> str:
    """Return only the new tail of an overlapping ASR window."""
    previous = (previous or "").strip()
    current = (current or "").strip()
    if not current:
        return ""
    if not previous:
        return current
    if previous == current:
        return ""
    index = current.find(previous)
    if index >= 0:
        return current[index + len(previous):].strip()
    index = previous.find(current)
    if index >= 0:
        return ""
    max_len = min(len(previous), len(current))
    for size in range(max_len, 1, -1):
        if previous[-size:] == current[:size]:
            return current[size:].strip()
    from difflib import SequenceMatcher

    if SequenceMatcher(None, previous, current).ratio() >= 0.9:
        return ""
    return current


class Engine:
    def __init__(self, config_path: Path, out_dir: Path, consent: bool,
                 duration: float = 0.0):
        self.config = load_config(config_path)
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.consent = bool(consent or self.config.get("consent"))
        self.duration = max(0.0, float(duration))
        self.session_id = str(self.config.get("session_id"))
        self.started = time.time()
        self.state = "starting"
        self.hits = 0
        self.transcript_lines: list[dict] = []
        self.last_hit: dict | None = None
        self.last_text = ""
        self.audio_info: dict = {}
        self.screen_info: dict = {}
        self.asr_info: dict = {}
        self.error = ""
        self._transcript_path = self.out / "transcript.jsonl"
        self._hits_path = self.out / "hits.jsonl"
        self._status_path = self.out / "status.json"
        self._stop_path = self.out / "STOP"
        self._last_status = 0.0

        self.interests = self.config["_interests"]
        trigger = self.config.get("trigger", {})
        self.window_seconds = max(5.0, float(trigger.get("window_seconds", 20.0)))
        legacy_lookback = float(trigger.get("lookback_seconds", 0.0))
        self.ring_seconds = int(max(
            180.0,
            float(trigger.get("ring_seconds", max(300.0, legacy_lookback + 60.0))),
        ))
        self.min_span = max(5.0, float(trigger.get("min_span", 45.0)))
        self.max_span = max(self.min_span, float(trigger.get("max_span", 600.0)))
        self.rel_threshold = max(0.01, float(trigger.get("rel_threshold", 0.15)))
        self.bridge = max(0, int(trigger.get("bridge", 2)))
        self.max_gap = max(1.0, float(trigger.get("max_gap", 20.0)))
        self.end_grace = max(1.0, float(trigger.get("end_grace_seconds", 60.0)))
        self.core_min = max(1.0, float(trigger.get("core_min", 30.0)))
        self.core_max = max(self.core_min, float(trigger.get("core_max", 90.0)))
        self.max_frames = max(3, int(trigger.get("max_screen_frames", 60)))

        capture = self.config.get("capture", {})
        self.capture_cfg = capture
        self.video_fps = max(1.0, min(10.0, float(capture.get("video_fps", 5.0))))
        self.video_width = max(320, int(capture.get("video_width", 1280)))
        self.video_crf = max(18, min(40, int(capture.get("video_crf", 28))))
        self.video_monitor = max(1, int(capture.get("monitor", 1)))

        self.matcher = LiveMatcher(self.interests, window_seconds=self.window_seconds)
        self.open_topics: dict[str, OpenTopic] = {}
        self._span_floor: dict[str, float] = {}
        self._hit_seq = 0

    # -- state -----------------------------------------------------------

    def status(self, **extra) -> None:
        now = time.time()
        payload = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "session_id": self.session_id,
            "title": self.config.get("title", ""),
            "state": self.state,
            "consent": self.consent,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started)),
            "elapsed": round(now - self.started, 1),
            "hits": self.hits,
            "last_text": self.last_text[-300:],
            "last_hit": self.last_hit,
            "open_hits": [
                {
                    "hit_id": topic.hit_id,
                    "interest_id": topic.interest_id,
                    "interest": topic.interest.question,
                    "at": topic.fire_hit.get("at"),
                    "span_start": round(topic.span_start, 2),
                    "span_start_text": fmt_ts(topic.span_start),
                    "duration": round(max(0.0, now - self.started - topic.span_start), 1),
                    "last_active": round(topic.last_active, 2),
                }
                for topic in self.open_topics.values()
            ],
            "transcript_lines": len(self.transcript_lines),
            "audio": self.audio_info,
            "screen": self.screen_info,
            "asr": self.asr_info,
            "error": self.error,
            "updated_at": _iso(),
        }
        payload.update(extra)
        atomic_write_json(self._status_path, payload)
        self._last_status = now

    def _record_line(self, start: float, end: float, text: str) -> dict:
        line = {"ts": round(start, 2), "end": round(end, 2), "text": text}
        self.transcript_lines.append(line)
        append_jsonl(self._transcript_path, line)
        self.last_text = text
        return line

    # -- topic span lifecycle -------------------------------------------

    def _turn(self, index: int) -> Turn:
        line = self.transcript_lines[index]
        return Turn(line["ts"], line["end"], "", line["text"])

    def _score(self, index: int, interest: Interest) -> float:
        score, _why = _score_turn(
            self._turn(index), interest.terms, interest.phrase
        )
        return score

    @staticmethod
    def _thin_frames(frames: list[tuple[float, bytes]],
                     cap: int) -> list[tuple[float, bytes]]:
        if cap > 0 and len(frames) > cap:
            step = max(1, len(frames) // cap)
            return frames[::step][:cap]
        return frames

    def _open_topic(self, hit: dict, line_index: int,
                    audio: LiveAudio | None, screen: ScreenRing | None) -> None:
        """Open a topic at the fire line and extend it backwards by relevance."""
        if line_index < 0:
            return
        interest = next(
            (item for item in self.interests if item.id == hit["interest_id"]), None
        )
        if interest is None or interest.id in self.open_topics:
            return

        turns = [self._turn(index) for index in range(len(self.transcript_lines))]
        scores = [
            _score_turn(turn, interest.terms, interest.phrase)[0] for turn in turns
        ]
        now_rel = max(turns[line_index].end, time.time() - self.started)
        lo, _hi, start, _end = _adaptive_span(
            turns,
            scores,
            line_index,
            0.0,
            self.max_span,
            self.rel_threshold,
            self.bridge,
            self.max_gap,
        )
        start = max(0.0, round(start, 2))
        # A topic that hit max_span continues in a new chunk: never reach back
        # into the chunk that was just saved.
        start = max(start, self._span_floor.get(interest.id, 0.0))
        if audio is not None:
            # Never reach further back than the ring buffer can still serve.
            oldest = max(0.0, now_rel - self.ring_seconds + 15.0)
            start = max(start, oldest)
        while lo < line_index and turns[lo].end <= start:
            lo += 1

        topic = OpenTopic(
            interest=interest,
            fire_hit=hit,
            hit_id="",
            fire_index=line_index,
            span_start=start,
            lines_start_index=lo,
            processed_index=line_index,
            last_topic_end=turns[line_index].end,
            last_active=turns[line_index].end,
            threshold=max(scores[line_index], 1.0) * self.rel_threshold,
        )
        self._hit_seq += 1
        topic.hit_id = f"hit_{self._hit_seq:03d}"

        end_abs = self.started + now_rel
        start_abs = self.started + start
        if audio is not None:
            try:
                topic.audio.extend(audio.read_window(start_abs, end_abs))
            except Exception as exc:  # noqa: BLE001
                self.error = f"命中音频截取失败：{type(exc).__name__}: {exc}"
            topic.audio_cursor = end_abs
        if screen is not None and screen.available:
            frames = []
            try:
                frames = screen.frames_since(start_abs, end_abs)
            except Exception as exc:  # noqa: BLE001
                self.error = f"命中屏幕帧读取失败：{type(exc).__name__}: {exc}"
            # Keep the pre-fire ring frames as a fallback for the screenshot path.
            topic.frames = frames
            try:
                recorder = ScreenVideoRecorder(
                    self.out / "hits" / topic.hit_id / "screen.mp4",
                    fps=self.video_fps,
                    width=self.video_width,
                    crf=self.video_crf,
                    monitor=self.video_monitor,
                    prologue=[(ts - self.started, data) for ts, data in frames],
                    prologue_span=(start, now_rel),
                )
                if recorder.available:
                    topic.video = recorder
                    # The prologue is already in the video; do not keep a
                    # second copy of the JPEG frames in memory.
                    topic.frames = []
                elif recorder.error:
                    self.error = self.error or recorder.error
            except Exception as exc:  # noqa: BLE001
                self.error = f"录屏启动失败：{type(exc).__name__}: {exc}"
            topic.frame_cursor = end_abs

        self.open_topics[interest.id] = topic
        append_jsonl(self._hits_path, {
            "event": "hit_open",
            "hit_id": topic.hit_id,
            "session_id": self.session_id,
            "interest_id": interest.id,
            "interest": interest.question,
            "at": hit.get("at"),
            "score": hit.get("score"),
            "quote": hit.get("quote", ""),
            "span_start": round(topic.span_start, 2),
            "span_start_text": fmt_ts(topic.span_start),
        })
        self.status()

    def _advance_topics(self, line_index: int | None, now_rel: float,
                        audio: LiveAudio | None,
                        screen: ScreenRing | None) -> None:
        """Score new transcript lines, keep media flowing, close ended topics."""
        for topic in list(self.open_topics.values()):
            reason = ""
            if line_index is not None and line_index > topic.processed_index:
                for index in range(topic.processed_index + 1, line_index + 1):
                    line = self.transcript_lines[index]
                    previous_end = (
                        self.transcript_lines[index - 1]["end"]
                        if index > 0 else line["ts"]
                    )
                    score = self._score(index, topic.interest)
                    if score >= topic.threshold:
                        topic.last_topic_end = line["end"]
                        topic.last_active = max(topic.last_active, line["end"])
                        topic.tail.clear()
                    elif any(cue in line["text"] for cue in BREAK_CUES):
                        reason = "break"
                        break
                    elif line["ts"] - previous_end > self.max_gap:
                        reason = "gap"
                        break
                    else:
                        topic.tail.append(index)
                        if len(topic.tail) > self.bridge:
                            reason = "topic_end"
                            break
                    if line["end"] - topic.span_start >= self.max_span:
                        reason = "max_span"
                        break
                topic.processed_index = max(topic.processed_index, line_index)

            if not reason and now_rel - topic.last_active >= self.end_grace:
                reason = "topic_end"
            if not reason and now_rel - topic.span_start >= self.max_span:
                reason = "max_span"

            # Keep accumulating media even when nobody is talking.
            if audio is not None:
                end_abs = self.started + now_rel
                if end_abs > topic.audio_cursor:
                    try:
                        topic.audio.extend(
                            audio.read_window(topic.audio_cursor, end_abs)
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"命中音频累积失败：{type(exc).__name__}: {exc}"
                    topic.audio_cursor = end_abs
            if (screen is not None and screen.available
                    and topic.video is None):
                end_abs = self.started + now_rel
                if end_abs > topic.frame_cursor:
                    try:
                        topic.append_frames(
                            screen.frames_since(topic.frame_cursor, end_abs),
                            self.max_frames,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"命中屏幕帧累积失败：{type(exc).__name__}: {exc}"
                    topic.frame_cursor = end_abs

            if reason:
                self._close_topic(topic, reason, now_rel, audio, screen)

    def _close_topic(self, topic: OpenTopic, reason: str, now_rel: float,
                     audio: LiveAudio | None,
                     screen: ScreenRing | None) -> None:
        """Finalize the full span: audio + screen frames + transcript + hit.json."""
        if topic.closed:
            return
        topic.closed = True

        span_start = topic.span_start
        span_end = topic.last_topic_end
        if reason in {"topic_end", "gap"}:
            span_end = max(span_end, min(span_start + self.min_span, now_rel))
        elif reason == "session_stop":
            span_end = max(span_end, min(now_rel, topic.last_topic_end + self.end_grace))
        span_end = min(span_end, span_start + self.max_span)
        span_end = max(span_end, span_start + 1.0)

        lines = [
            line for line in self.transcript_lines
            if line["end"] > span_start and line["ts"] < span_end
        ]
        if not lines:
            line = self.transcript_lines[topic.fire_index]
            lines = [line]
        # ASR lines overlap the chunk window; extend the span to the last whole
        # line so hit.json, transcript.txt and audio.wav end together.
        span_end = round(max(span_end, lines[-1]["end"]), 2)
        span_end = min(span_end, span_start + self.max_span)

        turns = [Turn(line["ts"], line["end"], "", line["text"]) for line in lines]
        scores = [
            _score_turn(turn, topic.interest.terms, topic.interest.phrase)[0]
            for turn in turns
        ]
        best_index = max(range(len(turns)), key=lambda index: scores[index])
        core_lo, core_hi = _dense_core(
            turns, scores, 0, len(turns) - 1, best_index,
            self.core_min, self.core_max,
        )
        core_start = turns[core_lo].start
        core_end = turns[core_hi].end

        audio_pcm = b""
        if topic.audio:
            keep = int(round((span_end - span_start) * ASR_RATE)) * 2
            audio_pcm = bytes(topic.audio[:max(0, keep)])

        screen_images: list[tuple[str, bytes]] = []
        screen_video = ""
        if topic.video is not None:
            try:
                screen_video = topic.video.stop(
                    keep_seconds=max(1.0, span_end - span_start)
                )
            except Exception as exc:  # noqa: BLE001
                self.error = f"录屏收尾失败：{type(exc).__name__}: {exc}"
            if not screen_video and topic.video.error:
                self.error = self.error or topic.video.error
        if not screen_video and topic.frames:
            low = self.started + span_start
            high = self.started + span_end
            frames = self._thin_frames(
                [(ts, data) for ts, data in topic.frames if low <= ts <= high],
                self.max_frames,
            )
            for index, (ts, data) in enumerate(frames, 1):
                name = f"frame_{index:02d}_{int(ts - low):04d}s.jpg"
                screen_images.append((name, data))

        hit = {
            "event": "hit_close",
            "hit_id": topic.hit_id,
            "session_id": self.session_id,
            "interest_id": topic.interest.id,
            "interest": topic.interest.question,
            "at": topic.fire_hit.get("at"),
            "score": topic.fire_hit.get("score"),
            "why": topic.fire_hit.get("why") or [],
            "quote": topic.fire_hit.get("quote", ""),
            "span_start": round(span_start, 2),
            "span_end": round(span_end, 2),
            "duration": round(span_end - span_start, 2),
            "span_start_text": fmt_ts(span_start),
            "span_end_text": fmt_ts(span_end),
            "core_start": round(core_start, 2),
            "core_end": round(core_end, 2),
            "core_duration": round(core_end - core_start, 2),
            "core_start_text": fmt_ts(core_start),
            "core_end_text": fmt_ts(core_end),
            "key_quote": turns[best_index].text,
            "key_quote_at": fmt_ts(turns[best_index].start),
            "text": render_window(turns, span_start, span_end),
            "core_text": render_window(turns, core_start, core_end),
            "close_reason": reason,
            "closed_at": round(now_rel, 2),
            "lines": len(lines),
            "audio": "audio.wav" if audio_pcm else None,
            "screen_video": "screen.mp4" if screen_video else None,
            "screens": len(screen_images),
        }
        written = write_hit_output(
            self.out, hit, lines, audio_pcm or None, screen_images
        )
        hit["dir"] = written["dir"]
        self.hits += 1
        self.last_hit = hit
        append_jsonl(self._hits_path, hit)
        self.open_topics.pop(topic.interest_id, None)
        if reason == "max_span":
            # The topic is still going: next chunk starts here, no cooldown gap.
            self._span_floor[topic.interest_id] = round(span_end, 2)
        else:
            self._span_floor.pop(topic.interest_id, None)
            self.matcher.set_cooldown(topic.interest_id, now_rel)
        self.status()

    def _close_all_topics(self, now_rel: float, audio: LiveAudio | None,
                          screen: ScreenRing | None) -> None:
        if not self.open_topics:
            return
        self._advance_topics(None, now_rel, audio, screen)
        for topic in list(self.open_topics.values()):
            self._close_topic(topic, "session_stop", now_rel, audio, screen)

    # -- loops -----------------------------------------------------------

    def _should_stop(self) -> bool:
        if self._stop_path.exists():
            return True
        return bool(self.duration and time.time() - self.started >= self.duration)

    def run_live(self) -> int:
        capture = self.config.get("capture", {})
        asr_cfg = self.config.get("asr", {})
        source = str(capture.get("source", "loopback"))
        audio = None
        screen = None
        asr = None
        try:
            self.state = "starting"
            self.status()
            audio = LiveAudio(
                source=source,
                audio_device=capture.get("audio_device"),
                mic_device=capture.get("mic_device"),
                ring_seconds=self.ring_seconds,
            )
            self.audio_info = audio.describe()
            if capture.get("screen", True):
                screen = ScreenRing(
                    fps=float(capture.get("fps", 1.0)),
                    ring_seconds=self.ring_seconds,
                    width=self.video_width,
                    monitor=self.video_monitor,
                )
                self.screen_info = screen.describe()
                video_ok, video_reason = video_available()
                self.screen_info["recording"] = "video" if video_ok else "frames"
                if not video_ok:
                    self.screen_info["video_error"] = video_reason
            self.state = "loading_asr"
            self.status()
            model_name = str(asr_cfg.get("model", "base")).strip().lower()
            if model_name in {"none", "off", "disabled"}:
                asr = NullASR()
            else:
                asr = WhisperASR(
                    model=model_name,
                    device=str(asr_cfg.get("device", "cpu")),
                    language=str(asr_cfg.get("language", "zh")),
                    vad_rms=int(asr_cfg.get("vad_rms", 250)),
                    vad_filter=bool(asr_cfg.get("vad_filter", True)),
                    initial_prompt=str(asr_cfg.get(
                        "initial_prompt", "以下是普通话课堂内容，请用简体中文转写。"
                    )),
                )
            self.asr_info = asr.describe()
            self.state = "recording"
            self.status()

            chunk = max(4.0, float(asr_cfg.get("chunk_seconds", 10.0)))
            step = max(1.0, float(asr_cfg.get("step_seconds", 3.0)))
            last_window_text = ""
            while not self._should_stop():
                loop_started = time.time()
                absolute_now = time.time()
                now_rel = absolute_now - self.started
                absolute_start = max(self.started, absolute_now - chunk)
                pcm = audio.read_window(absolute_start, absolute_now)
                text = asr.transcribe(pcm)
                appended = ""
                if text:
                    appended = _stitch(last_window_text, text)
                    last_window_text = text
                if appended:
                    span = max(0.1, absolute_now - absolute_start)
                    fraction = (len(text) - len(appended)) / max(1, len(text))
                    rel_start = max(0.0, absolute_start - self.started + span * fraction)
                    rel_end = max(rel_start + 0.1, absolute_now - self.started)
                    self._record_line(rel_start, rel_end, appended)
                    line_index = len(self.transcript_lines) - 1
                    self._advance_topics(line_index, rel_end, audio, screen)
                    hit = self.matcher.update(appended, rel_end, rel_start)
                    if hit and hit["interest_id"] not in self.open_topics:
                        self._open_topic(hit, line_index, audio, screen)
                else:
                    self._advance_topics(None, now_rel, audio, screen)
                if time.time() - self._last_status >= 2.0:
                    self.status()
                elapsed = time.time() - loop_started
                time.sleep(max(0.2, step - elapsed))

            self.state = "stopping"
            self.status()
            return 0
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.status()
            return 2
        finally:
            try:
                self._close_all_topics(
                    max(0.0, time.time() - self.started), audio, screen
                )
            except Exception as exc:  # noqa: BLE001
                self.error = f"收尾时关闭命中失败：{type(exc).__name__}: {exc}"
            if audio is not None:
                audio.stop()
            if screen is not None:
                screen.stop()
            self._finalize()

    def run_simulation(self, transcript_path: Path, speed: float = 10.0) -> int:
        try:
            data = json.loads(Path(transcript_path).read_text(encoding="utf-8-sig"))
            turns = data.get("turns") or data.get("segments") or []
            if not isinstance(turns, list) or not turns:
                raise ValueError("simulation transcript has no turns")
            self.audio_info = {"simulation": True, "source": str(transcript_path)}
            self.screen_info = {"simulation": True}
            self.asr_info = {"simulation": True, "model": "none"}
            self.state = "recording"
            self.status()
            for turn in turns:
                if self._stop_path.exists():
                    break
                start = float(turn.get("start", 0))
                end = float(turn.get("end", start + 1))
                text = str(turn.get("text", "")).strip()
                if speed > 0:
                    time.sleep(max(0.0, (end - start) / speed))
                if not text:
                    continue
                self._record_line(start, end, text)
                line_index = len(self.transcript_lines) - 1
                self._advance_topics(line_index, end, None, None)
                hit = self.matcher.update(text, end, start)
                if hit and hit["interest_id"] not in self.open_topics:
                    self._open_topic(hit, line_index, None, None)
                if time.time() - self._last_status >= 2.0:
                    self.status()
            last_end = self.transcript_lines[-1]["end"] if self.transcript_lines else 0.0
            self._close_all_topics(last_end, None, None)
            self.state = "stopped"
            self.status()
            return 0
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = f"{type(exc).__name__}: {exc}"
            self.status()
            return 2
        finally:
            self._finalize()

    def _finalize(self) -> None:
        if self.state == "stopping":
            self.state = "stopped"
        self.status()
        files = []
        for name in ("transcript.jsonl", "hits.jsonl", "status.json"):
            path = self.out / name
            if path.is_file():
                files.append({"path": name, "bytes": path.stat().st_size})
        session = {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "session_id": self.session_id,
            "title": self.config.get("title", ""),
            "state": self.state,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started)),
            "stopped_at": _iso(),
            "elapsed": round(time.time() - self.started, 1),
            "consent": self.consent,
            "hits": self.hits,
            "transcript_lines": len(self.transcript_lines),
            "config": _public_config(self.config),
            "audio": self.audio_info,
            "screen": self.screen_info,
            "asr": self.asr_info,
            "files": files,
            "privacy": "local-only; no network calls",
        }
        atomic_write_json(self.out / "session.json", session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="课堂实时定位引擎（前台运行）。")
    parser.add_argument("--config", required=True, help="interests.json")
    parser.add_argument("--out", required=True, help="会话输出目录")
    parser.add_argument("--consent", action="store_true", help="确认已获得录音/录屏许可")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="录制秒数；0=直到 STOP 文件或 Ctrl+C")
    parser.add_argument("--simulate", default="", help="用转写文件回放，不打开设备")
    parser.add_argument("--speed", type=float, default=10.0,
                        help="回放倍速；0=立即")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    consent = bool(args.consent or config.get("consent"))
    if not consent:
        print("拒绝启动：未确认录音/录屏许可。请加 --consent，或在配置里设置 consent=true。",
              file=sys.stderr)
        return 2

    engine = Engine(Path(args.config), Path(args.out), consent, args.duration)
    if args.simulate:
        return engine.run_simulation(Path(args.simulate), args.speed)
    return engine.run_live()


if __name__ == "__main__":
    raise SystemExit(main())
