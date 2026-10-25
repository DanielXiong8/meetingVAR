"""Phase 0 knowledge graph: rule-built nodes/edges, temporal queries, paths."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.kg.build import GraphBuilder  # noqa: E402
from meetingvar.kg.store import GraphStore  # noqa: E402
from meetingvar.agent.meeting_tools import build_meeting_registry  # noqa: E402


class KnowledgeGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_kg_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "星河项目"
        self.project.mkdir()
        self.data = Path(self.tmp.name) / "data"
        pool = self.data / "pool" / "s1"
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({
            "transcript": [
                {"ts": 1, "speaker": "小林", "text": "我们决定采用第二方案，周五交付第一版。"},
                {"ts": 2, "speaker": "小王", "text": "风险是测试时间不足。"},
            ]}, ensure_ascii=False), encoding="utf-8")
        store = FactStore(self.project)
        store.write(name="decision: 采用第二方案", description="采用第二方案",
                    body="决定采用第二方案，负责人小林。", fact_type="decision",
                    origin_meeting="s1", metadata={"owner": "小林"})
        store.write(name="action: 周五交付第一版", description="周五交付第一版",
                    body="周五交付第一版，负责人小林。", fact_type="action-item",
                    origin_meeting="s1", metadata={"owner": "小林", "due": "周五"})
        store.write(name="risk: 测试时间不足", description="测试时间不足",
                    body="测试时间不足，可能延期。", fact_type="risk",
                    origin_meeting="s1")
        store.write(name="action: 旧版本兼容", description="旧版本兼容",
                    body="旧版本兼容已解决。", fact_type="action-item",
                    origin_meeting="s1", status="expired",
                    valid_to="2025-01-01T00:00:00+0800")
        self.builder = GraphBuilder(self.project, self.data)
        self.report = self.builder.build()
        self.graph = GraphStore(self.project)

    def test_build_stats_and_find(self):
        self.assertTrue(self.report["ok"])
        self.assertGreaterEqual(self.report["nodes"], 8)
        self.assertGreaterEqual(self.report["edges"], 10)
        people = self.graph.find_nodes("小林", kind="Person")
        self.assertEqual(people[0]["kind"], "Person")
        exact = self.graph.resolve("采用第二方案")
        self.assertEqual(exact["kind"], "Decision")

    def test_neighbors_and_path(self):
        action = self.graph.resolve("周五交付第一版")
        person = self.graph.resolve("小林")
        project = self.graph.resolve("星河项目")
        neighbors = self.graph.neighbors(action["id"], depth=2)
        names = {node["name"] for node in neighbors["nodes"]}
        self.assertIn("小林", names)
        self.assertIn("会议 s1", names)
        self.assertIn("星河项目", names)
        path = self.graph.path(action["id"], person["id"], max_depth=2)
        self.assertTrue(path["found"])
        self.assertEqual(path["nodes"][-1]["name"], "小林")
        path2 = self.graph.path(action["id"], project["id"], max_depth=2)
        self.assertTrue(path2["found"])

    def test_timeline_and_expired_edge(self):
        action = self.graph.resolve("周五交付第一版")
        timeline = self.graph.timeline(action["id"])
        relations = {edge["relation"] for edge in timeline["edges"]}
        self.assertTrue({"assigned_to", "due_on", "derived_from", "part_of"} <= relations)
        old = self.graph.resolve("旧版本兼容")
        self.assertIsNotNone(old)
        # The expired fact's part_of/derived_from edges have valid_to in the past.
        active_neighbors = self.graph.neighbors(old["id"], depth=1)
        self.assertEqual(active_neighbors["edges"], [])

    def test_agent_tools_are_registered(self):
        names = {spec.name for spec in build_meeting_registry().specs()}
        self.assertTrue({"kg_find_entities", "kg_neighbors", "kg_timeline",
                         "kg_path", "kg_stats"} <= names)

    def test_merge_and_review_operations(self):
        a = self.graph.upsert_node(kind="Person", name="林工")
        b = self.graph.upsert_node(kind="Person", name="小林")
        action = self.graph.resolve("周五交付第一版")
        self.graph.upsert_edge(src_id=action["id"], dst_id=a["id"],
                               relation="assigned_to",
                               valid_from="2026-03-11T00:00:00+0800",
                               source_type="test", source_id="merge")
        merged = self.graph.merge_nodes(a["id"], b["id"])
        self.assertIn("林工", merged["aliases"])
        self.assertEqual(self.graph.get_node(a["id"])["status"], "merged")
        edges = self.graph.timeline(b["id"])["edges"]
        self.assertTrue(any(edge["relation"] == "assigned_to" for edge in edges))
        reviewed = self.graph.review_edge(edges[0]["id"], "retracted")
        self.assertEqual(reviewed["status"], "retracted")

    def test_rebuild_is_idempotent(self):
        first = self.report
        second = self.builder.build()
        self.assertEqual((first["nodes"], first["edges"]),
                         (second["nodes"], second["edges"]))
        self.assertEqual(self.graph.stats()["nodes"], first["nodes"])


if __name__ == "__main__":
    unittest.main()