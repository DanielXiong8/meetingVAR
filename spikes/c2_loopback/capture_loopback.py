"""Spike C2 · 系统音频环回采集。

用法：
    python capture_loopback.py --list                 # 枚举所有输出设备及其环回端点
    python capture_loopback.py --seconds 10 --out out.wav   # 采默认输出的环回

验收标准：播放任意系统声音（视频/音乐/TTS）期间采集，得到的 wav RMS 明显非零，
且送 ASR 能转出播放内容——即"采到的是系统输出而非麦克风"。
"""
from __future__ import annotations

import argparse
import audioop  # stdlib（py3.12 起移除；本环境 py3.10 可用）
import wave
from pathlib import Path

import pyaudiowpatch as pyaudio


def list_devices() -> None:
    p = pyaudio.PyAudio()
    print("== 输出设备及其环回端点 ==")
    for info in p.get_loopback_device_info_generator():
        print(f"  [{info['index']}] {info['name']}  ({int(info['maxInputChannels'])}ch @ {int(info['defaultSampleRate'])}Hz)")
    try:
        default = p.get_default_wasapi_loopback()
        print(f"== 默认环回: [{default['index']}] {default['name']}")
    except OSError as e:
        print(f"== 默认环回获取失败: {e}")


def capture(seconds: float, out: Path, device_index: int | None) -> None:
    p = pyaudio.PyAudio()
    if device_index is not None:
        dev = p.get_device_info_by_index(device_index)
    else:
        dev = p.get_default_wasapi_loopback()

    channels = int(dev["maxInputChannels"]) or 2
    rate = int(dev["defaultSampleRate"])
    frames_per_read = int(rate * 0.1)  # 100ms 一读

    print(f"[rec] {dev['name']} | {channels}ch @ {rate}Hz | {seconds}s -> {out}")
    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        frames_per_buffer=frames_per_read,
        input=True,
        input_device_index=dev["index"],
    )
    chunks: list[bytes] = []
    n_reads = int(seconds / 0.1)
    for _ in range(n_reads):
        chunks.append(stream.read(frames_per_read, exception_on_overflow=False))
    stream.stop_stream()
    stream.close()
    p.terminate()

    data = b"".join(chunks)
    rms = audioop.rms(data, 2) if data else 0
    peak = audioop.maxpp(data, 2) if data else 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)

    print(f"[ok] wrote {len(data)/1e6:.1f} MB | RMS={rms} peak-peak={peak}")
    print("     RMS<100 视为近似静音：检查系统音量/是否真的有声音在播")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", type=Path, default=Path("data/clips/loopback_test.wav"))
    ap.add_argument("--device", type=int, default=None, help="环回设备 index（--list 查看）")
    args = ap.parse_args()
    if args.list:
        list_devices()
    else:
        capture(args.seconds, args.out, args.device)
