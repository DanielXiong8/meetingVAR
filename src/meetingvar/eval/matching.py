"""事件级匹配 P/R 评估（外部评审三的落地：防滑窗重复命中抬高指标）。

核心：把"每个 8s 滑窗的命中"合并为**事件**，再与人工标注的真值事件做一对一匹配。

真值标注格式（eval/labels/<session>.json）：
    [{"start": 300.0, "end": 330.0, "desc": "PI 提出重测决策"}, ...]

系统输出格式：片段池 JSON（transcript 相对秒时间轴）。

匹配规则（可配置，默认取自评审建议）：
  - 预测事件 = 相邻命中片段按 merge_gap（默认 90s）合并；
  - 预测与真值匹配条件：时间重叠 OR 中心点偏差 ≤ match_tolerance（默认 45s）；
  - 一个真值最多被一个预测匹配（贪心，按重叠最大优先）；反之亦然。

输出：TP/FP/FN 原始计数、Precision/Recall/F1、每小时误触发数（评审要求的
"比单一 F1 更有价值"的口径）。
"""
from __future__ import annotations

import json
from pathlib import Path


def load_predictions(pool_dir: Path) -> list[tuple[float, float]]:
    """片段池 → [(start, end)]，相对秒。"""
    out = []
    for f in sorted(Path(pool_dir).glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        if "trigger" not in c:
            continue
        out.append((float(c["audio"]["start_ts"]), float(c["audio"]["end_ts"])))
    return sorted(out)


def merge_predictions(preds: list[tuple[float, float]], merge_gap: float = 90.0) -> list[tuple[float, float]]:
    """相邻命中合并为事件：间隙 ≤ merge_gap 视为同一事件。"""
    if not preds:
        return []
    merged = [list(preds[0])]
    for a, b in preds[1:]:
        if a - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def match(gold: list[tuple[float, float]], preds: list[tuple[float, float]], tol: float = 45.0) -> tuple[int, int, int, list]:
    """贪心一对一匹配。返回 (TP, FP, FN, 明细)。"""
    gold_left = [True] * len(gold)
    pred_left = [True] * len(preds)
    tp = 0
    details = []
    # 按重叠时长降序贪心
    pairs = []
    for gi, (gs, ge) in enumerate(gold):
        for pi, (ps, pe) in enumerate(preds):
            overlap = min(ge, pe) - max(gs, ps)
            center_ok = abs((gs + ge) / 2 - (ps + pe) / 2) <= tol
            if overlap > 0 or center_ok:
                pairs.append((max(overlap, 0), gi, pi))
    pairs.sort(reverse=True)
    for ov, gi, pi in pairs:
        if gold_left[gi] and pred_left[pi]:
            gold_left[gi] = pred_left[pi] = False
            tp += 1
            details.append(("TP", gold[gi], preds[pi]))
    fp = sum(pred_left)
    fn = sum(gold_left)
    return tp, fp, fn, details


def evaluate(session_pool_dir: Path, labels_file: Path, merge_gap: float = 90.0, tol: float = 45.0,
             meeting_hours: float | None = None) -> dict:
    gold = [(float(e["start"]), float(e["end"])) for e in json.loads(labels_file.read_text(encoding="utf-8"))]
    raw_preds = load_predictions(session_pool_dir)
    preds = merge_predictions(raw_preds, merge_gap)
    tp, fp, fn, details = match(gold, preds)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    out = {
        "gold_events": len(gold), "raw_hits": len(raw_preds), "merged_events": len(preds),
        "TP": tp, "FP": fp, "FN": fn,
        "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3),
        "details": details,
    }
    if meeting_hours:
        out["false_triggers_per_hour"] = round(fp / meeting_hours, 2)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--hours", type=float, default=None, help="会议时长（小时），用于误触发率")
    a = ap.parse_args()
    print(json.dumps(evaluate(Path(a.pool), Path(a.labels), meeting_hours=a.hours), ensure_ascii=False, indent=2))
