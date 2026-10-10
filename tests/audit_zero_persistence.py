"""零落盘审计（真实模型版）：把"默认零落盘"写成可测试的产品承诺。

审计声明（可测试版）：
  G1a 纯静音、零转写、零命中            → 产物目录零新增（文件与目录）；
  G1b 正常语音、成功转写、零命中        → 同上：草稿转写只存在于内存，会话结束即消失；
  G1c 正常语音、有命中                  → 仅允许策略内产物（片段 JSON+音频+全场转写文本
                                          +summary；digest 后另有 reports/），数量可枚举。

审计范围（**应用层持久化写入审计**，不是"操作系统级绝对零写盘证明**）：
  覆盖：① 产物目录 data/{pool,clips,hd,reports} 的新增文件与**目录**；
        ② 仓库工作树（排除 .git/、data/、__pycache__/）——捕获误写进项目的路径。
  未覆盖：OS 临时目录（ASR 转写用临时 wav，正常路径会删除）、注册表、页面文件、
          崩溃转储、其他进程行为——如需该级别结论需 Procmon 等系统级监控。

方法：目录快照（含目录）→ 跑对应 replay → 比对快照。
运行：D:/miniconda3/envs/meetingvar/python.exe tests/audit_zero_persistence.py -v
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402

TEST_WAV = Path("D:/env/test_terms.wav")  # 含热词的真实音频（18s）
TERMS = ["测序污染"]

PRODUCT_PATTERNS = [
    r"pool/",
    r"pool/[^/]+/",
    r"pool/[^/]+/transcript\.json",
    r"pool/[^/]+/summary\.json",
    r"pool/[^/]+/[0-9a-f-]{36}\.json",
    r"clips/",
    r"clips/[0-9a-f-]{36}/",
    r"clips/[0-9a-f-]{36}/[0-9a-f-]{36}\.wav",
]


def snapshot() -> set[str]:
    """产品目录（文件+目录）相对路径，posix 风格。"""
    out: set[str] = set()
    for d in ("pool", "clips", "hd", "reports"):
        p = config.DATA_DIR / d
        if not p.exists():
            continue
        for f in p.rglob("*"):
            rel = f.relative_to(config.DATA_DIR).as_posix()
            out.add(rel + "/") if f.is_dir() else out.add(rel)
    return out


def repo_snapshot() -> set[str]:
    out: set[str] = set()
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if rel.parts and rel.parts[0] in {".git", "data", ".github"}:
            continue
        if "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue
        out.add(rel.as_posix() + "/") if p.is_dir() else out.add(rel.as_posix())
    return out


class ZeroPersistenceAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv_audit_"))
        cls._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = cls.tmp
        config.POOL_DIR = cls.tmp / "pool"
        config.CLIPS_DIR = cls.tmp / "clips"

    @classmethod
    def tearDownClass(cls) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = cls._old
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.repo_before = repo_snapshot()

    def _assert_zero(self, msg: str) -> None:
        self.assertEqual(snapshot(), set(), msg)
        self.assertEqual(repo_snapshot(), self.repo_before, "仓库工作树出现意外变更")

    # ---- G1a：纯静音 ----
    @unittest.skipUnless(TEST_WAV.exists() and config.MODELS_ROOT.exists(), "需要本地模型栈与 D:/env/test_terms.wav")
    def test_g1a_silence_no_hit_creates_no_files(self):
        from meetingvar.replay import replay

        silence = self.tmp / "silence.wav"
        with wave.open(str(silence), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(2 * 16000 * 20))  # 20 秒纯静音
        before = snapshot()
        summary = replay(silence, "audit_g1a", terms=[], focus_docs=None, device="cpu")
        self.assertEqual(summary["lines"], 0, "静音不应产生转写（G1a 前提）")
        self._assert_zero(f"G1a 违规：静音未命中会话产生新增 {sorted(snapshot() - before)}")

    # ---- G1b：真实语音 + 真实转写 + 零命中（本阶段重点） ----
    @unittest.skipUnless(TEST_WAV.exists() and config.MODELS_ROOT.exists(), "需要本地模型栈与 D:/env/test_terms.wav")
    def test_g1b_real_speech_transcribed_no_hit_creates_no_files(self):
        """用真实音频/真实 ASR 转出真转写，但词表与语音无交集（terms=[]）→ 零落盘。

        词表故意留空（不借用测试音频里的热词），确保"命中"不可能发生，
        排除"词表意外命中测试音频"这一类假阴性。
        """
        from meetingvar.replay import replay

        before = snapshot()
        summary = replay(TEST_WAV, "audit_g1b", terms=[], focus_docs=None, device="cpu")
        self.assertGreater(summary["lines"], 0, "真实音频应转出有效转写（否则退化为 G1a，不构成 G1b）")
        self.assertEqual(summary["clips"], 0, "本用例不应有命中")
        self.assertIsNone(summary["transcript_json"], "未命中会话不得落盘转写")
        self._assert_zero(f"G1b 违规：有转写无命中的会话产生新增 {sorted(snapshot() - before)}")

    # ---- G1c：有命中 → 仅策略内产物、可枚举 ----
    @unittest.skipUnless(TEST_WAV.exists() and config.MODELS_ROOT.exists(), "需要本地模型栈与 D:/env/test_terms.wav")
    def test_g1c_hit_creates_only_policy_products(self):
        from meetingvar.replay import replay

        before = snapshot()
        summary = replay(TEST_WAV, "audit_g1c", terms=TERMS, focus_docs=None, device="cpu")
        self.assertGreater(summary["clips"], 0, "本用例应有命中")
        created = sorted(snapshot() - before)
        self.assertTrue(created, "命中会话应有产物")
        for c in created:
            self.assertTrue(any(re.fullmatch(p, c) for p in PRODUCT_PATTERNS), f"策略外产物: {c}")
        # 可枚举性：片段 JSON 数 == summary clips 数
        sid_dir = config.POOL_DIR / "audit_g1c"
        n_clips = sum(
            1 for f in sid_dir.glob("*.json") if "trigger" in json.loads(f.read_text(encoding="utf-8"))
        )
        self.assertEqual(n_clips, summary["clips"])
        self.assertEqual(repo_snapshot(), self.repo_before, "仓库工作树出现意外变更")


if __name__ == "__main__":
    unittest.main()
