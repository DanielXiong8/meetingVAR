"""OpenAI-compatible client with safe, structured diagnostics.

``chat`` keeps its historical str/None contract. Callers that need to explain a
failure use ``chat_result``; neither entry point prints response bodies, prompts
or credentials. Token estimates deliberately use a conservative UTF-8 byte
bound, not a guessed tokenizer for an unknown model.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


_ERROR_MESSAGES = {
    "not_configured": "模型地址或模型名未配置",
    "invalid_config": "模型配置无效：请检查地址、输出上限、上下文预算和思考模式",
    "auth": "模型服务鉴权失败，请检查凭据及访问权限",
    "model_not_found": "服务明确报告模型不存在或不可访问，请核对模型名和权限",
    "quota": "模型服务额度或余额不足",
    "rate_limit": "模型服务请求受限，请稍后重试",
    "network": "模型服务连接失败、超时或暂时不可用",
    "http_error": "模型服务返回 HTTP 错误，请检查端点和请求参数",
    "empty_content": "模型未返回有效最终内容（可能只有思考内容）",
    "truncated": "模型输出达到上限，截断结果未采用",
    "invalid_json": "模型返回的 JSON 或结果结构无效",
    "content_filtered": "模型服务未提供可用内容",
    "context_budget": "请求超出保守上下文预算，请调整分块、上下文或输出预算",
}
_RETRYABLE = {"network", "rate_limit"}
_FINISH_REASONS = {"stop", "length", "tool_calls", "function_call", "content_filter"}


class ClientConfigError(ValueError):
    def __init__(self, code: str = "invalid_config"):
        self.error_code = code
        super().__init__(_ERROR_MESSAGES[code])


def error_result(code: str, *, http_status: int | None = None,
                 finish_reason: str | None = None, usage: dict | None = None) -> dict:
    """Build a diagnostic using only application-owned messages and safe fields."""
    result = {"ok": False, "content": "", "error_code": code,
              "error": _ERROR_MESSAGES[code], "retryable": code in _RETRYABLE,
              "finish_reason": finish_reason, "usage": usage or {}}
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _positive_int(value) -> int:
    try:
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
            raise ValueError
        number = int(value)
        if number <= 0:
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise ClientConfigError() from None


@dataclass(frozen=True)
class TokenBudget:
    context_tokens: int
    max_output_tokens: int
    # Reserve for the chat template and tokenizer differences separately.
    chat_overhead_tokens: int = 128
    margin_tokens: int = 512
    method: str = "utf8_bytes_upper_bound"

    @property
    def input_limit(self) -> int:
        return self.context_tokens - self.max_output_tokens - self.margin_tokens

    def input_tokens(self, prompt: str) -> int:
        return len(prompt.encode("utf-8")) + self.chat_overhead_tokens

    def fits(self, prompt: str) -> bool:
        return self.input_tokens(prompt) <= self.input_limit

    def as_dict(self) -> dict:
        return {"context_tokens": self.context_tokens,
                "max_output_tokens": self.max_output_tokens,
                "chat_overhead_tokens": self.chat_overhead_tokens,
                "margin_tokens": self.margin_tokens,
                "input_limit": self.input_limit, "method": self.method}


def token_budget() -> TokenBudget:
    context = getattr(config, "LLM_CONTEXT_TOKENS",
                      os.environ.get("MEETINGVAR_LLM_CONTEXT_TOKENS", "16384"))
    budget = TokenBudget(_positive_int(context), _positive_int(config.LLM_MAX_TOKENS))
    if budget.input_limit <= budget.chat_overhead_tokens:
        raise ClientConfigError("context_budget")
    return budget


def client_profile() -> dict:
    """Effective non-secret request settings, also used for cache isolation."""
    base = str(config.LLM_BASE_URL or "").strip().rstrip("/")
    model = str(config.LLM_MODEL or "").strip()
    if not base or not model:
        raise ClientConfigError("not_configured")
    try:
        url = urllib.parse.urlsplit(base)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or any(c.isspace() for c in base)):
            raise ValueError
        # Accessing port validates malformed port numbers, including IPv6 URLs.
        url.port
    except ValueError:
        raise ClientConfigError() from None
    thinking = str(config.LLM_THINKING or "").strip().lower()
    if thinking not in {"", "enabled", "disabled"}:
        raise ClientConfigError()
    return {"endpoint": urllib.parse.urlunsplit(
                (url.scheme.lower(), url.netloc.lower(), url.path, "", "")),
            "model": model, "thinking": thinking, **token_budget().as_dict()}


def _safe_usage(data) -> dict:
    if not isinstance(data, dict):
        return {}
    usage = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
        value = data.get(key)
        if type(value) is int and value >= 0:
            usage[key] = value
    details = data.get("completion_tokens_details")
    if isinstance(details, dict):
        value = details.get("reasoning_tokens")
        if type(value) is int and value >= 0:
            usage["completion_tokens_details"] = {"reasoning_tokens": value}
    return usage


def parse_json_object(raw: str) -> dict | None:
    """Parse one strict JSON object, rejecting duplicate keys and NaN/Infinity."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError

    try:
        data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _http_failure(exc: urllib.error.HTTPError) -> dict:
    # Provider codes help distinguish a missing model from a missing route.
    # Never expose the free-text message, body, headers or exception string.
    codes = set()
    try:
        payload = json.loads(exc.read(8192))
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            codes = {v.lower() for k in ("code", "type")
                     if isinstance((v := error.get(k)), str)}
    except (OSError, ValueError, TypeError):
        pass
    finally:
        exc.close()
    if exc.code in {401, 403} or codes & {"invalid_api_key", "authentication_error"}:
        code = "auth"
    elif exc.code == 402 or codes & {"insufficient_quota", "quota_exceeded", "insufficient_balance"}:
        code = "quota"
    elif codes & {"model_not_found", "invalid_model", "model_not_exist", "model_not_exists"}:
        code = "model_not_found"
    elif codes & {"context_length_exceeded", "context_window_exceeded"}:
        code = "context_budget"
    elif exc.code == 429:
        code = "rate_limit"
    elif exc.code == 408 or 500 <= exc.code <= 599:
        code = "network"
    else:
        code = "http_error"
    return error_result(code, http_status=exc.code)


def _final_text(content) -> str | None:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if (not isinstance(part, dict) or part.get("type") not in {"text", "output_text"}
                    or not isinstance(part.get("text"), str)):
                return None
            parts.append(part["text"])
        content = "".join(parts)
    if not isinstance(content, str):
        return None
    # Incomplete thinking tags are not a final answer either. Separate
    # reasoning_content fields are deliberately never used as final content.
    return re.sub(r"<think>.*?(?:</think>|$)", "", content, flags=re.S | re.I).strip()


def _request_once(prompt: str, *, temperature: float, timeout: int,
                  profile: dict, max_tokens: int, thinking: str) -> dict:
    body = {"model": profile["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens}
    if thinking:
        body["thinking"] = {"type": thinking}
    try:
        req = urllib.request.Request(
            profile["endpoint"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.LLM_API_KEY}"})
        host = urllib.parse.urlsplit(profile["endpoint"]).hostname
        if host in {"127.0.0.1", "localhost", "::1"}:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        with resp as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return _http_failure(exc)
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        return error_result("network")
    except (ValueError, TypeError):
        return error_result("invalid_config")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return error_result("invalid_json")
    if not isinstance(data, dict):
        return error_result("invalid_json")
    usage = _safe_usage(data.get("usage"))
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return error_result("invalid_json", usage=usage)
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish is not None and (not isinstance(finish, str) or finish not in _FINISH_REASONS):
        return error_result("invalid_json", usage=usage)
    if finish == "length":
        return error_result("truncated", finish_reason=finish, usage=usage)
    if finish == "content_filter":
        return error_result("content_filtered", finish_reason=finish, usage=usage)
    message = choice.get("message")
    if not isinstance(message, dict) or "content" not in message:
        return error_result("invalid_json", finish_reason=finish, usage=usage)
    content = _final_text(message["content"])
    if content is None:
        return error_result("invalid_json", finish_reason=finish, usage=usage)
    if not content:
        return error_result("empty_content", finish_reason=finish, usage=usage)
    return {"ok": True, "content": content, "error_code": None, "error": None,
            "retryable": False, "finish_reason": finish, "usage": usage}


def _loopback_endpoint(profile: dict) -> bool:
    try:
        return urllib.parse.urlsplit(profile["endpoint"]).hostname in {"127.0.0.1", "localhost", "::1"}
    except (KeyError, ValueError):
        return False


def chat_result(prompt: str, temperature: float = 0.3, timeout: int = 900,
                max_tokens: int | None = None, thinking: str | None = None) -> dict:
    """Generate once, with one safe retry when a reasoning model exhausts the budget.

    Some OpenAI-compatible reasoning models count hidden reasoning tokens against
    max_tokens and return no final content when finish_reason=length. If such a
    response exposes reasoning_tokens, retry once with thinking disabled and a
    larger output allowance. Providers that reject the extension keep the
    original, truthful truncated result.
    """
    try:
        profile = client_profile()
        context = _positive_int(profile["context_tokens"])
        effective_max = profile["max_output_tokens"] if max_tokens is None else _positive_int(max_tokens)
        effective_thinking = profile["thinking"] if thinking is None else str(thinking).strip().lower()
        if effective_thinking not in {"", "enabled", "disabled"}:
            raise ClientConfigError()
        budget = TokenBudget(context, effective_max)
        if budget.input_limit <= budget.chat_overhead_tokens:
            raise ClientConfigError()
    except ClientConfigError as exc:
        return error_result(exc.error_code)
    if not isinstance(prompt, str):
        return error_result("invalid_config")
    if not budget.fits(prompt):
        return error_result("context_budget")
    result = _request_once(prompt, temperature=temperature, timeout=timeout,
                           profile=profile, max_tokens=effective_max, thinking=effective_thinking)
    if result.get("error_code") == "network" and _loopback_endpoint(profile):
        # A local server may still be binding its port; one short retry is safe
        # for loopback and removes a real startup race (and test flakes).
        time.sleep(0.05)
        result = _request_once(prompt, temperature=temperature, timeout=timeout,
                               profile=profile, max_tokens=effective_max,
                               thinking=effective_thinking)
    reasoning = (result.get("usage") or {}).get("completion_tokens_details", {}).get("reasoning_tokens", 0)
    if result.get("error_code") != "truncated" or effective_thinking == "disabled" or not isinstance(reasoning, int) or reasoning <= 0:
        return result
    retry_max = min(max(effective_max, 8192), context // 2)
    retry_budget = TokenBudget(context, retry_max)
    if retry_max <= effective_max or not retry_budget.fits(prompt):
        return result
    retried = _request_once(prompt, temperature=temperature, timeout=timeout,
                            profile=profile, max_tokens=retry_max, thinking="disabled")
    if retried.get("ok"):
        retried["retried_without_thinking"] = True
        retried["original_usage"] = result.get("usage") or {}
        return retried
    # Do not hide the original truncation if the provider does not support
    # disabling thinking; the retry failure is diagnostic-only.
    if retried.get("error_code") not in {"truncated", "http_error", "invalid_config"}:
        result = dict(result)
        result["thinking_retry_error"] = retried.get("error_code")
    return result


def chat(prompt: str, temperature: float = 0.3, timeout: int = 900) -> str | None:
    """Backward-compatible optional enhancement: failures still return None."""
    result = chat_result(prompt, temperature=temperature, timeout=timeout)
    return result["content"] if result["ok"] else None


def _messages_bytes(messages: list[dict], tools: list[dict] | None = None) -> int:
    """Conservative UTF-8 byte bound for a multi-turn tool-calling request."""
    size = 0
    for message in messages:
        size += len(str(message.get("role", "")).encode("utf-8")) + 16
        content = message.get("content")
        if isinstance(content, str):
            size += len(content.encode("utf-8"))
        elif content is not None:
            size += len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
        if message.get("tool_calls"):
            size += len(json.dumps(message["tool_calls"], ensure_ascii=False).encode("utf-8"))
        if message.get("name"):
            size += len(str(message["name"]).encode("utf-8"))
    if tools:
        size += len(json.dumps(tools, ensure_ascii=False).encode("utf-8"))
    return size


def chat_messages(messages: list[dict], *, tools: list[dict] | None = None,
                  tool_choice: str = "auto", temperature: float = 0.2,
                  timeout: int = 300, max_tokens: int | None = None,
                  thinking: str | None = None) -> dict:
    """One OpenAI-compatible chat turn with optional native function calling.

    The caller owns the loop. This function only validates the request budget,
    performs one HTTP request and returns the assistant message plus usage. It
    never prints prompts, responses or credentials.
    """
    try:
        profile = client_profile()
        context = _positive_int(profile["context_tokens"])
        effective_max = profile["max_output_tokens"] if max_tokens is None else _positive_int(max_tokens)
        effective_thinking = profile["thinking"] if thinking is None else str(thinking).strip().lower()
        if effective_thinking not in {"", "enabled", "disabled"}:
            raise ClientConfigError()
        budget = TokenBudget(context, effective_max)
        if budget.input_limit <= budget.chat_overhead_tokens:
            raise ClientConfigError()
    except ClientConfigError as exc:
        return error_result(exc.error_code)
    if not isinstance(messages, list) or not messages:
        return error_result("invalid_config")
    if _messages_bytes(messages, tools) > budget.input_limit:
        return error_result("context_budget")
    body = {"model": profile["model"], "messages": messages,
            "temperature": temperature, "max_tokens": effective_max}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if effective_thinking:
        body["thinking"] = {"type": effective_thinking}
    try:
        req = urllib.request.Request(
            profile["endpoint"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.LLM_API_KEY}"})
        host = urllib.parse.urlsplit(profile["endpoint"]).hostname
        if host in {"127.0.0.1", "localhost", "::1"}:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        with resp as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return _http_failure(exc)
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        return error_result("network")
    except (ValueError, TypeError):
        return error_result("invalid_config")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return error_result("invalid_json")
    if not isinstance(data, dict):
        return error_result("invalid_json")
    usage = _safe_usage(data.get("usage"))
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return error_result("invalid_json", usage=usage)
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish == "length":
        return error_result("truncated", finish_reason=finish, usage=usage)
    if finish == "content_filter":
        return error_result("content_filtered", finish_reason=finish, usage=usage)
    message = choice.get("message")
    if not isinstance(message, dict):
        return error_result("invalid_json", finish_reason=finish, usage=usage)
    tool_calls = []
    raw_calls = message.get("tool_calls")
    if raw_calls is not None:
        if not isinstance(raw_calls, list):
            return error_result("invalid_json", finish_reason=finish, usage=usage)
        for index, call in enumerate(raw_calls[:12]):
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                continue
            function = call["function"]
            name, arguments = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            tool_calls.append({"id": str(call.get("id") or f"call_{index}"),
                               "type": "function",
                               "function": {"name": name, "arguments": arguments}})
    content = _final_text(message.get("content")) if "content" in message else ""
    if content is None:
        return error_result("invalid_json", finish_reason=finish, usage=usage)
    if not tool_calls and not content:
        return error_result("empty_content", finish_reason=finish, usage=usage)
    return {"ok": True, "content": content or "", "message": {
                "role": "assistant", "content": content or None,
                "tool_calls": tool_calls},
            "tool_calls": tool_calls, "error_code": None, "error": None,
            "retryable": False, "finish_reason": finish, "usage": usage,
            "native_tools": bool(raw_calls)}
