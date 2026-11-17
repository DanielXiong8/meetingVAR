"""RAG index: project documents + meeting transcripts with citations."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unittest.mock import patch

from meetingvar.rag import RagIndex  # noqa: E402


class RagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_rag_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        (self.project / "docs").mkdir(parents=True)
        (self.project / "docs" / "需求.md").write_text(
            "# 星河项目\n客户要求增加数据导出功能，负责人张三，年底上线。\n",
            encoding="utf-8")
        self.data = Path(self.tmp.name) / "data"
        session = self.data / "pool" / "s1"
        session.mkdir(parents=True)
        (session / "transcript.json").write_text(json.dumps({
            "session_id": "s1",
            "transcript": [
                {"ts": 10, "speaker": "小林", "text": "我们决定采用第二方案，周五交付第一版。"},
                {"ts": 20, "speaker": "小王", "text": "风险是测试时间不足。"},
            ]}, ensure_ascii=False), encoding="utf-8")
        (self.project / "meetingvar" / "minutes" / "s1").mkdir(parents=True)
        self.index = RagIndex(self.project, self.data)

    def test_index_search_and_read_chunk(self):
        report = self.index.index_project()
        self.assertTrue(report["ok"])
        self.assertEqual(report["documents"], 2)
        self.assertGreaterEqual(report["chunks"], 2)
        docs = self.index.search("客户要求什么", rerank=False)
        self.assertEqual(docs[0]["source_type"], "document")
        self.assertIn("数据导出", docs[0]["text"])
        # Lexical-only retrieval must not depend on a local embedding model.
        filtered = self.index.search("谁负责交付", source_type="meeting", rerank=False)
        self.assertTrue(filtered)
        self.assertTrue(all(hit["source_type"] == "meeting" for hit in filtered))
        meetings = self.index.search("第二方案交付", rerank=False)
        self.assertEqual(meetings[0]["source_type"], "meeting")
        self.assertEqual(meetings[0]["source_id"], "s1")
        self.assertEqual(meetings[0]["start_ts"], 10)
        chunk = self.index.read_chunk(meetings[0]["chunk_id"])
        self.assertIn("第二方案", chunk["text"])

    def test_incremental_skip_and_force(self):
        self.index.index_project()
        second = self.index.index_project()
        self.assertEqual(second["documents"], 0)
        self.assertGreaterEqual(second["skipped"], 2)
        forced = self.index.index_project(force=True)
        self.assertEqual(forced["documents"], 2)

    def test_answer_returns_document_citation(self):
        from meetingvar.model_client import CallableModelClient
        self.index.index_project()
        client = CallableModelClient(lambda messages, **kwargs: {
            "ok": True, "content": "客户要求增加数据导出。[K1]",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        report = self.index.answer("客户要求什么", model_client=client)
        self.assertTrue(report["ok"], report)
        # An observed chunk supports traceability, not semantic correctness.
        self.assertTrue(report["source_validated"])
        self.assertFalse(report["semantic_verified"])
        self.assertFalse(report["grounded"])
        self.assertEqual(report["citations"][0]["kind"], "document")
        self.assertIn("需求.md", report["citations"][0]["title"])
        self.assertEqual(report["usage"]["model_calls"], 1)

    def test_answer_does_not_turn_broken_index_into_absence(self):
        self.index.path.parent.mkdir(parents=True, exist_ok=True)
        self.index.path.write_bytes(b"not a sqlite database")
        report = self.index.answer("客户要求什么")
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["error_code"], "rag_unavailable")
        self.assertFalse(report["answered"])
        self.assertNotIn("没有找到", report["answer"])

    def test_empty_top_k_is_partial_not_confirmed_absence(self):
        from meetingvar.model_client import CallableModelClient
        self.index.index_project()
        client = CallableModelClient(lambda messages, **kwargs: {"ok": True, "content": "x"})
        report = self.index.answer("完全无关的词汇条目", model_client=client)
        self.assertTrue(report["ok"], report)
        self.assertFalse(report["answered"])
        self.assertEqual(report["answer_status"], "partial")
        self.assertFalse(report["coverage"]["exhaustive"])
        self.assertIn("不代表", report["answer"])

    def test_stats_shape(self):
        self.index.index_project()
        stats = self.index.stats()
        self.assertEqual(stats["by_type"].get("document"), 1)
        self.assertEqual(stats["by_type"].get("meeting"), 1)


if __name__ == "__main__":
    unittest.main()