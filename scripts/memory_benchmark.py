"""Run the synthetic 40-question project-memory benchmark.

Usage:
  python scripts/memory_benchmark.py --json benchmark_report.json --md benchmark_report.md
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402

DEFAULT = ROOT / "benchmarks" / "memory" / "memory_benchmark.json"


def build_store(root: Path, payload: dict) -> FactStore:
    store = FactStore(root)
    for fact in payload["facts"]:
        metadata = dict(fact.get("metadata", {}))
        if fact.get("owner"):
            metadata["owner"] = fact["owner"]
        if fact.get("due"):
            metadata["due"] = fact["due"]
        store.write(
            name=fact["name"], description=fact["description"], body=fact["body"],
            fact_type=fact["type"], origin_meeting=fact.get("origin", ""),
            confidence=fact.get("confidence"), status=fact.get("status", "active"),
            valid_to=fact.get("valid_to", ""), metadata=metadata,
            conflict_policy="auto",
        )
    return store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT))
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()
    payload = json.loads(Path(args.cases).read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="mv_memory_bench_") as tmp:
        store = build_store(Path(tmp) / "project", payload)
        rows = []
        for question in payload["questions"]:
            top = store.search(question["query"], limit=5,
                               types=question.get("types") or None)
            names = [row["name"] for row in top]
            expected = question.get("expected") or []
            kind = question.get("kind", "positive")
            if expected:
                found = sum(1 for name in expected if name in names)
                recall = found / len(expected)
                top1 = bool(names and names[0] in expected)
            else:
                recall = 1.0 if not names else 0.0
                top1 = not names
            if kind == "stale":
                # Stale questions assert the expired/superseded fact is absent
                # from the default active view; unrelated active facts may match.
                stale_name = payload["stale"]["expected_expired"]
                recall = 1.0 if stale_name not in names else 0.0
                top1 = recall
            rows.append({"id": question["id"], "kind": kind,
                         "query": question["query"], "expected": expected,
                         "top5": names, "recall_at_5": round(recall, 3),
                         "top1": top1, "why": [row.get("_why", []) for row in top]})

        positives = [row for row in rows if row["kind"] in {"positive", "filter"}]
        negatives = [row for row in rows if row["kind"] == "negative"]
        stale = [row for row in rows if row["kind"] == "stale"]
        conflict = [row for row in rows if row["kind"] == "conflict"]
        pending = [row["name"] for row in store.pending_conflicts()]
        conflict_active = [row["name"] for row in store.search("供应商交付时间", limit=20)]
        stale_name = payload["stale"]["expected_expired"]
        stale_recover = [row["name"] for row in store.search(
            payload["stale"]["query"], limit=20, status="all", include_expired=True)]
        metrics = {
            "questions": len(rows),
            "recall_at_5": round(sum(row["recall_at_5"] for row in positives) / max(1, len(positives)), 3),
            "top1_accuracy": round(sum(1 for row in positives if row["top1"]) / max(1, len(positives)), 3),
            "citation_precision_at_1": round(sum(1 for row in positives if row["top1"]) / max(1, len(positives)), 3),
            "negative_accuracy": round(sum(1 for row in negatives if row["top1"]) / max(1, len(negatives)), 3),
            "stale_active_clean": round(sum(row["recall_at_5"] for row in stale) / max(1, len(stale)), 3),
            "stale_recoverable": bool(stale_name in stale_recover),
            "conflict_detected": bool(payload["conflict"]["new"] in pending),
            "conflict_not_active": bool(payload["conflict"]["new"] not in conflict_active),
            "conflict_old_active": bool(payload["conflict"]["old"] in conflict_active),
            "why_coverage": round(sum(1 for row in rows if any(row["why"])) / max(1, len(rows)), 3),
        }
        result = {"version": payload.get("version"), "metrics": metrics,
                  "rows": rows, "pending_conflicts": pending,
                  "conflict_active": conflict_active, "stale_recover": stale_recover}
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        lines = ["| 指标 | 结果 |", "|---|---:|"]
        for key, value in metrics.items():
            lines.append(f"| {key} | {value} |")
        lines += ["", "| id | kind | query | expected | top5 | recall@5 |",
                  "|---|---|---|---|---|---:|"]
        for row in rows:
            lines.append(f"| {row['id']} | {row['kind']} | {row['query']} | "
                         f"{', '.join(row['expected']) or '—'} | "
                         f"{', '.join(row['top5']) or '—'} | {row['recall_at_5']} |")
        table = "\n".join(lines)
        if args.md:
            Path(args.md).write_text(table + "\n", encoding="utf-8")
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())