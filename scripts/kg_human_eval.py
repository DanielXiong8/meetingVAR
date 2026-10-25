"""Human-labelled KG extraction evaluation export/score.

Export graph edges (with evidence quotes) for a human to mark:
head/tail entity correctness, relation correctness, evidence support.
No labels are invented; blank labels are excluded from human metrics.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.kg.store import GraphStore  # noqa: E402

FIELDS = ["id", "head", "relation", "tail", "status", "valid_from", "session_id",
          "quote", "head_correct", "tail_correct", "relation_correct",
          "evidence_supports", "notes"]


def _export(store: GraphStore, out: Path, samples: int) -> dict:
    rows = []
    for node in store.nodes(limit=max(1, samples * 4)):
        for edge in store.timeline(node["id"], limit=max(1, samples * 4))["edges"]:
            if len(rows) >= samples:
                break
            src, dst = store.get_node(edge["src_id"]), store.get_node(edge["dst_id"])
            rows.append({
                "id": edge["id"], "head": (src or {}).get("name", ""),
                "relation": edge["relation"], "tail": (dst or {}).get("name", ""),
                "status": edge["status"], "valid_from": edge["valid_from"],
                "session_id": edge.get("session_id", ""),
                "quote": (edge.get("quote") or "")[:800],
                "head_correct": "", "tail_correct": "", "relation_correct": "",
                "evidence_supports": "", "notes": "",
            })
        if len(rows) >= samples:
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"ok": True, "exported": len(rows), "out": str(out),
            "instruction": "对每条边填 0/1：head/tail 实体是否正确、关系是否正确、证据是否支持；不要看模型自评。"}


def _score(store: GraphStore, labels: Path, out: Path | None) -> dict:
    rows = []
    with labels.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            values = {}
            for key in ("head_correct", "tail_correct", "relation_correct",
                        "evidence_supports"):
                raw = str(row.get(key, "")).strip()
                values[key] = raw == "1"
            if not any(str(row.get(key, "")).strip() in {"0", "1"} for key in
                       ("head_correct", "tail_correct", "relation_correct",
                        "evidence_supports")):
                continue
            values["triple_correct"] = all(values[key] for key in
                                           ("head_correct", "tail_correct",
                                            "relation_correct"))
            values["id"] = row.get("id", "")
            values["head"] = row.get("head", "")
            values["relation"] = row.get("relation", "")
            values["tail"] = row.get("tail", "")
            values["quote"] = row.get("quote", "")
            rows.append(values)
    total = len(rows)
    metrics = {
        "labeled_edges": total,
        "head_accuracy": round(sum(1 for r in rows if r["head_correct"]) / max(1, total), 3),
        "tail_accuracy": round(sum(1 for r in rows if r["tail_correct"]) / max(1, total), 3),
        "relation_accuracy": round(sum(1 for r in rows if r["relation_correct"]) / max(1, total), 3),
        "evidence_support": round(sum(1 for r in rows if r["evidence_supports"]) / max(1, total), 3),
        "triple_precision": round(sum(1 for r in rows if r["triple_correct"]) / max(1, total), 3),
    }
    report = {"ok": True, "metrics": metrics, "rows": rows,
              "warning": "Only human-labelled rows count; synthetic/LLM labels are invalid."}
    if out:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--export", default="")
    parser.add_argument("--labels", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--samples", type=int, default=50)
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_dir():
        raise SystemExit(f"项目文件夹无效: {project}")
    store = GraphStore(project)
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