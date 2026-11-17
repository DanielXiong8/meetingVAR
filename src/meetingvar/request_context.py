"""Shared request admission for every injected model entrypoint."""
from __future__ import annotations

from .llm import ClientConfigError, _messages_bytes, error_result


def model_request(client, messages, *, current_turn=None, protected_indices=(),
                  ledger=None, tools=None, max_tokens=None, stream=False,
                  on_delta=None, max_retries=1, **kwargs):
    # Lazy import avoids coupling the low-level provider to agent startup.
    from .agent.context import ContextManager
    try:
        budget = client.budget()
    except ClientConfigError as exc:
        return error_result(exc.error_code)
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        return error_result("invalid_config")
    if any(type(getattr(budget, key, None)) is not int or getattr(budget, key) < 0
           for key in ("context_tokens", "max_output_tokens", "margin_tokens")):
        return error_result("invalid_config")
    output_limit = budget.max_output_tokens if max_tokens is None else min(max_tokens, budget.max_output_tokens)
    if output_limit <= 0 or not isinstance(messages, list) or not messages:
        return error_result("invalid_config")
    if ledger is not None:
        output_limit = min(output_limit, max(0, ledger.max_output_tokens - ledger.output_tokens))
        if output_limit <= 0:
            return error_result("output_budget_exhausted")
    input_limit = budget.context_tokens - output_limit - budget.margin_tokens
    pins = set(protected_indices) | {i for i, row in enumerate(messages)
                                    if isinstance(row, dict) and row.get("_context_protected")}
    fitted = ContextManager(input_limit=input_limit).fit(
        messages, tools=tools, current_turn=current_turn, protected_indices=pins)
    if not fitted.ok:
        result = error_result(fitted.error_code)
        result["context"] = {"input_tokens": fitted.input_tokens, "input_limit": input_limit}
        return result
    wire = [{k: v for k, v in row.items() if not k.startswith("_context_")} for row in fitted.messages]
    for attempt in range(max(0, min(2, int(max_retries))) + 1):
        if ledger is not None:
            can, reason = ledger.admit(fitted.input_tokens, output_limit)
            if not can:
                return error_result(reason)
        # Do not emit unvalidated provider fragments. Progress is an observer of
        # the completed result, never a switch to another transport/provider.
        try:
            if stream:
                result = client.stream(wire, tools=tools, max_tokens=output_limit, on_delta=None, **kwargs)
            else:
                result = client.complete(wire, tools=tools, max_tokens=output_limit, **kwargs)
        except (OSError, TimeoutError):
            result = error_result("network")
        except Exception:
            result = error_result("model_client_error")
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            result = error_result("invalid_result")
        result = dict(result)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        if result.get("ok"):
            message = result.get("message")
            calls = result.get("tool_calls") or (message.get("tool_calls", []) if isinstance(message, dict) else [])
            text = result.get("content", message.get("content") if isinstance(message, dict) else "")
            if (message is not None and not isinstance(message, dict)) or not isinstance(calls, list) \
                    or any(not isinstance(c, dict) or not isinstance(c.get("function"), dict) for c in calls) \
                    or (text is not None and not isinstance(text, str)):
                result = error_result("invalid_result", usage=usage)
            elif not calls and not text:
                result = error_result("empty_content", usage=usage)
            else:
                result["content"], result["tool_calls"] = text or "", calls
                result["message"] = {"role": "assistant", "content": text or None, "tool_calls": calls}
        estimated_output = (_messages_bytes([result.get("message") or {"role": "assistant",
            "content": result.get("content") or "", "tool_calls": result.get("tool_calls") or []}])
            if result.get("ok") else output_limit)
        # Unknown usage is not free: failed calls reserve the possible output.
        if ledger is not None:
            ledger.consume(usage, estimated_input=fitted.input_tokens,
                           estimated_output=estimated_output, model_call=True)
        result["usage_estimated"] = any(type(usage.get(k)) is not int for k in ("prompt_tokens", "completion_tokens"))
        result["attempts"] = attempt + 1
        result["request_budget"] = {"input_tokens": fitted.input_tokens,
            "input_limit": input_limit, "max_output_tokens": output_limit,
            "removed_groups": fitted.removed_groups}
        if result.get("ok"):
            if callable(on_delta) and result.get("content"):
                try:
                    on_delta("content", result["content"])
                except Exception:
                    pass
            return result
        if (result.get("error_code") not in {"network", "rate_limit"}
                or not result.get("retryable") or attempt >= max_retries):
            return result
        if ledger is not None:
            can, _ = ledger.admit(fitted.input_tokens, output_limit)
            if not can:
                return result  # preserve the real provider error, not a retry budget error
    return result
