"""双源混音纯函数测试（model-free）：时间偏移摆放 / 补零 / 饱和相加 / 裁头。

背景：在线会议场景下"远端声音走系统环回、自己声音走麦克风"，
DualCapture 用 mix_window 把两路按墙钟时间戳叠进同一窗口。
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.capture.audio import mix_window  # noqa: E402

RATE = 16000


def pcm(*vals: int) -> bytes:
    return struct.pack("<" + "h" * len(vals), *vals)


def samples(buf: bytes) -> tuple:
    return struct.unpack("<" + "h" * (len(buf) // 2), buf)


class MixWindowTests(unittest.TestCase):
    def test_single_source_at_window_start(self) -> None:
        out = mix_window([(10.0, pcm(100, 200))], 10.0, 4 * 2)
        self.assertEqual(samples(out), (100, 200, 0, 0))

    def test_offset_places_samples_at_right_position(self) -> None:
        # 数据从窗口内 +0.5s 处开始（8000 采样）
        total = 8002 * 2
        out = mix_window([(10.5, pcm(100, 200))], 10.0, total)
        vals = samples(out)
        self.assertEqual(vals[:8000], (0,) * 8000, "前半必须补零")
        self.assertEqual(vals[8000:], (100, 200))

    def test_two_sources_sum_with_saturation(self) -> None:
        out = mix_window([(10.0, pcm(100, 32767)), (10.0, pcm(200, 32767))], 10.0, 2 * 2)
        self.assertEqual(samples(out), (300, 32767), "相加且正溢出饱和到 32767")

    def test_negative_sum_saturates(self) -> None:
        out = mix_window([(10.0, pcm(-20000, -32768)), (10.0, pcm(-20000, -32768))], 10.0, 2 * 2)
        self.assertEqual(samples(out), (-32768, -32768))

    def test_data_earlier_than_window_gets_head_trimmed(self) -> None:
        # 数据时间戳早于窗口 0.5s：前 8000 采样应被裁掉，只剩最后两个 (300, 400)
        long_pcm = pcm(*([7] * 8000 + [300, 400]))
        out = mix_window([(9.5, long_pcm)], 10.0, 2 * 2)
        self.assertEqual(samples(out), (300, 400))

    def test_empty_returns_silence_of_requested_length(self) -> None:
        self.assertEqual(mix_window([], 10.0, 3 * 2), pcm(0, 0, 0))

    def test_disjoint_sources_no_clipping_ghost(self) -> None:
        out = mix_window([(10.0, pcm(100)), (10.001, pcm(200))], 10.0, 60 * 2)
        vals = samples(out)
        self.assertEqual(vals[0], 100)
        self.assertEqual(vals[16], 200, "16000Hz 下 1ms ≈ 16 采样")


if __name__ == "__main__":
    unittest.main()
