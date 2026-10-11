"""F1 可追溯性测试：定位替换 / 同形多处 / 连续改写 / 整行原文精确恢复 / termset_id。"""
from __future__ import annotations

import json
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
from meetingvar.transcribe.correct import correct  # noqa: E402

T0 = 5_000_000.0


class LocateReplaceTests(unittest.TestCase):
    """correct() 的定位替换语义（F1：record 与具体出现位置一一对应）。"""

    def test_same_form_twice_in_one_run_replaces_first_only(self) -> None:
        """同一行同形词出现两次：只改第一处（宁漏勿误），record 数=1。"""
        text = "这个测试污染和那个测试污染都要查"
        out, recs = correct(text, ["测序污染"])
        self.assertEqual(out.count("测序污染"), 1, f"只应替换第一处: {out}")
        self.assertEqual(out.count("测试污染"), 1, "第二处保持原文")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "accepted")

    def test_separate_runs_each_get_one_fix(self) -> None:
        """两个独立汉字段（被非汉字隔开）各改一处。"""
        out, recs = correct("测试污染 ok 测试污染", ["测序污染"])
        self.assertEqual(out.count("测序污染"), 2)
        self.assertEqual(len(recs), 2)

    def test_consecutive_two_terms(self) -> None:
        out, recs = correct("基因学习和随习森林", ["机器学习", "随机森林"])
        self.assertEqual(out, "机器学习和随机森林")
        self.assertEqual([r["status"] for r in recs], ["accepted", "accepted"])
        self.assertEqual({r["rule_id"] for r in recs}, {"zh_same_len"})

    def test_replaced_region_not_reattacked_by_chain_term(self) -> None:
        """term in run 守卫：替换后的文本不被链上另一个词再次改写。"""
        out, recs = correct("测试污染检测流程", ["测序污染", "污染检测"])
        self.assertEqual(out, "测序污染检测流程")
        self.assertEqual(len(recs), 1, "污染检测 因已在段内被跳过")


class RewriteTraceTests(unittest.TestCase):
    """会话级可追溯性：ts/termset_id 补全、整行原文快照、transcript.json 携带 rewrites。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_trace_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    def _session(self, sid: str, terms: list[str], segments: list) -> Session:
        install_fake_asr(pipeline_mod, FakeASR(default=segments))
        return Session(sid, terms=terms, focus_docs=None, device="cpu", echo=False)

    def test_records_carry_ts_and_termset_id(self) -> None:
        s = self._session("trace_a", terms=["测序污染"],
                          segments=[(1.0, 3.0, "spk0", "这个测试污染很严重")])
        s.ingest_pcm(marker_pcm(20), T0)
        self.assertEqual(len(s.corrections), 1)
        r = s.corrections[0]
        self.assertIsInstance(r["ts"], float)
        self.assertEqual(r["termset_id"], s.termset_id)
        self.assertTrue(r["termset_id"])  # 纠错词表非空 → 哈希非空（不伪造未知）

    def test_line_snapshot_restores_original_exactly(self) -> None:
        """整行快照恢复：text 换回 orig_text 必须逐字符等于原始 ASR 文本（含未改部分）。"""
        orig = "这个测试污染和那个测试污染都很重要"  # 同形两处，只改第一处
        s = self._session("trace_b", terms=["测序污染"], segments=[(1.0, 3.0, "spk0", orig)])
        s.ingest_pcm(marker_pcm(20), T0)
        self.assertEqual(len(s.rewrite_log), 1)
        entry = s.rewrite_log[0]
        self.assertEqual(entry["orig_text"], orig, "快照必须与原始 ASR 文本逐字符一致")
        self.assertNotEqual(entry["text"], orig)
        restored = entry["text"].replace(entry["records"][0]["dst"], entry["records"][0]["src"], 1)
        self.assertEqual(restored, orig, "逆序撤销（替换回唯一被改片段）必须精确恢复原文")

    def test_transcript_json_carries_rewrites(self) -> None:
        s = self._session("trace_c", terms=["测序污染"],
                          segments=[(1.0, 3.0, "spk0", "这个测试污染很严重"),
                                    (9.0, 11.0, "spk0", "测序污染的结果要重测")])
        s.ingest_pcm(marker_pcm(20), T0)
        summary = s.finish()
        self.assertEqual(summary["clips"], 1)          # 命中会话 → transcript.json 存在
        data = json.loads(Path(summary["transcript_json"]).read_text(encoding="utf-8"))
        self.assertIn("rewrites", data)
        self.assertEqual(len(data["rewrites"]), 1)     # 第二行原本就正确，不进改写日志
        self.assertEqual(data["rewrites"][0]["orig_text"], "这个测试污染很严重")

    def test_termset_id_is_content_hash(self) -> None:
        s1 = self._session("trace_d1", terms=["测序污染", "随机森林"], segments=[])
        s2 = self._session("trace_d2", terms=["随机森林", "测序污染"], segments=[])  # 乱序同集
        s3 = self._session("trace_d3", terms=["测序污染"], segments=[])
        self.assertEqual(s1.termset_id, s2.termset_id, "同内容不同顺序 → 同版本")
        self.assertNotEqual(s1.termset_id, s3.termset_id)
        self.assertEqual(len(s1.termset_id), 12)

    def test_transcribe_export_writes_rewrites_json(self) -> None:
        from meetingvar.replay import replay

        install_fake_asr(pipeline_mod, FakeASR(default=[(1.0, 3.0, "spk0", "这个测试污染很严重")]))
        import wave

        wav = self.tmp / "in.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(marker_pcm(20))
        out = self.tmp / "tx.txt"
        replay(wav, "trace_e", terms=["测序污染"], focus_docs=None, device="cpu",
               transcript_out=out,
               correction_terms=["测序污染"])
        rw = out.with_name("tx.rewrites.json")
        self.assertTrue(rw.exists(), "显式导出应附带改写记录 JSON")
        entries = json.loads(rw.read_text(encoding="utf-8"))
        self.assertEqual(entries[0]["orig_text"], "这个测试污染很严重")


if __name__ == "__main__":
    unittest.main()
