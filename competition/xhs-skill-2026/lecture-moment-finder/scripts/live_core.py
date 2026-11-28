"""Live lecture capture, real-time ASR and interest triggering.

This module is the "meetingVAR live mode" for the skill. It is deliberately
standalone: it does not import the meetingVAR engine, so the skill can be
installed from a zip. The offline locator in moment_core.py still handles
post-class search.

Windows + Python 3.10 is the target. Live mode needs:
  - pyaudiowpatch (WASAPI loopback + microphone)
  - mss + Pillow (screen frames)
  - numpy + faster-whisper (local ASR)
"""

from __future__ import annotations

import audioop
import io
import json
import math
import os
import re
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from moment_core import Turn, _clean_why, _score_turn, fmt_ts, query_terms

ASR_RATE = 16000
TOOL_NAME = "lecture-moment-finder-live"
TOOL_VERSION = "0.3.0"


# ---------------------------------------------------------------------------
# audio helpers (adapted from meetingVAR capture/audio.py)
# ---------------------------------------------------------------------------

def to_pcm16k(raw: bytes, channels: int, rate: int, state=None):
    if channels == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    elif channels != 1:
        raise ValueError(f"仅支持单声道或双声道音频，当前 {channels} 声道")
    return audioop.ratecv(raw, 2, 1, rate, ASR_RATE, state)


def _place_window(data: bytes, data_start: float, win_start: float, total_bytes: int) -> bytes:
    offset = int(round((data_start - win_start) * ASR_RATE)) * 2
    if offset < 0:
        data = data[-offset:]
        offset = 0
    if offset >= total_bytes:
        return b"\x00" * total_bytes
    body = data[: total_bytes - offset]
    return b"\x00" * offset + body + b"\x00" * (total_bytes - offset - len(body))


def mix_window(placed: list[tuple[float, bytes]], win_start: float, total_bytes: int) -> bytes:
    if total_bytes <= 0:
        return b""
    mixed = None
    for start, data in placed:
        buf = _place_window(data, start, win_start, total_bytes)
        mixed = buf if mixed is None else audioop.add(mixed, buf, 2)
    return mixed if mixed is not None else b"\x00" * total_bytes


def _slice_frames(frames: list[tuple[float, bytes]], start_ts: float,
                  end_ts: float | None = None) -> list[tuple[float, bytes]]:
    selected = []
    for timestamp, data in frames:
        count = len(data) // 2
        lo = max(0, round((start_ts - timestamp) * ASR_RATE))
        hi = count if end_ts is None else min(count, round((end_ts - timestamp) * ASR_RATE))
        if hi > lo:
            selected.append((timestamp + lo / ASR_RATE, data[lo * 2:hi * 2]))
    return selected


def audio_rms(pcm: bytes) -> int:
    if not pcm:
        return 0
    try:
        return audioop.rms(pcm, 2)
    except audioop.error:
        return 0


class AudioSource:
    """One WASAPI loopback or microphone stream with a time-indexed ring."""

    def __init__(self, device_index: int | None = None, mic: bool = False,
                 ring_seconds: int = 180):
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            if device_index is not None:
                self._dev = self._pa.get_device_info_by_index(device_index)
            elif mic:
                info = self._pa.get_default_input_device_info()
                if info.get("isLoopbackDevice"):
                    info = next(
                        (d for d in self._pa.get_device_info_generator()
                         if d.get("maxInputChannels", 0) and not d.get("isLoopbackDevice")),
                        None,
                    )
                    if info is None:
                        raise RuntimeError("未找到可用麦克风设备")
                self._dev = info
            else:
                self._dev = self._pa.get_default_wasapi_loopback()
            if mic and self._dev.get("isLoopbackDevice"):
                raise ValueError("该设备是环回而非麦克风")
            self._rate = int(self._dev["defaultSampleRate"])
            self._channels = int(self._dev["maxInputChannels"]) or 2
            if self._channels not in (1, 2) or self._rate <= 0:
                raise ValueError("音频端点必须为有效采样率的单声道或双声道设备")
            self._frames_per_read = int(self._rate * 0.1)
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._rate,
                frames_per_buffer=self._frames_per_read,
                input=True,
                input_device_index=self._dev["index"],
            )
        except BaseException:
            self._pa.terminate()
            raise

        self._ring: deque[tuple[float, bytes]] = deque(maxlen=ring_seconds * 10)
        self._state = None
        self._origin: float | None = None
        self._samples = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.session_start = time.time()
        self._clock = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def name(self) -> str:
        return str(self._dev.get("name", "audio"))

    def describe(self) -> dict:
        return {
            "name": self.name,
            "index": self._dev.get("index"),
            "rate": self._rate,
            "channels": self._channels,
            "loopback": bool(self._dev.get("isLoopbackDevice")),
        }

    def _append(self, mono: bytes, captured_at: float) -> None:
        if not mono:
            return
        with self._lock:
            if self._origin is None:
                self._origin = captured_at - len(mono) / 2 / ASR_RATE
                self._samples = 0
            start = self._origin + self._samples / ASR_RATE
            self._ring.append((start, mono))
            self._samples += len(mono) // 2

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._stream.read(self._frames_per_read, exception_on_overflow=True)
            except OSError:
                self._state = None
                with self._lock:
                    self._origin = None
                self._stop.wait(0.1)
                continue
            mono, self._state = to_pcm16k(raw, self._channels, self._rate, self._state)
            self._append(mono, self.session_start + time.monotonic() - self._clock)

    def frames_since(self, start_ts: float, end_ts: float) -> list[tuple[float, bytes]]:
        with self._lock:
            frames = list(self._ring)
        return _slice_frames(frames, start_ts, end_ts)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            self._pa.terminate()


class LiveAudio:
    """Loopback + optional microphone, mixed onto one 16k timeline."""

    def __init__(self, source: str = "loopback", audio_device: int | None = None,
                 mic_device: int | None = None, ring_seconds: int = 180):
        self.sources: list[AudioSource] = []
        self.errors: list[str] = []
        if source in ("both", "loopback"):
            try:
                self.sources.append(AudioSource(audio_device, mic=False, ring_seconds=ring_seconds))
            except Exception as exc:  # noqa: BLE001 - report and degrade
                self.errors.append(f"系统声音打开失败：{type(exc).__name__}: {exc}")
        if source in ("both", "mic"):
            try:
                self.sources.append(AudioSource(mic_device, mic=True, ring_seconds=ring_seconds))
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"麦克风打开失败：{type(exc).__name__}: {exc}")
        if not self.sources:
            raise RuntimeError("；".join(self.errors) or "没有可用音频输入")

    def describe(self) -> dict:
        return {
            "sources": [src.describe() for src in self.sources],
            "errors": self.errors,
        }

    def read_window(self, start: float, end: float) -> bytes:
        total = int(round((end - start) * ASR_RATE)) * 2
        placed: list[tuple[float, bytes]] = []
        for src in self.sources:
            placed.extend(src.frames_since(start, end))
        return mix_window(placed, start, total)

    def stop(self) -> None:
        for src in self.sources:
            try:
                src.stop()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# screen ring
# ---------------------------------------------------------------------------

class ScreenRing:
    def __init__(self, fps: float = 1.0, ring_seconds: int = 180,
                 width: int = 1280, monitor: int = 1):
        self.fps = max(0.2, min(5.0, float(fps)))
        self.width = max(320, int(width))
        self.monitor = max(1, int(monitor))
        self.available = False
        self.error = ""
        self._ring: deque[tuple[float, bytes]] = deque(maxlen=int(self.fps * ring_seconds))
        self._stop = threading.Event()
        self._thread = None
        self.session_start = time.time()
        self._clock = time.monotonic()
        try:
            import mss  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.error = f"屏幕采集依赖缺失：{type(exc).__name__}: {exc}"
            return
        self.available = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        import mss
        from PIL import Image

        interval = 1.0 / self.fps
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[self.monitor]
                while not self._stop.is_set():
                    started = time.monotonic()
                    try:
                        shot = sct.grab(monitor)
                        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                        if image.width > self.width:
                            height = max(2, round(image.height * self.width / image.width))
                            image = image.resize((self.width, height), Image.BILINEAR)
                        buffer = io.BytesIO()
                        image.save(buffer, format="JPEG", quality=70)
                        self._ring.append(
                            (self.session_start + time.monotonic() - self._clock,
                             buffer.getvalue())
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"屏幕帧失败：{type(exc).__name__}: {exc}"
                    self._stop.wait(max(0.05, interval - (time.monotonic() - started)))
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.error = f"屏幕采集不可用：{type(exc).__name__}: {exc}"

    def frames_since(self, start: float, end: float) -> list[tuple[float, bytes]]:
        return [(ts, data) for ts, data in list(self._ring) if start <= ts <= end]

    def save_frames(self, target: Path, start: float, end: float, limit: int = 12) -> list[str]:
        frames = self.frames_since(start, end)
        if len(frames) > limit:
            step = max(1, len(frames) // limit)
            frames = frames[::step][:limit]
        saved = []
        target.mkdir(parents=True, exist_ok=True)
        for index, (ts, data) in enumerate(frames, 1):
            name = f"frame_{index:02d}_{int(ts - start):03d}s.jpg"
            (target / name).write_bytes(data)
            saved.append(name)
        return saved

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def describe(self) -> dict:
        return {"available": self.available, "fps": self.fps,
                "width": self.width, "monitor": self.monitor,
                "error": self.error}


# ---------------------------------------------------------------------------
# screen video (PyAV; no external ffmpeg)
# ---------------------------------------------------------------------------

def video_available() -> tuple[bool, str]:
    """Return whether in-process screen video encoding is available."""
    try:
        import av  # noqa: F401
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
        import numpy  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _trim_mp4(source: Path, target: Path, keep_seconds: float) -> bool:
    """Copy video packets up to keep_seconds (no re-encode)."""
    try:
        import av
    except Exception:  # noqa: BLE001
        return False
    try:
        container_in = av.open(str(source))
        container_out = av.open(str(target), mode="w", format="mp4")
        stream_in = container_in.streams.video[0]
        stream_out = container_out.add_stream_from_template(stream_in)
        for packet in container_in.demux(stream_in):
            if packet.dts is None:
                continue
            if (packet.pts is not None
                    and float(packet.pts * packet.time_base) > keep_seconds):
                break
            packet.stream = stream_out
            container_out.mux(packet)
        container_out.close()
        container_in.close()
        return target.is_file() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        try:
            target.unlink()
        except OSError:
            pass
        return False


class ScreenVideoRecorder:
    """Record one open topic's screen to MP4 with PyAV.

    A recorder is started when a topic opens. It first encodes the ring frames
    covering [span_start, fire_time] as a prologue (so the clip starts where
    the topic started), then streams live screen frames at `fps`. On stop it
    trims the tail to the topic end. No external ffmpeg is required.
    """

    def __init__(self, target: str | Path, fps: float = 5.0,
                 width: int = 1280, crf: int = 28, monitor: int = 1,
                 prologue: list[tuple[float, bytes]] | None = None,
                 prologue_span: tuple[float, float] | None = None):
        self.target = Path(target)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.fps = max(1.0, min(10.0, float(fps)))
        self.width = max(320, int(width))
        self.crf = max(18, min(40, int(crf)))
        self.monitor = max(1, int(monitor))
        self.available = False
        self.error = ""
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._pending: deque = deque(maxlen=max(4, int(self.fps * 4)))
        self._thread: threading.Thread | None = None
        self._container = None
        self._stream = None
        self._encoded = 0
        ok, reason = video_available()
        if not ok:
            self.error = f"录屏依赖缺失：{reason}"
            return
        try:
            import mss

            with mss.mss() as sct:
                info = sct.monitors[self.monitor]
            self._height = int(round(
                self.width * info.get("height", 720)
                / max(1, info.get("width", 1280)) / 2
            )) * 2
            self._open()
        except Exception as exc:  # noqa: BLE001
            self.error = f"录屏初始化失败：{type(exc).__name__}: {exc}"
            self._close_quietly()
            return
        self.available = True
        # Capture immediately; frames grabbed while the prologue is being
        # written are queued and encoded right after it.
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if prologue and prologue_span:
            try:
                self._write_prologue(prologue, prologue_span)
            except Exception as exc:  # noqa: BLE001
                self.error = f"录屏前段失败：{type(exc).__name__}: {exc}"
        self._ready.set()

    def _open(self) -> None:
        import av
        from fractions import Fraction

        rate = Fraction(self.fps).limit_denominator(1000)
        self._container = av.open(str(self.target), mode="w", format="mp4")
        try:
            self._stream = self._container.add_stream("libx264", rate=rate)
        except Exception:  # noqa: BLE001 - fall back to MPEG-4
            self._container.close()
            try:
                self.target.unlink()
            except OSError:
                pass
            self._container = av.open(str(self.target), mode="w", format="mp4")
            self._stream = self._container.add_stream("mpeg4", rate=rate)
        self._stream.width = self.width
        self._stream.height = self._height
        self._stream.pix_fmt = "yuv420p"
        self._stream.options = {
            "crf": str(self.crf),
            "preset": "ultrafast",
            "g": str(max(1, int(self.fps * 2))),
        }

    def _encode(self, frame) -> None:
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        self._encoded += 1

    def _write_prologue(self, frames: list[tuple[float, bytes]],
                        span: tuple[float, float]) -> None:
        import av
        import numpy as np
        from PIL import Image

        start, end = float(span[0]), float(span[1])
        duration = end - start
        if duration <= 0:
            return
        items = sorted(
            (float(ts), data) for ts, data in frames if start <= ts <= end
        )
        if not items:
            return
        # The first ring frame also covers the gap from the span start.
        items[0] = (start, items[0][1])
        total = max(1, int(round(duration * self.fps)))
        written = 0
        for index, (ts, data) in enumerate(items):
            boundary = items[index + 1][0] if index + 1 < len(items) else end
            count = int(round(max(0.0, boundary - ts) * self.fps))
            if index == len(items) - 1:
                count = max(0, total - written)
            count = max(1, count)
            image = Image.open(io.BytesIO(data)).convert("RGB")
            array = np.array(image)
            for _ in range(count):
                if written >= total:
                    break
                frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                frame = frame.reformat(
                    width=self.width, height=self._height, format="yuv420p"
                )
                self._encode(frame)
                written += 1
            if written >= total:
                break

    def _loop(self) -> None:
        import av
        import mss
        import numpy as np
        from PIL import Image

        interval = 1.0 / self.fps
        live_frames = 0
        started_at = time.monotonic()
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[self.monitor]
                while not self._stop.is_set():
                    started = time.monotonic()
                    shot = sct.grab(monitor)
                    image = Image.frombytes(
                        "RGB", shot.size, shot.bgra, "raw", "BGRX"
                    )
                    array = np.array(image)
                    # Wall-clock pacing: if encoding falls behind, repeat the
                    # frame so the video timeline still matches the audio.
                    target = int(round((started - started_at) * self.fps))
                    count = max(1, target - live_frames)
                    count = min(count, max(1, int(self.fps * 10)))
                    for _ in range(count):
                        frame = av.VideoFrame.from_ndarray(
                            array, format="rgb24"
                        )
                        frame = frame.reformat(
                            width=self.width, height=self._height,
                            format="yuv420p",
                        )
                        if not self._ready.is_set():
                            self._pending.append(frame)
                        else:
                            while self._pending:
                                self._encode(self._pending.popleft())
                            self._encode(frame)
                        live_frames += 1
                    self._stop.wait(
                        max(0.0, interval - (time.monotonic() - started))
                    )
        except Exception as exc:  # noqa: BLE001
            self.error = f"录屏中断：{type(exc).__name__}: {exc}"

    def _close_quietly(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:  # noqa: BLE001
                pass
            self._container = None

    def stop(self, keep_seconds: float | None = None) -> str:
        """Stop recording, trim the tail, and return the MP4 path (or "")."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            if self._stream is not None and self._container is not None:
                for packet in self._stream.encode():
                    self._container.mux(packet)
        except Exception as exc:  # noqa: BLE001
            self.error = self.error or f"录屏收尾失败：{type(exc).__name__}: {exc}"
        self._close_quietly()
        if keep_seconds and keep_seconds > 0 and self._encoded:
            trimmed = self.target.with_name(
                self.target.stem + ".trim" + self.target.suffix
            )
            if _trim_mp4(self.target, trimmed, float(keep_seconds)):
                trimmed.replace(self.target)
        if self.target.is_file() and self.target.stat().st_size > 0:
            return str(self.target)
        return ""


# ---------------------------------------------------------------------------
# ASR
# ---------------------------------------------------------------------------

def _is_prompt_echo(text: str, prompt: str) -> bool:
    """Whisper may echo the initial prompt when the chunk is only noise."""
    left = re.sub(r"\s+", "", text or "")
    right = re.sub(r"\s+", "", prompt or "")
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio() >= 0.85


class NullASR:
    """Used for a capture-only smoke test (asr.model = none)."""

    def transcribe(self, pcm: bytes) -> str:
        return ""

    def describe(self) -> dict:
        return {"model": "none", "note": "ASR disabled; capture-only smoke test"}


class WhisperASR:
    def __init__(self, model: str = "base", device: str = "cpu",
                 language: str = "zh", vad_rms: int = 250,
                 vad_filter: bool = True,
                 initial_prompt: str = "以下是普通话课堂内容，请用简体中文转写。"):
        from faster_whisper import WhisperModel

        self.language = language
        self.vad_rms = int(vad_rms)
        self.vad_filter = bool(vad_filter)
        self.model_name = model
        self.initial_prompt = initial_prompt
        self._model = WhisperModel(model, device=device, compute_type="int8")

    def transcribe(self, pcm: bytes) -> str:
        if not pcm or audio_rms(pcm) < self.vad_rms:
            return ""
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def run(vad: bool) -> str:
            segments, _info = self._model.transcribe(
                audio,
                language=self.language,
                vad_filter=vad,
                beam_size=1,
                condition_on_previous_text=False,
                initial_prompt=self.initial_prompt or None,
            )
            return "".join(segment.text for segment in segments).strip()

        text = run(self.vad_filter)
        if not text and self.vad_filter:
            text = run(False)
        if _is_prompt_echo(text, self.initial_prompt):
            return ""
        return text

    def describe(self) -> dict:
        return {
            "model": self.model_name,
            "language": self.language,
            "vad_filter": self.vad_filter,
            "initial_prompt": self.initial_prompt,
        }


# ---------------------------------------------------------------------------
# interest matching
# ---------------------------------------------------------------------------

@dataclass
class Interest:
    id: str
    question: str
    keywords: list[str] = field(default_factory=list)
    min_score: float = 25.0
    min_keyword_hits: int = 1
    consecutive_hits: int = 1
    cooldown_seconds: float = 60.0
    terms: list[tuple[str, float]] = field(default_factory=list)
    phrase: str = ""

    def __post_init__(self):
        query = self.question + " " + " ".join(self.keywords)
        self.terms, self.phrase = query_terms(query)
        self.keywords = [word for word in self.keywords if word]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "keywords": self.keywords,
            "min_score": self.min_score,
            "min_keyword_hits": self.min_keyword_hits,
            "consecutive_hits": self.consecutive_hits,
            "cooldown_seconds": self.cooldown_seconds,
        }


class LiveMatcher:
    def __init__(self, interests: list[Interest], window_seconds: float = 20.0):
        self.interests = interests
        self.window_seconds = max(5.0, float(window_seconds))
        self._recent: deque[tuple[float, str]] = deque()
        self._streak: dict[str, int] = {item.id: 0 for item in interests}
        self._last_fire: dict[str, float] = {item.id: -1e9 for item in interests}

    def update(self, text: str, at: float, window_start: float) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None
        self._recent.append((at, text))
        while self._recent and at - self._recent[0][0] > self.window_seconds:
            self._recent.popleft()
        joined = " ".join(item for _ts, item in self._recent)
        if not joined:
            return None

        candidates = []
        active_ids = set()
        for interest in self.interests:
            if at - self._last_fire[interest.id] < interest.cooldown_seconds:
                continue
            score, why = _score_turn(
                Turn(window_start, at, "", joined), interest.terms, interest.phrase
            )
            required_keywords = interest.min_keyword_hits if interest.keywords else 0
            keyword_hits = sum(
                1 for word in interest.keywords if word and word.casefold() in joined.casefold()
            )
            if score < interest.min_score or keyword_hits < required_keywords:
                self._streak[interest.id] = 0
                continue
            candidates.append((score, interest, why))
            active_ids.add(interest.id)

        for interest in self.interests:
            if interest.id not in active_ids:
                self._streak[interest.id] = 0

        if not candidates:
            return None

        score, interest, why = max(candidates, key=lambda item: item[0])
        self._streak[interest.id] += 1
        if self._streak[interest.id] < max(1, interest.consecutive_hits):
            return None
        self._streak[interest.id] = 0
        self._last_fire[interest.id] = at
        return {
            "interest_id": interest.id,
            "interest": interest.question,
            "at": round(at, 2),
            "window_start": round(window_start, 2),
            "score": round(float(score), 2),
            "why": _clean_why(why)[:6],
            "quote": _best_sentence(joined, interest),
            "text": joined,
        }

    def set_cooldown(self, interest_id: str, at: float) -> None:
        """Start a new cooldown (used after a topic span closes)."""
        self._last_fire[interest_id] = at


def _best_sentence(text: str, interest: Interest) -> str:
    sentences = [part.strip() for part in re.split(r"[。！？!?；;\n]+", text) if part.strip()]
    if not sentences:
        return text[:300]
    best = max(
        sentences,
        key=lambda sentence: _score_turn(
            Turn(0, 0, "", sentence), interest.terms, interest.phrase
        )[0],
    )
    return best[:500]


@dataclass
class OpenTopic:
    """A topic that is still being discussed.

    Opened the moment the matcher fires; kept alive while the lecture is still
    on-topic. Media and transcript lines are accumulated for the whole stretch,
    so the saved clip covers e.g. 01:30-06:30 instead of a fixed 90s lookback.
    Closed by the engine when the topic ends, a break cue appears, the max span
    is reached, or the session stops.
    """

    interest: Interest
    fire_hit: dict
    hit_id: str
    fire_index: int
    span_start: float
    lines_start_index: int
    processed_index: int
    last_topic_end: float
    last_active: float
    threshold: float
    tail: list[int] = field(default_factory=list)
    audio: bytearray = field(default_factory=bytearray)
    audio_cursor: float = 0.0  # absolute timestamp already accumulated
    frames: list[tuple[float, bytes]] = field(default_factory=list)
    frame_cursor: float = 0.0  # absolute timestamp already collected
    video: "ScreenVideoRecorder | None" = None
    closed: bool = False

    @property
    def interest_id(self) -> str:
        return self.interest.id

    def append_frames(self, frames: list[tuple[float, bytes]], cap: int) -> None:
        if not frames:
            return
        self.frames.extend(frames)
        if len(self.frames) > cap > 0:
            step = max(1, len(self.frames) // cap)
            self.frames = self.frames[::step][:cap]


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------

def atomic_write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def append_jsonl(path: str | Path, payload: dict) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(ASR_RATE)
        handle.writeframes(pcm)


def write_hit_output(out_dir: Path, hit: dict, transcript_lines: list[dict],
                     audio_pcm: bytes | None,
                     screen_images: list[tuple[str, bytes]]) -> dict:
    """Save one replayable topic folder (no manifest, no hashes, no zip).

    The learning skill is about replay: audio + screen frames + transcript +
    hit.json. Integrity hashes belong to the legal evidence skill.
    """
    target = out_dir / "hits" / hit["hit_id"]
    target.mkdir(parents=True, exist_ok=True)

    transcript_text = "\n".join(
        f"[{fmt_ts(line['ts'])}] {line['text']}" for line in transcript_lines
    ) + "\n"
    (target / "transcript.txt").write_text(transcript_text, encoding="utf-8")
    (target / "hit.json").write_text(
        json.dumps(hit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if audio_pcm:
        write_wav(target / "audio.wav", audio_pcm)
    if screen_images:
        screens = target / "screens"
        screens.mkdir(exist_ok=True)
        for name, data in screen_images:
            (screens / name).write_bytes(data)
    return {"dir": str(target)}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    if not data.get("interests"):
        raise ValueError("config.interests must contain at least one interest")
    interests = []
    for item in data["interests"]:
        if not item.get("id") or not item.get("question"):
            raise ValueError("each interest needs id and question")
        interests.append(Interest(
            id=str(item["id"]),
            question=str(item["question"]),
            keywords=[str(word) for word in item.get("keywords", [])],
            min_score=float(item.get("min_score", 25.0)),
            min_keyword_hits=int(item.get("min_keyword_hits", 1)),
            consecutive_hits=int(item.get("consecutive_hits", 1)),
            cooldown_seconds=float(item.get("cooldown_seconds", 60.0)),
        ))
    data["_interests"] = interests
    data.setdefault("session_id", time.strftime("lecture-%Y%m%d-%H%M%S"))
    data.setdefault("capture", {})
    data.setdefault("asr", {})
    data.setdefault("trigger", {})
    return data


def list_devices() -> dict:
    result = {"audio": [], "monitors": [], "errors": []}
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            for index in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(index)
                if not (info.get("maxInputChannels", 0) or info.get("isLoopbackDevice")):
                    continue
                result["audio"].append({
                    "index": index,
                    "name": info.get("name", ""),
                    "loopback": bool(info.get("isLoopbackDevice")),
                    "input_channels": int(info.get("maxInputChannels", 0)),
                    "rate": int(info.get("defaultSampleRate", 0)),
                })
        finally:
            pa.terminate()
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"音频设备：{type(exc).__name__}: {exc}")
    try:
        import mss

        with mss.mss() as sct:
            for index, monitor in enumerate(sct.monitors):
                result["monitors"].append({
                    "index": index,
                    "width": monitor.get("width"),
                    "height": monitor.get("height"),
                    "left": monitor.get("left"),
                    "top": monitor.get("top"),
                })
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"显示器：{type(exc).__name__}: {exc}")
    return result