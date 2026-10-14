"""系统音频环回采集（C2 改造）：线程化 + 16k 单声道环形缓冲。

环形缓冲存 (时间戳, 16k 单声道 PCM bytes)，命中回溯时按时间窗拼装。
"""
from __future__ import annotations

import audioop
import threading
import time
from collections import deque

from .. import config


def to_pcm16k(raw: bytes, channels: int, rate: int, state=None):
    """16-bit mono/stereo -> mono 16k, shared by replay and live capture."""
    if channels == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    elif channels != 1:
        raise ValueError(f"仅支持单声道或双声道音频，当前 {channels} 声道")
    return audioop.ratecv(raw, 2, 1, rate, config.ASR_RATE, state)


def pick_live_endpoint(pa, probe_seconds: float = 1.5) -> dict:
    """跟随系统默认输出，避免逐端点试开流导致驱动互锁。

    播放设备不一致时用 devices 查看编号，再显式 --audio-device 选择。
    probe_seconds 保留以兼容已有调用；不再创建无法取消的探测线程。
    """
    return pa.get_default_wasapi_loopback()


class AudioCapture:
    def __init__(self, device_index: int | None = None, ring_seconds: int = config.RING_SECONDS, auto_pick: bool = True):
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            if device_index is not None:
                self._dev = self._pa.get_device_info_by_index(device_index)
            elif auto_pick:
                self._dev = pick_live_endpoint(self._pa)
            else:
                self._dev = self._pa.get_default_wasapi_loopback()
            self._rate = int(self._dev["defaultSampleRate"])
            self._channels = int(self._dev["maxInputChannels"]) or 2
            if self._channels not in (1, 2) or self._rate <= 0:
                raise ValueError("环回端点必须为有效采样率的单声道或双声道设备")
            self._frames_per_read = int(self._rate * 0.1)  # 100ms
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
        self._state = None  # audioop.ratecv 状态
        self._next_audio_ts: float | None = None
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
            mono, self._state = to_pcm16k(raw, self._channels, self._rate, self._state)
            with self._lock:
                duration = len(mono) / 2 / config.ASR_RATE
                start = self._next_audio_ts
                if start is None:
                    start = time.time() - duration
                self._ring.append((start, mono))
                self._next_audio_ts = start + duration

    def ingest_since(self, since_ts: float) -> tuple[bytes, float]:
        """取出自 since_ts 以来的全部音频，返回 (pcm, 起始绝对时间戳)。"""
        with self._lock:
            items = [(t, b) for t, b in self._ring if t >= since_ts - 0.5 / config.ASR_RATE]
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
