"""GraphRAG: graph seeds + temporal edges + RAG chunks + citations."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.kg.build import GraphBuilder  # noqa: E402
from meetingvar.kg.graphrag import GraphRAG  # noqa: E402
from meetingvar.rag import RagIndex  # noqa: E402


class GraphRagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_graphrag_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "星河项目"
        self.project.mkdir()
        self.data = Path(self.tmp.name) / "data"
        pool = self.data / "pool" / "s1"
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({
            "transcript": [
                {"ts": 1, "speaker": "小林", "text": "决定采用第二方案，小林负责周五交付第一版。"},
                {"ts": 2, "speaker": "小王", "text": "测试时间不足会影响交付。"},
            ]}, ensure_ascii=False), encoding="utf-8")
        (self.project / "需求.md").write_text(
            "星河项目要求周五交付第一版，负责人小林。", encoding="utf-8")
        store = FactStore(self.project)
        store.write(name="decision: 采用第二方案", description="采用第二方案",
                    body="决定采用第二方案。", fact_type="decision",
                    origin_meeting="s1")
        store.write(name="action: 周五交付第一版", description="周五交付第一版",
                    body="负责人小林，周五交付第一版。", fact_type="action-item",
                    origin_meeting="s1", metadata={"owner": "小林", "due": "周五"})
        GraphBuilder(self.project, self.data).build()
        RagIndex(self.project, self.data).index_project()

    def test_search_expands_graph_and_keeps_edge_evidence(self):
        rag = GraphRAG(self.project, self.data)
        result = rag.search("谁负责周五交付第一版", hops=2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["seeds"])
        relations = {edge["relation"] for edge in result["edges"]}
        self.assertIn("assigned_to", relations)
        self.assertTrue(any(item["kind"] == "graph" for item in result["context"]))
        assigned = next(edge for edge in result["edges"] if edge["relation"] == "assigned_to")
        person = rag.store.resolve("小林", kind="Person")
        self.assertEqual(assigned["dst_id"], person["id"])

    def test_answer_accepts_graph_citation(self):
        from meetingvar.model_client import CallableModelClient
        rag = GraphRAG(self.project, self.data)
        client = CallableModelClient(lambda messages, **kwargs: {
            "ok": True, "content": "小林负责周五交付第一版。[G1]",
            "usage": {"prompt_tokens": 20, "completion_tokens": 5}})
        report = rag.answer("谁负责周五交付？", model_client=client)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["source_validated"], report)
        self.assertFalse(report["semantic_verified"])
        self.assertFalse(report["grounded"], report)
        self.assertEqual(report["citations"][0]["kind"], "edge")

    def test_temporal_query_includes_history(self):
        rag = GraphRAG(self.project, self.data)
        result = rag.search("之前交付时间是什么", hops=2)
        self.assertTrue(result["temporal"])


class GraphRerankPolicyTests(unittest.TestCase):
    """Agent retrieval can opt out; direct callers keep the existing default."""

    def setUp(self):
        from types import SimpleNamespace
        self.tmp = tempfile.TemporaryDirectory(prefix="mv-rerank-policy-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "empty-data"
        provider = SimpleNamespace(available=False, name="synthetic")
        patcher = patch("meetingvar.rag.get_embedding_provider", return_value=provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        for index in range(3):
            (self.root / f"source{index}.txt").write_text(
                f"Alpha searchable evidence source {index}.", encoding="utf-8")
        self.rag = RagIndex(self.root, self.data)
        report = self.rag.index_project(include_meetings=False)
        self.assertTrue(report["ok"], report)
        self.graph = GraphRAG(self.root, self.data)

    def assert_no_hidden_generation(self, search):
        from meetingvar.agent.rerank import llm_rerank
        # Force the model-available branch: a regression must fail, not silently
        # pass because the offline machine has no configured global model.
        with patch("meetingvar.agent.rerank._llm_available", return_value=True), \
             patch("meetingvar.llm.chat_result", side_effect=AssertionError(
                 "global generation must not run during agent retrieval")) as global_model, \
             patch("meetingvar.rag.llm_rerank", wraps=llm_rerank) as reranker, \
             patch.object(self.graph, "seed_nodes", return_value=[]):
            hits = search()
            self.assertGreaterEqual(len(hits), 2)
            reranker.assert_not_called()
            global_model.assert_not_called()

    def test_rag_rerank_false_rejects_global_model(self):
        self.assert_no_hidden_generation(
            lambda: self.rag.search("Alpha", rerank=False))

    def test_graph_rerank_false_rejects_global_model(self):
        self.assert_no_hidden_generation(
            lambda: self.graph.search("Alpha", rerank=False)["chunks"])

    def test_direct_graph_default_and_explicit_true_keep_reranking(self):
        for options in ({}, {"rerank": True}):
            with self.subTest(options=options), \
                 patch.object(self.graph, "seed_nodes", return_value=[]), \
                 patch("meetingvar.llm.chat_result", side_effect=AssertionError(
                     "offline test must not call a global model")) as global_model, \
                 patch("meetingvar.rag.llm_rerank",
                       side_effect=lambda query, rows, top_k: rows[:top_k]) as reranker:
                report = self.graph.search("Alpha", **options)
                self.assertGreaterEqual(len(report["chunks"]), 2)
                reranker.assert_called_once()
                global_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()