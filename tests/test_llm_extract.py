"""LLM 语义抽取层的契约测试（model-free：用本地桩服务器冒充 OpenAI 兼容端点）。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.memory.extract import llm_extract  # noqa: E402

SAMPLE = ROOT / "spikes" / "b1_rag" / "sample_project"


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "terms": ["测序污染", "S3", "UMI 评估"],
                                    "persons": [
                                        {"name": "李明", "role": "执行人", "why": "聊天记录中负责 S3 重测"},
                                        {"name": "PI", "role": "拍板人", "why": "布置 UMI 论文评估"},
                                    ],
                                    "focus": "S3 污染排查与重测决策",
                                }
                            )
                        }
                    }
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


class LlmExtractTests(unittest.TestCase):
    def test_llm_extract_via_stub(self) -> None:
        srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        old = config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY
        config.LLM_BASE_URL = f"http://127.0.0.1:{srv.server_port}/v1"
        config.LLM_MODEL = "stub"
        config.LLM_API_KEY = "stub"
        with tempfile.TemporaryDirectory() as sandbox:
            old_data = config.DATA_DIR
            config.DATA_DIR = Path(sandbox)
            try:
                r = llm_extract(SAMPLE)
            finally:
                config.DATA_DIR = old_data
                config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY = old
                srv.shutdown()
                srv.server_close()
                thread.join(timeout=2)
        self.assertIsNotNone(r, "桩端点存在时不应回落 None")
        terms, persons = r
        self.assertIn("测序污染", terms)
        self.assertIn("LLM", terms["测序污染"])
        names = {p["name"] for p in persons}
        self.assertIn("李明", names)
        roles = {p["name"]: p.get("role") for p in persons}
        self.assertEqual(roles.get("PI"), "拍板人")  # jieba 永远给不出的"特异性"

    def test_unconfigured_returns_none(self) -> None:
        old = config.LLM_BASE_URL, config.LLM_MODEL
        config.LLM_BASE_URL, config.LLM_MODEL = "", ""
        try:
            self.assertIsNone(llm_extract(SAMPLE))
        finally:
            config.LLM_BASE_URL, config.LLM_MODEL = old


if __name__ == "__main__":
    unittest.main()
