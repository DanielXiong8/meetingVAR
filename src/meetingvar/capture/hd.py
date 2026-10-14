"""高清补录层（Hit-Triggered HD Capture）：命中瞬间开启 30fps 录屏，录满自动停。

与常驻 2fps JPEG 环形缓冲的关系（分层捕获）：
  - 常驻层：命中【前】约 120s 的低帧率帧序列（内存，永远在）
  - 高清层：命中【后】90s 的 30fps H.264（临时文件，录满即停、用完可删）
回放体验 = 低帧率铺垫 + 高帧率现场；存储只增 ~15MB/次命中（mp4 本体）。

实现：mss 高频抓屏 → 原始 RGB 帧写入临时目录（不做逐帧编码，避免阻塞采集线程）
→ 独立 ffmpeg 进程把 rawvideo 帧序列合成为 mp4 → 与 Clip 关联。
raw 帧无自描述头，尺寸/帧率/帧数落在 meta.json（异常退出后仍可再合成）。
合成失败时**保留原始帧**并如实报错，不假装成功。
"""
from __future__ import annotations

import json
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
        self._frame_size: tuple[int, int] | None = None
        self._elapsed: float = 0.0

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
        start = time.time()
        deadline = start + self.duration
        n = 0
        with mss.mss() as sct:
            mon = sct.monitors[1]
            while time.time() < deadline and not self._stop.is_set():
                t0 = time.time()
                try:
                    img = sct.grab(mon)
                    self._frame_size = tuple(img.size)
                    # 纯写盘、无编码：采集线程不阻塞；合成时按 rawvideo 解码
                    (self.session_dir / f"f_{n:05d}.rgb").write_bytes(img.rgb)
                    n += 1
                except Exception:
                    pass
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)
        self._elapsed = max(time.time() - start, 1e-3)
        self._write_meta(n)
        self._synthesize(n)

    def _actual_fps(self, frames: int) -> float:
        """实测帧率 = 帧数/实际录制时长（≤ 标称值）。

        抓屏跟不上标称 30fps 时（大屏 + raw 写盘），若仍按标称帧率合成，
        视频时长会短于真实时长、与证据时间轴错位——按实测值合成才是诚实的。
        """
        if frames <= 1 or self._elapsed <= 0.05:
            return float(self.fps)
        return min(float(self.fps), frames / self._elapsed)

    def _write_meta(self, frames: int) -> None:
        """raw 帧的自描述元数据：异常退出后仍能凭它重新合成 mp4。"""
        if not self.session_dir:
            return
        w, h = self._frame_size or (0, 0)
        meta = {
            "format": "rgb24", "width": w, "height": h, "frames": frames,
            "fps_nominal": self.fps, "fps_actual": round(self._actual_fps(frames), 2),
            "elapsed_s": round(self._elapsed, 2),
        }
        (self.session_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _synthesize(self, frames: int) -> None:
        """raw RGB 帧序列 → mp4（image2 + rawvideo 输入）。失败则保留帧、如实报错。"""
        ff = _ffmpeg()
        if not ff or frames == 0 or not self._frame_size:
            print(f"[hd] 跳过合成（ffmpeg={'有' if ff else '无'}，帧数={frames}）：保留原始帧于 {self.session_dir}")
            return
        w, h = self._frame_size
        fps = self._actual_fps(frames)
        out = self.session_dir / "hd_clip.mp4"
        cmd = [
            ff, "-y", "-v", "error",
            "-f", "image2", "-framerate", f"{fps:.3f}",
            "-c:v", "rawvideo", "-pix_fmt", "rgb24", "-video_size", f"{w}x{h}",
            "-i", str(self.session_dir / "f_%05d.rgb"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(out),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300)
        except Exception as e:  # noqa: BLE001
            print(f"[hd] 合成失败（进程异常: {e}）：保留原始帧于 {self.session_dir}")
            return
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            tail = (r.stderr or b"")[-300:].decode("utf-8", "replace")
            print(f"[hd] 合成失败（ffmpeg rc={r.returncode}）: {tail}；保留原始帧于 {self.session_dir}")
            return
        for p in self.session_dir.glob("f_*.rgb"):
            p.unlink()
        print(
            f"[hd] 高清片段合成完成: {out}（{frames} 帧 / 实测 {fps:.1f}fps，"
            f"标称 {self.fps}fps，实际 {self._elapsed:.1f}s）"
        )
