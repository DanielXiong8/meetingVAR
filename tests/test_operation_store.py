"""Isolated operation-journal contract tests; no model or real project data."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meetingvar.agent.operations import OperationStore


class OperationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv-operations-test-")
        self.root = Path(self.temp.name)
        self.store = OperationStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def start(self, kind="run_command", **kwargs):
        op = self.store.create(kind, {"command": "test only"}, **kwargs)
        self.store.claim(op["id"])
        return op["id"]

    def test_read_queries_do_not_create_a_database(self):
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.artifacts(verify=True), [])
        self.assertEqual(self.store.query(), [])
        self.assertEqual(self.store.recent_context(), "")
        self.assertIsNone(self.store.get("not-an-operation"))
        self.assertFalse((self.root / "meetingvar").exists())

    def test_approval_is_not_execution(self):
        op = self.store.create("write_document", {"path": "report.pptx"}, requires_approval=True)
        self.assertEqual(op["status"], "awaiting_approval")
        with self.assertRaises(ValueError):
            self.store.claim(op["id"])
        self.assertEqual(self.store.approve(op["id"])["status"], "approved")
        self.assertEqual(self.store.artifacts(op["id"]), [])
        self.assertEqual(self.store.claim(op["id"])["status"], "running")
        with self.assertRaises(ValueError):
            self.store.claim(op["id"])

    def test_parallel_claim_has_exactly_one_winner(self):
        ident = self.store.create("run_command")["id"]
        def claim_once(_):
            try:
                OperationStore(self.root).claim(ident)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim_once, range(4))), 1)

    def test_result_round_trip_and_terminal_immutability(self):
        ident = self.start(thread_id="conversation", run_id="run1")
        result = {"ok": True, "stdout": "x" * 10000, "exit_code": 0,
                  "parts": [{"id": "a", "ok": True}, {"id": "b", "ok": False}]}
        self.store.finish(ident, result)
        loaded = OperationStore(self.root).get(ident)
        self.assertEqual(loaded["result"], result)
        self.assertEqual(loaded["status"], "succeeded")
        with self.assertRaises(ValueError):
            self.store.finish(ident, {"ok": False})
        events = self.store.events(ident)
        self.assertEqual([x["event"] for x in events], ["requested", "started", "finished"])

    def test_write_receipt_verifies_file_and_later_deletion(self):
        target = self.root / "report.docx"
        target.write_bytes(b"isolated artifact")
        ident = self.start("write_document", thread_id="one")
        done = self.store.finish(ident, {"ok": True, "path": target.name})
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["artifacts"][0]["absolute_path"], str(target.resolve()))
        self.assertEqual(self.store.artifacts(ident, verify=True)[0]["current_state"], "unchanged")
        target.write_bytes(b"changed artifact")
        self.assertEqual(self.store.artifacts(ident, verify=True)[0]["current_state"], "changed")
        target.unlink()
        self.assertEqual(self.store.artifacts(ident, verify=True)[0]["current_state"], "missing")
        self.assertEqual(self.store.get(ident)["status"], "succeeded")
        self.assertIn("missing", self.store.recent_context("one"))

    def test_missing_write_path_cannot_succeed(self):
        ident = self.start("write_file")
        done = self.store.finish(ident, {"ok": True, "path": "absent.txt"})
        self.assertEqual(done["status"], "failed")
        self.assertFalse(done["result"]["artifact_verified"])
        self.assertEqual(done["artifacts"], [])
        ident2 = self.start("write_document")
        self.assertEqual(self.store.finish(ident2, {"ok": True})["status"], "failed")

    def test_tool_proposal_is_not_a_document_receipt(self):
        ident = self.start("tool.write_document")
        done = self.store.finish(ident, {"ok": True, "ui_action": "write_document",
                                       "payload": {"path": "unwritten.docx"}})
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["artifacts"], [])

    def test_cross_project_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "other.txt"
            target.write_text("not this project", encoding="utf-8")
            ident = self.start("write_file")
            done = self.store.finish(ident, {"ok": True, "path": str(target)})
            self.assertEqual(done["status"], "failed")
            self.assertEqual(done["artifacts"], [])

    def test_cancel_request_does_not_claim_rollback(self):
        ident = self.start(run_id="r1")
        self.assertEqual(self.store.request_cancel(ident)["status"], "cancel_requested")
        done = self.store.finish(ident, {"ok": True, "exit_code": 0})
        self.assertEqual(done["status"], "succeeded")
        self.assertTrue(done["result"]["cancellation_requested"])

    def test_interrupt_is_scoped_and_not_automatically_replayed(self):
        one = self.start(run_id="one")
        two = self.start(run_id="two")
        waiting = self.store.create("write_file", run_id="one", requires_approval=True)
        self.assertEqual(self.store.interrupt_run("one"), 1)
        self.assertEqual(self.store.get(one)["status"], "interrupted")
        self.assertEqual(self.store.get(two)["status"], "running")
        self.assertEqual(self.store.get(waiting["id"])["status"], "awaiting_approval")
        with self.assertRaises(ValueError):
            self.store.claim(one)

    def test_thread_context_does_not_leak_other_conversations(self):
        one = self.start(thread_id="one")
        two = self.start(thread_id="two")
        self.store.finish(one, {"ok": True, "value": "first-private-thread"})
        self.store.finish(two, {"ok": True, "value": "second-private-thread"})
        context = self.store.recent_context("one")
        self.assertIn("first-private-thread", context)
        self.assertNotIn("second-private-thread", context)

    def test_credentials_are_redacted_in_storage_and_context(self):
        op = self.store.create("run_command", {"api_key": "do-not-store-this",
                                              "command": "curl -H 'Authorization: Bearer example-token'"})
        self.store.claim(op["id"])
        self.store.finish(op["id"], {"ok": True, "password": "private-password"})
        rendered = str(self.store.get(op["id"]))
        self.assertNotIn("do-not-store-this", rendered)
        self.assertNotIn("example-token", rendered)
        self.assertNotIn("private-password", rendered)

    def test_reusing_id_with_changed_request_is_rejected(self):
        self.store.create("run_command", {"command": "one"}, operation_id="stable")
        with self.assertRaises(ValueError):
            self.store.create("run_command", {"command": "two"}, operation_id="stable")


if __name__ == "__main__":
    unittest.main()
