"""Evidence Pack, Commitment Ledger and Find-Moment skills."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.chat import ChatAgent  # noqa: E402
from meetingvar.agent.evidence_skills import (  # noqa: E402
    CommitmentLedgerSkill, EvidencePackSkill, FindMomentSkill)
from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.agent.sessions import SessionCatalog  # noqa: E402
from meetingvar.rag import RagIndex  # noqa: E402


class EvidenceSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_evidence_skills_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "星河项目"
        self.project.mkdir()
        self.data = self.root / "data"
        self.session_id = "s1"
        (self.project / "meetingvar" / "minutes" / "s1").mkdir(parents=True)
        self.clip_id = str(uuid.uuid4())
        pool = self.data / "pool" / self.session_id
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({
            "session_id": self.session_id,
            "transcript": [
                {"ts": 12, "speaker": "小林", "text": "小林负责周五交付第一版。"},
                {"ts": 30, "speaker": "小王", "text": "测试时间不足会影响交付。"},
            ],
            "clips": [{"clip_id": self.clip_id, "at": 20}],
        }, ensure_ascii=False), encoding="utf-8")
        clip_dir = self.data / "clips" / self.clip_id
        (clip_dir / "screens").mkdir(parents=True)
        (clip_dir / "audio.wav").write_bytes(b"RIFF-fake-audio")
        (clip_dir / "screens" / "frame_0000.jpg").write_bytes(b"fake-jpeg")
        (pool / f"{self.clip_id}.json").write_text(json.dumps({
            "clip_id": self.clip_id, "session_id": self.session_id,
            "trigger": {"layer": "L2", "evidence": "热词:交付", "at": 20},
            "audio": {"path": f"data/clips/{self.clip_id}/audio.wav",
                      "start_ts": 10, "end_ts": 40},
            "screen": {"keyframes": [f"{self.clip_id}/screens/frame_0000.jpg"]},
            "transcript": [
                {"ts": 12, "speaker": "小林", "text": "小林负责周五交付第一版。"},
                {"ts": 30, "speaker": "小王", "text": "测试时间不足会影响交付。"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def test_evidence_pack_zip_manifest_and_hash(self):
        with patch.object(EvidencePackSkill, "_html_to_pdf", return_value=False):
            report = EvidencePackSkill(self.project, self.data).run(
                session_id=self.session_id, clip_id=self.clip_id,
                out_dir=self.root / "packs")
        self.assertTrue(report["ok"], report)
        archive = Path(report["zip_path"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        self.assertTrue({"manifest.json", "SHA256SUMS.txt", "audio.wav",
                         "transcript.txt", "summary.md", "summary.html"} <= names)
        manifest = json.loads((Path(report["directory"]) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["session_id"], self.session_id)
        self.assertEqual(manifest["clip_id"], self.clip_id)
        self.assertTrue(manifest["files"])

    def test_commitment_ledger_shows_due_change(self):
        store = FactStore(self.project)
        store.write(name="action: 交付第一版", description="周五交付第一版",
                    body="负责人小林，周五交付。", fact_type="action-item",
                    metadata={"owner": "小林", "due": "周五"})
        proposal_path = store.write(
            name="action: 交付第一版", description="周五交付第一版",
            body="负责人小林，周一交付。", fact_type="action-item",
            metadata={"owner": "小林", "due": "周一"})
        proposal = store.read_path(proposal_path)
        self.assertEqual(proposal["status"], "pending-conflict")
        self.assertEqual(store.read("action: 交付第一版")["due"], "周五")
        report = CommitmentLedgerSkill(self.project).run()
        self.assertTrue(report["rows"])
        active = [row for row in report["rows"] if row["status"] == "active"]
        pending = [row for row in report["rows"] if row["status"] == "pending-conflict"]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(active[0]["due"], "周五")
        self.assertEqual(pending[0]["due"], "周一")
        self.assertEqual(active[0]["changes"], [])
        self.assertEqual(pending[0]["changes"], [])

        store.resolve_conflict(proposal["name"], "keep_new")
        self.assertEqual(store.read("action: 交付第一版")["status"], "superseded")
        report = CommitmentLedgerSkill(self.project).run()
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["status"], "active")
        self.assertEqual(report["rows"][0]["due"], "周一")
        changes = report["rows"][0]["changes"]
        self.assertTrue(any(change["field"] == "due" and change["to"] == "周一"
                            for change in changes))
        self.assertEqual([change for change in changes if change["field"] == "due"],
                         [{"field": "due", "from": "周五", "to": "周一"}])

    def test_commitment_ledger_rejected_proposal_does_not_change_active_due(self):
        store = FactStore(self.project)
        common = dict(name="交付", description="交付第一版", fact_type="action-item")
        store.write(**common, body="周五交付", metadata={"due": "周五"})
        proposal = store.read_path(store.write(
            **common, body="周一交付", metadata={"due": "周一"}))
        store.resolve_conflict(proposal["name"], "keep_old")
        rows = CommitmentLedgerSkill(self.project).run()["rows"]
        active = [row for row in rows if row["status"] == "active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["due"], "周五")
        self.assertEqual(active[0]["changes"], [])
        self.assertTrue(any(row["status"] == "rejected" for row in rows))

    def test_find_moment_uses_rag_chunks(self):
        (self.project / "需求.md").write_text(
            "小林负责周五交付第一版，测试时间不足会影响交付。", encoding="utf-8")
        RagIndex(self.project, self.data).index_project(include_meetings=True)
        report = FindMomentSkill(self.project, self.data, limit=3).run("谁负责周五交付")
        self.assertTrue(report["segments"])
        self.assertEqual(report["segments"][0]["session_id"], self.session_id)
        self.assertIn("小林", report["segments"][0]["text"])

    def test_chat_evidence_pack_action(self):
        with patch.object(EvidencePackSkill, "_html_to_pdf", return_value=False):
            tool_call = {"id":"evidence-call","type":"function","function":{
                "name":"prepare_evidence_pack","arguments":json.dumps({"query":"周五交付"})}}
            replies = iter([{"ok":True,"content":'{"steps":[]}',"tool_calls":[]},
                {"ok":True,"content":"","tool_calls":[tool_call],
                 "message":{"role":"assistant","content":None,"tool_calls":[tool_call]}}])
            agent = ChatAgent(self.project, data_dir=self.data,
                              llm=lambda *args, **kwargs: next(replies))
            result = agent.ask("生成证据包")
            action = result["message"]["actions"][0]
            done = agent.execute_action(action["id"])
        self.assertTrue(done["ok"], done)
        self.assertIn("证据包已生成", done["message"]["content"])


if __name__ == "__main__":
    unittest.main()