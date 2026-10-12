"""高清补录层（HDCapture）生命周期测试（可控屏幕替身，不接触真实显示器）。

被测承诺：命中 → 30fps 补录 90s 的**状态机与产物完整性**：
  - 启动条件：只在命中时启动；已在录时重复命中不重复启动；
  - 启动速度：start() 返回后短时间内有首帧；
  - 结束条件：录满 duration 自动停；stop() 立即停（不空等剩余时长）；
  - 产物：帧序列 → mp4（含正确帧数与时长）；帧序列在合成成功后清理；
  - 降级：mss 不可用时不崩、静默退出。

边界声明（不得含糊）：本文件用假屏幕源验证状态机与编码产物，
**真实显示器/真实 1080p 抓屏吞吐尚未端到端验证**（见 docs/eval_protocol.md）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _fakes import install_fake_mss, remove_fake_mss  # noqa: E402
from meetingvar.capture.hd import HDCapture, _ffmpeg  # noqa: E402

FAKE_W, FAKE_H = 64, 48
PREV_MSS = sys.modules.get("mss", "<absent>")


def _ffprobe() -> str | None:
    ff = _ffmpeg()
    if not ff:
        return None
    cand = Path(ff).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(cand) if cand.exists() else None


def probe_video(path: Path) -> dict:
    """ffprobe 读取帧数/时长/分辨率（完整性证据）。"""
    pr = _ffprobe()
    if not pr:
        return {}
    cmd = [
        pr, "-v", "error", "-count_frames",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames,width,height,duration",
        "-of", "json", str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return {"error": out.stderr.strip()}
    st = (json.loads(out.stdout).get("streams") or [{}])[0]
    res = {}
    for k in ("nb_read_frames", "width", "height"):
        if k in st:
            res[k] = int(st[k])
    if "duration" in st:
        res["duration"] = float(st["duration"])
    return res


def wait_until(pred, timeout: float = 15.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class HdLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_hd_"))
        install_fake_mss(FAKE_W, FAKE_H)

    def tearDown(self) -> None:
        if PREV_MSS == "<absent>":
            remove_fake_mss()
        else:
            sys.modules["mss"] = PREV_MSS  # type: ignore[assignment]

    def _hd(self, fps: int = 10, duration_s: int = 2) -> HDCapture:
        return HDCapture(fps=fps, duration_s=duration_s, out_root=self.tmp / "hd")

    # ---- 1. 完整一轮：产出可解码 mp4、帧数与时长正确 ----
    def test_full_run_produces_decodable_mp4_with_expected_length(self) -> None:
        hd = self._hd(fps=10, duration_s=2)
        t0 = time.time()
        hd.start()
        # 启动速度：首帧出现不得超过 2s（假屏幕源，仅测状态机延迟）
        self.assertTrue(
            wait_until(lambda: hd.session_dir and any(hd.session_dir.glob("*")), timeout=2.0),
            "start() 后 2s 内未见任何帧文件",
        )
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0), "录满后线程未退出")
        wall = time.time() - t0
        mp4 = hd.session_dir / "hd_clip.mp4"
        if _ffmpeg() is None:
            self.skipTest("本机无 ffmpeg：仅能验证帧序列降级路径（见 test_degrade_without_ffmpeg）")
        self.assertTrue(mp4.exists(), f"合成 mp4 缺失（ffmpeg 解码失败？目录内容 {list(hd.session_dir.iterdir())}）")
        info = probe_video(mp4)
        self.assertNotIn("error", info, f"mp4 无法解析: {info}")
        self.assertEqual((info.get("width"), info.get("height")), (FAKE_W, FAKE_H), "分辨率与抓屏尺寸不一致")
        frames = info.get("nb_read_frames", 0)
        self.assertGreaterEqual(frames, int(10 * 2 * 0.7), f"帧数过少（丢帧严重）: {info}")
        self.assertLessEqual(frames, int(10 * 2 * 1.3), f"帧数异常偏多: {info}")
        # 视频时长必须与真实录制时长一致（而不是与"帧数/标称帧率"强行对齐）
        self.assertAlmostEqual(info.get("duration", 0.0), wall, delta=0.7, msg="视频时长与真实录制时长不一致")
        self.assertEqual(list(hd.session_dir.glob("f_*")), [], "合成成功后帧序列应被清理（避免重复占盘）")
        # meta.json：raw 帧的自描述（尺寸/帧数/实测帧率），异常退出后可再合成
        meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["width"], meta["height"], meta["format"]), (FAKE_W, FAKE_H, "rgb24"))
        self.assertEqual(meta["frames"], frames)
        self.assertAlmostEqual(meta["fps_actual"], 10.0, delta=1.5, msg="实测帧率记录不准")

    # ---- 2. 重复命中：录制中再次 start() 不重复启动 ----
    def test_repeat_start_while_recording_is_noop(self) -> None:
        hd = self._hd(fps=10, duration_s=2)
        hd.start()
        d1 = hd.session_dir
        hd.start()  # 录制中的第二次命中
        self.assertEqual(hd.session_dir, d1, "录制中重复命中不得另起目录")
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0))
        dirs = [p for p in (self.tmp / "hd").iterdir() if p.is_dir()]
        self.assertEqual(len(dirs), 1, f"重复命中产生了多个录制目录: {dirs}")

    # ---- 3. 提前 stop：立即停，不空等剩余时长 ----
    def test_stop_early_returns_promptly_and_keeps_partial(self) -> None:
        hd = self._hd(fps=10, duration_s=30)  # 若 stop 无效将等待 30s
        hd.start()
        time.sleep(0.6)
        t0 = time.time()
        hd.stop()
        self.assertLess(time.time() - t0, 10.0, "stop() 未立即返回（仍在空等录满）")
        self.assertFalse(hd.is_recording())
        mp4 = hd.session_dir / "hd_clip.mp4"
        if mp4.exists():
            info = probe_video(mp4)
            self.assertLess(info.get("duration", 0.0), 5.0, "提前停止后不应产出超长视频")
        else:
            frames = list(hd.session_dir.glob("f_*"))
            self.assertTrue(frames, "停止后应保留已录帧（降级路径）")

    # ---- 4. 录满后再命中：新目录 ----
    def test_hit_after_finish_starts_new_session_dir(self) -> None:
        hd = self._hd(fps=10, duration_s=1)
        hd.start()
        d1 = hd.session_dir
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0))
        hd.start()
        self.assertNotEqual(hd.session_dir, d1, "上一轮录满后再次命中应另起新目录")
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0))
        dirs = [p for p in (self.tmp / "hd").iterdir() if p.is_dir()]
        self.assertEqual(len(dirs), 2)

    # ---- 5. mss 不可用：静默降级 ----
    def test_missing_mss_degrades_without_crash(self) -> None:
        sys.modules["mss"] = None  # type: ignore[assignment]
        hd = self._hd(fps=10, duration_s=1)
        hd.start()
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=5.0), "无 mss 时应立即退出")
        self.assertEqual(list(hd.session_dir.glob("f_*")), [], "无 mss 时不应产生帧")

    def test_encode_failure_keeps_source_frames(self):
        hd = self._hd()
        hd.session_dir = self.tmp / "failed"
        hd.session_dir.mkdir()
        hd._frame_size = (64, 48)
        raw = hd.session_dir / "f_00000.rgb"
        raw.write_bytes(bytes(64 * 48 * 3))
        failed = subprocess.CompletedProcess([], 1, b"", b"encoder failed")
        with patch("meetingvar.capture.hd._ffmpeg", return_value="fake"), \
             patch("meetingvar.capture.hd.subprocess.run", return_value=failed):
            hd._synthesize(1)
        self.assertTrue(raw.exists())

    def test_undecodable_output_keeps_source_frames(self):
        hd = self._hd()
        hd.session_dir = self.tmp / "corrupt"
        hd.session_dir.mkdir()
        hd._frame_size = (64, 48)
        raw = hd.session_dir / "f_00000.rgb"
        raw.write_bytes(bytes(64 * 48 * 3))
        (hd.session_dir / "hd_clip.mp4").write_bytes(b"corrupt video")
        responses = [subprocess.CompletedProcess([], 0, b"", b""),
                     subprocess.CompletedProcess([], 1, b"", b"decode failed")]
        with patch("meetingvar.capture.hd._ffmpeg", return_value="fake"), \
             patch("meetingvar.capture.hd.subprocess.run", side_effect=responses):
            hd._synthesize(1)
        self.assertTrue(raw.exists())

    def test_missing_ffmpeg_keeps_source_frames(self):
        hd = self._hd()
        hd.session_dir = self.tmp / "no_ffmpeg"
        hd.session_dir.mkdir()
        hd._frame_size = (64, 48)
        raw = hd.session_dir / "f_00000.rgb"
        raw.write_bytes(bytes(64 * 48 * 3))
        with patch("meetingvar.capture.hd._ffmpeg", return_value=None):
            hd._synthesize(1)
        self.assertTrue(raw.exists())


if __name__ == "__main__":
    unittest.main()
