"""低帧率录屏环形缓冲（C4 改造）：线程化，dxcam 优先 / mss 兜底，缓冲存 JPEG 字节。"""
from __future__ import annotations

import io
import threading
import time
from collections import deque
from pathlib import Path

from .. import config


class ScreenCapture:
    def __init__(self, fps: float = config.SCREEN_FPS, buffer_frames: int = config.SCREEN_BUFFER_FRAMES):
        self._fps = fps
        self.ring: deque[tuple[float, bytes]] = deque(maxlen=buffer_frames)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.backend = ""
        self._start_ok = threading.Event()
        self._startup_error = None
        self._thread.start()
        if not self._start_ok.wait(timeout=5):
            self.stop()
            raise RuntimeError("录屏初始化超时；可用 --no-screen 仅采集音频")
        if self._startup_error is not None:
            raise RuntimeError("录屏初始化失败；可用 --no-screen 仅采集音频") from self._startup_error

    def _make_grabber(self):
        try:
            import dxcam

            cam = dxcam.create(output_color="RGB")
            if cam is None:
                raise RuntimeError("dxcam.create() 返回 None")
            self.backend = "dxcam"

            def grab():
                return cam.grab()

            return grab, cam.release
        except Exception as e:  # noqa: BLE001
            print(f"[screen] dxcam 不可用（{e}），退 mss")
            import mss
            from PIL import Image

            sct = mss.mss()
            mon = sct.monitors[1]
            self.backend = "mss"

            def grab():
                img = sct.grab(mon)
                return Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

            return grab, sct.close

    def _loop(self) -> None:
        try:
            grab, release = self._make_grabber()
        except Exception as exc:
            self._startup_error = exc
            self._start_ok.set()
            return
        self._start_ok.set()
        interval = 1.0 / self._fps
        while not self._stop.is_set():
            loop_start = time.time()
            try:
                frame = grab()
                if frame is not None:
                    self.ring.append((time.time(), self._encode(frame)))
            except Exception:
                pass
            dt = time.time() - loop_start
            if dt < interval:
                time.sleep(interval - dt)
        try:
            release()
        except Exception:
            pass

    @staticmethod
    def _encode(frame) -> bytes:
        from PIL import Image

        img = frame if isinstance(frame, Image.Image) else Image.fromarray(frame)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()

    def frames_since(self, since_ts: float) -> list[tuple[float, bytes]]:
        return [(t, j) for t, j in list(self.ring) if t >= since_ts]

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def dump_frames(self, frames: list[tuple[float, bytes]], out_dir: Path) -> int:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "manifest.txt", "w", encoding="utf-8") as mf:
            for i, (ts, jpg) in enumerate(frames):
                (out_dir / f"frame_{i:04d}.jpg").write_bytes(jpg)
                mf.write(f"frame_{i:04d}.jpg\t{ts:.3f}\n")
        return len(frames)
