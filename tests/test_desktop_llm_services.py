"""Desktop service contracts with synthetic configuration and no network/models."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
import app


class ServiceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mv_llm_services_")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in {"CONFIG_DIR": self.root, "CONFIG_FILE": self.root / "config.json",
                            "SESSMAP_FILE": self.root / "map.json", "LLM_LOG_FILE": self.root / "model.log"}.items():
            setter = patch.object(app, name, value); setter.start(); self.addCleanup(setter.stop)
        with patch.object(app.Backend, "_autodetect"):
            self.backend = app.Backend()
        self.backend._cfg.update(engine_python=sys.executable, repo_dir=str(self.root), llm_provider="custom",
                                 llm_base_url="https://example.invalid/v1", llm_model="test-model", llm_api_key="fake-secret")
        logger = patch.object(app, "log"); logger.start(); self.addCleanup(logger.stop)

    def test_configuration_is_not_generation_verification(self):
        self.assertTrue(self.backend._provider_info()["configured"])
        self.assertFalse(self.backend._provider_info()["ready"])
        env = self.backend._llm_env()
        self.backend._llm_verified.add(self.backend._llm_fingerprint(env))
        self.assertTrue(self.backend._provider_info()["ready"])
        self.backend._cfg["llm_api_key"] = "another-fake-key"
        self.assertFalse(self.backend._provider_info()["ready"])

    def test_reading_and_interest_share_provider_but_have_explicit_budgets(self):
        reading = self.backend._llm_env(purpose="reading")
        interest = self.backend._llm_env(provider="custom", purpose="interest")
        for key in ["MEETINGVAR_LLM_BASE_URL", "MEETINGVAR_LLM_MODEL", "MEETINGVAR_LLM_API_KEY"]:
            self.assertEqual(reading[key], interest[key])
        self.assertEqual(reading["MEETINGVAR_LLM_MAX_TOKENS"], "8192")
        self.assertEqual(interest["MEETINGVAR_LLM_MAX_TOKENS"], "1600")
        self.assertEqual(reading["MEETINGVAR_LLM_CONTEXT_TOKENS"], "32768")

    def test_local_model_keeps_full_id_and_actual_budget(self):
        with patch.object(self.backend, "llm_status", return_value={"up": True, "model": "C:/models/model.gguf", "context_tokens": 8192}):
            env = self.backend._llm_env(provider="local")
        self.assertEqual(env["MEETINGVAR_LLM_MODEL"], "C:/models/model.gguf")
        self.assertEqual(env["MEETINGVAR_LLM_MAX_TOKENS"], "2048")
        self.assertEqual(env["MEETINGVAR_LLM_THINKING"], "")

    def test_validation_uses_bridge_and_same_reading_environment(self):
        response = Mock(stdout=json.dumps({"ok": True, "usage": {"completion_tokens": 4}}), returncode=0)
        with patch.object(app.subprocess, "run", return_value=response) as run:
            result = self.backend.test_llm()
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args.args[0][-1], "test_llm")
        env = run.call_args.kwargs["env"]
        for key, value in self.backend._llm_env().items():
            self.assertEqual(env[key], value)
        self.assertTrue(self.backend._provider_info()["ready"])
        stored = (self.root / "llm_validation.json").read_text()
        self.assertNotIn("fake-secret", stored)
        self.assertNotIn("example.invalid", stored)
        with patch.object(app.Backend, "_autodetect"), patch.object(app, "load_config", return_value=dict(self.backend._cfg)):
            reopened = app.Backend()
        self.assertTrue(reopened._provider_info()["ready"])

    def test_failed_generation_is_not_ready(self):
        response = Mock(stdout=json.dumps({"ok": False, "error_code": "output_truncated", "error": "输出达到上限"}), returncode=1)
        with patch.object(app.subprocess, "run", return_value=response):
            result = self.backend.test_llm()
        self.assertEqual(result["error_code"], "output_truncated")
        self.assertFalse(self.backend._provider_info()["ready"])

    def test_start_is_idempotent_while_loading(self):
        with patch.object(self.backend, "llm_status", return_value={"up": False, "managed": True}), \
             patch.object(app.subprocess, "Popen") as popen:
            result = self.backend.llm_start()
        self.assertTrue(result["starting"])
        popen.assert_not_called()

    def test_start_reports_process_failure_without_log_contents(self):
        from local_llm import safe_startup_error
        self.assertNotIn("secret-path", safe_startup_error("ArrayMemoryError: secret-path Unable to allocate 18.5 GiB"))
        fake = Mock()
        fake.poll.return_value = 1
        launch = {"cmd": ["fake-server"], "backend": "native", "context_tokens": 16384}
        states = [{"up": False, "managed": False}, {"up": False, "managed": False, "error": "内存分配失败"}]
        with patch.object(self.backend, "llm_status", side_effect=states), patch("local_llm.build_launch", return_value=launch), \
             patch.object(app.subprocess, "Popen", return_value=fake):
            result = self.backend.llm_start()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "内存分配失败")

    def test_understand_error_keeps_classification(self):
        proc = Mock(stdout=io.StringIO(json.dumps({"ok": False, "error_code": "quota", "error": "额度不足"}) + "\n"), returncode=1)
        self.backend._und.update(folder="synthetic", running=True)
        self.backend._und_reader(proc)
        self.assertEqual(self.backend.understand_status()["error_code"], "quota")
        self.assertEqual(self.backend.understand_status()["error"], "额度不足")

    def test_abnormal_exit_is_not_reported_as_user_cancellation(self):
        self.backend._und.update(folder="synthetic", running=True, error="")
        proc = Mock(stdout=io.StringIO(""), returncode=3)
        self.backend._und_reader(proc)
        self.assertIn("异常退出", self.backend._und["error"])
        self.assertNotIn("已取消", self.backend._und["error"])


if __name__ == "__main__":
    unittest.main()
