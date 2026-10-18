"""Client/bridge contracts. No real configuration, network or model is used."""
import http.client
import io
import json
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "desktop"))

# Importing config normally loads cwd/.env. These tests must never open it.
with patch("meetingvar.env.load_local_env"):
    from meetingvar import config
    from meetingvar.llm import chat, chat_result, token_budget
import engine_bridge


class LlmClientTests(unittest.TestCase):
    def setUp(self):
        settings = patch.multiple(
            config, LLM_BASE_URL="https://example.invalid/v1/", LLM_MODEL="test",
            LLM_API_KEY="test-only-secret", LLM_MAX_TOKENS="2048", LLM_THINKING="",
            LLM_CONTEXT_TOKENS="16384", create=True,
        )
        settings.start()
        self.addCleanup(settings.stop)
        network = patch("meetingvar.llm.urllib.request.urlopen")
        self.urlopen = network.start()
        self.addCleanup(network.stop)
        local = patch("meetingvar.llm.urllib.request.build_opener")
        self.opener = local.start()
        self.addCleanup(local.stop)
        self.respond("ok")

    def envelope(self, data):
        raw = json.dumps(data).encode()
        self.urlopen.return_value.__enter__.return_value.read.return_value = raw
        self.opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw

    def respond(self, content, finish_reason="stop", usage=None):
        self.envelope({"choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                       "usage": usage or {}})

    def test_legacy_signature_and_generic_request(self):
        self.assertEqual(chat("synthetic input", 0.2, 12), "ok")
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "test")
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["max_tokens"], 2048)
        self.assertNotIn("thinking", body)
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 12)

    def test_explicit_non_thinking_and_exact_model_name(self):
        with patch.multiple(config, LLM_BASE_URL="https://api.deepseek.com",
                            LLM_MODEL="deepseek-flash", LLM_MAX_TOKENS="4096", LLM_THINKING="disabled"):
            self.assertEqual(chat("synthetic input"), "ok")
        request = self.urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["max_tokens"], 4096)

    def test_reasoning_truncation_retries_once_without_thinking(self):
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.payload).encode()
        first = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                 "usage": {"completion_tokens": 2048,
                           "completion_tokens_details": {"reasoning_tokens": 1500}}}
        second = {"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                  "usage": {"completion_tokens": 20}}
        self.urlopen.side_effect = [Response(first), Response(second)]
        result = chat_result("synthetic input", max_tokens=2048)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["retried_without_thinking"])
        self.assertEqual(self.urlopen.call_count, 2)
        retry = json.loads(self.urlopen.call_args_list[1].args[0].data)
        self.assertEqual(retry["thinking"], {"type": "disabled"})
        self.assertEqual(retry["max_tokens"], 8192)

    def test_invalid_budget_and_thinking_never_make_requests(self):
        for key, values in (("LLM_MAX_TOKENS", ("", "NaN", "0", "-1", True)),
                            ("LLM_CONTEXT_TOKENS", ("", "bad", "0", "-1")),
                            ("LLM_THINKING", ("unknown",))):
            for value in values:
                with self.subTest(key=key, value=value), patch.object(config, key, value):
                    self.assertEqual(chat_result("test")["error_code"], "invalid_config")
                    self.assertIsNone(chat("test"))
        self.urlopen.assert_not_called()
        self.opener.assert_not_called()

    def test_unconfigured_or_invalid_endpoint(self):
        for base, code in (("", "not_configured"), ("ftp://example.invalid", "invalid_config"),
                           ("https://user:secret@example.invalid", "invalid_config"),
                           ("https://example.invalid?key=secret", "invalid_config")):
            with self.subTest(base=base), patch.object(config, "LLM_BASE_URL", base):
                self.assertEqual(chat_result("test")["error_code"], code)
        self.urlopen.assert_not_called()

    def test_context_reserves_input_output_template_and_margin(self):
        budget = token_budget()
        boundary = budget.input_limit - budget.chat_overhead_tokens
        self.assertTrue(chat_result("a" * boundary)["ok"])
        self.urlopen.reset_mock()
        self.assertEqual(chat_result("a" * (boundary + 1))["error_code"], "context_budget")
        with patch.object(config, "LLM_CONTEXT_TOKENS", "2048"):
            self.assertEqual(chat_result("x")["error_code"], "context_budget")
        self.urlopen.assert_not_called()

    def test_loopback_hosts_bypass_proxy_including_ipv6(self):
        for base in ("http://127.0.0.1:9999/v1", "http://localhost:9999/v1", "http://[::1]:9999/v1"):
            with self.subTest(base=base), patch.object(config, "LLM_BASE_URL", base):
                self.assertTrue(chat_result("synthetic")["ok"])
                handler = self.opener.call_args.args[0]
                self.assertEqual(handler.proxies, {})
        self.urlopen.assert_not_called()

    def test_http_classification_without_body_or_key_leaks(self):
        cases = [(401, {}, "auth"), (403, {}, "auth"), (402, {}, "quota"),
                 (429, {"code": "insufficient_quota"}, "quota"), (429, {}, "rate_limit"),
                 (404, {"code": "model_not_found"}, "model_not_found"),
                 (404, {}, "http_error"), (400, {"code": "context_length_exceeded"}, "context_budget"),
                 (400, {}, "http_error"), (503, {}, "network")]
        for status, error, code in cases:
            with self.subTest(status=status, code=code):
                body = json.dumps({"error": {**error, "message": "sensitive response test-only-secret"}}).encode()
                self.urlopen.side_effect = urllib.error.HTTPError(
                    "https://example.invalid", status, "sensitive exception", None, io.BytesIO(body))
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = chat_result("sensitive prompt")
                self.assertEqual(result["error_code"], code)
                self.assertEqual(result["http_status"], status)
                self.assertFalse(result["ok"])
                self.assertEqual(result["content"], "")
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
                self.assertNotIn("sensitive", json.dumps(result))
                self.assertNotIn("test-only-secret", json.dumps(result))

    def test_network_and_incomplete_response_are_safe(self):
        for error in (urllib.error.URLError("sensitive"), TimeoutError("sensitive"),
                      http.client.IncompleteRead(b"sensitive response")):
            with self.subTest(error=type(error).__name__):
                self.urlopen.side_effect = error
                result = chat_result("test")
                self.assertEqual(result["error_code"], "network")
                self.assertTrue(result["retryable"])
                self.assertNotIn("sensitive", json.dumps(result))

    def test_truncated_response_keeps_only_safe_metadata(self):
        self.respond("sensitive partial text", "length", {"completion_tokens": 2048})
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = chat_result("sensitive prompt")
            self.assertIsNone(chat("sensitive prompt"))
        self.assertEqual(result["error_code"], "truncated")
        self.assertEqual(result["finish_reason"], "length")
        self.assertEqual(result["usage"], {"completion_tokens": 2048})
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("sensitive", json.dumps(result))

    def test_empty_and_thinking_only_are_not_successful(self):
        for value in (None, "", "   ", "<think>not final</think>", "<think>unfinished", []):
            with self.subTest(value=value):
                self.respond(value)
                self.assertEqual(chat_result("test")["error_code"], "empty_content")
                self.assertIsNone(chat("test"))
        self.envelope({"choices": [{"finish_reason": "stop", "message": {
            "content": None, "reasoning_content": "sensitive thought"}}]})
        self.assertEqual(chat_result("test")["error_code"], "empty_content")

    def test_final_text_parts_and_safe_usage(self):
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
                 "completion_tokens_details": {"reasoning_tokens": 3, "secret": "hidden"},
                 "secret": "hidden"}
        self.respond([{"type": "text", "text": "<think>hidden</think>"},
                      {"type": "text", "text": "answer"}], usage=usage)
        result = chat_result("test")
        self.assertEqual(result["content"], "answer")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["completion_tokens_details"], {"reasoning_tokens": 3})
        self.assertNotIn("hidden", json.dumps(result))

    def test_malformed_response_structure(self):
        for data in (None, [], {}, {"choices": []}, {"choices": [None]},
                     {"choices": [{"message": []}]},
                     {"choices": [{"message": {"content": 5}}]},
                     {"choices": [{"message": {"content": [{"type": "image"}]}}]}):
            with self.subTest(data=data):
                self.envelope(data)
                self.assertEqual(chat_result("test")["error_code"], "invalid_json")
        self.urlopen.return_value.__enter__.return_value.read.return_value = b"not JSON secret"
        self.assertEqual(chat_result("test")["error_code"], "invalid_json")

    def test_bridge_synthetic_requires_exact_json_schema(self):
        for text in ('{"ok":true}', ' {"ok": true}\n'):
            self.respond(text, usage={"prompt_tokens": 5, "completion_tokens": 5})
            result = engine_bridge.cmd_test_llm()
            self.assertTrue(result["ok"])
            self.assertEqual(result["model"], "test")
            self.assertEqual(result["finish_reason"], "stop")
            self.assertEqual(result["usage"]["completion_tokens"], 5)
            self.assertNotIn("content", result)
            request = json.loads(self.urlopen.call_args.args[0].data)
            self.assertEqual(request["max_tokens"], 2048)
            self.assertEqual(request["temperature"], 0.2)
        for text in ('OK', '```json\n{"ok":true}\n```', '{"ok":false}', '{"ok":1}',
                     '{}', '[]', '{"ok":true,"extra":1}', '{"ok":false,"ok":true}',
                     'prefix {"ok":true}', '{"ok":NaN}'):
            with self.subTest(text=text):
                self.respond(text)
                result = engine_bridge.cmd_test_llm()
                self.assertEqual(result["error_code"], "invalid_json")
                self.assertNotIn("content", result)
        self.respond('{"ok":true}', finish_reason="length")
        self.assertEqual(engine_bridge.cmd_test_llm()["error_code"], "truncated")

    def test_bridge_command_emits_one_json_and_sanitizes_unexpected_errors(self):
        self.respond('{"ok":true}')
        output = io.StringIO()
        with patch.object(sys, "argv", ["engine_bridge.py", "test_llm"]), redirect_stdout(output):
            engine_bridge.main()
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        output = io.StringIO()
        with patch.object(sys, "argv", ["engine_bridge.py", "test_llm"]), redirect_stdout(output), \
                patch.object(engine_bridge, "cmd_test_llm", side_effect=RuntimeError("test-only-secret")):
            engine_bridge.main()
        self.assertEqual(json.loads(output.getvalue())["error_code"], "internal_error")
        self.assertNotIn("test-only-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
