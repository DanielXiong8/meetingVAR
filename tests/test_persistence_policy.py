"""零落盘策略的可测试边界（model-free：可控 ASR 替身，不动模型/设备）。

产品承诺（README 设计红线 2 / plan.md）：默认零落盘，只存命中片段——
"没事故就什么都不落盘"。本文件把该承诺拆成三个可判定级别：

  G1a 纯静音、零转写、零命中      → 产物目录零新增（文件 + 目录）
  G1b 正常语音、成功转写、零命中  → 同上；转写只存在于内存，随会话结束消失
  G1c 正常语音、有转写、有命中    → 仅允许策略内产物：片段（JSON+音频）+ 全场转写文本
                                    （薄纪要所需）+ summary；产物数可枚举

审计范围（应用层持久化写入审计，非"操作系统级绝对零写盘证明"）：
  1) 被测进程显式写入的产物目录（data/{pool,clips,hd,reports} 的**文件与目录**）；
  2) 仓库工作树（排除 .git/、data/、__pycache__/）——捕获"顺手写进项目里"的路径；
  未覆盖：OS 临时目录/注册表/页面文件/崩溃转储/其他进程行为（见 docs/eval_protocol.md）。
"""
from __future__ import annotations

import re
import os
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

T0 = 1_000_000.0  # 绝对时间轴起点（远离墙钟，确保测试不依赖真实时钟）
SENT_PLAIN = [
    (1.0, 2.0, "spk0", "我们先过一遍昨天的实验数据"),
    (3.0, 4.0, "spk1", "整体趋势和上次一致，没有异常点"),
    (6.0, 8.0, "spk0", "下午把图表整理好发到群里"),
]


def snapshot_tree(root: Path, skip: set[str]) -> set[str]:
    """工作树内所有文件+目录的相对路径（排除 skip 命名的顶层目录与 __pycache__）。"""
    out: set[str] = set()
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name != "__pycache__"
                       and not (Path(directory) == root and (name in skip
                           or name in {".venv", ".runtimes", ".tools", ".secrets"}
                           or name.startswith(".venv-")))]
        for name in children + files:
            p = Path(directory) / name
            if p.suffix != ".pyc":
                rel = p.relative_to(root).as_posix()
                out.add(rel + "/" if p.is_dir() else rel)
    return out


def snapshot_data(data_dir: Path) -> set[str]:
    """产物目录的全部新增（文件与目录都算，防止空目录留下会话痕迹）。"""
    if not data_dir.exists():
        return set()
    out: set[str] = set()
    for p in data_dir.rglob("*"):
        rel = p.relative_to(data_dir)
        out.add(rel.as_posix() + "/") if p.is_dir() else out.add(rel.as_posix())
    return out


class PersistencePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_policy_"))
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = self.tmp
        config.POOL_DIR = self.tmp / "pool"
        config.CLIPS_DIR = self.tmp / "clips"
        self._old_asr = pipeline_mod.ASR
        self.repo_before = snapshot_tree(ROOT, {".git", "data", ".github"})

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        pipeline_mod.ASR = self._old_asr

    # ---- 运行辅助 ----
    def _session(self, sid: str, terms: list[str], fake: FakeASR, seconds: int = 20) -> Session:
        install_fake_asr(pipeline_mod, fake)
        s = Session(sid, terms=terms, focus_docs=None, device="cpu", echo=False)
        s.ingest_pcm(marker_pcm(seconds), T0)
        return s

    def _assert_no_repo_writes(self) -> None:
        after = snapshot_tree(ROOT, {".git", "data", ".github"})
        self.assertEqual(after, self.repo_before, f"仓库工作树出现意外变更: {sorted(after ^ self.repo_before)}")

    def _assert_products_match(self, expected_patterns: list[str]) -> None:
        created = sorted(snapshot_data(self.tmp))
        for pat in expected_patterns:
            matched = [c for c in created if re.fullmatch(pat, c)]
            self.assertTrue(matched, f"缺少预期产物: {pat}；实际: {created}")
        # 反向：每个新增都必须匹配某个模式（防种类外的杂散写入）
        for c in created:
            self.assertTrue(
                any(re.fullmatch(pat, c) for pat in expected_patterns),
                f"出现策略外产物: {c}",
            )

    # ---- G1a ----
    def test_g1a_silence_no_terms_creates_nothing(self) -> None:
        fake = FakeASR(default=[])  # 无任何转写
        s = self._session("g1a", terms=[], fake=fake)
        summary = s.finish()
        self.assertEqual(summary["lines"], 0)
        self.assertEqual(summary["clips"], 0)
        self.assertIsNone(summary["transcript_json"])
        self.assertEqual(snapshot_data(self.tmp), set(), "G1a：纯静音未命中会话不得产生任何文件/目录")
        self._assert_no_repo_writes()

    # ---- G1b（本阶段重点） ----
    def test_g1b_speech_transcribed_no_hit_creates_nothing(self) -> None:
        fake = FakeASR(default=SENT_PLAIN)
        # 词表与语音内容无交集，杜绝"测试词表意外命中测试音频"
        s = self._session("g1b", terms=["量子纠缠", "S3"], fake=fake)
        summary = s.finish()
        # 证明音频真的进了处理链路、且转写成功
        self.assertTrue(fake.calls and sum(fake.calls) > 0, "ASR 从未被调用，测试无效")
        self.assertGreater(summary["lines"], 0, "应有有效转写（否则退化成 G1a，不构成 G1b）")
        self.assertEqual(summary["clips"], 0, "词表意外命中，测试无效")
        self.assertIsNone(summary["transcript_json"], "未命中会话不得落盘转写")
        self.assertEqual(
            snapshot_data(self.tmp), set(),
            "G1b：有转写但零命中的会话必须零落盘（草稿转写只应存在于内存）",
        )
        self._assert_no_repo_writes()

    # ---- G1c ----
    def test_g1c_hit_products_enumerable_and_within_policy(self) -> None:
        fake = FakeASR(default=SENT_PLAIN + [(9.0, 11.0, "spk0", "测序污染的结果要重新测一遍")])
        s = self._session("g1c", terms=["测序污染"], fake=fake)
        summary = s.finish()
        self.assertGreater(summary["clips"], 0, "应产生命中片段")
        self.assertTrue(summary["transcript_json"].endswith("transcript.json"))
        # 命中会话的允许产物：片段 JSON + 片段音频 + 全场转写文本 + summary
        self._assert_products_match(
            [
                r"pool/",
                r"pool/g1c/",
                r"pool/g1c/transcript\.json",
                r"pool/g1c/summary\.json",
                r"pool/g1c/[0-9a-f-]{36}\.json",
                r"clips/",
                r"clips/[0-9a-f-]{36}/",
                r"clips/[0-9a-f-]{36}/[0-9a-f-]{36}\.wav",
            ],
        )
        self._assert_no_repo_writes()

        # 片段数与 clips 数一致（可枚举性）
        pool_jsons = list((self.tmp / "pool" / "g1c").glob("*.json"))
        clip_jsons = [p for p in pool_jsons if p.name not in {"transcript.json", "summary.json"}]
        self.assertEqual(len(clip_jsons), summary["clips"])

    def test_g1c_digest_products_within_policy(self) -> None:
        fake = FakeASR(default=SENT_PLAIN + [(9.0, 11.0, "spk0", "测序污染的结果要重新测一遍")])
        s = self._session("g1c_digest", terms=["测序污染"], fake=fake)
        s.finish()
        from meetingvar.report.generate import digest

        digest("g1c_digest", use_llm=False)
        self._assert_products_match(
            [
                r"pool/",
                r"pool/g1c_digest/",
                r"pool/g1c_digest/transcript\.json",
                r"pool/g1c_digest/summary\.json",
                r"pool/g1c_digest/[0-9a-f-]{36}\.json",
                r"clips/",
                r"clips/[0-9a-f-]{36}/",
                r"clips/[0-9a-f-]{36}/[0-9a-f-]{36}\.wav",
                r"reports/",
                r"reports/g1c_digest/",
                r"reports/g1c_digest/minutes\.md",
                r"reports/g1c_digest/var_reports\.md",
            ],
        )

    def test_g1c_manual_var_without_transcript_still_saves_clip(self) -> None:
        """L3 手动 VAR 是命中：即使回溯窗内无转写，也允许保存音频片段。"""
        fake = FakeASR(default=[])
        s = self._session("g1c_manual", terms=[], fake=fake)
        clip_id = s.trigger_manual()
        self.assertIsNotNone(clip_id)
        summary = s.finish()
        self.assertEqual(summary["clips"], 1)
        self.assertIsNotNone(summary["transcript_json"])
        self._assert_products_match(
            [
                r"pool/",
                r"pool/g1c_manual/",
                r"pool/g1c_manual/transcript\.json",
                r"pool/g1c_manual/summary\.json",
                r"pool/g1c_manual/[0-9a-f-]{36}\.json",
                r"clips/",
                r"clips/[0-9a-f-]{36}/",
                r"clips/[0-9a-f-]{36}/[0-9a-f-]{36}\.wav",
            ],
        )

    def test_no_hit_session_digest_is_noop_not_crash(self) -> None:
        """未命中会话（零产物）上跑 digest：应优雅提示"无内容可出"，不得崩栈。"""
        fake = FakeASR(default=SENT_PLAIN)
        s = self._session("g1b_digest", terms=["量子纠缠"], fake=fake)
        s.finish()
        from meetingvar.report.generate import digest

        digest("g1b_digest", use_llm=False)
        self.assertEqual(snapshot_data(self.tmp), set(), "未命中会话的 digest 不得凭空产生纪要")


if __name__ == "__main__":
    unittest.main()
