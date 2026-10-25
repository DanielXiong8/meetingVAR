"""KG scale benchmark: 10k nodes / 100k-1M edges.

Measures bulk import, neighbor expansion, shortest path, stats and DB size.
Synthetic graph only; no meeting content.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.kg.store import GraphStore  # noqa: E402

KINDS = ["Person", "Project", "Action", "Risk", "Decision", "Meeting", "Topic"]
RELATIONS = ["assigned_to", "part_of", "about", "depends_on", "blocks", "related_to"]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * p))], 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=10000)
    parser.add_argument("--edges", type=int, default=100000)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mv_kg_scale_") as tmp:
        root = Path(tmp) / "scale"
        store = GraphStore(root)
        node_ids = [f"{KINDS[i % len(KINDS)].casefold()}:{i}" for i in range(args.nodes)]
        nodes = [{"id": node_ids[i], "kind": KINDS[i % len(KINDS)],
                  "name": f"实体{i}", "description": "scale benchmark"}
                 for i in range(args.nodes)]

        def edge_iter():
            for i in range(args.edges):
                if i < args.nodes - 1:
                    src, dst = node_ids[i], node_ids[i + 1]
                else:
                    src = node_ids[i % args.nodes]
                    dst = node_ids[(i * 17 + 7) % args.nodes]
                    if src == dst:
                        dst = node_ids[(i + 1) % args.nodes]
                yield {"id": f"scale:e{i}", "src_id": src, "dst_id": dst,
                       "relation": RELATIONS[i % len(RELATIONS)],
                       "valid_from": "2026-01-01T00:00:00+0800",
                       "source_type": "scale", "source_id": "benchmark",
                       "confidence": 0.8}

        t0 = time.time()
        seeded = store.bulk_seed(nodes, edge_iter())
        import_s = round(time.time() - t0, 2)
        rng = random.Random(42)
        seeds = [rng.choice(node_ids) for _ in range(args.queries)]
        neighbor_times = []
        path_times = []
        neighbor_edges = 0
        path_found = 0
        for seed in seeds:
            started = time.time()
            result = store.neighbors(seed, depth=2)
            neighbor_times.append((time.time() - started) * 1000)
            neighbor_edges += len(result.get("edges", []))
            index = node_ids.index(seed)
            other = node_ids[min(args.nodes - 1, index + 3)]
            started = time.time()
            path = store.path(seed, other, max_depth=3)
            path_times.append((time.time() - started) * 1000)
            path_found += 1 if path.get("found") else 0
        stats = store.stats()
        db_bytes = Path(store.db_path).stat().st_size if Path(store.db_path).exists() else 0
        metrics = {
            "nodes": seeded["nodes"], "edges": seeded["edges"],
            "import_seconds": import_s,
            "nodes_per_second": round(seeded["nodes"] / import_s, 1) if import_s else 0,
            "edges_per_second": round(seeded["edges"] / import_s, 1) if import_s else 0,
            "neighbors_p50_ms": _percentile(neighbor_times, .50),
            "neighbors_p95_ms": _percentile(neighbor_times, .95),
            "path_p50_ms": _percentile(path_times, .50),
            "path_p95_ms": _percentile(path_times, .95),
            "avg_neighbor_edges": round(neighbor_edges / max(1, len(seeds)), 1),
            "path_found_rate": round(path_found / max(1, len(seeds)), 3),
            "db_mb": round(db_bytes / 1024 / 1024, 1),
            "by_relation": stats.get("by_relation", {}),
        }
        result = {"ok": True, "metrics": metrics}
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        lines = ["| 指标 | 值 |", "|---|---:|"]
        for key, value in metrics.items():
            if key != "by_relation":
                lines.append(f"| {key} | {value} |")
        table = "\n".join(lines)
        if args.md:
            Path(args.md).write_text(table + "\n", encoding="utf-8")
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())