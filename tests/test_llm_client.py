"""LLM request contracts; every network operation is mocked."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meetingvar import config
from meetingvar.llm import chat


class LlmClientTests(unittest.TestCase):
    def setUp(self):
        settings = patch.multiple(
            config, LLM_BASE_URL="https://example.invalid/v1/", LLM_MODEL="test",
            LLM_API_KEY="test-only-secret", LLM_MAX_TOKENS="2048", LLM_THINKING="",
        )
        settings.start()
        self.addCleanup(settings.stop)
        self.network = patch("meetingvar.llm.urllib.request.urlopen")
        self.urlopen = self.network.start()
        self.addCleanup(self.network.stop)
        self.respond("ok")

    def respond(self, content, finish_reason="stop"):
        self.urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
        ).encode()

    def test_generic_request_is_bounded_without_provider_extension(self):
        self.assertEqual(chat("synthetic input"), "ok")
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        body = json.loads(request.data)
        self.assertEqual(body["max_tokens"], 2048)
        self.assertNotIn("thinking", body)

    def test_explicit_deepseek_non_thinking_mode(self):
        with patch.multiple(config, LLM_MAX_TOKENS="128", LLM_THINKING="disabled"):
            self.assertEqual(chat("synthetic input"), "ok")
        body = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 128)

    def test_invalid_output_limits_never_make_requests(self):
        for value in ("", "not-an-integer", "0", "-1"):
            with self.subTest(value=value), patch.object(config, "LLM_MAX_TOKENS", value):
                self.assertIsNone(chat("synthetic input"))
        self.urlopen.assert_not_called()

    def test_invalid_thinking_mode_never_makes_request(self):
        with patch.object(config, "LLM_THINKING", "unknown"):
            self.assertIsNone(chat("synthetic input"))
        self.urlopen.assert_not_called()

    def test_truncated_response_falls_back_without_logging_content(self):
        self.respond("sensitive partial text", "length")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertIsNone(chat("sensitive prompt"))
        self.assertNotIn("sensitive", output.getvalue())
        self.assertNotIn("test-only-secret", output.getvalue())

    def test_empty_or_thinking_only_response_falls_back(self):
        for value in ("", "   ", "<think>not final</think>"):
            with self.subTest(value=value):
                self.respond(value)
                self.assertIsNone(chat("synthetic input"))

    def test_malformed_choice_falls_back(self):
        self.urlopen.return_value.__enter__.return_value.read.return_value = b'{"choices": [null]}'
        self.assertIsNone(chat("synthetic input"))


if __name__ == "__main__":
    unittest.main()
