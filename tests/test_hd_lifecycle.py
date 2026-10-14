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

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
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


def has_pil() -> bool:
    """PIL 可用性（find_spec 在某些受限环境会抛异常——一律视为不可用）。"""
    try:
        return importlib.util.find_spec("PIL") is not None
    except Exception:  # noqa: BLE001
        return False


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

    def _hd(self, fps: int = 10, duration_s: int = 2, max_width: int = 1280,
            temp_budget_bytes: int | None = None) -> HDCapture:
        return HDCapture(
            fps=fps, duration_s=duration_s, out_root=self.tmp / "hd",
            max_width=max_width, temp_budget_bytes=temp_budget_bytes,
        )

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
        self.assertIsNone(hd.session_dir, "无 mss 时不应创建目录")

    # ---- 6. 双线性缩放到 1280 宽（临时空间与 MB 级产物） ----
    @unittest.skipUnless(
        has_pil(),
        "无 pillow：缩放路径不可用（降级路径由 test_scaling_degrades_without_pillow 覆盖）",
    )
    def test_frames_scaled_to_hd_width(self) -> None:
        """2560×1600 源屏 → 帧缩至 1280×800 落盘（比例保持、宽高取偶）。"""
        install_fake_mss(2560, 1600)
        hd = self._hd(fps=10, duration_s=1)
        hd.start()
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0))
        meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["width"], meta["height"]), (1280, 800), "缩放后尺寸不符")
        self.assertEqual(
            meta["temp_bytes_this_session"], meta["frames"] * 1280 * 800 * 3,
            "落盘帧字节数应为缩放后尺寸（3 字节/像素）",
        )
        mp4 = hd.session_dir / "hd_clip.mp4"
        if _ffmpeg() is not None:
            info = probe_video(mp4)
            self.assertEqual((info.get("width"), info.get("height")), (1280, 800), "mp4 分辨率应为缩放后尺寸")

    def test_scaling_degrades_without_pillow(self) -> None:
        """无 pillow：不缩放仍照常录制，且临时预算硬上限仍生效（如实提示）。"""
        prev = sys.modules.get("PIL", "<absent>")
        sys.modules["PIL"] = None  # type: ignore[assignment]
        try:
            install_fake_mss(2560, 1600)
            frame = 2560 * 1600 * 3
            hd = self._hd(fps=10, duration_s=1, temp_budget_bytes=frame * 4)
            hd.start()
            self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=20.0))
            meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
            self.assertGreater(meta["frames"], 0, "无 pillow 时也应有帧（不做缩放）")
            self.assertLessEqual(meta["temp_used_session_total"], frame * 4, "无 pillow 时预算硬上限仍必须生效")
        finally:
            if prev == "<absent>":
                sys.modules.pop("PIL", None)
            else:
                sys.modules["PIL"] = prev  # type: ignore[assignment]

    def test_small_screen_not_upscaled(self) -> None:
        """源屏窄于 HD_WIDTH 时不放大（避免假清晰度与无谓体积）。"""
        hd = self._hd(fps=10, duration_s=1)  # 源屏 64×48
        hd.start()
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=15.0))
        meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["width"], meta["height"]), (FAKE_W, FAKE_H))

    # ---- 7. 临时预算：折算帧率 / 硬上限 / 跨命中累计 / 用尽跳过 ----
    def test_budget_plans_lower_fps_and_keeps_full_duration(self) -> None:
        """预算只够 20 帧 / 4s → 目标帧率降到 5fps；时长仍覆盖 4s，总量不超预算。"""
        frame = FAKE_W * FAKE_H * 3
        budget = frame * 20
        hd = self._hd(fps=10, duration_s=4, temp_budget_bytes=budget)
        t0 = time.time()
        hd.start()
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=20.0))
        wall = time.time() - t0
        meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertLessEqual(meta["temp_used_session_total"], budget, "临时帧总量必须不超预算")
        self.assertLessEqual(meta["fps_planned"], 5.2, f"预算编排未降低帧率: {meta['fps_planned']}")
        self.assertGreaterEqual(meta["fps_planned"], 4.0)
        self.assertAlmostEqual(wall, 4.0, delta=1.0, msg="预算紧也应覆盖完整补录时长")
        self.assertFalse(meta["budget_hit"])

    def test_budget_hard_cap_and_cumulative_across_hits(self) -> None:
        """同一实例跨多次命中共享预算：逐帧硬上限，用尽后跳过且不留空目录。"""
        frame = FAKE_W * FAKE_H * 3
        hd = self._hd(fps=10, duration_s=2, temp_budget_bytes=frame * 30)  # 够 1.5 轮
        sdirs = []
        for i in range(2):
            hd.start()
            sdirs.append(hd.session_dir)
            self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=20.0), f"第 {i+1} 轮未结束")
            self.assertLessEqual(hd._used_bytes, hd.temp_budget, "累计用量必须不超预算")
        ms = [json.loads((d / "meta.json").read_text(encoding="utf-8")) for d in sdirs]
        self.assertLessEqual(ms[1]["fps_planned"], ms[0]["fps_planned"] - 1.0, "第 2 轮帧率未按剩余预算下调")
        self.assertLessEqual(ms[1]["temp_used_session_total"], frame * 30)
        # 第 3 轮：预算放不下 1 帧 → 跳过、无新目录
        dirs_before = sorted(p.name for p in (self.tmp / "hd").iterdir() if p.is_dir())
        hd.start()
        self.assertTrue(hd._skipped_for_budget, "预算用尽应跳过补录")
        self.assertFalse(hd.is_recording())
        self.assertEqual(sorted(p.name for p in (self.tmp / "hd").iterdir() if p.is_dir()), dirs_before)

    def test_budget_floor_uses_hard_cap(self) -> None:
        """预算只够 3 帧 / 4s：帧率触底 1fps 后由硬上限提前结束，且总量不超预算。"""
        frame = FAKE_W * FAKE_H * 3
        hd = self._hd(fps=10, duration_s=4, temp_budget_bytes=frame * 3)
        hd.start()
        self.assertTrue(wait_until(lambda: not hd.is_recording(), timeout=20.0))
        meta = json.loads((hd.session_dir / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["frames"], 3, f"硬上限应恰好在 3 帧停下: {meta}")
        self.assertTrue(meta["budget_hit"])
        self.assertLessEqual(meta["temp_used_session_total"], frame * 3)

    # ---- 8. 与管线的接线：命中 → 启动 HD → 片段池记录高清目录 ----
    def test_pipeline_hit_starts_hd_and_links_clip_dir(self) -> None:
        import meetingvar.pipeline as pipeline_mod
        from _fakes import FakeASR, install_fake_asr, marker_pcm
        from meetingvar import config
        from meetingvar.pipeline import Session

        tmp = Path(tempfile.mkdtemp(prefix="mv_hd_pipe_"))
        old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        old_asr = pipeline_mod.ASR
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = tmp, tmp / "pool", tmp / "clips"
        try:
            install_fake_asr(pipeline_mod, FakeASR(default=[(1.0, 2.0, "spk0", "测序污染得重测一遍")]))
            s = Session("hd_pipe", terms=["测序污染"], focus_docs=None, device="cpu", echo=False)
            s.hd = HDCapture(fps=10, duration_s=1, out_root=self.tmp / "hd")
            s.ingest_pcm(marker_pcm(20), 1_000_000.0)
            summary = s.finish()          # 内含 hd.stop()（等待补录线程收尾）
            self.assertEqual(summary["clips"], 1)
            clip_files = [
                p for p in (config.POOL_DIR / "hd_pipe").glob("*.json")
                if p.name not in {"transcript.json", "summary.json"}
            ]
            clip = json.loads(clip_files[0].read_text(encoding="utf-8"))
            hd_dir = clip["screen"]["hd_clip_dir"]
            self.assertIsNotNone(hd_dir, "命中片段应关联高清补录目录")
            self.assertTrue(Path(hd_dir).exists())
            if _ffmpeg() is not None:
                self.assertTrue((Path(hd_dir) / "hd_clip.mp4").exists(), "HD 应产出可播放 mp4")
        finally:
            config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = old
            pipeline_mod.ASR = old_asr


if __name__ == "__main__":
    unittest.main()
