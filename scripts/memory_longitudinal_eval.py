"""30-day longitudinal memory simulation.

This is deliberately synthetic: it exercises updates, a resolved conflict,
expiry and retrieval at day 30.  It is not a substitute for a real-user
30-day human evaluation; see scripts/memory_human_eval.py for that workflow.
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

DEFAULT = ROOT / "benchmarks" / "memory" / "longitudinal_30d.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", default=str(DEFAULT))
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()
    payload = json.loads(Path(args.timeline).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="mv_memory_30d_") as tmp:
        root = Path(tmp) / "project"
        store = FactStore(root)
        pending_after_conflict = 0
        conflict_detected = False
        for event in payload["events"]:
            if event["op"] == "write":
                metadata = {}
                if event.get("owner"):
                    metadata["owner"] = event["owner"]
                if event.get("due"):
                    metadata["due"] = event["due"]
                store.write(
                    name=event["name"], description=event["description"],
                    body=event["body"], fact_type=event["type"],
                    origin_meeting=f"day-{event['day']}",
                    status=event.get("status", "active"),
                    valid_to=event.get("valid_to", ""),
                    metadata=metadata,
                )
                if store.pending_conflicts():
                    pending_after_conflict = len(store.pending_conflicts())
                    conflict_detected = True
            elif event["op"] == "resolve":
                store.resolve_conflict(event["name"], event["resolution"])
            elif event["op"] == "expire":
                store.expire(event["name"])
        rows = []
        for query in payload["queries"]:
            top = store.search(query["query"], limit=5)
            names = [row["name"] for row in top]
            expected = query["expected"]
            if expected:
                recall = sum(1 for name in expected if name in names) / len(expected)
            else:
                recall = 1.0 if not names else 0.0
            rows.append({"id": query["id"], "query": query["query"],
                         "expected": expected, "top5": names,
                         "recall_at_5": round(recall, 3)})
        active = store.list_facts(limit=100)
        all_facts = store.list_facts(limit=100, status="all", include_inactive=True)
        reloaded = FactStore(root)
        persisted = {fact["name"] for fact in reloaded.list_facts(limit=100)}
        metrics = {
            "days": payload.get("days", 30),
            "events": len(payload["events"]),
            "questions": len(rows),
            "recall_at_5": round(sum(row["recall_at_5"] for row in rows) / max(1, len(rows)), 3),
            "negative_accuracy": round(
                sum(1 for row in rows if not row["expected"] and row["recall_at_5"] == 1.0)
                / max(1, sum(1 for row in rows if not row["expected"])), 3),
            "active_facts": len(active),
            "versioned_facts": sum(1 for fact in all_facts if int(fact.get("version", 1)) > 1),
            "conflict_detected": conflict_detected,
            "pending_after_conflict": pending_after_conflict,
            "pending_final": len(store.pending_conflicts()),
            "expired_visible_in_default": any(
                row["name"] == "外部接口风险" for row in store.search("外部接口风险")),
            "persisted_after_reload": all(
                name in persisted for name in ("交付计划变更", "测试安排", "需求整理")),
        }
        result = {"ok": True, "synthetic": True, "metrics": metrics, "rows": rows,
                  "note": payload.get("note", "")}
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        lines = ["| 指标 | 结果 |", "|---|---:|"]
        for key, value in metrics.items():
            lines.append(f"| {key} | {value} |")
        lines += ["", "| id | query | expected | top5 | recall@5 |", "|---|---|---|---|---:|"]
        for row in rows:
            lines.append(f"| {row['id']} | {row['query']} | "
                         f"{', '.join(row['expected']) or '—'} | "
                         f"{', '.join(row['top5']) or '—'} | {row['recall_at_5']} |")
        table = "\n".join(lines)
        if args.md:
            Path(args.md).write_text(table + "\n", encoding="utf-8")
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())