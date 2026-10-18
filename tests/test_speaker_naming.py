"""说话人点名测试（model-free）：新说话人询问 / 追溯改名 / 跳过 /
声纹名册（自动识别、跨会话持久化与质心合并） / 已存片段同步改名。
"""
from __future__ import annotations

import json
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
from meetingvar.memory.speaker_book import SpeakerBook  # noqa: E402
from meetingvar.pipeline import Session  # noqa: E402

T0 = 9_000_000.0


def vec(base: int, dim: int = 8, noise: float = 0.0, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    return [(1.0 if i == base else 0.0) + rnd.uniform(-noise, noise) for i in range(dim)]


def seg(start, end, text, emb, speaker="spk0"):
    return {"start": start, "end": end, "speaker": speaker, "text": text, "emb": emb}


class NamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_name_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    def _session(self, per_call, book=None, terms=None) -> Session:
        install_fake_asr(pipeline_mod, FakeASR(per_call=per_call))
        return Session("name_x", terms=terms or [], focus_docs=None, device="cpu",
                       echo=False, speaker_book=book)

    @staticmethod
    def _drive(s: Session) -> None:
        s.ingest_pcm(marker_pcm(10), T0)
        s.ingest_pcm(marker_pcm(10, offset_seconds=10), T0 + 10)

    def test_new_speaker_queues_question_with_sample(self) -> None:
        s = self._session([[seg(1, 3, "大家好先同步一下进度", vec(0, seed=1))]])
        self._drive(s)
        prompt = s.next_question_prompt()
        self.assertIsNotNone(prompt)
        self.assertIn("spk0", prompt)
        self.assertIn("大家好先同步一下进度", prompt)
        self.assertIn("他是谁", prompt)
        self.assertIsNone(s.next_question_prompt(), "同一个问题只提示一次")

    def test_submit_answer_names_retroactively_and_keeps_for_future(self) -> None:
        s = self._session([
            [seg(1, 3, "第一句话内容", vec(0, seed=1)), seg(4, 6, "第二句话内容", vec(0, noise=0.05, seed=2))],
            [seg(1, 3, "第三句话内容", vec(0, noise=0.05, seed=3))],
        ])
        self._drive(s)
        self.assertEqual([l["speaker"] for l in s.transcript], ["spk0", "spk0", "spk0"])
        s.next_question_prompt()
        msg = s.submit_answer("坤鑫")
        self.assertIn("坤鑫", msg)
        self.assertEqual([l["speaker"] for l in s.transcript], ["坤鑫", "坤鑫", "坤鑫"],
                         "命名应追溯改写本场已有行")

        # 再来一块同一说话人：新行直接显示名字（无需再问）
        s.ingest_pcm(marker_pcm(10, offset_seconds=20), T0 + 20)
        self.assertEqual(s.transcript[-1]["speaker"], "坤鑫")
        self.assertIsNone(s.next_question_prompt(), "已命名说话人不再询问")

    def test_skip_keeps_id_and_consumes_question(self) -> None:
        s = self._session([[seg(1, 3, "一段普通发言", vec(0, seed=1))]])
        self._drive(s)
        s.next_question_prompt()
        msg = s.submit_answer("")
        self.assertIn("跳过", msg)
        self.assertEqual(s.transcript[0]["speaker"], "spk0")
        self.assertIsNone(s.next_question_prompt())

    def test_submit_without_question_returns_none(self) -> None:
        s = self._session([])
        self._drive(s)
        self.assertIsNone(s.submit_answer("随便打的"), "无待答点名时空输入应回落到手动 VAR 处理")

    def test_book_auto_match_skips_question(self) -> None:
        book = SpeakerBook(self.tmp / "book.json")
        book.add("坤鑫", vec(0, seed=11))
        s = self._session([[seg(1, 3, "又是这位在说话", vec(0, seed=11))]], book=book)
        self._drive(s)
        self.assertEqual(s.transcript[0]["speaker"], "坤鑫", "名册命中应自动命名")
        self.assertIsNone(s.next_question_prompt(), "自动识别后不再询问")

    def test_book_persists_and_merges_across_sessions(self) -> None:
        path = self.tmp / "book.json"
        book = SpeakerBook(path)
        s1 = self._session([[seg(1, 3, "本场第一次出现", vec(0, seed=21))]], book=book)
        self._drive(s1)
        s1.next_question_prompt()
        s1.submit_answer("坤鑫")
        s1.finish()
        self.assertTrue(path.exists(), "命名后名册应落盘")
        saved = SpeakerBook.load(path)
        self.assertEqual(saved.entries[0]["name"], "坤鑫")
        self.assertEqual(saved.entries[0]["count"], 1)

        # 第二个会话：同一声音自动识别，且质心合并计数 +1（声音漂移自适应）
        book2 = SpeakerBook.load(path)
        s2 = self._session([[seg(1, 3, "第二场又来了", vec(0, noise=0.05, seed=22))]], book=book2)
        self._drive(s2)
        self.assertEqual(s2.transcript[0]["speaker"], "坤鑫")
        s2.finish()
        merged = SpeakerBook.load(path)
        self.assertEqual(merged.entries[0]["count"], 2)

    def test_clip_json_renamed_after_naming(self) -> None:
        s = self._session([[seg(1, 3, "今天讨论测序污染的问题", vec(0, seed=31))]],
                          terms=["测序污染"])
        self._drive(s)
        self.assertEqual(len(s.clips), 1, "热词命中应产生片段")
        clip_path = config.POOL_DIR / s.session_id / f"{s.clips[0]['clip_id']}.json"
        self.assertTrue(clip_path.exists())
        s.next_question_prompt()
        s.submit_answer("坤鑫")
        data = json.loads(clip_path.read_text(encoding="utf-8"))
        self.assertEqual(data["transcript"][0]["speaker"], "坤鑫",
                         "已落盘片段里的说话人应同步改名（厚纪要与证据一致）")


if __name__ == "__main__":
    unittest.main()
