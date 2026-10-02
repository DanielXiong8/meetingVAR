"""Spike C4 · 低帧率录屏环形缓冲（命中转正式）。

设计要点（与 D6 定案一致）：
  - 1–2 fps 低帧率采集；**缓冲存 JPEG 编码字节**（1080p 裸帧 ~6MB/张，120 帧裸存≈700MB 会爆内存；
    JPEG q70 每张几十 KB，120 帧 ≈ 几 MB）
  - deque(maxlen=N) 天然环形，旧的自动淘汰；默认零落盘
  - "命中"= 模拟触发，把缓冲里的帧倾倒到 data/clips/screen_<ts>/（正式版此处改为封装 mp4）

用法：
    python screen_buffer.py --seconds 10 --fps 2 --buffer 120
"""
from __future__ import annotations

import argparse
import io
import time
from collections import deque
from pathlib import Path

from PIL import Image


def make_grabber():
    """优先 dxcam（Desktop Duplication），失败退 mss。返回 (backend, grab_fn, release_fn)。"""
    try:
        import dxcam

        cam = dxcam.create(output_color="RGB")
        if cam is None:
            raise RuntimeError("dxcam.create() 返回 None（设备被占用或不支持）")

        def grab():
            return cam.grab()  # ndarray(RGB) 或 None（画面无更新）

        return "dxcam", grab, cam.release
    except Exception as e:  # noqa: BLE001
        print(f"[warn] dxcam 不可用：{e}\n[warn] 退回 mss 截屏序列")
        import mss

        sct = mss.mss()
        mon = sct.monitors[1]

        def grab():
            img = sct.grab(mon)
            return Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        return "mss", grab, sct.close


def encode_jpeg(frame) -> bytes:
    if isinstance(frame, Image.Image):
        img = frame
    else:
        img = Image.fromarray(frame)  # numpy RGB
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--buffer", type=int, default=120, help="环形缓冲帧数上限")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    backend, grab, release = make_grabber()
    ring: deque[tuple[float, bytes]] = deque(maxlen=args.buffer)
    interval = 1.0 / args.fps

    print(f"[rec] backend={backend} fps={args.fps} buffer={args.buffer} frames  duration={args.seconds}s")
    t0 = time.time()
    n_grab = n_new = 0
    while time.time() - t0 < args.seconds:
        loop_start = time.time()
        n_grab += 1
        frame = grab()
        if frame is not None:
            ring.append((time.time(), encode_jpeg(frame)))
            n_new += 1
        dt = time.time() - loop_start
        if dt < interval:
            time.sleep(interval - dt)
    release()

    mem = sum(len(j) for _, j in ring)
    print(f"[stats] captures={n_grab} new-frames={n_new} buffered={len(ring)} "
          f"avg-jpeg={mem / max(len(ring), 1) / 1024:.0f}KB buffer-mem={mem / 1024 / 1024:.1f}MB")

    # 模拟"命中"：倾倒缓冲
    out = args.out or Path(f"data/clips/screen_{int(time.time())}")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "manifest.txt", "w", encoding="utf-8") as mf:
        for i, (ts, jpg) in enumerate(ring):
            (out / f"frame_{i:04d}.jpg").write_bytes(jpg)
            mf.write(f"frame_{i:04d}.jpg\t{ts:.3f}\n")
    print(f"[dump] {len(ring)} frames -> {out}")


if __name__ == "__main__":
    main()
