"""Synthetic offline tests: no real project data or model/network calls."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meetingvar.agent.file_ops import read_text_file, list_directory, search_text
from meetingvar.agent.meeting_tools import build_meeting_registry, _catalog
from meetingvar.agent.sessions import SessionCatalog
from meetingvar.agent.tools import ToolContext, ToolError, ToolRegistry, ToolSpec
from meetingvar.agent.contracts import normalize_tool_evidence


class ToolEvidenceContractsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache_temp.cleanup)
        cache = patch("meetingvar.memory.files._cache_dir", return_value=Path(self.cache_temp.name))
        cache.start()
        self.addCleanup(cache.stop)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_large_file_pages_reconstruct_exact_original(self):
        text = "Original text 0123456789\n" * 9000 + "FINAL_SOURCE_TAIL"
        path = self.write("large.txt", text)
        cursor, parts = None, []
        while True:
            value = read_text_file(self.root, str(path), limit=14000, cursor=cursor)
            parts.append(value["text"])
            self.assertEqual(value["revision"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(value["source_path"], str(path.resolve()))
            cursor = value["next_cursor"]
            if not cursor:
                self.assertTrue(value["coverage"]["complete"])
                break
            self.assertFalse(value["coverage"]["complete"])
        self.assertEqual("".join(parts), text)

    def test_line_bounds_are_inclusive_and_original_whitespace_survives(self):
        self.write("lines.txt", "one\n two \nthree\nfour\n")
        value = read_text_file(self.root, "lines.txt", line_start=2, line_end=3)
        self.assertEqual(value["text"], " two \nthree\n")
        self.assertEqual((value["line_start"], value["line_end"]), (2, 3))

    def test_changed_bytes_invalidate_cursor_even_with_same_size_mtime(self):
        path = self.write("source.txt", "abcdefghijk")
        cursor = read_text_file(self.root, "source.txt", limit=3)["next_cursor"]
        stamp = path.stat()
        path.write_text("ABCDEFGHIJK", encoding="utf-8")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "cursor_invalid"):
            read_text_file(self.root, "source.txt", limit=3, cursor=cursor)

    def test_cursor_cannot_cross_project_path_or_query(self):
        self.write("a/source.txt", "same text body")
        self.write("b/source.txt", "same text body")
        cursor = read_text_file(self.root / "a", "source.txt", limit=3)["next_cursor"]
        with self.assertRaisesRegex(ValueError, "cursor_invalid"):
            read_text_file(self.root / "b", "source.txt", limit=3, cursor=cursor)
        with self.assertRaisesRegex(ValueError, "cursor_invalid"):
            read_text_file(self.root / "a", "source.txt", limit=4, cursor=cursor)

    def test_pdf_page_45_tail_and_incomplete_extraction(self):
        self.write("book.pdf", "%PDF-1.4 synthetic test")
        pages = [{"number": i, "text": f"page {i} original end\n",
                  "status": "ok", "method": "text"} for i in range(1, 46)]
        text = "".join(page["text"] for page in pages)
        document = {"text": text, "pages": pages, "complete": True,
                    "total_pages": 45, "errors": []}
        with patch("meetingvar.memory.files.read_document", return_value=document, create=True):
            result = read_text_file(self.root, "book.pdf", page_start=45, page_end=45)
            self.assertIn("page 45 original end", result["text"])
            self.assertEqual(result["page_start"], 45)
            self.assertEqual(result["page_end"], 45)
            self.assertTrue(result["complete"])
            document["complete"] = False
            document["errors"] = [{"code": "page_failed", "page": 2}]
            result = read_text_file(self.root, "book.pdf", page_start=45, page_end=45)
            self.assertFalse(result["complete"])
            self.assertEqual(result["status"], "partial")

    def test_list_over_200_files_has_no_missing_or_duplicate_entries(self):
        for i in range(213):
            self.write(f"file_{i:03}.txt", "needle")
        cursor, paths = None, []
        while True:
            value = list_directory(self.root, limit=70, cursor=cursor)
            paths.extend(row["path"] for row in value["entries"])
            self.assertEqual(value["total"], 213)
            cursor = value["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(paths), 213)
        self.assertEqual(len(set(paths)), 213)
        self.assertEqual(paths, sorted(paths))

    def test_exact_list_limit_is_not_truncated(self):
        self.write("one.txt", "one")
        result = list_directory(self.root, limit=1)
        self.assertFalse(result["truncated"])
        self.assertIsNone(result["next_cursor"])

    def test_search_continues_past_200_files_even_without_initial_hits(self):
        for i in range(215):
            self.write(f"file_{i:03}.txt", "tail_needle" if i == 214 else "no hit")
        first = search_text(self.root, "tail_needle", max_files=200)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["scanned"], 200)
        self.assertEqual(first["total"], 215)
        last = search_text(self.root, "tail_needle", max_files=200, cursor=first["next_cursor"])
        self.assertEqual(last["hits"][0]["path"], "file_214.txt")
        self.assertTrue(last["coverage"]["complete"])

    def test_search_cursor_resumes_inside_same_file(self):
        self.write("matches.txt", "needle\n" * 17)
        cursor, lines = None, []
        while True:
            result = search_text(self.root, "needle", limit=3, cursor=cursor)
            lines.extend(row["line"] for row in result["hits"])
            cursor = result["next_cursor"]
            if not cursor:
                break
        self.assertEqual(lines, list(range(1, 18)))

    def test_search_cursor_invalid_after_source_change(self):
        path = self.write("matches.txt", "needle\n" * 5)
        result = search_text(self.root, "needle", limit=2)
        path.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cursor_invalid"):
            search_text(self.root, "needle", limit=2, cursor=result["next_cursor"])

    def test_empty_and_failed_search_are_distinct(self):
        self.write("ok.txt", "no match")
        self.assertEqual(search_text(self.root, "absent")["status"], "empty")
        with patch("meetingvar.agent.file_ops._document", side_effect=ValueError("unreadable")):
            result = search_text(self.root, "absent")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failed"], 1)
        self.assertFalse(result["coverage"]["complete"])

    def test_empty_file_is_not_an_extraction_failure(self):
        self.write("empty.txt", "")
        result = read_text_file(self.root, "empty.txt")
        self.assertEqual(result["status"], "empty")
        self.assertTrue(result["complete"])

    def test_policy_blocks_private_sources_and_absolute_escape(self):
        self.write("secrets/private.txt", "synthetic private")
        with self.assertRaises(ValueError):
            read_text_file(self.root, "secrets/private.txt")
        with self.assertRaises(ValueError):
            list_directory(self.root, "secrets")
        with self.assertRaises(ValueError):
            list_directory(self.root, str(self.root.parent))
        self.assertEqual(list_directory(self.root)["entries"], [])

    def test_schema_accepts_max_files_and_read_ranges(self):
        registry = build_meeting_registry()
        self.assertIn("max_files", registry.spec("search_files").input_schema["properties"])
        for key in ("offset", "limit", "cursor", "line_start", "line_end", "page_start", "page_end"):
            self.assertIn(key, registry.spec("read_file").input_schema["properties"])

    def test_registry_keeps_value_and_evidence_and_owns_one_receipt(self):
        self.write("source.txt", "original evidence")
        registry = build_meeting_registry()
        context = ToolContext(project_root=self.root, metadata={"thread_id": "thread-a", "tool_call_id": "call-a"})
        result = registry.invoke("read_file", {"path": "source.txt"}, context=context)
        self.assertEqual(result.value["text"], "original evidence")
        self.assertIn("original evidence", result.text)
        self.assertEqual(result.tool_name, "read_file")
        self.assertEqual(result.evidence["status"], "ok")
        self.assertEqual(result.evidence["operation_id"], result.operation_id)
        rows = context.operation_store.query(thread_id="thread-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], result.operation_id)

    def test_registry_failure_has_identity_and_error_evidence(self):
        registry = build_meeting_registry()
        context = ToolContext(project_root=self.root, metadata={"tool_call_id": "call-fail"})
        with self.assertRaises(ToolError) as caught:
            registry.invoke("read_file", {"path": "missing.txt"}, context=context)
        exc = caught.exception
        self.assertEqual(exc.tool_name, "read_file")
        self.assertEqual(exc.tool_call_id, "call-fail")
        self.assertTrue(exc.operation_id)
        self.assertEqual(exc.evidence["status"], "error")
        self.assertEqual(context.operation_store.get(exc.operation_id)["status"], "failed")

    def test_registry_cache_cannot_cross_project_or_arguments(self):
        a, b = self.root / "a", self.root / "b"
        a.mkdir(); b.mkdir()
        registry = ToolRegistry()
        registry.register(ToolSpec("identity", "test", {"type": "object"}),
                          lambda args, ctx: {"project": str(ctx.project_root), "args": args})
        first = registry.invoke("identity", {"key": 1}, context=ToolContext(project_root=a), idempotency_key="same")
        second = registry.invoke("identity", {"key": 1}, context=ToolContext(project_root=b), idempotency_key="same")
        third = registry.invoke("identity", {"key": 2}, context=ToolContext(project_root=b), idempotency_key="same")
        self.assertNotEqual(first.value, second.value)
        self.assertNotEqual(second.value, third.value)

    def test_project_status_numeric_facts_never_treated_as_list(self):
        value = {"facts": 4, "recent_facts": [{"name": "fact-one", "description": "actual fact"}]}
        result = normalize_tool_evidence("project_status", {}, value)
        self.assertTrue(any(row.get("name") == "fact-one" for row in result["items"]))
        self.assertEqual(value["facts"], 4)

    def test_shared_catalog_never_mutates_when_context_changes_project(self):
        catalog = SessionCatalog(self.root / "pool-data", project_root=self.root / "a")
        other = _catalog(ToolContext(project_root=self.root / "b", extra={"catalog": catalog}))
        self.assertEqual(catalog.project_root, (self.root / "a").resolve())
        self.assertEqual(other.project_root, (self.root / "b").resolve())

    def test_session_cache_uses_bytes_revision_not_only_mtime(self):
        path = self.write("pool/s1/transcript.json", json.dumps([{"text": "before"}]))
        catalog = SessionCatalog(self.root)
        before = catalog.load("s1")
        stamp = path.stat()
        path.write_text(json.dumps([{"text": "after!"}]), encoding="utf-8")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        after = catalog.load("s1")
        self.assertNotEqual(before["revision"], after["revision"])
        window = catalog.window("s1", ["turn_1"])
        window[0]["text"] = "mutated"
        self.assertEqual(catalog.load("s1")["turns"][0]["text"], "after!")

    def test_session_listing_and_matching_turns_page_without_drops(self):
        for sid in ("s1", "s2", "s3"):
            self.write(f"pool/{sid}/transcript.json", json.dumps([{"text": "needle text"} for _ in range(5)]))
        catalog = SessionCatalog(self.root)
        first = catalog.list_sessions_page(limit=2)
        second = catalog.list_sessions_page(limit=2, cursor=first["next_cursor"])
        self.assertEqual(len(first["sessions"] + second["sessions"]), 3)
        cursor, seen = None, []
        while True:
            page = catalog.search_page("needle", limit=2, per_session=2, cursor=cursor)
            seen.extend((row["session_id"], m["id"]) for row in page["results"] for m in row["matches"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 15)
        self.assertEqual(len(set(seen)), 15)
        self.assertTrue(page["coverage"]["complete"])

    def test_session_cursor_invalid_when_transcript_changes(self):
        self.write("pool/s1/transcript.json", json.dumps([{"text": "needle"}] * 3))
        catalog = SessionCatalog(self.root)
        page = catalog.search_page("needle", per_session=1)
        self.write("pool/s1/transcript.json", json.dumps([{"text": "changed"}]))
        with self.assertRaisesRegex(ValueError, "cursor_invalid"):
            catalog.search_page("needle", per_session=1, cursor=page["next_cursor"])

    def test_registry_rejects_foreign_operation_store_without_writing(self):
        from meetingvar.agent.operations import OperationStore
        foreign = OperationStore(self.root / "other")
        registry = build_meeting_registry()
        context = ToolContext(project_root=self.root / "current", operation_store=foreign)
        with self.assertRaises(ToolError) as caught:
            registry.invoke("query_operations", {}, context=context)
        self.assertEqual(caught.exception.code, "tool_context_invalid")
        self.assertEqual(caught.exception.evidence["status"], "error")
        self.assertFalse(foreign.path.exists())

    def test_registry_rejects_foreign_fact_store(self):
        from meetingvar.agent.memory import FactStore
        registry = build_meeting_registry()
        context = ToolContext(project_root=self.root / "current", fact_store=FactStore(self.root / "other"))
        with self.assertRaises(ToolError) as caught:
            registry.invoke("project_status", {}, context=context)
        self.assertEqual(caught.exception.code, "tool_context_invalid")

    def test_read_registry_does_not_cache_changed_source(self):
        path = self.write("source.txt", "before")
        registry = build_meeting_registry()
        context = ToolContext(project_root=self.root)
        first = registry.invoke("read_file", {"path": "source.txt"}, context=context, idempotency_key="same")
        path.write_bytes(b"after")
        second = registry.invoke("read_file", {"path": "source.txt"}, context=context, idempotency_key="same")
        self.assertEqual(first.value["text"], "before")
        self.assertEqual(second.value["text"], "after")
        self.assertNotEqual(first.value["revision"], second.value["revision"])

    def test_malformed_cursor_and_negative_range_rejected(self):
        self.write("source.txt", "contents")
        for cursor in ("not base64!!!", "e30"):
            with self.assertRaises(ValueError):
                read_text_file(self.root, "source.txt", cursor=cursor)
        with self.assertRaises(ValueError):
            read_text_file(self.root, "source.txt", line_start=0)

    def test_actual_pdf_45_pages_can_be_read_to_last_page(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF not installed; mocked extraction contract is still tested")
        document = fitz.open()
        for i in range(1, 46):
            page = document.new_page()
            page.insert_text((72, 72), f"Source page {i} FINAL_{i}")
        document.save(str(self.root / "real_synthetic.pdf"))
        document.close()
        result = read_text_file(self.root, "real_synthetic.pdf", page_start=45, page_end=45)
        self.assertIn("FINAL_45", result["text"])
        self.assertEqual(result["page_start"], 45)
        self.assertEqual(result["total_pages"], 45)


if __name__ == "__main__":
    unittest.main()
