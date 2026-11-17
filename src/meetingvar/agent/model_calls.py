"""Prompt-only tool helpers sharing the owning request's model and ledger."""
from __future__ import annotations

from ..model_client import CallableModelClient, resolve_model_client
from ..request_context import model_request
from .policy import AgentBudget


class ToolModelCalls:
    def __init__(self, *, model_client=None, model_budget=None, llm=None):
        if model_client is None and llm is not None:
            def complete(messages, **kwargs):
                prompt = chr(10).join(str(m.get("content") or "") for m in messages)
                reply = llm(prompt)
                return {"ok": True, "content": reply} if isinstance(reply, str) else reply
            model_client = CallableModelClient(complete)
        self.client = resolve_model_client(model_client)
        # Only standalone legacy tasks create a ledger; nested tasks reuse the
        # exact object supplied by AgentLoop, including all prior usage.
        self.budget = model_budget if model_budget is not None else AgentBudget(max_seconds=900)
        self.last_result = {}
        self.errors = []
        self._start = (self.budget.input_tokens, self.budget.output_tokens, self.budget.model_calls)

    def complete(self, prompt: str, *, temperature=0.2, timeout=120,
                 max_tokens: int | None = None) -> dict:
        reply = model_request(self.client, [{"role": "user", "content": str(prompt)}],
            current_turn=0, ledger=self.budget, temperature=temperature, timeout=timeout,
            max_tokens=max_tokens, stream=False, max_retries=1)
        self.last_result = reply
        if not reply.get("ok"):
            self.errors.append({"stage": "model", "code": reply.get("error_code") or "model_error",
                "message": reply.get("error") or "模型调用失败",
                "retryable": bool(reply.get("retryable"))})
        return reply

    def report(self, report: dict) -> dict:
        result = dict(report)
        result["budget"] = self.budget.as_dict()
        result["usage"] = {"prompt_tokens": self.budget.input_tokens-self._start[0],
            "completion_tokens": self.budget.output_tokens-self._start[1],
            "model_calls": self.budget.model_calls-self._start[2]}
        result["model_errors"] = [dict(e) for e in self.errors]
        errors = list(result.get("errors") or [])
        for error in self.errors:
            if not any(isinstance(e, dict) and e.get("code") == error["code"] for e in errors):
                errors.append(dict(error))
        result["errors"] = errors
        if self.errors:
            result["ok"] = False
            last = self.errors[-1]
            fatal = last["code"] in {"auth", "quota", "model_not_found",
                                     "invalid_config", "not_configured"}
            generic = result.get("error_code") in {None, "", "save_failed", "tool_failed"}
            # A provider failure that aborted the work is the actionable root
            # cause; a stage-specific code such as summary_failed stays visible.
            if not result.get("error_code") or (fatal and generic):
                result["error_code"] = last["code"]
            if not result.get("error"):
                result["error"] = last["message"]
            result.setdefault("retryable", last["retryable"])
        return result


def model_kwargs(context) -> dict:
    """Fail closed if a registered tool lacks its owning client or ledger."""
    from .tools import ToolError
    extra = context.extra or {}
    client, budget = extra.get("model_client"), extra.get("model_budget")
    if client is None or budget is None:
        raise ToolError("model_context_required", "模型工具必须由宿主注入客户端与同一任务预算")
    return {"model_client": client, "model_budget": budget}


def proposal_diagnostics(report: dict) -> dict:
    return {key: report[key] for key in ("budget", "usage", "errors", "model_errors",
        "coverage", "retryable", "partial_candidates") if key in report}
