"""Offline operation recovery/maintenance tests; all journals are temporary."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meetingvar.agent import recovery
from meetingvar.agent.operations import OperationStore, _SCHEMA


class OperationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("MEETINGVAR_OPERATION_OWNER_PID", "MEETINGVAR_OPERATION_OWNER_START"):
            os.environ.pop(name, None)
        self.temp = tempfile.TemporaryDirectory(prefix="mv-operation-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = OperationStore(self.root)

    def start(self, **kwargs):
        ident = self.store.create("run_command", **kwargs)["id"]
        self.store.claim(ident)
        return ident

    def sql(self, sql, args=()):
        with sqlite3.connect(self.store.path) as db:
            return db.execute(sql, args).fetchall()

    def child(self, *, wait=False, delegated=False):
        # Only this tiny offline script is executed. -I excludes cwd/user imports.
        code = '''import sys

def audit(event, args):
    if event.startswith("socket.") and event != "socket.gethostname":
        raise RuntimeError("network disabled")
sys.addaudithook(audit)
sys.path.insert(0, sys.argv[1])
from meetingvar.agent.operations import OperationStore
s = OperationStore(sys.argv[2])
ident = s.create("run_command", run_id="child")["id"]
s.claim(ident)
print(ident, flush=True)
if sys.argv[3] == "wait":
    sys.stdin.readline()
'''
        env = dict(os.environ)
        if delegated:
            owner = recovery.owner_identity()
            env["MEETINGVAR_OPERATION_OWNER_PID"] = str(owner["owner_pid"])
            env["MEETINGVAR_OPERATION_OWNER_START"] = owner["owner_start"]
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", code,
                                 str(Path(__file__).resolve().parents[1] / "src"),
                                 str(self.root), "wait" if wait else "exit"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env, cwd=Path.cwd())
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=10)
        self.addCleanup(cleanup)
        if wait:
            ident = proc.stdout.readline().strip()
            self.assertTrue(ident, "test child must create its isolated receipt")
        else:
            out, err = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, err)
            ident = out.strip()
        return proc, ident

    def test_empty_maintenance_does_not_create_database(self):
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.assertEqual(self.store.storage_stats()["total_bytes"], 0)
        self.assertEqual(self.store.archive_results(dry_run=False)["archived"], 0)
        self.assertFalse((self.root / "meetingvar").exists())

    def test_claim_persists_identity_and_heartbeat_survives_new_store(self):
        ident = self.start()
        before = self.store.get(ident)
        self.assertEqual(before["owner_pid"], os.getpid())
        self.assertTrue(before["owner_start"])
        self.assertTrue(before["owner_id"])
        self.assertGreater(before["lease_expires_at"], before["heartbeat_at"])
        self.store.request_cancel(ident)
        after = OperationStore(self.root).heartbeat(ident, lease_seconds=600)
        self.assertEqual(after["status"], "cancel_requested")
        self.assertGreater(after["lease_expires_at"], before["lease_expires_at"])
        self.assertEqual(len(self.store.events(ident)), 3)

    def test_proven_live_owner_wins_over_expired_lease(self):
        ident = self.start()
        self.sql("UPDATE operations SET lease_expires_at=1 WHERE id=?", (ident,))
        self.assertEqual(OperationStore(self.root).reconcile_orphans()["interrupted"], 0)
        self.assertEqual(self.store.get(ident)["status"], "running")

    def test_dead_child_recovered_once_without_replay(self):
        _, ident = self.child()
        self.store.request_cancel(ident)
        pending = self.store.create("write_file", requires_approval=True)["id"]
        preview = self.store.reconcile_orphans(dry_run=True)
        self.assertEqual(preview["operation_ids"], [ident])
        self.assertEqual(preview["interrupted"], 0)
        self.assertEqual(self.store.get(ident)["status"], "cancel_requested")
        changed = OperationStore(self.root).reconcile_orphans()
        self.assertEqual(changed["interrupted"], 1)
        self.assertIn(changed["reasons"][ident], {"owner_dead", "owner_replaced"})
        row = self.store.get(ident)
        self.assertEqual(row["status"], "interrupted")
        self.assertTrue(row["result"]["outcome_unknown"])
        self.assertEqual(self.store.get(pending)["status"], "awaiting_approval")
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.assertEqual(sum(e["event"] == "interrupted" for e in self.store.events(ident)), 1)
        with self.assertRaises(ValueError):
            self.store.claim(ident)
        with self.assertRaises(ValueError):
            self.store.finish(ident, {"ok": True})

    def test_other_live_process_protected_from_reconcile_and_interrupt(self):
        proc, ident = self.child(wait=True)
        self.assertIsNone(proc.poll())
        self.sql("UPDATE operations SET lease_expires_at=1 WHERE id=?", (ident,))
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.assertEqual(self.store.interrupt_run("child"), 0)
        with self.assertRaises(ValueError):
            self.store.heartbeat(ident)
        self.assertEqual(self.store.get(ident)["status"], "running")

    def test_short_lived_bridge_delegates_to_actual_host(self):
        _, ident = self.child(delegated=True)
        row = self.store.get(ident)
        self.assertEqual(row["owner_pid"], os.getpid())
        self.assertEqual(row["owner_start"], recovery.owner_identity()["owner_start"])
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.store.heartbeat(ident)
        self.assertEqual(self.store.finish(ident, {"ok": True})["status"], "succeeded")

    def test_explicit_owner_and_bad_environment_are_validated_before_claim(self):
        ident = self.store.create("run_command")["id"]
        owner = recovery.owner_identity()
        with self.assertRaises(ValueError):
            self.store.claim(ident, owner={**owner, "owner_start": "stale-identity"})
        self.assertEqual(self.store.get(ident)["status"], "pending")
        with patch.dict(os.environ, {"MEETINGVAR_OPERATION_OWNER_PID": str(os.getpid())}):
            with self.assertRaises(ValueError):
                self.store.claim(ident)
        self.assertEqual(self.store.claim(ident, owner=owner)["owner_pid"], os.getpid())

    def test_pid_reuse_is_proven_loss_of_original_owner(self):
        ident = self.start()
        self.sql("UPDATE operations SET owner_start='prior-process-start' WHERE id=?", (ident,))
        report = self.store.reconcile_orphans()
        self.assertEqual(report["reasons"][ident], "owner_replaced")
        self.assertEqual(report["interrupted"], 1)

    def test_unknown_owner_needs_explicit_expired_lease(self):
        fresh = self.start()
        expired = self.start()
        self.sql("UPDATE operations SET lease_expires_at=1 WHERE id=?", (expired,))
        with patch.object(recovery, "process_identity", return_value=("unknown", "")):
            report = self.store.reconcile_orphans()
        self.assertEqual(report["operation_ids"], [expired])
        self.assertEqual(report["reasons"][expired], "lease_expired")
        self.assertEqual(self.store.get(fresh)["status"], "running")

    def test_foreign_host_and_legacy_unknown_are_preserved(self):
        foreign, legacy = self.start(), self.start()
        self.sql("UPDATE operations SET owner_host='another-machine',lease_expires_at=1 WHERE id=?", (foreign,))
        self.sql("UPDATE operations SET owner_pid=NULL,owner_start=NULL,owner_host=NULL,owner_id=NULL,heartbeat_at=NULL,lease_expires_at=NULL WHERE id=?", (legacy,))
        with patch.object(recovery, "process_identity", return_value=("dead", "")):
            self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.assertEqual(self.store.get(legacy)["status"], "running")

    def test_only_current_project_rows_reconciled(self):
        one, other = self.start(), self.start()
        self.sql("UPDATE operations SET project_id='foreign-project' WHERE id=?", (other,))
        with patch.object(recovery, "process_identity", return_value=("dead", "")):
            report = self.store.reconcile_orphans()
        self.assertEqual(report["operation_ids"], [one])
        self.assertEqual(self.sql("SELECT status FROM operations WHERE id=?", (other,))[0][0], "running")
        separate = OperationStore(self.root / "separate")
        self.assertEqual(separate.reconcile_orphans()["scanned"], 0)
        self.assertFalse(separate.path.exists())

    def test_concurrent_reconcile_has_one_interruption_event(self):
        _, ident = self.child()
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: OperationStore(self.root).reconcile_orphans(), range(4)))
        self.assertEqual(sum(r["interrupted"] for r in reports), 1)
        self.assertEqual(sum(e["event"] == "interrupted" for e in self.store.events(ident)), 1)

    def test_terminal_heartbeat_rejected_and_cancel_never_claims_rollback(self):
        ident = self.start()
        self.store.request_cancel(ident)
        self.store.finish(ident, {"ok": True})
        with self.assertRaises(ValueError):
            self.store.heartbeat(ident)
        self.assertTrue(self.store.get(ident)["result"]["cancellation_requested"])

    def test_invalid_lease_does_not_claim(self):
        ident = self.store.create("run_command")["id"]
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.store.claim(ident, lease_seconds=value)
        self.assertEqual(self.store.get(ident)["status"], "pending")

    def test_pre_owner_schema_is_readable_and_upgraded_only_on_write(self):
        self.store.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.store.path) as db:
            db.executescript(_SCHEMA)
            db.execute("INSERT INTO operation_meta VALUES ('project_id','old-project')")
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("old", "old-project", "", "", "r", "", "", "agent",
                        "run_command", "{}", "running", None, 0, 1, 1))
        before = self.store.path.read_bytes()
        self.assertIsNone(self.store.get("old")["owner_pid"])
        self.assertEqual(self.store.reconcile_orphans(dry_run=True)["operation_ids"], [])
        self.assertEqual(self.store.storage_stats()["operations"], 1)
        self.assertEqual(self.store.archive_results()["archived"], 0)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 0)
        self.assertEqual(self.store.get("old")["status"], "running")
        new = self.start()
        self.assertTrue(self.store.get(new)["owner_start"])
        self.assertEqual(self.store.project_id, "old-project")

    def legacy(self, ident="legacy-one", **kw):
        return {"id": ident, "kind": "run_command", "arguments": {"command": "synthetic"},
                "status": "succeeded", "result": {"ok": True, "stdout": "synthetic"}, **kw}

    def test_legacy_dry_run_is_non_creating_and_success_remains_unverified(self):
        records = [self.legacy(), {"role": "assistant", "content": "已完成，文件保存成功"},
                   "successfully saved file", self.legacy("bad", result="completed successfully")]
        report = self.store.migrate_legacy(records, source="synthetic-fixture")
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["rejected"], [1, 2, 3])
        self.assertFalse(self.store.path.exists())
        report = self.store.migrate_legacy(records, source="synthetic-fixture", dry_run=False)
        self.assertEqual(report["imported"], 1)
        row = self.store.get(report["candidates"][0]["operation_id"])
        self.assertEqual(row["status"], "unverified")
        self.assertEqual(row["result"]["legacy_status"], "succeeded")
        self.assertTrue(row["result"]["outcome_unknown"])
        with self.assertRaises(ValueError):
            self.store.claim(row["id"])
        with self.assertRaises(ValueError):
            self.store.approve(row["id"])
        with self.assertRaises(ValueError):
            self.store.finish(row["id"], {"ok": True})
        self.assertEqual(self.store.request_cancel(row["id"])["status"], "unverified")
        self.assertIn("unverified", self.store.recent_context())

    def test_legacy_import_idempotent_source_scoped_and_redacted(self):
        record = self.legacy(arguments={"api_key": "never-store-this"},
                             result={"ok": True, "password": "synthetic-secret"})
        first = self.store.migrate_legacy([record], source="fixture", dry_run=False)
        second = self.store.migrate_legacy([record], source="fixture", dry_run=False)
        self.assertEqual((first["imported"], second["imported"], second["skipped"]), (1, 0, 1))
        row = self.store.get(first["candidates"][0]["operation_id"])
        self.assertNotIn("never-store-this", json.dumps(row))
        self.assertNotIn("synthetic-secret", json.dumps(row))
        self.assertEqual(len(self.store.events(row["id"])), 1)
        self.assertEqual(self.store.migrate_legacy([record], source="another-fixture", dry_run=False)["imported"], 1)

    def test_legacy_conflicts_roll_back_whole_batch(self):
        self.store.migrate_legacy([self.legacy()], source="fixture", dry_run=False)
        changed = self.legacy(arguments={"command": "changed"})
        preview = self.store.migrate_legacy([self.legacy("new"), changed], source="fixture")
        self.assertEqual(len(preview["conflicts"]), 1)
        with self.assertRaises(ValueError):
            self.store.migrate_legacy([self.legacy("new"), changed], source="fixture", dry_run=False)
        self.assertEqual(self.store.storage_stats()["operations"], 1)

    def test_legacy_foreign_project_rejected_and_reply_text_is_ignored(self):
        report = self.store.migrate_legacy([self.legacy(project_id="another"),
                    self.legacy("text", result=None, reply="finished successfully")],
                    source="fixture", dry_run=False)
        self.assertEqual(report["rejected"], [0])
        row = self.store.get(report["candidates"][0]["operation_id"])
        self.assertEqual(row["status"], "unverified")
        self.assertIsNone(row["result"]["legacy_result"])
        self.assertNotIn("finished successfully", json.dumps(row))

    def test_storage_quota_warns_without_deleting_or_blocking_finish(self):
        ident = self.start()
        before = self.store.path.read_bytes()
        stats = self.store.storage_stats(quota_bytes=1)
        self.assertTrue(stats["quota_exceeded"])
        self.assertIn("operation_store_quota_exceeded", stats["warnings"])
        self.assertEqual(stats["retention"], "keep_all")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.finish(ident, {"ok": True})["status"], "succeeded")

    def test_storage_counts_sidecars_and_soft_warning_threshold(self):
        self.start()
        total = self.store.storage_stats()["total_bytes"]
        stats = self.store.storage_stats(quota_bytes=int(total / 0.9))
        self.assertFalse(stats["quota_exceeded"])
        self.assertIn("operation_store_quota_near", stats["warnings"])
        self.assertEqual(stats["operations"], 1)
        self.assertEqual(stats["events"], 2)

    def test_archive_roundtrip_search_and_no_pending_loss(self):
        ident = self.start(thread_id="archive-thread")
        result = {"ok": True, "stdout": "归档回读 unique-archived-needle " * 10000}
        self.store.finish(ident, result)
        pending = self.store.create("write_file")["id"]
        running = self.start()
        self.sql("UPDATE operations SET result=? WHERE id IN (?,?)", (json.dumps(result), pending, running))
        before = self.store.path.read_bytes()
        preview = self.store.archive_results(older_than_seconds=0)
        self.assertEqual(preview["operation_ids"], [ident])
        self.assertEqual(preview["archived"], 0)
        self.assertEqual(self.store.path.read_bytes(), before)
        done = self.store.archive_results(older_than_seconds=0, dry_run=False)
        self.assertEqual(done["archived"], 1)
        self.assertGreater(done["saved_bytes"], 0)
        loaded = OperationStore(self.root).get(ident)
        self.assertEqual(loaded["result"], result)
        self.assertTrue(loaded["result_archived"])
        self.assertEqual(self.store.query("unique-archived-needle", thread_id="archive-thread")[0]["id"], ident)
        self.assertEqual(self.store.list(thread_id="archive-thread")[0]["result"], result)
        self.assertEqual(self.store.finish(ident, result)["result"], result)
        self.assertEqual(self.store.get(pending)["status"], "pending")
        self.assertFalse(self.store.get(pending)["result_archived"])
        self.assertEqual(self.store.get(running)["status"], "running")
        self.assertEqual(self.store.archive_results(older_than_seconds=0, dry_run=False)["archived"], 0)
        self.assertEqual(self.store.storage_stats()["archived_results"], 1)

    def test_archive_preserves_artifacts_and_default_retention(self):
        path = self.root / "synthetic-artifact.txt"
        path.write_text("test artifact", encoding="utf-8")
        ident = self.store.create("write_file")["id"]
        self.store.claim(ident)
        original = self.store.finish(ident, {"ok": True, "path": path.name, "stdout": "x" * 100000})
        self.assertEqual(self.store.archive_results(dry_run=False)["archived"], 0)
        self.store.archive_results(older_than_seconds=0, dry_run=False)
        self.assertEqual(self.store.get(ident)["artifacts"], original["artifacts"])
        self.assertEqual(self.store.artifacts(ident, verify=True)[0]["current_state"], "unchanged")
        self.assertEqual(path.read_text(encoding="utf-8"), "test artifact")

    def test_archive_integrity_failure_is_explicit(self):
        ident = self.start()
        self.store.finish(ident, {"ok": True, "stdout": "x" * 100000})
        self.store.archive_results(older_than_seconds=0, dry_run=False)
        envelope = json.loads(self.sql("SELECT result FROM operations WHERE id=?", (ident,))[0][0])
        envelope["sha256"] = "0" * 64
        self.sql("UPDATE operations SET result=? WHERE id=?", (json.dumps(envelope), ident))
        with self.assertRaisesRegex(ValueError, "归档结果校验失败"):
            self.store.get(ident)

    def test_archive_failure_rolls_back_original_result(self):
        ident = self.start()
        result = {"ok": True, "stdout": "x" * 100000}
        self.store.finish(ident, result)
        with patch.object(self.store, "_event", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                self.store.archive_results(older_than_seconds=0, dry_run=False)
        self.assertEqual(self.store.get(ident)["result"], result)
        self.assertFalse(self.store.get(ident)["result_archived"])

    def test_parallel_archive_is_idempotent(self):
        ident = self.start()
        self.store.finish(ident, {"ok": True, "stdout": "x" * 100000})
        with ThreadPoolExecutor(max_workers=3) as pool:
            reports = list(pool.map(lambda _: OperationStore(self.root).archive_results(
                older_than_seconds=0, dry_run=False), range(3)))
        self.assertEqual(sum(r["archived"] for r in reports), 1)
        self.assertEqual(sum(e["event"] == "result_archived" for e in self.store.events(ident)), 1)

    def test_reads_never_reconcile_a_dead_owner(self):
        _, ident = self.child()
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.get(ident)["status"], "running")
        self.assertEqual(self.store.list()[0]["status"], "running")
        self.assertEqual(self.store.query(ident)[0]["status"], "running")
        self.store.recent_context()
        self.store.storage_stats()
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_delegation_to_other_live_host_and_wrong_host_rejection(self):
        proc, _ = self.child(wait=True)
        host = recovery.owner_identity(proc.pid)
        ident = self.store.create("run_command")["id"]
        with self.assertRaises(ValueError):
            self.store.claim(ident, owner={**host, "owner_host": "foreign"})
        self.assertEqual(self.store.claim(ident, owner=host)["owner_pid"], proc.pid)
        self.store.heartbeat(ident, owner=host)
        with self.assertRaises(ValueError):
            self.store.heartbeat(ident)
        proc.stdin.write("exit\n")
        proc.stdin.flush()
        proc.communicate(timeout=10)
        self.assertEqual(self.store.reconcile_orphans()["interrupted"], 2)

    def test_delegation_stale_environment_never_falls_back_to_bridge(self):
        ident = self.store.create("run_command")["id"]
        with patch.dict(os.environ, {"MEETINGVAR_OPERATION_OWNER_PID": str(os.getpid()),
                                     "MEETINGVAR_OPERATION_OWNER_START": "old-start"}):
            with self.assertRaises(ValueError):
                self.store.claim(ident)
        self.assertEqual(self.store.get(ident)["status"], "pending")

    def test_legacy_malformed_status_rejected_without_aborting_valid_rows(self):
        report = self.store.migrate_legacy([self.legacy("bad", status=[]), self.legacy()],
                                          source="fixture", dry_run=False)
        self.assertEqual(report["rejected"], [0])
        self.assertEqual(report["imported"], 1)

    def test_legacy_duplicate_conflicts_and_artifact_paths_are_not_read(self):
        records = [self.legacy(), self.legacy(result={"ok": False})]
        with self.assertRaises(ValueError):
            self.store.migrate_legacy(records, source="fixture", dry_run=False)
        self.assertEqual(self.store.storage_stats()["operations"], 0)
        record = self.legacy(result={"ok": True, "artifacts": ["nonexistent-outside-file.txt"]})
        with patch.object(self.store, "_artifact", side_effect=AssertionError("no artifact reads")):
            self.assertEqual(self.store.migrate_legacy([record], source="fixture", dry_run=False)["imported"], 1)

    def test_wal_and_shared_memory_are_included_in_quota(self):
        self.start()
        with sqlite3.connect(self.store.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("INSERT INTO operation_meta VALUES ('synthetic','value')")
            db.commit()
            expected = sum(Path(str(self.store.path) + suffix).stat().st_size
                           for suffix in ("-wal", "-shm"))
            stats = self.store.storage_stats()
            self.assertEqual(stats["sidecar_bytes"], expected)
            self.assertEqual(stats["total_bytes"], stats["database_bytes"] + expected)

    def test_archive_batch_limit_all_terminal_statuses_and_foreign_scope(self):
        ids = []
        for status in ("succeeded", "failed", "cancelled", "interrupted"):
            ident = self.start()
            self.store.finish(ident, {"ok": status == "succeeded", "stdout": "z" * 100000}, status=status)
            ids.append(ident)
        foreign = self.start()
        self.store.finish(foreign, {"ok": True, "stdout": "q" * 100000})
        self.sql("UPDATE operations SET project_id='foreign' WHERE id=?", (foreign,))
        first = self.store.archive_results(older_than_seconds=0, limit=2, dry_run=False)
        second = self.store.archive_results(older_than_seconds=0, limit=2, dry_run=False)
        self.assertEqual((first["archived"], second["archived"]), (2, 2))
        self.assertEqual(set(first["operation_ids"] + second["operation_ids"]), set(ids))
        self.assertFalse(self.store.get(foreign)["result_archived"])

    def test_archive_never_treats_user_result_as_archive_metadata(self):
        ident = self.start()
        result = {"ok": True, "codec": "zlib+base64", "data": "not compressed"}
        self.store.finish(ident, result)
        self.assertEqual(self.store.get(ident)["result"], result)
        self.assertFalse(self.store.get(ident)["result_archived"])

    def test_unverified_imports_remain_unarchived_even_if_large_and_old(self):
        record = self.legacy(result={"ok": True, "stdout": "legacy" * 20000}, created_at=1)
        report = self.store.migrate_legacy([record], source="fixture", dry_run=False)
        self.assertEqual(self.store.archive_results(older_than_seconds=0, dry_run=False)["archived"], 0)
        self.assertEqual(self.store.get(report["candidates"][0]["operation_id"])["status"], "unverified")

    def test_maintenance_input_validation(self):
        for quota in (0, -1):
            with self.assertRaises(ValueError):
                self.store.storage_stats(quota_bytes=quota)
        for ratio in (0, 2, float("nan")):
            with self.assertRaises(ValueError):
                self.store.storage_stats(warning_ratio=ratio)
        for age in (-1, float("nan")):
            with self.assertRaises(ValueError):
                self.store.archive_results(older_than_seconds=age)
        with self.assertRaises(ValueError):
            self.store.migrate_legacy("assistant prose", source="fixture")
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
