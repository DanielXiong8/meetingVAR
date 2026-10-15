"""音频采集：系统环回（听"别人说的"）+ 麦克风（听"你说的"）双源。

环形缓冲存 (时间戳, 16k 单声道 PCM bytes)，命中回溯时按时间窗拼装。
DualCapture 把两路按墙钟时间戳叠进同一窗口（audioop 饱和混音）——
在线会议：远端声音走环回、你的声音走麦克风，两边都不丢。
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


def _place_window(data: bytes, data_start: float, win_start: float, total_bytes: int) -> bytes:
    """把一段（起始时刻 data_start 的 16bit mono）放进 [win_start, +total] 的零缓冲。"""
    off = int(round((data_start - win_start) * config.ASR_RATE)) * 2  # 字节偏移
    if off < 0:  # 数据早于窗口起点：裁头
        data = data[-off:]
        off = 0
    if off >= total_bytes:
        return b"\x00" * total_bytes
    body = data[: total_bytes - off]
    return b"\x00" * off + body + b"\x00" * (total_bytes - off - len(body))


def mix_window(placed: list[tuple[float, bytes]], win_start: float, total_bytes: int) -> bytes:
    """多路 16bit mono 按时间偏移放入等长窗口并饱和相加（audioop.add）。

    placed: [(起始时间戳, pcm)]；不足处补零；重叠处饱和混音（不会溢出回卷）。
    """
    if total_bytes <= 0:
        return b""
    mixed = None
    for start, data in placed:
        buf = _place_window(data, start, win_start, total_bytes)
        mixed = buf if mixed is None else audioop.add(mixed, buf, 2)
    return mixed if mixed is not None else b"\x00" * total_bytes


def pick_live_endpoint(pa, probe_seconds: float = 1.5) -> dict:
    """跟随系统默认输出，避免逐端点试开流导致驱动互锁。

    播放设备不一致时用 devices 查看编号，再显式 --audio-device 选择。
    probe_seconds 保留以兼容已有调用；不再创建无法取消的探测线程。
    """
    return pa.get_default_wasapi_loopback()


class AudioCapture:
    def __init__(
        self,
        device_index: int | None = None,
        ring_seconds: int = config.RING_SECONDS,
        auto_pick: bool = True,
        mic: bool = False,
    ):
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        try:
            if mic:
                # 麦克风源：默认输入设备（若非环回）；显式给编号则直接用它
                if device_index is not None:
                    self._dev = self._pa.get_device_info_by_index(device_index)
                else:
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
                if self._dev.get("isLoopbackDevice"):
                    raise ValueError("该设备是环回而非麦克风（--mic-device 请填输入设备编号）")
            elif device_index is not None:
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

    def frames_since(self, since_ts: float) -> list[tuple[float, bytes]]:
        with self._lock:
            return [(t, b) for t, b in self._ring if t >= since_ts - 0.5 / config.ASR_RATE]

    def ingest_since(self, since_ts: float) -> tuple[bytes, float]:
        """取出自 since_ts 以来的全部音频，返回 (pcm, 起始绝对时间戳)。"""
        items = self.frames_since(since_ts)
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


class DualCapture:
    """系统环回 + 麦克风 双源采集（在线会议：远端走环回、自己走麦克风，两头都录）。

    两路各自线程采集、各自按墙钟锚定时间戳（与 AudioCapture 同一契约）；
    ingest_since 把两路叠进同一时间窗（饱和混音）。麦克风打不开时如实降级为仅环回。
    """

    def __init__(self, loopback_index: int | None = None, mic_index: int | None = None,
                 ring_seconds: int = config.RING_SECONDS):
        self._loop = AudioCapture(device_index=loopback_index, ring_seconds=ring_seconds)
        self._mic: AudioCapture | None = None
        try:
            self._mic = AudioCapture(device_index=mic_index, ring_seconds=ring_seconds, mic=True)
        except Exception as exc:  # noqa: BLE001 — 无麦克风/被独占：降级不阻断会议
            print(f"[rec] 麦克风打开失败（{type(exc).__name__}: {exc}），本次降级为仅系统声音")
        # 鸭子类型：测试沙箱的虚拟采集没有 session_start（getattr 兜底，live 层同样写法）
        ls = getattr(self._loop, "session_start", time.time())
        ms = getattr(self._mic, "session_start", ls) if self._mic is not None else ls
        self.session_start = min(ls, ms)

    @property
    def name(self) -> str:
        if self._mic is not None:
            return f"系统[{self._loop.name}] + 麦克风[{self._mic.name}]"
        return f"系统[{self._loop.name}]（麦克风不可用，已降级）"

    def ingest_since(self, since_ts: float) -> tuple[bytes, float]:
        # 用子源的 ingest_since（与单源同一契约；测试沙箱的虚拟采集也只实现这一个）
        caps = [c for c in (self._loop, self._mic) if c is not None]
        placed: list[tuple[float, bytes]] = []
        end = since_ts
        for c in caps:
            data, start = c.ingest_since(since_ts)
            if data:
                placed.append((start, data))
                end = max(end, start + len(data) / 2 / config.ASR_RATE)
        if not placed:
            return b"", time.time()
        total_bytes = int((end - since_ts) * config.ASR_RATE) * 2
        mixed = mix_window(placed, since_ts, total_bytes)
        return mixed, min(start for start, _ in placed)

    def stop(self) -> None:
        self._loop.stop()
        if self._mic is not None:
            self._mic.stop()
