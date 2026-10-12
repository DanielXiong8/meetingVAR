"""短句过滤回归测试：区分"进入转写 / 影响触发 / 写入证据与报告"三个问题。

背景：PR #27 给 _flush 加了 `len(text.strip()) < 2 → 丢弃`，本意是剔除噪声碎片。
该过滤发生在转写入库前，因此同时影响：
  (1) 转写结果（transcript）
  (2) 触发判断（监听只看保留下来的句子）
  (3) 证据与报告（片段 transcript、digest 纪要）

本测试用确定性替身固定住这些语义边界，避免"为了除噪悄悄丢掉有效短句"：
  - 单字确认（是）、单字指令（停）、噪声（嗯/空串）→ 当前策略丢弃；
  - 双字否定（不是）、双字指令（重测）、正常长句 → 保留；
  - 阈值可配置（config.MIN_TEXT_CHARS），改小即可保留单字句（可逆，不是硬编码死路）。
"""
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

T0 = 3_000_000.0

SEGS = [
    (0.5, 1.0, "spk0", "是"),                       # 单字确认
    (1.5, 2.0, "spk0", "不是"),                     # 双字否定
    (2.5, 3.0, "spk1", "停"),                       # 单字指令
    (3.5, 4.0, "spk1", "重测"),                     # 双字指令
    (4.5, 5.0, "spk0", "嗯"),                       # 噪声碎片
    (5.5, 6.0, "spk0", ""),                          # 空转写
    (6.5, 7.5, "spk1", "会后请确认测序污染结果"),     # 正常长句
]
KEPT = ["不是", "重测", "会后请确认测序污染结果"]
DROPPED = ["是", "停", "嗯", ""]


class ShortSentenceFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_filter_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    def _run(self, sid: str, terms: list[str], segments=SEGS) -> tuple[Session, FakeASR]:
        fake = FakeASR(default=segments)
        install_fake_asr(pipeline_mod, fake)
        s = Session(sid, terms=terms, focus_docs=None, device="cpu", echo=False)
        s.ingest_pcm(marker_pcm(20), T0)
        return s, fake

    # ---- (1) 进入转写 ----
    def test_transcript_keeps_only_non_fragment_lines(self) -> None:
        s, fake = self._run("filt_tr", terms=[])
        self.assertTrue(fake.calls and sum(fake.calls) > 0, "音频必须真的进了转写链路")
        texts = [l["text"] for l in s.transcript]
        self.assertEqual(texts, KEPT, f"转写保留集合不符（丢失有效句或漏掉碎片）: {texts}")

    # ---- (2) 影响触发 ----
    def test_single_char_command_cannot_trigger(self) -> None:
        """单字句被丢弃后不参与监听：仅含单字热词（停）不触发。"""
        s, _ = self._run("filt_trig1", terms=["停"])
        s.finish()
        self.assertEqual(s.clips, [], "单字句按策略不参与触发判断（已知边界，见 docs/eval_protocol.md）")

    def test_two_char_command_can_trigger(self) -> None:
        s, _ = self._run("filt_trig2", terms=["重测"])
        s.finish()
        self.assertEqual(len(s.clips), 1, "双字短指令必须保留并能触发（过滤不得扩大化）")

    def test_long_sentence_can_trigger(self) -> None:
        s, _ = self._run("filt_trig3", terms=["测序污染"])
        s.finish()
        self.assertEqual(len(s.clips), 1, "正常长句必须能触发")

    # ---- (3) 写入证据与报告 ----
    def test_evidence_and_report_exclude_dropped_fragments(self) -> None:
        s, _ = self._run("filt_ev", terms=["测序污染"])
        summary = s.finish()
        self.assertEqual(summary["clips"], 1)
        # 片段 JSON 内的证据转写
        clip_files = [
            p for p in (config.POOL_DIR / "filt_ev").glob("*.json")
            if p.name not in {"transcript.json", "summary.json"}
        ]
        self.assertEqual(len(clip_files), 1)
        clip = json.loads(clip_files[0].read_text(encoding="utf-8"))
        clip_texts = [l["text"] for l in clip["transcript"]]
        for bad in DROPPED:
            if bad:
                self.assertNotIn(bad, clip_texts, f"碎片 {bad!r} 不得进入证据片段")
        for good in KEPT:
            self.assertIn(good, clip_texts, f"有效短句 {good!r} 不得被证据流水丢弃")
        # 落盘的全场转写（薄纪要原料）与内存一致
        persisted = json.loads(Path(summary["transcript_json"]).read_text(encoding="utf-8"))
        self.assertEqual([l["text"] for l in persisted["transcript"]], KEPT)
        # digest 纪要同样不含碎片
        from meetingvar.report.generate import digest

        digest("filt_ev", use_llm=False)
        minutes = (config.DATA_DIR / "reports" / "filt_ev" / "minutes.md").read_text(encoding="utf-8")
        self.assertIn("会后请确认测序污染结果", minutes)
        self.assertNotIn("spk1: 停", minutes, "单字碎片不得出现在纪要里")

    # ---- 阈值可逆 ----
    def test_threshold_is_configurable_not_hardwired(self) -> None:
        """阈值=1 时单字句保留，且能作为热词触发——证明过滤策略可调而非写死。"""
        had = hasattr(config, "MIN_TEXT_CHARS")
        old = getattr(config, "MIN_TEXT_CHARS", None)
        config.MIN_TEXT_CHARS = 1
        try:
            s, _ = self._run("filt_knob", terms=["停"], segments=[(1.0, 2.0, "spk0", "停")])
            s.finish()
            self.assertEqual(
                [l["text"] for l in s.transcript], ["停"],
                "阈值=1 时单字句应保留（当前实现若硬编码 <2 过滤，此断言失败）",
            )
            self.assertEqual(len(s.clips), 1, "阈值=1 时单字热词应能触发")
        finally:
            if had:
                config.MIN_TEXT_CHARS = old
            else:
                delattr(config, "MIN_TEXT_CHARS")


if __name__ == "__main__":
    unittest.main()
