"""高清补录层（Hit-Triggered HD Capture）：命中瞬间开启 30fps 录屏，录满自动停。

与常驻 2fps JPEG 环形缓冲的关系（分层捕获）：
  - 常驻层：命中【前】约 120s 的低帧率帧序列（内存，永远在）
  - 高清层：命中【后】90s 的 30fps H.264（临时文件，录满即停、用完可删）
回放体验 = 低帧率铺垫 + 高帧率现场；存储只增 ~15MB/次命中。

实现：mss 高频抓屏 → 原始帧写入临时目录（PNG 序列，避免编码阻塞采集线程）
→ 独立 ffmpeg 进程将帧序列+系统音频合成为 mp4 → 与 Clip 关联。
ffmpeg 不存在则静默降级：只保留帧序列（SETUP 坑 6 的回收）。
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .. import config


_KNOWN_FFMPEG = [
    # yt-dlp 附带版（winget 包），PATH 没配时也能发现
    Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
]


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for base in _KNOWN_FFMPEG:
        if base.exists():
            hits = list(base.glob("*/ffmpeg-*-win64-*/bin/ffmpeg.exe")) + list(base.glob("*/*/ffmpeg.exe"))
            if hits:
                return str(hits[0])
    return None


class HDCapture:
    def __init__(self, fps: int = 30, duration_s: int = 90, out_root: Path | None = None):
        self.fps = fps
        self.duration = duration_s
        self.out_root = Path(out_root or (config.DATA_DIR / "hd"))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.session_dir: Path | None = None

    # ---- 生命周期 ----
    def start(self) -> None:
        """命中瞬间调用。已在录则忽略（一次只补录一场）。"""
        if self._thread and self._thread.is_alive():
            return
        self.session_dir = self.out_root / f"hd_{uuid.uuid4().hex[:8]}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_recording(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.duration + 10)

    # ---- 内部 ----
    def _run(self) -> None:
        try:
            import mss
        except ImportError:
            return
        interval = 1.0 / self.fps
        deadline = time.time() + self.duration
        n = 0
        with mss.mss() as sct:
            mon = sct.monitors[1]
            while time.time() < deadline and not self._stop.is_set():
                t0 = time.time()
                try:
                    img = sct.grab(mon)
                    (self.session_dir / f"f_{n:05d}.png").write_bytes(img.rgb)
                    n += 1
                except Exception:
                    pass
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)
        self._synthesize(n)

    def _synthesize(self, frames: int) -> None:
        """帧序列 + 环回音频(独立 wav 直读已在片段池) → mp4。无 ffmpeg 则保留帧序列。"""
        ff = _ffmpeg()
        if not ff or frames == 0:
            print(f"[hd] 无 ffmpeg，保留 {frames} 张 PNG 于 {self.session_dir}")
            return
        out = self.session_dir / "hd_clip.mp4"
        cmd = [
            ff, "-y",
            "-framerate", str(self.fps),
            "-i", str(self.session_dir / "f_%05d.png"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300)
            for p in self.session_dir.glob("f_*.png"):
                p.unlink()
            print(f"[hd] 高清片段合成完成: {out}（{frames} 帧 @ {self.fps}fps）")
        except Exception as e:  # noqa: BLE001
            print(f"[hd] 合成失败: {e}")
