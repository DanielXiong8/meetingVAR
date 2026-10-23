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
        self.index = RagIndex(self.project, self.data)

    def test_index_search_and_read_chunk(self):
        report = self.index.index_project()
        self.assertTrue(report["ok"])
        self.assertEqual(report["documents"], 2)
        self.assertGreaterEqual(report["chunks"], 2)
        docs = self.index.search("客户要求什么", rerank=False)
        self.assertEqual(docs[0]["source_type"], "document")
        self.assertIn("数据导出", docs[0]["text"])
        meetings = self.index.search("谁负责交付", rerank=False)
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
        self.index.index_project()
        fake = {"ok": True, "content": "客户要求增加数据导出。[K1]",
                "usage": {"total_tokens": 10}}
        with patch("meetingvar.llm.chat_result", return_value=fake):
            report = self.index.answer("客户要求什么")
        self.assertTrue(report["ok"])
        self.assertTrue(report["grounded"])
        self.assertEqual(report["citations"][0]["kind"], "document")
        self.assertIn("需求.md", report["citations"][0]["title"])

    def test_stats_shape(self):
        self.index.index_project()
        stats = self.index.stats()
        self.assertEqual(stats["by_type"].get("document"), 1)
        self.assertEqual(stats["by_type"].get("meeting"), 1)


if __name__ == "__main__":
    unittest.main()