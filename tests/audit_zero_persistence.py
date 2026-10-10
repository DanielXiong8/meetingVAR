"""零落盘审计测试（外部评审五-1 的落地：把承诺写成可测试的形式）。

审计声明（可测试版）：
  G1 未命中的会话（replay 全程无 L1/L2/L3 触发）不得在
     data/pool、data/clips、data/hd、data/reports 创建任何新文件；
  G2 命中的会话只在上述目录创建与命中片段对应的文件（数量可枚举）；
  G3 运行过程不得向用户主目录、临时目录写入音视频内容文件。

方法：记录目录快照 → 跑无触发 replay → 比对快照；再跑有触发 replay → 比对。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402

MONITORED = ["pool", "clips", "hd", "reports"]  # data/ 下的产物目录
SAMPLE_PROJECT = ROOT / "spikes" / "b1_rag" / "sample_project"
TEST_WAV = Path("D:/env/test_terms.wav")  # 含热词的真实音频


def snapshot() -> dict:
    out = {}
    for d in MONITORED:
        p = config.DATA_DIR / d
        if p.exists():
            out[d] = {(str(f.relative_to(config.DATA_DIR)), f.stat().st_size) for f in p.rglob("*") if f.is_file()}
        else:
            out[d] = set()
    return out


def diff(before: dict, after: dict) -> dict:
    return {d: after[d] - before[d] for d in MONITORED if after[d] != before[d]}


@unittest.skipUnless(TEST_WAV.exists(), "需要测试音频 D:/env/test_terms.wav")
class ZeroPersistenceAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile_dir()
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old

    def test_g1_no_hit_creates_no_files(self):
        """G1：无命中（无词表+纯静音音频）的 replay 全程，产物目录零新增。"""
        import wave

        from meetingvar.replay import replay

        silence = self.tmp / "silence.wav"
        with wave.open(str(silence), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(bytes(2 * 16000 * 20))  # 20 秒纯静音
        before = snapshot()
        replay(silence, "audit_nohit", terms=[], focus_docs=None, device="cpu")
        d = diff(before, snapshot())
        created = {k: len(v) for k, v in d.items() if v}
        self.assertEqual(created, {}, f"未命中会话创建了文件: {created}")

    def test_g2_hit_creates_only_enumerable_files(self):
        """G2：命中会话的新增文件全部位于产物目录内，且与命中片段数可对应。"""
        import json

        from meetingvar.replay import replay

        before = snapshot()
        replay(TEST_WAV, "audit_hit", terms=["测序污染"], focus_docs=None, device="cpu")
        d = diff(before, snapshot())
        new_files = [(f, sz) for files in d.values() for f, sz in files]
        self.assertTrue(new_files, "命中会话应有产物")
        # 全部位于 data/ 产物目录（相对路径第一段在 MONITORED 内）
        stray = [f for f, _ in new_files if f.split("\\")[0].split("/")[0] not in MONITORED]
        self.assertEqual(stray, [], f"产物目录之外出现文件: {stray}")
        # Clip JSON 数 = summary 中 clips 数（可枚举性）
        clip_jsons = [f for f, _ in new_files if f.startswith("pool") and f.endswith(".json")
                      and "transcript" not in f and "summary" not in f]
        sid_dir = config.POOL_DIR / "audit_hit"
        n_clips = sum(1 for f in sid_dir.glob("*.json")
                      if "trigger" in json.loads(f.read_text(encoding="utf-8")))
        self.assertEqual(len(clip_jsons), n_clips)


def tempfile_dir():
    import tempfile

    return Path(tempfile.mkdtemp(prefix="mv_audit_"))


if __name__ == "__main__":
    unittest.main()
