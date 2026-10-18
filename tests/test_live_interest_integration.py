"""Synthetic end-to-end checks for streaming ASR and semantic-only live capture."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "desktop")]
from meetingvar import config
from meetingvar.pipeline import Session
from meetingvar.pool.store import Pool
from meetingvar.monitor.semantic import SemanticMonitor
import app as desktop


class LiveInterestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mv_interest_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        settings = patch.multiple(config, DATA_DIR=self.root, POOL_DIR=self.root / "pool", CLIPS_DIR=self.root / "clips")
        settings.start(); self.addCleanup(settings.stop)

    def test_terms_and_similarity_never_trigger_when_disabled(self):
        session = Session("no_rules", ["专业词"], focus_docs=["专业词 项目 讨论"], auto_triggers=False, echo=False)
        session.asr.with_embeddings = False
        with patch.object(session.asr, "transcribe_pcm16k", return_value=[{"start": 0, "end": 2, "text": "专业词 项目 讨论"}]), \
             patch.object(session.monitor, "check", side_effect=AssertionError("rules must not run")):
            session.ingest_pcm(bytes(8 * 32000), 1000)
            result = session.finish()
        self.assertEqual(result["clips"], 0)
        self.assertFalse((self.root / "pool").exists())

    def test_only_confirmed_correction_terms_reach_go_session(self):
        from meetingvar.go import cmd_go
        project = self.root / "project"
        (project / "meetingvar").mkdir(parents=True)
        (project / "meetingvar/terms.txt").write_text("#!keep AlphaGenome\n", encoding="utf-8")
        (project / "notes.txt").write_text("无意义 raw raw raw", encoding="utf-8")
        args = argparse.Namespace(project=str(project), llm=False, interest="manual")
        summary = {"lines": 0, "clips": 0, "corrections": [], "transcript_json": None}
        with patch("meetingvar.memory.extract.extract_terms", side_effect=AssertionError("jieba must not run")), \
             patch("meetingvar.live.record_session", return_value=summary) as record, contextlib.redirect_stdout(io.StringIO()):
            cmd_go(args)
        self.assertEqual(record.call_args.args[2:5], ([], None, None))
        self.assertEqual(record.call_args.kwargs["correction_terms"], ["AlphaGenome"])
        self.assertIn("AlphaGenome", record.call_args.kwargs["interest_context"])

    def test_raw_final_evidence_drives_api_and_saved_clip(self):
        session = Session("semantic", [], asr_engine="xfyun_rtasr", auto_triggers=False, echo=False)
        session.ingest_pcm(bytes(8 * 32000), 1000)
        session.accept_segments([{"id": "s1", "start": 0, "end": 3, "text": "那就按第二个方案做，我周五交付。"}], 1002)
        def chat(prompt, **kw):
            self.assertIn("我周五交付", prompt)
            return json.dumps({"hit": True, "event_type": "COMMITMENT", "event_key": "second-plan-friday",
                               "reason": "确定负责人和周五交付", "evidence_ids": ["s1"]})
        judge = SemanticMonitor("项目背景", chat_fn=chat)
        self.addCleanup(judge.stop)
        judge.submit(session.interest_turns, 1008, force=True)
        hits = [e for e in judge.finish() if e["type"] == "hit"]
        self.assertEqual(len(hits), 1)
        session.capture_interest(hits[0])
        clip = Pool("semantic").list_clips()[0]
        self.assertEqual(clip["audio"]["end_ts"], 5)
        self.assertIn("API兴趣", clip["trigger"]["evidence"])
        self.assertIn("s1: 那就按第二个方案做", clip["trigger"]["explanation"])

    def test_invalid_evidence_and_api_failure_do_not_save(self):
        session = Session("failure", [], asr_engine="xfyun_rtasr", auto_triggers=False, echo=False)
        session.ingest_pcm(bytes(4 * 32000), 1000)
        session.accept_segments([{"id": "one", "start": 0, "end": 2, "text": "讨论一个普通术语"}], 1000)
        self.assertIsNone(session.capture_interest({"evidence_ids": ["made-up"]}))
        judge = SemanticMonitor("", chat_fn=lambda *a, **k: None)
        self.addCleanup(judge.stop)
        judge.submit(session.interest_turns, 1004, force=True)
        events = judge.finish()
        self.assertTrue(any(e.get("state") == "error" for e in events))
        self.assertFalse(any(e["type"] == "hit" for e in events))
        self.assertFalse((self.root / "pool").exists())
        session.trigger_manual()
        self.assertEqual(len(session.clips), 1)

    def test_live_partial_final_and_api_form_one_capture(self):
        from meetingvar.live import record_session
        class Stream:
            def __init__(self, *args): self.count = 0; self.stopped = False
            def start(self): pass
            def feed(self, pcm, ts): pass
            def poll(self):
                self.count += 1
                if self.count == 1:
                    return [{"type": "status", "state": "ready", "message": "connected"},
                            {"type": "partial", "id": "u1", "text": "那就", "start_ts": 1000, "end_ts": 1000}]
                return [{"type": "final", "id": "u1", "text": "那就按第二方案执行。", "start_ts": 1000, "end_ts": 1001.5}]
            def finish(self, timeout=10): return []
            def stop(self): self.stopped = True
        audio = Mock(session_start=1000)
        audio.name = "synthetic"
        audio.ingest_since.side_effect = [(bytes(32000), 1000), (bytes(32000), 1001), KeyboardInterrupt, (b"", 1002)]
        args = argparse.Namespace(no_screen=True, hd=False, device="cpu", duration=0, source="loopback",
                                  asr="xfyun_rtasr", xfyun_cred=str(self.root), interest="api", no_ask_speakers=True)
        def chat(prompt, **kw):
            self.assertIn("第二方案", prompt)
            return json.dumps({"hit": True, "event_type": "DECISION", "event_key": "plan2", "reason": "确定第二方案", "evidence_ids": ["u1"]})
        output = io.StringIO()
        with patch.dict(os.environ, {"MEETINGVAR_DESKTOP_EVENTS": "1"}), \
             patch.multiple(config, LLM_BASE_URL="https://example.invalid/v1", LLM_MODEL="fake"), \
             patch("meetingvar.llm.chat", side_effect=chat), \
             patch("meetingvar.transcribe.xfyun_rtasr.XfyunRealtime", Stream), \
             patch("meetingvar.capture.audio.AudioCapture", return_value=audio), \
             patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(output):
            summary = record_session(args, "stream_api", [], None, None, interest_context="demo")
        self.assertEqual(summary["lines"], 1)
        self.assertEqual(summary["clips"], 1)
        events = [json.loads(line.split(" ", 1)[1]) for line in output.getvalue().splitlines() if line.startswith("@@MEETINGVAR_EVENT ")]
        self.assertTrue(any(e["type"] == "partial_line" and e["text"] == "那就" for e in events))
        self.assertEqual(len([e for e in events if e["type"] == "line"]), 1)
        self.assertEqual(Pool("stream_api").list_clips()[0]["audio"]["end_ts"], 1.5)
        audio.stop.assert_called_once()

    def backend(self):
        with patch.object(desktop, "load_config", return_value=dict(desktop.CONFIG_DEFAULTS)), \
             patch.object(desktop, "SESSMAP_FILE", self.root / "missing.json"), patch.object(desktop.Backend, "_autodetect"):
            b = desktop.Backend()
        b._cfg.update(engine_python=sys.executable, repo_dir=str(self.root), asr_engine="local")
        return b

    def test_missing_api_does_not_silently_fallback_to_hotwords(self):
        b = self.backend()
        with patch.object(desktop.subprocess, "Popen") as popen:
            result = b.start_meeting("", "record")
        self.assertFalse(result["ok"])
        self.assertIn("不会退回热词", result["error"])
        popen.assert_not_called()

    def test_rtasr_and_api_are_explicitly_passed_to_engine(self):
        b = self.backend()
        b._cfg.update(asr_engine="xfyun_rtasr", xfyun_dir=str(self.root), interest_mode="api",
                      llm_base_url="https://example.invalid/v1", llm_model="test-model", llm_api_key="fake-only")
        with patch.object(desktop.subprocess, "Popen", side_effect=OSError("test stop")) as popen:
            b.start_meeting("", "record")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--asr") + 1], "xfyun_rtasr")
        self.assertEqual(command[command.index("--interest") + 1], "api")
        self.assertIn("--no-ask-speakers", command)
        self.assertNotIn("fake-only", " ".join(command))

    def test_upgrade_selects_rtasr_api_without_overwriting_existing_api(self):
        path = self.root / "cfg.json"
        path.write_text(json.dumps({"asr_engine": "local", "llm_base_url": "https://example.invalid/v1",
                                   "llm_model": "chosen", "llm_api_key": "fake-only"}), encoding="utf-8")
        with patch.object(desktop, "CONFIG_FILE", path):
            cfg = desktop.load_config()
        self.assertEqual(cfg["asr_engine"], "xfyun_rtasr")
        self.assertEqual(cfg["interest_mode"], "api")
        self.assertEqual(cfg["llm_model"], "chosen")
        self.assertEqual(cfg["llm_api_key"], "fake-only")


if __name__ == "__main__":
    unittest.main()
