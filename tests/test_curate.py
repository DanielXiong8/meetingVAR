"""选词人模式测试（model-free：本地桩 LLM 服务器 + 上限硬执行）。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.memory.curate import TERM_CAP, _reference_material, curate_terms  # noqa: E402


class _StubHandler(BaseHTTPRequestHandler):
    body_out = json.dumps(
        {"keywords": [{"term": f"术语{i}", "why": f"理由{i}"} for i in range(30)],
         "persons": [{"name": "李明", "role": "执行人", "why": "负责重测"}]}
    ).encode()

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": self.body_out.decode()}}]}).encode())

    def log_message(self, *args):  # 静默
        pass


# Use an ephemeral port: fixed ports caused flaky collisions in the full suite.
_PORT = 0


class CurateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = HTTPServer(("127.0.0.1", _PORT), _StubHandler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._old = config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY
        config.LLM_BASE_URL = f"http://127.0.0.1:{cls.srv.server_port}/v1"
        config.LLM_MODEL = "stub"
        config.LLM_API_KEY = "sk-stub"

    @classmethod
    def tearDownClass(cls) -> None:
        config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY = cls._old
        cls.srv.shutdown()

    def test_cap_hard_enforced(self) -> None:
        """桩服务器返回 30 个词 → 超出 TERM_CAP 的必须被截断（上限硬执行）。"""
        import tempfile

        mem = Path(tempfile.mkdtemp(prefix="mv_curate_"))
        mem_md = mem / "notes.md"
        mem_md.write_text("# 项目笔记\nAlphaGenome 与 burden test 的比较实验。", encoding="utf-8")
        r = curate_terms(mem, None)
        self.assertIsNotNone(r)
        self.assertEqual(len(r["keywords"]), TERM_CAP, f"超过 {TERM_CAP} 的关键词必须截断")
        self.assertEqual(r["keywords"][0]["why"], "理由0")
        self.assertEqual(r["persons"][0]["name"], "李明")
        self.assertTrue(r["termset_id"])

    def test_material_includes_repo_symbols_when_repo_given(self) -> None:
        repo = ROOT / "src" / "meetingvar"
        mat = _reference_material(None, repo)
        self.assertIn("代码高频定义", mat)
        # 不再断言具体符号（曾断言 "Session" 在 top-80——新增文件把它挤出去就脆断）；
        # 只验证"仓库符号确实被带进参考资料"这一性质
        self.assertTrue(any(sym in mat for sym in ("cmd_go", "understand", "extract_terms", "Session", "ASR")),
                        "repo 符号应出现在参考资料里")

    def test_unconfigured_llm_returns_none(self) -> None:
        old = config.LLM_BASE_URL, config.LLM_MODEL
        config.LLM_BASE_URL, config.LLM_MODEL = "", ""
        try:
            self.assertIsNone(curate_terms(None, None), "未配置 LLM 必须显式返回 None（上层回落）")
        finally:
            config.LLM_BASE_URL, config.LLM_MODEL = old


if __name__ == "__main__":
    unittest.main()
