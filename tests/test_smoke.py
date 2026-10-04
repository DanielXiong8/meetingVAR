"""不依赖模型、音频设备或网络的 MVP 契约回归测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.monitor.hotwords import HotwordMonitor  # noqa: E402
from meetingvar.pool.store import Pool  # noqa: E402
from meetingvar.report.generate import _fmt_ts  # noqa: E402


class MvpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = root
        config.POOL_DIR = root / "pool"
        config.CLIPS_DIR = root / "clips"

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        self.tmp.cleanup()

    def test_hotword_debounce(self) -> None:
        monitor = HotwordMonitor(["S3"], debounce_seconds=10)
        self.assertEqual(monitor.check("提到 S3", now=100), ["S3"])
        self.assertEqual(monitor.check("再次提到 s3", now=105), [])
        self.assertEqual(monitor.check("再次提到 s3", now=111), ["S3"])

    def test_pool_persists_screen_frames(self) -> None:
        pool = Pool("smoke")
        clip_id = pool.add_clip(
            "L1",
            "热词:S3",
            b"RIFF",
            [{"ts": 12.0, "speaker": "S1", "text": "S3"}],
            screens=[(12.0, b"jpeg")],
        )
        clip = pool.list_clips()[0]
        self.assertEqual(clip["clip_id"], clip_id)
        self.assertEqual(clip["audio"]["start_ts"], 12.0)
        self.assertEqual(len(clip["screen"]["keyframes"]), 1)
        self.assertTrue((config.CLIPS_DIR / clip_id / "screens" / "frame_0000.jpg").exists())

    def test_relative_timestamp_format(self) -> None:
        self.assertEqual(_fmt_ts(0), "00:00:00")
        self.assertEqual(_fmt_ts(3723), "01:02:03")


if __name__ == "__main__":
    unittest.main()
