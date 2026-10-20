"""Commercial layer contracts: offline license, updates, telemetry, data controls."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.commercial import data, updates  # noqa: E402
from meetingvar.commercial.license import (issue_license, machine_id,  # noqa: E402
                                           trial_status, verify_license)
from meetingvar.commercial.telemetry import write_crash  # noqa: E402


class LicenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        cls.key = Ed25519PrivateKey.generate()
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
        cls.public = cls.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def setUp(self):
        patcher = mock.patch.dict("os.environ",
                                           {"MEETINGVAR_LICENSE_PUBLIC_KEY": self.public})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_issue_and_verify(self):
        token = issue_license(self.pem, issued_to="客户A", expires="2099-12-31",
                              features=["meeting", "agent"])
        result = verify_license(token)
        self.assertTrue(result["valid"])
        self.assertEqual(result["issued_to"], "客户A")
        self.assertIn("agent", result["features"])

    def test_tamper_and_expiry_are_rejected(self):
        token = issue_license(self.pem, issued_to="客户B", expires="2000-01-01")
        self.assertFalse(verify_license(token)["valid"])
        self.assertFalse(verify_license(token[:-2] + "aa")["valid"])

    def test_machine_binding(self):
        token = issue_license(self.pem, issued_to="客户C", machine="machine-1")
        self.assertTrue(verify_license(token, "machine-1")["valid"])
        self.assertFalse(verify_license(token, "machine-2")["valid"])

    def test_trial(self):
        self.assertEqual(trial_status("2026-01-01", __import__("datetime").date(2026, 1, 2))["days_left"], 13)
        self.assertTrue(trial_status("2026-01-01", __import__("datetime").date(2026, 2, 1))["expired"])


class _Manifest(BaseHTTPRequestHandler):
    payload = b""
    binary = b""

    def do_GET(self):  # noqa: N802
        body = self.payload if self.path.endswith(".json") else self.binary
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Manifest)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_check_and_download_verified(self):
        binary = b"synthetic installer"
        _Manifest.binary = binary
        _Manifest.payload = json.dumps({
            "version": "9.9.9", "url": self.base + "setup.exe",
            "sha256": hashlib.sha256(binary).hexdigest(),
        }).encode()
        check = updates.check_manifest(self.base + "latest.json", "0.9.0")
        self.assertTrue(check["ok"] and check["available"])
        with tempfile.TemporaryDirectory() as tmp:
            result = updates.download_update(check["url"], check["sha256"], tmp)
            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["path"]).read_bytes(), binary)

    def test_bad_hash_is_rejected(self):
        _Manifest.binary = b"bytes"
        _Manifest.payload = json.dumps({
            "version": "9.9.9", "url": self.base + "setup.exe",
            "sha256": "0" * 64,
        }).encode()
        check = updates.check_manifest(self.base + "latest.json", "0.9.0")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(updates.download_update(check["url"], check["sha256"], tmp)["ok"])


class DataAndTelemetryTests(unittest.TestCase):
    def test_inventory_export_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "meetingvar" / "agent_memory" / "conversations").mkdir(parents=True)
            (root / "meetingvar" / "agent_memory" / "conversations" / "a.json").write_text("{}")
            data_dir = Path(tmp) / "data"
            (data_dir / "clips").mkdir(parents=True)
            (data_dir / "clips" / "x.bin").write_bytes(b"x")
            inv = data.inventory(root, data_dir)
            self.assertTrue(inv["ok"])
            export = data.export_project(root, Path(tmp) / "export.zip")
            self.assertTrue(export["ok"] and Path(export["path"]).exists())
            self.assertFalse(data.delete_categories(root, data_dir, ["agent"], "wrong")["ok"])
            result = data.delete_categories(root, data_dir, ["agent", "clips"], "DELETE")
            self.assertTrue(result["ok"])
            self.assertFalse((root / "meetingvar" / "agent_memory").exists())

    def test_crash_report_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                raise RuntimeError("api_key=secret-value")
            except RuntimeError as exc:
                path = write_crash(type(exc), exc, exc.__traceback__, app_version="0.9.0",
                                   logs_dir=Path(tmp),
                                   context={"api_key": "secret", "prompt": "private"})
            self.assertIsNotNone(path)
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("secret-value", text)
            self.assertIn("prompt", text)


if __name__ == "__main__":
    unittest.main()