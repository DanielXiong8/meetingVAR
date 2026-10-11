"""词表治理层与纠错守卫测试（GPT 裁决 2026-10-11 第一版的行为钉子）。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.memory.govern import EN_COMMON, classify, govern_terms  # noqa: E402


def _has_pypinyin() -> bool:
    try:
        return importlib.util.find_spec("pypinyin") is not None
    except Exception:  # noqa: BLE001
        return False


class GovernClassifyTests(unittest.TestCase):
    def test_hard_excluded_forms(self) -> None:
        cases = ["331,444 个唯一变异", "10-06 SnpEff 版", "A. 打分方法清单", "score_sai",
                 "https://github.com/x/y", "# 自动生成词表", "a", "72 pathogenic 阳性对照"]
        for w in cases:
            g = classify(w)
            self.assertEqual(g.status, "excluded", f"{w!r} 应被硬排除（reason={g.reason}）")
            self.assertTrue(g.reason, f"{w!r} 应给出排除原因")
        # 短字母数字码是真术语，不得被数字守卫误杀
        self.assertEqual(classify("S3").status, "correction")

    def test_common_english_word_excluded(self) -> None:
        for w in ["user", "query", "framework", "messages", "think", "This", "used"]:
            g = classify(w)
            self.assertEqual(g.status, "excluded", f"常用英文词 {w!r} 应整表排除（B 组改写链根源）")
        self.assertIn("user", EN_COMMON)
        self.assertIn("think", EN_COMMON)

    def test_common_chinese_word_excluded(self) -> None:
        for w in ["数据", "区域", "结果", "注释"]:
            g = classify(w)
            self.assertEqual(g.status, "excluded", f"通用中文词 {w!r} 应排除（触发无辨识价值）")

    def test_multiword_phrase_watch_only(self) -> None:
        """burden test 这类多词术语：允许监听，v1 不参与改写（单 token 匹配天然够不着）。"""
        g = classify("burden test")
        self.assertEqual(g.status, "watch", "多词短语应仅监听")
        g = classify("Mann-Whitney U")
        self.assertEqual(g.status, "watch")

    def test_domain_terms_pass(self) -> None:
        for w in ["AlphaGenome", "gnomAD", "pangolin", "SpliceAI", "随机森林", "测序污染", "S3"]:
            g = classify(w)
            self.assertEqual(g.status, "correction", f"领域词 {w!r} 应可纠错（reason={g.reason}）")

    def test_short_chinese_watch_only(self) -> None:
        self.assertEqual(classify("位点").status, "watch")
        self.assertEqual(classify("阈值").status, "watch")

    def test_manual_overrides_filters(self) -> None:
        """人工词绕过自动过滤器（用户意图优先）。"""
        g = classify("数据", source="manual")
        self.assertEqual(g.status, "approved")
        g = classify("run", source="manual")
        self.assertEqual(g.status, "approved")

    def test_govern_terms_splits_lists(self) -> None:
        raw = {"pangolin": ["工具层"], "user": ["拉丁"], "不同区域": ["jieba"], "burden test": ["LLM"],
               "随机森林": ["手动"], "score_sai": ["工具层"]}
        g = govern_terms(raw, manual={"随机森林"})
        self.assertIn("pangolin", g.correction)
        self.assertIn("随机森林", g.correction)          # 人工词 → approved（监听+纠错）
        self.assertNotIn("user", g.watch)                 # 常用词连监听都排除
        self.assertNotIn("score_sai", g.watch)            # 标识符排除
        self.assertIn("burden test", g.watch)
        self.assertNotIn("burden test", g.correction)
        self.assertIn("不同区域", g.watch)                 # 5 字中文短语 → watch（纠错由长度守卫拦）
        self.assertEqual(g.stats().get("excluded"), 2)


@unittest.skipUnless(_has_pypinyin(), "无 pypinyin：纠错降级为恒等，守卫测试不适用")
class CorrectGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        from meetingvar.transcribe.correct import correct

        self.correct = correct

    def test_chinese_same_length_accepted(self) -> None:
        out, recs = self.correct("我们讨论基因学习的部分", ["机器学习"])
        self.assertIn("机器学习", out)
        self.assertEqual(recs[0]["status"], "accepted")

    def test_chinese_length_diff_goes_to_review(self) -> None:
        """不同区域→不限定区域（±1 字）必须停在待审，文本不变——B 组中文误改的对策。"""
        out, recs = self.correct("不同区域的结果", ["不限定区域"])
        self.assertEqual(out, "不同区域的结果", "灰区改写不得改动文本")
        self.assertEqual(recs[0]["status"], "review_required")

    def test_latin_high_ratio_accepted(self) -> None:
        out, recs = self.correct("we need the framnework ready", ["framework"])
        self.assertIn("framework", out)
        self.assertEqual(recs[0]["status"], "accepted")

    def test_latin_gray_zone_review(self) -> None:
        """four→figure 相似度 0.60：宁漏勿误，进待审不改文本。"""
        out, recs = self.correct("pick four items", ["figure"])
        self.assertEqual(out, "pick four items")
        self.assertEqual(recs[0]["status"], "review_required")

    def test_common_source_word_blocked(self) -> None:
        """used→user：原词是常用英文词 → 永远 review（改写链根源）。"""
        out, recs = self.correct("he used it", ["user"])
        self.assertEqual(out, "he used it")
        self.assertEqual(recs[0]["status"], "review_required")
        self.assertIn("常用", recs[0]["reason"])

    def test_record_shape(self) -> None:
        _, recs = self.correct("基因学习很重要", ["机器学习"])
        self.assertEqual(set(recs[0]), {"src", "dst", "status", "reason"})


if __name__ == "__main__":
    unittest.main()
