"""事件级匹配器边界测试（阶段 4）。

覆盖：合并阈值边界 / 重叠归属 / 容差边界 / 空输入 / 非法输入 / 一对一唯一性 /
确定性 / TP·FP·FN 与 P·R·F1 的关系 / 每小时误触发数分母口径。

设计参数声明（不是已被真实数据验证的最优值）：
  - merge_gap = 90s：相邻预测间隙 ≤ 90s 视为同一事件；
  - tol = 45s：中心点偏差 ≤ tol 才算命中（时间重叠亦算命中）；
  - 贪心：按重叠时长降序一对一匹配（确定性；不保证全局最优，符合协议定义即可）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.eval.matching import evaluate, load_predictions, match, merge_predictions  # noqa: E402


class MergeTests(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(merge_predictions([]), [])
        self.assertEqual(merge_predictions([(5, 9)]), [(5, 9)])

    def test_gap_threshold_boundary(self):
        """间隙严格小于 / 等于 / 大于 merge_gap 的三种行为。"""
        self.assertEqual(len(merge_predictions([(0, 100), (189.9, 220)], merge_gap=90)), 1)   # 89.9
        self.assertEqual(len(merge_predictions([(0, 100), (190.0, 220)], merge_gap=90)), 1)   # == 90
        self.assertEqual(len(merge_predictions([(0, 100), (190.1, 220)], merge_gap=90)), 2)   # 90.1

    def test_chain_merges_transitively(self):
        """链式相邻（A-B 80s，B-C 80s）合并为一个事件，跨度取并集。"""
        merged = merge_predictions([(0, 10), (90, 100), (180, 190)], merge_gap=90)
        self.assertEqual(merged, [(0, 190)])

    def test_unsorted_input_is_normalized(self):
        """乱序输入不得因顺序不同而合并结果不同（内部须排序）。"""
        a = merge_predictions([(600, 630), (100, 130), (700, 730)], merge_gap=90)
        b = merge_predictions([(100, 130), (600, 630), (700, 730)], merge_gap=90)
        self.assertEqual(a, b, "乱序输入改变了合并结果——排序缺失")
        self.assertEqual(a, [(100, 130), (600, 730)])

    def test_illegal_interval_rejected(self):
        with self.assertRaises(ValueError):
            merge_predictions([(330, 300)])
        with self.assertRaises(ValueError):
            merge_predictions([(-5, 10)])


class MatchTests(unittest.TestCase):
    def test_one_pred_matches_one_gold_only(self):
        """一个预测不得同时匹配两个真值（一对一）。"""
        tp, fp, fn, _ = match([(100, 200)], [(100, 200)])
        self.assertEqual((tp, fp, fn), (1, 0, 0))
        # 两个相邻真值 vs 一个预测 → 只能得到 1 TP + 1 FN
        tp, fp, fn, _ = match([(100, 200), (240, 260)], [(100, 260)])
        self.assertEqual((tp, fp, fn), (1, 0, 1))

    def test_overlapping_two_gold_picks_max_overlap(self):
        """预测与多个真值重叠：按重叠最大者归属（确定性）。"""
        gold = [(100, 200), (180, 190)]
        preds = [(150, 195)]  # 与 A 重叠 45s，与 B 重叠 10s
        tp, fp, fn, details = match(gold, preds)
        self.assertEqual((tp, fp, fn), (1, 0, 1))
        self.assertEqual(details[0][1], (100, 200), "应归属重叠最大的真值")
        # 重复运行结果一致
        self.assertEqual(match(gold, preds), match(gold, preds))

    def test_identical_gold_tie_is_deterministic(self):
        """完全相同的两个真值：贪心按索引确定归属，结果可复现（格式为 (TP,FP,FN)）。"""
        gold = [(100, 200), (100, 200)]
        r1 = match(gold, [(100, 200)])
        r2 = match(gold, [(100, 200)])
        self.assertEqual(r1[:3], (1, 0, 1))
        self.assertEqual(r1, r2)

    def test_tolerance_boundary(self):
        """中心偏差 == tol 算命中；略超即不算。"""
        gold = [(300, 330)]          # 中心 315
        tp, fp, fn, _ = match(gold, [(345, 375)])       # 中心 360 → 偏差 45（== tol）
        self.assertEqual((tp, fp, fn), (1, 0, 0))
        tp, fp, fn, _ = match(gold, [(345.1, 375.1)])   # 偏差 45.1
        self.assertEqual((tp, fp, fn), (0, 1, 1))

    def test_touching_intervals_match_by_center(self):
        """仅相邻（重叠为 0）但中心接近 → 仍按容差判为命中。"""
        tp, fp, fn, _ = match([(300, 330)], [(330, 360)])
        self.assertEqual((tp, fp, fn), (1, 0, 0))

    def test_point_events(self):
        """起止相等的点事件：中心即为自身。"""
        self.assertEqual(match([(300, 300)], [(300, 300)])[:3], (1, 0, 0))
        self.assertEqual(match([(300, 300)], [(340, 340)])[:3], (1, 0, 0))
        self.assertEqual(match([(300, 300)], [(400, 400)])[:3], (0, 1, 1))

    def test_zero_predictions_all_fn(self):
        self.assertEqual(match([(10, 20), (30, 40)], [])[:3], (0, 0, 2))

    def test_zero_gold_all_fp(self):
        self.assertEqual(match([], [(10, 20), (30, 40)])[:3], (0, 2, 0))

    def test_both_empty(self):
        self.assertEqual(match([], [])[:3], (0, 0, 0))

    def test_reversed_or_negative_rejected(self):
        with self.assertRaises(ValueError):
            match([(330, 300)], [(300, 330)])
        with self.assertRaises(ValueError):
            match([(300, 330)], [(330, 300)])
        with self.assertRaises(ValueError):
            match([(-1, 10)], [(0, 5)])


class EvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mv_eval_"))
        self.pool = self.tmp / "pool" / "s1"
        self.pool.mkdir(parents=True)

    def _clip(self, start: float, end: float, name: str) -> None:
        (self.pool / f"{name}.json").write_text(
            json.dumps({"trigger": {"layer": "L1", "evidence": "热词:x"},
                        "audio": {"start_ts": start, "end_ts": end}}),
            encoding="utf-8",
        )

    def _labels(self, events: list[tuple[float, float]]) -> Path:
        p = self.tmp / "labels.json"
        p.write_text(json.dumps([{"start": a, "end": b, "desc": ""} for a, b in events]), encoding="utf-8")
        return p

    def test_non_clip_files_are_skipped(self) -> None:
        self._clip(100, 130, "a")
        (self.pool / "transcript.json").write_text(json.dumps({"transcript": [], "clips": []}), encoding="utf-8")
        (self.pool / "summary.json").write_text(json.dumps({"lines": 0}), encoding="utf-8")
        self.assertEqual(load_predictions(self.pool), [(100.0, 130.0)])

    def test_window_hits_do_not_inflate_recall(self) -> None:
        """5 个滑窗命中同一事件 → 合并为 1 个预测事件 → 只算 1 TP（防重复计数）。"""
        for i, s in enumerate([295, 315, 335, 355, 375]):
            self._clip(s, s + 15, f"w{i}")
        out = evaluate(self.pool, self._labels([(300, 390)]))
        self.assertEqual((out["raw_hits"], out["merged_events"], out["TP"], out["FP"], out["FN"]), (5, 1, 1, 0, 0))
        self.assertEqual((out["precision"], out["recall"], out["f1"]), (1.0, 1.0, 1.0))

    def test_prf_relation_with_counts(self) -> None:
        """P/R/F1 必须与 TP/FP/FN 精确对应。"""
        self._clip(300, 330, "a")     # 命中真值1
        self._clip(700, 730, "b")     # 误报
        out = evaluate(self.pool, self._labels([(305, 335), (600, 640)]))
        self.assertEqual((out["TP"], out["FP"], out["FN"]), (1, 1, 1))
        self.assertEqual(out["precision"], 0.5)
        self.assertEqual(out["recall"], 0.5)
        self.assertEqual(out["f1"], 0.5)

    def test_merge_gap_can_swallow_two_close_events(self) -> None:
        """已知边界：两个真实事件间隔 < merge_gap 且各有一个预测时，合并会吃掉一个。

        记录该行为而不是掩盖：merge_gap 是可调设计参数，需真实数据校准。
        """
        self._clip(300, 330, "a")
        self._clip(380, 410, "b")     # 与 a 间隙 50s < 90s → 合并为一个预测事件
        out = evaluate(self.pool, self._labels([(300, 330), (380, 410)]))
        self.assertEqual((out["merged_events"], out["TP"], out["FP"], out["FN"]), (1, 1, 0, 1))
        self.assertEqual(out["recall"], 0.5, "合并参数影响召回——真实评测必须报告该参数敏感性")

    def test_false_triggers_per_hour_denominator(self) -> None:
        self._clip(700, 730, "b")     # 1 个误报
        labels = self._labels([(300, 330)])
        out = evaluate(self.pool, labels, meeting_hours=2.0)
        self.assertEqual(out["false_triggers_per_hour"], 0.5)          # 1 / 2h
        out = evaluate(self.pool, labels, meeting_hours=None)
        self.assertNotIn("false_triggers_per_hour", out, "未提供时长时不得凭空给出误触率")
        with self.assertRaises(ValueError):
            evaluate(self.pool, labels, meeting_hours=0.0)
        with self.assertRaises(ValueError):
            evaluate(self.pool, labels, meeting_hours=-1.0)

    def test_zero_zero_convention(self) -> None:
        """零预测/零真值：P/R/F1 一律 0.0（约定值，不抛除零）。"""
        out = evaluate(self.pool, self._labels([]))
        self.assertEqual((out["TP"], out["FP"], out["FN"]), (0, 0, 0))
        self.assertEqual((out["precision"], out["recall"], out["f1"]), (0.0, 0.0, 0.0))

    def test_invalid_labels_rejected(self) -> None:
        self._clip(300, 330, "a")
        with self.assertRaises(ValueError):
            evaluate(self.pool, self._labels([(400, 300)]))     # 反向区间
        with self.assertRaises(ValueError):
            evaluate(self.pool, self._labels([(-10, 30)]))      # 负时间戳
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps([{"desc": "缺 start/end"}]), encoding="utf-8")
        with self.assertRaises(ValueError):
            evaluate(self.pool, bad)


if __name__ == "__main__":
    unittest.main()
