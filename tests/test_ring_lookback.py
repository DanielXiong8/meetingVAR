"""300 秒回溯缓冲语义与边界测试（model-free：可控 ASR + 直流标记音频）。

时间轴定义（与实现严格对应，见 docs/eval_protocol.md）：
  - 绝对时间戳：Session 内部统一使用（live=墙钟；replay=模拟基点+偏移）；
  - 会议相对秒：pool JSON 的 ts / audio 字段（绝对时间戳 − 会话起点）；
  - 环形缓冲：固定 1 秒一格（16bit 16kHz = 32000 字节/格），保留 RING_SECONDS 秒。

被测承诺：
  1. 每格时间戳与其音频长度严格一致（ts 间隔 1.0s ⇔ 音频 32000 字节）；
  2. 触发时保存 [trigger-LOOKBACK, trigger] 已经到达的数据（不含未来数据），
     格对齐下界 = trigger-LOOKBACK，上界 = floor(trigger)-1；
  3. 无重复拼接、无缺口、无越界（标记音频逐半秒解码后必须连续递增）；
  4. 缓冲区容量 = RING_SECONDS 格，旧数据被正确淘汰（不会保留过早数据）。
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import meetingvar.pipeline as pipeline_mod  # noqa: E402
from _fakes import FakeASR, assert_contiguous_levels, decode_marker_halves, install_fake_asr, marker_pcm  # noqa: E402
from meetingvar import config  # noqa: E402
from meetingvar.pipeline import Session  # noqa: E402

T0 = 2_000_000.0  # 绝对轴起点（远离墙钟）
BYTES_PER_SECOND = 32000   # 16bit 16kHz：每秒 32000 字节
SAMPLES_PER_SECOND = 16000  # 16bit 单声道：每秒 16000 采样
LOOKBACK = config.LOOKBACK_SECONDS

# (喂入总时长秒, 每块秒数) —— 块长覆盖 live 抖动(0.5s)、replay(5s)、不均匀(3.7s)
SHAPES = [("live", 0.5), ("replay", 5.0), ("uneven", 3.7)]
# 触发点：早期/首块/回溯边界/保留上限边界/长时运行/非整秒
OFFSETS = [3.0, 8.0, 299.0, 300.0, 301.0, 599.0, 600.0, 601.0, 900.0, 305.5]


class RingLookbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_ring_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    # ---- 运行辅助 ----
    def _feed_until_trigger(self, sid: str, total_s: int, chunk_s: float, trigger_s: float):
        """喂入恰好 trigger_s 秒（块长 chunk_s，末块可为残块），在触发点手动触发（L3）。

        语义（与实现严格一致）：只保存触发时刻**已经到达**的数据，不含任何"未来"音频。
        """
        fake = FakeASR(default=[])  # 无转写：本测试只测缓冲/片段音频语义
        install_fake_asr(pipeline_mod, fake)
        s = Session(sid, terms=[], focus_docs=None, device="cpu", echo=False)
        pcm = marker_pcm(total_s)
        bpc = int(round(chunk_s * BYTES_PER_SECOND))
        target = int(round(trigger_s * BYTES_PER_SECOND))
        fed = 0
        while fed < target:
            take = min(bpc, target - fed)
            s.ingest_pcm(pcm[fed : fed + take], T0 + fed / BYTES_PER_SECOND)
            fed += take
        self.assertEqual(fed, target, "喂入长度必须等于触发点")
        clip_id = s.trigger_manual()
        self.assertIsNotNone(clip_id, "手动 VAR 必须产生片段")
        s.finish()
        return s, clip_id

    @staticmethod
    def _expected(trig: float) -> tuple[list[int], int]:
        """固定语义下的期望标记序列（半秒粒度）与期望采样数。

        片段 = [trigger-LOOKBACK, trigger] 内已到达的 1s 整格 + 不足 1s 的尾块；
        下界按格对齐上取整（ceil(trigger-LOOKBACK)），尾块只贡献末半个/不足半秒。
        """
        first = max(0, math.ceil(trig - LOOKBACK - 1e-9))
        levels: list[int] = []
        for k in range(first, math.floor(trig)):
            levels.extend([k, k])
        if trig - math.floor(trig) > 1e-9:
            levels.append(math.floor(trig))  # 尾块（解码只含完整半块）
        n_samples = int(round((trig - first) * SAMPLES_PER_SECOND))
        return levels, n_samples

    def _clip(self, s: Session, clip_id: str):
        c = json.loads((config.POOL_DIR / s.session_id / f"{clip_id}.json").read_text(encoding="utf-8"))
        return c, Path(c["audio"]["path"])

    # ---- 1. 时间戳/容量不变量 ----
    def test_capacity_invariant_covers_lookback(self) -> None:
        """设计不变量：环形缓冲保留时长必须 ≥ 回溯窗（config 层约束）。"""
        self.assertGreaterEqual(
            config.RING_SECONDS, config.LOOKBACK_SECONDS,
            "RING_SECONDS 必须 ≥ LOOKBACK_SECONDS，否则回溯证据会被静默截短",
        )

    def test_ring_item_ts_matches_audio_length(self) -> None:
        """每格时间戳间隔 1.0s，且该格音频恰为 1.0s → 时间戳与音频长度一致。"""
        s, _ = self._feed_until_trigger("ring_unit", total_s=20, chunk_s=5.0, trigger_s=20.0)
        ring = list(s._ring)
        self.assertGreater(len(ring), 0)
        for (t0, b0), (t1, _b1) in zip(ring, ring[1:]):
            self.assertAlmostEqual(t1 - t0, 1.0, places=6, msg="格间时间戳必须 1.0s")
            self.assertEqual(len(b0), BYTES_PER_SECOND, "每格音频必须恰为 1s（32000 字节）")
        self.assertEqual(len(ring[-1][1]), BYTES_PER_SECOND)

    # ---- 2. 触发窗口 / 连续性 ----
    def test_trigger_window_and_continuity(self) -> None:
        """形状 × 触发点全组合：窗口边界正确、序列连续（无重复/缺口/越界）。"""
        for shape_name, chunk_s in SHAPES:
            for trig in OFFSETS:
                with self.subTest(shape=shape_name, trigger=trig):
                    sid = f"ring_{shape_name}_{int(trig*10)}"
                    s, clip_id = self._feed_until_trigger(sid, total_s=int(math.ceil(trig)), chunk_s=chunk_s, trigger_s=trig)
                    _c, wav = self._clip(s, clip_id)
                    halves, n_samples = decode_marker_halves(wav)
                    expected, n_expected = self._expected(trig)
                    # 期望：只保存触发前已到达的数据；下界 = trigger-LOOKBACK（格对齐上取整）
                    self.assertEqual(halves, expected, "音频标记序列不连续（重复/缺口/越界）或窗口边界不符")
                    # 音频长度与时间戳相互一致
                    self.assertEqual(n_samples, n_expected)
                    # 不含任何"未来"音频（越界读取）
                    self.assertLess(max(halves), trig)
                    # 也不能早于回溯窗（旧数据不得被错误保留）
                    self.assertGreaterEqual(min(halves), max(0, trig - LOOKBACK - 1))
                    # 回溯上限：不多存（含尾块最多触发点-下界 ≤ 300s+残块）
                    self.assertLessEqual(n_samples / SAMPLES_PER_SECOND, LOOKBACK + 1e-6)

    def test_clip_not_silent_when_triggered_early(self) -> None:
        """缓冲未填满时触发：片段必须包含从会话起点到触发的全部音频（有信号）。"""
        s, clip_id = self._feed_until_trigger("ring_early", total_s=3, chunk_s=0.5, trigger_s=3.0)
        _c, wav = self._clip(s, clip_id)
        halves, n = decode_marker_halves(wav)
        assert_contiguous_levels(self, halves, 0, 2)
        self.assertEqual(n, 3 * SAMPLES_PER_SECOND)

    # ---- 3. 容量与淘汰 ----
    def test_capacity_and_eviction_after_long_run(self) -> None:
        """运行 900s：缓冲恰好保留最近 RING_SECONDS 格，过早数据被淘汰。"""
        s, clip_id = self._feed_until_trigger("ring_cap", total_s=900, chunk_s=1.0, trigger_s=900.0)
        ring = list(s._ring)
        self.assertEqual(len(ring), config.RING_SECONDS, f"缓冲容量应为 {config.RING_SECONDS} 格")
        oldest_rel = ring[0][0] - T0
        self.assertAlmostEqual(oldest_rel, 900.0 - config.RING_SECONDS, places=6, msg="最旧格 = 当前-保留时长")
        # 片段窗口只含 [600, 900)，不含被淘汰的 0..299
        _c, wav = self._clip(s, clip_id)
        halves, n = decode_marker_halves(wav)
        assert_contiguous_levels(self, halves, 600, 899)
        self.assertEqual(n, 300 * SAMPLES_PER_SECOND)

    def test_eviction_boundary_does_not_leak_old_audio(self) -> None:
        """601s 触发（刚发生一次淘汰）：片段下界仍是 301，不得残留被淘汰内容。"""
        s, clip_id = self._feed_until_trigger("ring_evict", total_s=601, chunk_s=1.0, trigger_s=601.0)
        _c, wav = self._clip(s, clip_id)
        halves, _n = decode_marker_halves(wav)
        assert_contiguous_levels(self, halves, 301, 600)


if __name__ == "__main__":
    unittest.main()
