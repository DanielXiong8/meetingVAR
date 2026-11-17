"""One injected provider boundary for planning, execution and synthesis."""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from . import llm as provider


@runtime_checkable
class ModelClient(Protocol):
    def budget(self) -> provider.TokenBudget: ...
    def complete(self, messages: list[dict], **kwargs) -> dict: ...
    def stream(self, messages: list[dict], *, on_delta=None, **kwargs) -> dict: ...


class ProviderModelClient:
    def budget(self) -> provider.TokenBudget:
        return provider.token_budget()

    def complete(self, messages: list[dict], **kwargs) -> dict:
        return provider.chat_messages(messages, **kwargs)

    def stream(self, messages: list[dict], *, on_delta=None, **kwargs) -> dict:
        return provider.chat_messages_stream(messages, on_delta=on_delta, **kwargs)


class CallableModelClient:
    """Legacy/fake callables never escape to a global streaming provider."""
    def __init__(self, complete, *, stream=None, budget=None):
        self._complete = complete
        self._stream = stream
        self._budget = budget

    def budget(self) -> provider.TokenBudget:
        return self._budget if self._budget is not None else provider.token_budget()

    def complete(self, messages: list[dict], **kwargs) -> dict:
        return self._complete(messages, **kwargs)

    def stream(self, messages: list[dict], *, on_delta=None, **kwargs) -> dict:
        result = (self._stream(messages, on_delta=on_delta, **kwargs)
                  if self._stream else self.complete(messages, **kwargs))
        if not self._stream and callable(on_delta) and isinstance(result, dict) and result.get("ok"):
            try:
                on_delta("content", result.get("content") or "")
            except Exception:
                pass
        return result


def resolve_model_client(client=None, *, complete=None):
    if client is not None:
        if not callable(getattr(client, "complete", None)):
            raise TypeError("model_client must provide complete")
        if callable(getattr(client, "stream", None)) and callable(getattr(client, "budget", None)):
            return client
        budget = getattr(client, "budget", None)
        if callable(budget):
            budget = budget()
        return CallableModelClient(client.complete, stream=getattr(client, "stream", None), budget=budget)
    if complete is not None:
        return CallableModelClient(complete)
    return ProviderModelClient()
