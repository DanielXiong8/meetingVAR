"""Export/score a human-labelled project-memory evaluation.

The synthetic 40-question benchmark is a regression suite.  This tool is for
real-user evaluation: it exports sampled facts as an annotation template, then
scores retrieval after a human fills the labels.  It never invents labels.

Export:
  python scripts/memory_human_eval.py --project "D:/项目/X" --export labels.csv

Score:
  python scripts/memory_human_eval.py --project "D:/项目/X" --labels labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402

FIELDS = ["id", "question", "expected_fact", "fact_type", "created_at",
          "label_relevant", "label_correct_source", "label_stale", "notes"]


def _export(store: FactStore, out: Path, samples: int) -> dict:
    facts = store.list_facts(limit=max(1, samples))
    rows = []
    for index, fact in enumerate(facts, 1):
        description = str(fact.get("description") or fact.get("name") or "")
        rows.append({
            "id": f"h{index:03d}",
            "question": "",  # human writes a natural question
            "expected_fact": fact.get("name", ""),
            "fact_type": fact.get("type", ""),
            "created_at": fact.get("created_at", ""),
            "label_relevant": "",
            "label_correct_source": "",
            "label_stale": "",
            "notes": f"事实描述：{description[:300]}",
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"ok": True, "exported": len(rows), "out": str(out),
            "instruction": "为每条事实写一个自然问题；label_* 填 0/1；不要看 expected_fact 后反向编题。"}


def _score(store: FactStore, labels: Path, out: Path | None) -> dict:
    rows = []
    with labels.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            question = str(row.get("question") or "").strip()
            expected = str(row.get("expected_fact") or "").strip()
            if not question:
                continue
            top = store.search(question, limit=5)
            names = [item.get("name", "") for item in top]
            relevant = str(row.get("label_relevant", "")).strip() == "1"
            correct = str(row.get("label_correct_source", "")).strip() == "1"
            stale = str(row.get("label_stale", "")).strip() == "1"
            rows.append({
                "id": row.get("id", ""), "question": question,
                "expected": expected, "top5": names,
                "recall_at_5": 1.0 if expected and expected in names else 0.0,
                "top1_correct": 1.0 if expected and names[:1] == [expected] else 0.0,
                "human_relevant": relevant, "human_correct_source": correct,
                "human_stale": stale,
                "_labeled": any(str(row.get(key, "")).strip() in {"0", "1"} for key in
                                ("label_relevant", "label_correct_source", "label_stale")),
            })
    total = len(rows)
    labeled = [row for row in rows if row.get("_labeled")]
    report = {
        "ok": True,
        "questions": total,
        "labeled_questions": len(labeled),
        "recall_at_5": round(sum(r["recall_at_5"] for r in rows) / max(1, total), 3),
        "top1_accuracy": round(sum(r["top1_correct"] for r in rows) / max(1, total), 3),
        "human_relevant_rate": round(
            sum(1 for r in labeled if r["human_relevant"]) / max(1, len(labeled)), 3),
        "human_correct_source_rate": round(
            sum(1 for r in labeled if r["human_correct_source"]) / max(1, len(labeled)), 3),
        "human_stale_rate": round(
            sum(1 for r in labeled if r["human_stale"]) / max(1, len(labeled)), 3),
        "rows": [{key: value for key, value in row.items() if key != "_labeled"}
                 for row in rows],
        "warning": "Only rows with human labels are meaningful; blank labels are excluded from human rates.",
    }
    if out:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--export", default="")
    parser.add_argument("--labels", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"项目文件夹无效: {project}")
    store = FactStore(project)
    if args.export:
        report = _export(store, Path(args.export), args.samples)
    elif args.labels:
        report = _score(store, Path(args.labels), Path(args.out) if args.out else None)
    else:
        raise SystemExit("需要 --export 或 --labels")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())