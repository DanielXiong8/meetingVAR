import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AVAILABLE = all(importlib.util.find_spec(name) for name in ("cryptography", "dotenv"))


@unittest.skipUnless(AVAILABLE, "requirements-secrets.txt required")
class EnvSharingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("share_env", ROOT / "scripts/share_env.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"
        self.enc = self.root / "meetingvar.env.enc"
        self.key = self.root / ".secrets" / "meetingvar-env.key"
        self.raw = ("MEETINGVAR_LLM_BASE_URL=https://example.invalid/v1\n"
                    "MEETINGVAR_LLM_API_KEY='test-only-not-a-real-key'\n"
                    "MEETINGVAR_LLM_MODEL=test-model\n# 中文备注\n").encode("utf-8")
        self.env.write_bytes(self.raw)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_ciphertext_hides_config(self):
        self.tool.encrypt(self.env, self.enc, self.key)
        self.assertNotIn(b"test-only-not-a-real-key", self.enc.read_bytes())
        output = self.root / ".env.received"
        self.tool.decrypt(self.enc, output, self.key.read_bytes())
        self.assertEqual(output.read_bytes(), self.raw)

    def test_wrong_key_never_writes_plaintext(self):
        from cryptography.fernet import Fernet
        self.tool.encrypt(self.env, self.enc, self.key)
        output = self.root / ".env.received"
        with self.assertRaises(ValueError):
            self.tool.decrypt(self.enc, output, Fernet.generate_key())
        self.assertFalse(output.exists())

    def test_tampered_ciphertext_is_rejected(self):
        self.tool.encrypt(self.env, self.enc, self.key)
        token = bytearray(self.enc.read_bytes())
        token[30] = ord("A") if token[30] != ord("A") else ord("B")
        self.enc.write_bytes(token)
        output = self.root / ".env.received"
        with self.assertRaises(ValueError):
            self.tool.decrypt(self.enc, output, self.key.read_bytes())
        self.assertFalse(output.exists())

    def test_refuses_overwrite_and_empty_configuration(self):
        self.env.write_text("MEETINGVAR_LLM_API_KEY=\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.tool.encrypt(self.env, self.enc, self.key)
        self.assertFalse(self.key.exists())
        self.env.write_bytes(self.raw)
        self.tool.encrypt(self.env, self.enc, self.key)
        with self.assertRaises(FileExistsError):
            self.tool.encrypt(self.env, self.enc, self.key)
        with self.assertRaises(FileExistsError):
            self.tool.decrypt(self.enc, self.env, self.key.read_bytes())
        self.assertEqual(self.env.read_bytes(), self.raw)

    def test_env_loading_keeps_existing_environment_and_literal_values(self):
        from meetingvar.env import load_local_env
        self.env.write_text("MEETINGVAR_LLM_MODEL=from-file\nMEETINGVAR_LLM_API_KEY='literal-${VALUE}'\n", encoding="utf-8")
        with patch.dict(os.environ, {"MEETINGVAR_LLM_MODEL": "from-process"}, clear=True):
            load_local_env(self.env)
            self.assertEqual(os.environ["MEETINGVAR_LLM_MODEL"], "from-process")
            self.assertEqual(os.environ["MEETINGVAR_LLM_API_KEY"], "literal-${VALUE}")


if __name__ == "__main__":
    unittest.main()
