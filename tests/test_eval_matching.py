"""事件级匹配规则测试（model-free）。"""
import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meetingvar.eval.matching import merge_predictions, match

class MatchingTests(unittest.TestCase):
    def test_merge_adjacent_hits(self):
        # 3 个滑窗命中彼此间隔 30s <= 90s → 合并为 1 个事件
        preds = [(100, 130), (160, 190), (220, 250)]
        self.assertEqual(len(merge_predictions(preds, merge_gap=90)), 1)

    def test_distant_hits_not_merged(self):
        preds = [(100, 130), (600, 630)]
        self.assertEqual(len(merge_predictions(preds, merge_gap=90)), 2)

    def test_match_counts(self):
        gold = [(300, 330), (600, 640)]
        preds = [(305, 335), (700, 730)]
        tp, fp, fn, _ = match(gold, preds)
        self.assertEqual((tp, fp, fn), (1, 1, 1))

    def test_no_double_count(self):
        # 一个真值事件被 5 个滑窗命中 → 事件合并后只算 1 TP
        gold = [(300, 330)]
        preds = [(295, 310), (315, 330), (335, 350), (355, 370), (375, 390)]
        merged = merge_predictions(preds, merge_gap=90)
        tp, fp, fn, _ = match(gold, merged)
        self.assertEqual((tp, fp, fn), (1, 0, 0))

if __name__ == "__main__":
    unittest.main()
