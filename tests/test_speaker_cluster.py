"""会话级在线声纹聚类测试（model-free）：不加载任何模型。

覆盖：跨块身份稳定 / 新说话人编号 / 短句规则（不新建、就近放宽、未知兜底）/
首句时长门槛 / 无声纹降级回块内编号 / 管线端到端跨块分人。
"""
from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import meetingvar.pipeline as pipeline_mod  # noqa: E402
from _fakes import FakeASR, install_fake_asr, marker_pcm  # noqa: E402
from meetingvar import config  # noqa: E402
from meetingvar.pipeline import Session  # noqa: E402
from meetingvar.transcribe.speaker import (  # noqa: E402
    MIN_NEW_SPEAKER_SECONDS,
    OnlineSpeakerCluster,
)

T0 = 7_000_000.0


def vec(base: int, dim: int = 8, noise: float = 0.0, seed: int = 0) -> list[float]:
    """确定性假声纹：base 维为主，其余为噪声——同 base 高相似、异 base 低相似。"""
    rnd = random.Random(seed)
    return [(1.0 if i == base else 0.0) + rnd.uniform(-noise, noise) for i in range(dim)]


class ClusterUnitTests(unittest.TestCase):
    def test_empty_embedding_is_unknown(self) -> None:
        c = OnlineSpeakerCluster()
        self.assertEqual(c.assign(None, 3.0), "spk?")
        self.assertEqual(c.assign([], 3.0), "spk?")
        self.assertEqual(c.n_speakers, 0)

    def test_first_tiny_segment_does_not_create_speaker(self) -> None:
        """一句短应答不能定义全场第一个人。"""
        c = OnlineSpeakerCluster()
        self.assertEqual(c.assign(vec(0, seed=1), 0.4), "spk?")
        self.assertEqual(c.n_speakers, 0)
        self.assertEqual(c.assign(vec(0, seed=2), 2.0), "spk0")

    def test_same_speaker_across_chunks_keeps_id(self) -> None:
        c = OnlineSpeakerCluster()
        first = c.assign(vec(0, seed=1), 2.0)
        second = c.assign(vec(0, noise=0.08, seed=2), 3.0)
        self.assertEqual(first, "spk0")
        self.assertEqual(second, "spk0", "同一声纹的跨块出现必须沿用同一编号")
        self.assertEqual(c.n_speakers, 1)

    def test_different_speaker_gets_next_id(self) -> None:
        c = OnlineSpeakerCluster()
        self.assertEqual(c.assign(vec(0, seed=1), 2.0), "spk0")
        self.assertEqual(c.assign(vec(1, seed=2), 2.0), "spk1")
        self.assertEqual(c.assign(vec(0, noise=0.05, seed=3), 2.0), "spk0", "回到第一个人不新建")
        self.assertEqual(c.n_speakers, 2)

    def test_short_segment_attaches_relaxed_never_creates(self) -> None:
        """短句相似度只差一点：放宽归附；差太多：spk?（都不新建说话人）。"""
        c = OnlineSpeakerCluster(threshold=0.55)
        c.assign([1.0, 0.0], 3.0)  # 质心 = [1,0] → spk0
        near = c.assign([0.5, (1 - 0.25) ** 0.5], 0.8)   # cos=0.50 ∈ [0.45, 0.55)
        self.assertEqual(near, "spk0")
        far = c.assign([0.2, (1 - 0.04) ** 0.5], 0.8)    # cos=0.20 < 0.45
        self.assertEqual(far, "spk?")
        self.assertEqual(c.n_speakers, 1, "短句绝不新建说话人")

    def test_long_dissimilar_segment_creates_speaker(self) -> None:
        c = OnlineSpeakerCluster(threshold=0.55)
        c.assign([1.0, 0.0], 3.0)
        v = [0.2, (1 - 0.04) ** 0.5]
        self.assertEqual(c.assign(v, MIN_NEW_SPEAKER_SECONDS), "spk1")
        self.assertEqual(c.n_speakers, 2)

    def test_short_segment_cannot_switch_back_to_inactive_speaker(self) -> None:
        """短句只延长当前发言：B 说话时，靠近 A 的填充词不得被"切回"成 A。"""
        c = OnlineSpeakerCluster(threshold=0.55)
        c.assign([1.0, 0.0, 0.0], 3.0)   # A → spk0
        c.assign([0.0, 1.0, 0.0], 3.0)   # B → spk1（当前活跃变为 B）
        filler_near_a = [0.51, 0.40, 0.76]   # 最近命中 A（0.51∈放宽区）但 A 已不活跃
        self.assertEqual(c.assign(filler_near_a, 0.5), "spk?")
        filler_near_b = [0.40, 0.51, 0.76]   # 命中当前活跃的 B → 归附
        self.assertEqual(c.assign(filler_near_b, 0.5), "spk1")
        self.assertEqual(c.n_speakers, 2)


class PipelineSpeakerTests(unittest.TestCase):
    """端到端：两次摄入（两块 ASR）→ 跨块说话人编号稳定。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_spk_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    def _drive(self, per_call: list) -> Session:
        install_fake_asr(pipeline_mod, FakeASR(per_call=per_call))
        s = Session("spk_e2e", terms=[], focus_docs=None, device="cpu", echo=False)
        s.ingest_pcm(marker_pcm(10), T0)
        s.ingest_pcm(marker_pcm(10, offset_seconds=10), T0 + 10)
        return s

    def test_cross_chunk_speaker_ids_stable(self) -> None:
        per_call = [
            [{"start": 1.0, "end": 3.0, "speaker": "spk0", "text": "第一段话内容",
              "emb": vec(0, seed=11)}],
            [{"start": 1.0, "end": 3.0, "speaker": "spk0", "text": "第二段话内容",
              "emb": vec(0, noise=0.05, seed=12)},
             {"start": 4.0, "end": 6.0, "speaker": "spk0", "text": "第三段话内容",
              "emb": vec(1, seed=13)}],
        ]
        s = self._drive(per_call)
        speakers = [line["speaker"] for line in s.transcript]
        self.assertEqual(speakers, ["spk0", "spk0", "spk1"],
                         f"跨块分人应稳定（块内局部编号全是 spk0，正确结果靠声纹聚类）：{speakers}")

    def test_missing_embedding_falls_back_to_local_speaker(self) -> None:
        per_call = [
            [{"start": 1.0, "end": 3.0, "speaker": "spk7", "text": "没有声纹的一段话"}],
        ]
        s = self._drive(per_call)
        self.assertEqual(s.transcript[0]["speaker"], "spk7",
                         "emb 缺失时逐字沿用块内编号（降级兜底，不被聚类改写）")

    def test_embeddings_enabled_but_too_short_is_unknown(self) -> None:
        """声纹模式开启、片段太短取不到声纹：标 spk?，绝不退回块内局部编号。"""
        fake = FakeASR(per_call=[
            [{"start": 1.0, "end": 1.2, "speaker": "spk0", "text": "嗯嗯一下"}],
        ])
        fake.with_embeddings = True
        install_fake_asr(pipeline_mod, fake)
        s = Session("spk_short", terms=[], focus_docs=None, device="cpu", echo=False)
        s.ingest_pcm(marker_pcm(10), T0)
        s.ingest_pcm(marker_pcm(10, offset_seconds=10), T0 + 10)
        self.assertEqual(s.transcript[0]["speaker"], "spk?",
                         "短碎片不得泄漏块内局部编号（那正是全场 spk0 的病根）")

    def test_short_missing_match_not_invented(self) -> None:
        """短句且不像任何人：标 spk?，不得凭空造出说话人。"""
        per_call = [
            [{"start": 1.0, "end": 3.0, "speaker": "spk0", "text": "第一段正常话",
              "emb": vec(0, seed=21)}],
            [{"start": 1.0, "end": 1.6, "speaker": "spk0", "text": "嗯嗯哦哦",
              "emb": vec(5, seed=22)}],
        ]
        s = self._drive(per_call)
        self.assertEqual(s.transcript[1]["speaker"], "spk?")
        self.assertEqual(s.speaker_cluster.n_speakers, 1)


if __name__ == "__main__":
    unittest.main()
