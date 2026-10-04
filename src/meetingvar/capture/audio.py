"""系统音频环回采集（C2 改造）：线程化 + 16k 单声道环形缓冲。

环形缓冲存 (时间戳, 16k 单声道 PCM bytes)，命中回溯时按时间窗拼装。
"""
from __future__ import annotations

import audioop
import threading
import time
from collections import deque

import pyaudiowpatch as pyaudio

from .. import config


class AudioCapture:
    def __init__(self, device_index: int | None = None, ring_seconds: int = config.RING_SECONDS):
        self._pa = pyaudio.PyAudio()
        self._dev = (
            self._pa.get_device_info_by_index(device_index)
            if device_index is not None
            else self._pa.get_default_wasapi_loopback()
        )
        self._rate = int(self._dev["defaultSampleRate"])
        self._channels = int(self._dev["maxInputChannels"]) or 2
        self._frames_per_read = int(self._rate * 0.1)  # 100ms
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._channels,
            rate=self._rate,
            frames_per_buffer=self._frames_per_read,
            input=True,
            input_device_index=self._dev["index"],
        )
        self._ring: deque[tuple[float, bytes]] = deque(maxlen=ring_seconds * 10)
        self._state = None  # audioop.ratecv 状态
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.session_start = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def name(self) -> str:
        return self._dev["name"]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._stream.read(self._frames_per_read, exception_on_overflow=False)
            except OSError:
                time.sleep(0.1)
                continue
            mono, self._state = audioop.ratecv(
                raw, 2, self._channels, self._rate, config.ASR_RATE, self._state
            )
            with self._lock:
                self._ring.append((time.time(), mono))

    def ingest_since(self, since_ts: float) -> tuple[bytes, float]:
        """取出自 since_ts 以来的全部音频，返回 (pcm, 起始绝对时间戳)。"""
        with self._lock:
            items = [(t, b) for t, b in self._ring if t >= since_ts]
        if not items:
            return b"", time.time()
        return b"".join(b for _, b in items), items[0][0]

    def span_pcm(self, start_ts: float, end_ts: float) -> bytes:
        with self._lock:
            items = [(t, b) for t, b in self._ring if start_ts <= t <= end_ts]
        return b"".join(b for _, b in items)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            self._pa.terminate()


def pcm_to_wav_bytes(pcm: bytes, rate: int = config.ASR_RATE) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
