"""Low-cost screen evidence: compressed frames, timed retention, bounded memory."""
from __future__ import annotations

import base64
import io
import threading
import time
from collections import deque
from pathlib import Path

from .. import config


class ScreenCapture:
    def __init__(self, fps=None, buffer_frames=None, *, retention_seconds=None, max_bytes=None, max_width=None):
        self._fps = float(config.SCREEN_FPS if fps is None else fps)
        self._retention = float(config.LOOKBACK_SECONDS + 30 if retention_seconds is None else retention_seconds)
        self._max_bytes = int(config.SCREEN_MAX_BYTES if max_bytes is None else max_bytes)
        self._max_width = int(config.SCREEN_MAX_WIDTH if max_width is None else max_width)
        if self._fps <= 0 or self._retention <= 0 or self._max_bytes <= 0 or self._max_width <= 0:
            raise ValueError("Screen capture limits must be positive")
        if buffer_frames is not None and buffer_frames < 1:
            raise ValueError("buffer_frames must be positive")
        self._frame_limit = buffer_frames
        self.ring = deque()
        self._lock = threading.Lock()
        self._bytes = 0
        self._last_success = None
        self._last_frame = None
        self._error = ""
        self._limited = False
        self._preview = None
        self._stop = threading.Event()
        self._start_ok = threading.Event()
        self._startup_error = None
        self.backend = ""
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if not self._start_ok.wait(timeout=5):
            self.stop()
            raise RuntimeError("录屏初始化超时；请检查屏幕采集权限")
        if self._startup_error is not None:
            raise RuntimeError("录屏初始化失败") from self._startup_error

    def _make_grabber(self):
        try:
            import dxcam
            cam = dxcam.create(output_color="RGB")
            if cam is None:
                raise RuntimeError("dxcam.create returned None")
            self.backend = "dxcam"
            return cam.grab, cam.release
        except Exception as exc:
            print(f"[screen] dxcam 不可用（{exc}），退 mss", flush=True)
            import mss
            from PIL import Image
            sct = mss.mss()
            mon = sct.monitors[1]
            self.backend = "mss"
            def grab():
                img = sct.grab(mon)
                return Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            return grab, sct.close

    def _store_frame(self, ts, jpg, preview=None):
        with self._lock:
            if not self.ring or self.ring[-1][1] != jpg:
                self.ring.append((ts, jpg))
                self._bytes += len(jpg)
                self._last_frame = ts
            if preview is not None:
                self._preview = preview
            cutoff = ts - self._retention
            # Keep the preceding frame: it defines screen state at the boundary.
            while len(self.ring) > 1 and self.ring[1][0] <= cutoff:
                self._bytes -= len(self.ring.popleft()[1])
            while self.ring and (self._bytes > self._max_bytes or
                    (self._frame_limit is not None and len(self.ring) > self._frame_limit)):
                self._bytes -= len(self.ring.popleft()[1])
                self._limited = True
            self._last_success = ts
            self._error = ""

    def _observe(self, grab):
        try:
            frame = grab()
            ts = time.time()
            if frame is not None:
                from PIL import Image
                img = frame if isinstance(frame, Image.Image) else Image.fromarray(frame)
                jpg = self._encode(img, self._max_width)
                preview = self._encode(img, 480)
                self._store_frame(ts, jpg, preview)
            else:
                with self._lock:
                    # DXGI returns None for an unchanged desktop, not a new frame.
                    if self._last_frame is not None and not self._error:
                        self._last_success = ts
            return True
        except Exception as exc:
            with self._lock:
                already_failed = bool(self._error)
                last_success = self._last_success
            if not already_failed:
                try:
                    from PIL import Image, ImageDraw
                    missing = Image.new("RGB", (960, 540), (23, 33, 45))
                    ImageDraw.Draw(missing).text((24, 24), "Screen capture interrupted", fill="white")
                    self._store_frame(time.time(), self._encode(missing), self._encode(missing, 480))
                except Exception:
                    pass
            with self._lock:
                self._last_success = last_success
                self._error = f"{type(exc).__name__}: {exc}"[:240]
            return False

    def _loop(self):
        release = None
        try:
            grab, release = self._make_grabber()
            if not self._observe(grab):
                raise RuntimeError(self._error)
        except Exception as exc:
            self._startup_error = exc
            self._start_ok.set()
            if release:
                release()
            return
        self._start_ok.set()
        try:
            while not self._stop.wait(1.0 / self._fps):
                self._observe(grab)
        finally:
            try:
                release()
            except Exception:
                pass

    @staticmethod
    def _encode(frame, max_width=None):
        from PIL import Image
        img = frame if isinstance(frame, Image.Image) else Image.fromarray(frame)
        width = config.SCREEN_MAX_WIDTH if max_width is None else max_width
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))), Image.Resampling.BILINEAR)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=config.SCREEN_JPEG_QUALITY)
        return buf.getvalue()

    def frames_since(self, since_ts):
        with self._lock:
            frames = list(self.ring)
        before = [item for item in frames if item[0] < since_ts]
        return before[-1:] + [item for item in frames if item[0] >= since_ts]

    def get_status(self):
        with self._lock:
            return {"backend": self.backend, "fps_target": self._fps,
                    "max_width": self._max_width, "frame_count": len(self.ring),
                    "buffer_bytes": self._bytes, "budget_bytes": self._max_bytes,
                    "oldest_ts": self.ring[0][0] if self.ring else None,
                    "last_frame_ts": self._last_frame, "last_success_ts": self._last_success,
                    "limited": self._limited, "error": self._error,
                    "preview": ("data:image/jpeg;base64," + base64.b64encode(self._preview).decode("ascii")) if self._preview else None}

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=3)

    def dump_frames(self, frames, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "manifest.txt", "w", encoding="utf-8") as mf:
            for i, (ts, jpg) in enumerate(frames):
                (out_dir / f"frame_{i:04d}.jpg").write_bytes(jpg)
                mf.write(f"frame_{i:04d}.jpg\t{ts:.3f}\n")
        return len(frames)
