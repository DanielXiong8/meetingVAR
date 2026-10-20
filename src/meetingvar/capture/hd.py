"""高清补录层（Hit-Triggered HD Capture）：命中瞬间开启高频录屏，录满自动停。

与常驻 2fps JPEG 环形缓冲的关系（分层捕获）：
  - 常驻层：命中【前】约 120s 的低帧率帧序列（内存，永远在）
  - 高清层：命中【后】至多 90s 的高帧率 H.264（临时文件，录满即停、用完即删）
回放体验 = 低帧率铺垫 + 高帧率现场；最终产物只增 mp4 本体（MB 级）。

资源约束（用户拍板 2026-10-10）：
  - 帧先**双线性缩到 HD_WIDTH=1280 宽**再落盘 → 临时空间与合成耗时降 ~4 倍；
  - **每场（=进程）临时帧总预算 5GiB（config.HD_TEMP_BUDGET_BYTES），跨多次命中共享，逐帧硬上限**：
    预算折算目标帧率 = 剩余预算 / 帧尺寸 / 补录时长（标称 30fps 只是上限，
    抓屏/写盘跟不上或预算紧则自动降；实际帧率落 meta.json 并用于合成，
    视频时长始终≈真实时长）；预算放不下 1 帧时跳过补录并如实提示。

实现：mss 高频抓屏 → PIL 双线性缩放 → 原始 RGB 帧写入临时目录（不做逐帧编码，
避免阻塞采集线程）→ 独立 ffmpeg 进程把 rawvideo 帧序列合成为 mp4 → 与 Clip 关联。
raw 帧无自描述头，尺寸/帧率/帧数/预算用量落在 meta.json（异常退出后仍可再合成）。
合成失败时**保留原始帧**并如实报错，不假装成功。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import config


_KNOWN_FFMPEG = [
    # yt-dlp 附带版（winget 包）仅作最后兜底；新机器不应依赖它。
    Path.home() / "AppData/Local/Microsoft/WinGet/Packages",
]

FPS_FLOOR = 1.0  # 预算折算的最低帧率；低于此值由硬上限提前结束录制


def _ffmpeg() -> str | None:
    """Find an app-owned ffmpeg first; host PATH/installed apps are last resort."""
    override = os.environ.get("MEETINGVAR_FFMPEG", "").strip()
    if override and Path(override).is_file():
        return str(Path(override))
    candidates = []
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "bin" / "ffmpeg.exe")
    candidates += [
        Path(sys.executable).resolve().parent / "ffmpeg.exe",
        Path(__file__).resolve().parents[3] / "runtime" / "bin" / "ffmpeg.exe",
        Path(__file__).resolve().parents[3] / "bin" / "ffmpeg.exe",
        Path(__file__).resolve().parents[3] / "desktop" / "assets" / "bin" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    try:
        import imageio_ffmpeg

        packaged = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if packaged.is_file():
            return str(packaged)
    except (ImportError, OSError, RuntimeError):
        pass
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
    def __init__(
        self,
        fps: int = 30,
        duration_s: int = 90,
        out_root: Path | None = None,
        max_width: int | None = None,
        temp_budget_bytes: int | None = None,
    ):
        self.fps = fps
        self.duration = duration_s
        self.out_root = Path(out_root or (config.DATA_DIR / "hd"))
        self.max_width = int(max_width or config.HD_WIDTH)          # 帧缩放目标宽（双线性）
        self.temp_budget = int(                              # 每场（=进程）临时帧总预算
            config.HD_TEMP_BUDGET_BYTES if temp_budget_bytes is None else temp_budget_bytes
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.session_dir: Path | None = None
        self._frame_size: tuple[int, int] | None = None
        self._elapsed: float = 0.0
        self._fps_plan: float = float(fps)
        self._used_bytes: int = 0      # 本场（跨多次命中）累计写入的临时帧字节
        self._budget_hit: bool = False
        self._skipped_for_budget: bool = False
        self._scale_warned: bool = False

    # ---- 生命周期 ----
    def start(self) -> None:
        """命中瞬间调用。已在录则忽略（一次只补录一场）。

        先探一帧确定缩放后帧尺寸（~50ms），据此精确判断本场剩余临时预算是否够开录：
        - 放不下 1 帧 → 跳过补录（不留空目录；回溯音频与截屏照常保存）；
        - 放得下 → 按"预算/帧尺寸/补录时长"折算目标帧率（见 _budget_fps），
          运行期还有逐帧硬上限兜底（不超预算一个字节）。
        """
        if self._thread and self._thread.is_alive():
            return
        probed = self._probe_frame_size()
        if probed is None:
            return  # mss 不可用：静默降级
        self._frame_size = probed
        frame_bytes = probed[0] * probed[1] * 3
        remaining = self.temp_budget - self._used_bytes
        if remaining < frame_bytes:
            self._skipped_for_budget = True
            print(
                f"[hd] 本场临时预算已用尽（已写 {self._used_bytes / 1024**3:.2f}GB / "
                f"上限 {self.temp_budget / 1024**3:.2f}GB）：跳过本次高清补录（回溯音频与截屏照常保存）"
            )
            return
        self.session_dir = self.out_root / f"hd_{uuid.uuid4().hex[:8]}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._budget_hit = False
        self._thread = threading.Thread(target=self._run, args=(frame_bytes,), daemon=True)
        self._thread.start()

    def _probe_frame_size(self) -> tuple[int, int] | None:
        """抓一帧确定缩放后尺寸（不落盘）。mss 不可用/抓屏失败返回 None。"""
        try:
            import mss
        except ImportError:
            return None
        try:
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
        except Exception:
            return None
        return self._scaled_size(tuple(shot.size))

    def is_recording(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.duration + 10)

    # ---- 内部 ----
    def _scaled_size(self, size: tuple[int, int]) -> tuple[int, int]:
        """缩放后尺寸（宽≤max_width，保持比例，宽高取偶——H.264 yuv420p 要求）。"""
        w, h = int(size[0]), int(size[1])
        if w > self.max_width:
            h = int(h * self.max_width / w)
            w = self.max_width
        w -= w % 2
        h -= h % 2
        return max(2, w), max(2, h)

    def _to_rgb24(self, shot) -> bytes:
        """原始帧 → 缩放后 rgb24 字节（双线性；同尺寸零拷贝直通）。

        无 pillow 时降级为原尺寸落盘（不缩放）：预算逐帧硬上限仍生效，
        只是单帧更大、可录帧数更少——如实提示，不静默改变记录行为。
        """
        if tuple(shot.size) == self._frame_size:
            return shot.rgb
        try:
            from PIL import Image
        except ImportError:
            if not self._scale_warned:
                print("[hd] 未安装 pillow：跳过缩放（按原尺寸落盘；临时预算仍逐帧硬限制）")
                self._scale_warned = True
            return shot.rgb
        img = Image.frombytes("RGB", tuple(shot.size), shot.rgb)
        img = img.resize(self._frame_size, Image.BILINEAR)
        return img.tobytes()

    def _budget_fps(self, frame_bytes: int, remaining: int) -> float:
        """把剩余临时预算折算成帧率：预算/帧尺寸 = 总帧数，再摊到补录时长上。

        标称 fps 只是上限；同场多次命中共享预算（先到先用），越靠后的命中帧率越低。
        """
        if frame_bytes <= 0:
            return float(self.fps)
        max_frames = remaining / frame_bytes
        planned = max_frames / max(self.duration, 1)
        return max(FPS_FLOOR, min(float(self.fps), planned))

    def _run(self, frame_bytes: int) -> None:
        try:
            import mss
        except ImportError:
            return
        n = 0
        start = time.time()
        with mss.mss() as sct:
            mon = sct.monitors[1]
            try:
                shot = sct.grab(mon)
            except Exception:
                return
            remaining = max(0, self.temp_budget - self._used_bytes)
            self._fps_plan = self._budget_fps(frame_bytes, remaining)
            interval = 1.0 / self._fps_plan
            deadline = start + self.duration
            while time.time() < deadline and not self._stop.is_set():
                t0 = time.time()
                if self._used_bytes + frame_bytes > self.temp_budget:
                    # 仅当"本还会继续录"时才算预算截断（末尾恰好用尽的正常结束不算）
                    self._budget_hit = time.time() < deadline - 0.5 * interval
                    break
                try:
                    data = self._to_rgb24(shot)
                    (self.session_dir / f"f_{n:05d}.rgb").write_bytes(data)
                    n += 1
                    self._used_bytes += len(data)
                except Exception:
                    pass
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)
                try:
                    shot = sct.grab(mon)  # 下一帧
                except Exception:
                    break
        self._elapsed = max(time.time() - start, 1e-3)
        self._write_meta(n, frame_bytes)
        self._synthesize(n)

    def _actual_fps(self, frames: int) -> float:
        """实测帧率 = 帧数/实际录制时长（≤ 标称值）。

        抓屏+缩放耗时跟不上标称帧率时（大屏机型），若仍按标称帧率合成，
        视频时长会短于真实时长、与证据时间轴错位——按实测值合成才是诚实的。
        """
        if frames <= 1 or self._elapsed <= 0.05:
            return float(self.fps)
        return min(float(self.fps), frames / self._elapsed)

    def _write_meta(self, frames: int, frame_bytes: int) -> None:
        """raw 帧的自描述元数据：异常退出后仍能凭它重新合成 mp4。"""
        if not self.session_dir:
            return
        w, h = self._frame_size or (0, 0)
        meta = {
            "format": "rgb24", "width": w, "height": h, "frames": frames,
            "fps_nominal": self.fps, "fps_planned": round(self._fps_plan, 2),
            "fps_actual": round(self._actual_fps(frames), 2),
            "elapsed_s": round(self._elapsed, 2),
            "temp_bytes_this_session": frames * frame_bytes,
            "temp_budget_bytes": self.temp_budget,
            "temp_used_session_total": self._used_bytes,
            "budget_hit": self._budget_hit,
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
        # 完整解码验证（队友分支的加固，采纳）：rc=0 且文件非空仍可能不可解码
        # （moov atom 缺损一类）——解不出来就当失败，源帧绝不删除
        verify_cmd = [ff, "-v", "error", "-i", str(out), "-f", "null", "-"]
        try:
            v = subprocess.run(verify_cmd, capture_output=True, timeout=300)
        except Exception as e:  # noqa: BLE001
            print(f"[hd] 解码验证异常（{e}）：保留原始帧于 {self.session_dir}")
            return
        if v.returncode != 0:
            tail = (v.stderr or b"")[-300:].decode("utf-8", "replace")
            print(f"[hd] 解码验证失败: {tail}；保留原始帧于 {self.session_dir}")
            return
        for p in self.session_dir.glob("f_*.rgb"):
            p.unlink()
        note = "，已达临时预算提前结束" if self._budget_hit else ""
        print(
            f"[hd] 高清片段合成完成: {out}（{w}x{h}，{frames} 帧 / 实测 {fps:.1f}fps，"
            f"视频 {frames / fps:.1f}s；mp4 {out.stat().st_size / 1e6:.1f}MB；"
            f"本场临时累计 {self._used_bytes / 1024**3:.2f}GB/{self.temp_budget / 1024**3:.2f}GB{note}）"
        )
