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


def pick_live_endpoint(pa, probe_seconds: float = 1.5) -> dict:
    """坑 20 对策：默认环回端点可能静音（播放走另一端点）。
    并发试采所有环回端点，选 RMS 最大者；全静音则回落默认端点并提示。"""
    import audioop
    import threading

    devs = list(pa.get_loopback_device_info_generator())
    if len(devs) <= 1:
        return devs[0]
    # 坑 19：环回流并发互锁；坑 23：残留进程持锁会让 pa.open 永不返回。
    # 对策：每个端点的探测放独立 daemon 线程 + join 超时，卡住即跳过该端点。
    results: dict[int, float] = {}
    for dev in devs:
        idx = dev["index"]
        result_box: dict = {}

        def _one(box: dict) -> None:
            try:
                rate = int(dev["defaultSampleRate"])
                ch = int(dev["maxInputChannels"]) or 2
                st = pa.open(
                    format=pyaudio.paInt16, channels=ch, rate=rate,
                    frames_per_buffer=int(rate * 0.1), input=True, input_device_index=idx,
                )
                chunks = []
                t_end = time.time() + probe_seconds
                while time.time() < t_end:
                    chunks.append(st.read(int(rate * 0.1), exception_on_overflow=False))
                st.stop_stream(); st.close()
                box["rms"] = audioop.rms(b"".join(chunks), 2)
            except Exception:
                box["rms"] = -1.0

        th = threading.Thread(target=_one, args=(result_box,), daemon=True)
        th.start()
        th.join(timeout=probe_seconds + 5)
        results[idx] = result_box.get("rms", -2.0)  # -2 = 超时跳过
    live = {i: r for i, r in results.items() if r > 100}
    if live:
        best = max(live, key=live.get)
        chosen = pa.get_device_info_by_index(best)
        print(f"[audio] 探测到活动端点: {chosen['name']} (RMS={live[best]:.0f})")
        return chosen
    print("[audio] 警告：所有环回端点均静音（系统当前没有声音输出？），回落默认端点")
    return devs[0]


class AudioCapture:
    def __init__(self, device_index: int | None = None, ring_seconds: int = config.RING_SECONDS, auto_pick: bool = True):
        self._pa = pyaudio.PyAudio()
        if device_index is not None:
            self._dev = self._pa.get_device_info_by_index(device_index)
        elif auto_pick:
            self._dev = pick_live_endpoint(self._pa)
        else:
            self._dev = self._pa.get_default_wasapi_loopback()
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
