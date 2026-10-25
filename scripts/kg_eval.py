"""Knowledge-graph Phase 2 evaluation (GraphRAG vs RAG-only evidence).

Synthetic project: rules graph + one meeting + one requirement document.
Measures entity alias resolution, neighbors, path, timeline, temporal
invalidation, graph evidence recall, and GraphRAG citation grounding.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.kg.build import GraphBuilder  # noqa: E402
from meetingvar.kg.graphrag import GraphRAG  # noqa: E402
from meetingvar.kg.store import GraphStore  # noqa: E402
from meetingvar.rag import RagIndex  # noqa: E402


def _project(root: Path, data: Path) -> GraphStore:
    project = root / "星河项目"
    project.mkdir(parents=True)
    pool = data / "pool" / "s1"
    pool.mkdir(parents=True)
    rows = [
        {"ts": 100, "speaker": "小林", "text": "小林决定采用第二方案，周五交付第一版。"},
        {"ts": 200, "speaker": "小王", "text": "测试时间不足会影响交付。"},
        {"ts": 300, "speaker": "小林", "text": "交付改到周一，负责人小林。"},
    ]
    (pool / "transcript.json").write_text(
        json.dumps({"transcript": rows}, ensure_ascii=False), encoding="utf-8")
    (project / "需求.md").write_text(
        "星河项目要求周五交付第一版，负责人小林。测试必须覆盖回归。",
        encoding="utf-8")
    facts = FactStore(project)
    facts.write(name="decision: 采用第二方案", description="采用第二方案",
                body="决定采用第二方案。", fact_type="decision", origin_meeting="s1")
    facts.write(name="action: 周五交付第一版", description="周五交付第一版",
                body="负责人小林，周五交付第一版。", fact_type="action-item",
                origin_meeting="s1", metadata={"owner": "小林", "due": "周五"})
    facts.write(name="risk: 测试时间不足", description="测试时间不足",
                body="测试时间不足，可能影响交付。", fact_type="risk", origin_meeting="s1")
    GraphBuilder(project, data).build()
    store = GraphStore(project)
    person = store.resolve("小林", kind="Person")
    if person:
        store.upsert_node(kind="Person", name="小林", aliases=["林工"], confidence=0.9)
    action = store.resolve("周五交付第一版", kind="Action")
    if action:
        # A later due date invalidates the previous singleton due_on edge.
        later = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 3600))
        store.upsert_edge(
            src_id=action["id"], dst_id=store.upsert_node(kind="Date", name="周一")["id"],
            relation="due_on", valid_from=later, source_type="meeting", source_id="s1",
            session_id="s1", turn_ids=["turn_3"], quote="交付改到周一", confidence=0.9,
            extractor="llm")
    RagIndex(project, data).index_project()
    return store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="")
    parser.add_argument("--md", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mv_kg_eval_") as tmp:
        store = _project(Path(tmp), Path(tmp) / "data")
        project = Path(tmp) / "星河项目"
        data = Path(tmp) / "data"
        rows = []

        def record(name, passed, detail="", *, check=True):
            rows.append({"name": name, "pass": bool(passed), "detail": detail,
                         "check": bool(check)})

        record("entity_alias", (store.find_nodes("林工") or [{}])[0].get("name") == "小林",
               str([node["name"] for node in store.find_nodes("林工")]))
        action = store.resolve("周五交付第一版")
        neighbors = store.neighbors(action["id"], depth=1) if action else {"edges": []}
        record("neighbor_assigned", any(edge["relation"] == "assigned_to"
                                         for edge in neighbors["edges"]),
               str(sorted({edge["relation"] for edge in neighbors["edges"]})))
        project_node = store.resolve("星河项目")
        path = store.path(action["id"], project_node["id"], max_depth=2) if action and project_node else {}
        record("path_action_project", bool(path.get("found")), str(len(path.get("nodes", []))))
        timeline = store.timeline(action["id"]) if action else {"edges": []}
        due = [edge for edge in timeline["edges"] if edge["relation"] == "due_on"]
        record("timeline_due", len(due) >= 2, f"due_edges={len(due)}")
        active_now = [edge for edge in store.neighbors(action["id"], depth=1)["edges"]
                      if edge["relation"] == "due_on"]
        record("temporal_invalidation", len(active_now) == 1
               and active_now[0].get("dst_id") == store.resolve("周一")["id"],
               f"active_due={len(active_now)}")

        rag = GraphRAG(project, data)
        started = time.time()
        search = rag.search("谁负责周五交付第一版", hops=2)
        graph_ms = (time.time() - started) * 1000
        record("graphrag_edge_recall", any(item["kind"] == "graph"
                                           for item in search["context"]),
               f"context={len(search['context'])}")
        record("graphrag_doc_recall", any(item["kind"] == "rag"
                                          for item in search["context"]),
               f"chunks={len(search['chunks'])}")
        rag_only = RagIndex(project, data).search("谁负责周五交付第一版", limit=8, rerank=False)
        record("rag_only_edge_recall", any("assigned_to" in item.get("text", "")
                                            for item in rag_only),
               f"rag_chunks={len(rag_only)}", check=False)

        fake = {"ok": True, "content": "小林负责周五交付第一版。[G1]",
                "usage": {"total_tokens": 20}}
        with patch("meetingvar.llm.chat_result", return_value=fake):
            answer = rag.answer("谁负责周五交付第一版？")
        record("graphrag_citation", bool(answer.get("grounded")),
               str([c.get("kind") for c in answer.get("citations", [])]))

        metrics = {
            "checks": len(rows),
            "checked": sum(1 for row in rows if row.get("check", True)),
            "passed": sum(1 for row in rows if row["pass"] and row.get("check", True)),
            "pass_rate": round(sum(1 for row in rows if row["pass"] and row.get("check", True))
                               / max(1, sum(1 for row in rows if row.get("check", True))), 3),
            "graphrag_retrieval_ms": round(graph_ms, 1),
            "graph_nodes": store.stats()["nodes"],
            "graph_edges": store.stats()["edges"],
        }
        result = {"ok": True, "metrics": metrics, "rows": rows}
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        lines = ["| 检查 | 结果 | 详情 |", "|---|---|---|"]
        for row in rows:
            lines.append(f"| {row['name']} | {'PASS' if row['pass'] else 'FAIL'} | {row['detail']} |")
        lines += ["", "| 指标 | 值 |", "|---|---:|"]
        for key, value in metrics.items():
            lines.append(f"| {key} | {value} |")
        table = "\n".join(lines)
        if args.md:
            Path(args.md).write_text(table + "\n", encoding="utf-8")
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())