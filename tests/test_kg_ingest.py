"""Phase 1 extraction, entity linking and incremental meeting ingestion."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.kg.extract import parse_extraction  # noqa: E402
from meetingvar.kg.ingest import GraphIngestor  # noqa: E402
from meetingvar.kg.store import GraphStore  # noqa: E402


def fake_llm(payload):
    def call(prompt):
        return {"ok": True, "content": json.dumps(payload, ensure_ascii=False)}
    return call


class GraphIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_kg_ingest_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "星河项目"
        self.project.mkdir()
        self.data = Path(self.tmp.name) / "data"
        pool = self.data / "pool" / "s1"
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({
            "transcript": [
                {"ts": 1, "speaker": "小林",
                 "text": "小林决定采用第二方案，周五交付第一版。"},
                {"ts": 2, "speaker": "小王", "text": "测试时间不足是风险。"},
            ]}, ensure_ascii=False), encoding="utf-8")
        store = FactStore(self.project)
        store.write(name="decision: 采用第二方案", description="采用第二方案",
                    body="决定采用第二方案。", fact_type="decision",
                    origin_meeting="s1")
        self.payload = {
            "entities": [
                {"surface": "小林", "kind": "Person", "aliases": [],
                 "evidence_turn_ids": ["turn_1"], "confidence": 0.9},
                {"surface": "周五交付第一版", "kind": "Action", "aliases": [],
                 "evidence_turn_ids": ["turn_1"], "confidence": 0.9},
                {"surface": "周五", "kind": "Date", "aliases": [],
                 "evidence_turn_ids": ["turn_1"], "confidence": 0.9},
            ],
            "relations": [
                {"head": "周五交付第一版", "relation": "assigned_to", "tail": "小林",
                 "evidence_turn_ids": ["turn_1"], "confidence": 0.85},
                {"head": "周五交付第一版", "relation": "due_on", "tail": "周五",
                 "evidence_turn_ids": ["turn_1"], "confidence": 0.85},
            ],
        }

    def test_ingest_links_existing_and_adds_edge(self):
        ingestor = GraphIngestor(self.project, self.data, llm=fake_llm(self.payload))
        report = ingestor.ingest_session("s1")
        self.assertTrue(report["ok"], report)
        self.assertGreaterEqual(report["entities"], 2)
        self.assertEqual(report["relations"], 2)
        graph = GraphStore(self.project)
        person = graph.resolve("小林", kind="Person")
        action = graph.resolve("周五交付第一版", kind="Action")
        self.assertIsNotNone(person)
        self.assertIsNotNone(action)
        edges = graph.timeline(action["id"])["edges"]
        self.assertTrue(any(edge["relation"] == "assigned_to"
                            and edge["dst_id"] == person["id"] for edge in edges))
        self.assertTrue(any(edge["relation"] == "due_on" for edge in edges))
        self.assertTrue(any(edge["extractor"] == "llm" for edge in edges))

    def test_reingest_skips_same_episode(self):
        ingestor = GraphIngestor(self.project, self.data, llm=fake_llm(self.payload))
        first = ingestor.ingest_session("s1")
        second = ingestor.ingest_session("s1")
        self.assertTrue(first["ok"])
        self.assertEqual(second["skipped"], first["chunks"])
        self.assertEqual(second["relations"], 0)

    def test_relation_endpoint_is_inferred_from_domain(self):
        chunk = {"id": "x", "turns": [{"id": "turn_1", "speaker": "小林",
                                        "text": "小林负责周五交付第一版。"}],
                 "text": "[turn_1] 小林: 小林负责周五交付第一版。"}
        parsed = parse_extraction(json.dumps({
            "entities": [{"surface": "小林", "kind": "Person",
                          "evidence_turn_ids": ["turn_1"], "confidence": 0.9}],
            "relations": [{"head": "周五交付第一版", "relation": "assigned_to",
                           "tail": "小林", "evidence_turn_ids": ["turn_1"],
                           "confidence": 0.8}],
        }), chunk)
        self.assertEqual(len(parsed["relations"]), 1)
        inferred = [entity for entity in parsed["entities"] if entity.get("inferred")]
        self.assertEqual(inferred[0]["kind"], "Action")

    def test_invalid_evidence_is_rejected(self):
        chunk = {"id": "x", "turns": [{"id": "turn_1", "speaker": "小林",
                                        "text": "小林采用第二方案。"}],
                 "text": "[turn_1] 小林: 小林采用第二方案。"}
        parsed = parse_extraction(json.dumps({
            "entities": [{"surface": "小林", "kind": "Person",
                          "evidence_turn_ids": ["turn_99"], "confidence": 0.9}],
            "relations": [],
        }), chunk)
        self.assertEqual(parsed["entities"], [])


if __name__ == "__main__":
    unittest.main()