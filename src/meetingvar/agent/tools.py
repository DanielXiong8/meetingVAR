"""Tool contracts for meetingVAR's controlled agent runtime.

The registry is intentionally small: it does not execute arbitrary model-written
code. Every tool declares its input schema, read/write nature, approval needs,
timeout and output budget. Handlers receive a context object owned by the
application.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    """A safe, classified tool failure suitable for an audit trail."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


class ToolValidationError(ToolError):
    def __init__(self, message: str):
        super().__init__("invalid_arguments", message)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    read_only: bool = True
    requires_approval: bool = False
    timeout_s: float = 10.0
    max_output_chars: int = 4000
    idempotent: bool = True
    side_effect_scope: str = "none"  # none / meeting / project / external

    def contract(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
            "timeout_s": self.timeout_s,
            "max_output_chars": self.max_output_chars,
            "side_effect_scope": self.side_effect_scope,
        }


@dataclass
class ToolContext:
    session_id: str = ""
    project_root: Any = None
    transcript: list[dict] = field(default_factory=list)
    fact_store: Any = None
    dry_run: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    value: Any
    text: str
    truncated: bool = False


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_arguments(value: Any, schema: dict, path: str = "$") -> None:
    """Validate the JSON-Schema subset used by local tool contracts."""
    if not isinstance(schema, dict):
        raise ToolValidationError(f"{path}: schema must be an object")
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        raise ToolValidationError(f"{path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"{path}: value is not allowed")
    if expected == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise ToolValidationError(f"{path}.{key}: required")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ToolValidationError(f"{path}: unknown field {sorted(unknown)[0]}")
        for key, child in properties.items():
            if key in value:
                validate_arguments(value[key], child, f"{path}.{key}")
    elif expected == "array":
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ToolValidationError(f"{path}: too few items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ToolValidationError(f"{path}: too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_arguments(item, item_schema, f"{path}[{index}]")
    elif expected == "string":
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ToolValidationError(f"{path}: too short")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ToolValidationError(f"{path}: too long")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _result_text(value: Any, limit: int) -> tuple[str, bool]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[truncated]", True


class ToolRegistry:
    """Register and invoke tools with fail-closed approval and bounded results."""

    def __init__(self, trace=None):
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[[dict, ToolContext], Any]] = {}
        self._cache: dict[str, tuple[Any, str, bool]] = {}
        self._trace = trace

    def register(self, spec: ToolSpec, handler: Callable[[dict, ToolContext], Any]) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if spec.side_effect_scope not in {"none", "meeting", "project", "external"}:
            raise ValueError("invalid side_effect_scope")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise ToolError("unknown_tool", f"unknown tool: {name}") from None

    def contracts(self, names: list[str] | None = None) -> list[dict]:
        selected = names or list(self._specs)
        return [self._specs[name].contract() for name in selected if name in self._specs]

    def invoke(self, name: str, args: dict, *, context: ToolContext | None = None,
               approval: Callable[[dict], bool] | None = None,
               idempotency_key: str | None = None) -> ToolResult:
        spec = self.spec(name)
        if not isinstance(args, dict):
            raise ToolValidationError("arguments must be an object")
        validate_arguments(args, spec.input_schema)
        context = context or ToolContext()
        if spec.requires_approval:
            if approval is None:
                raise ToolError("approval_required", f"{name} requires explicit approval")
            request = {"name": name, "args": args, "description": spec.description,
                       "side_effect_scope": spec.side_effect_scope}
            try:
                allowed = approval(request)
            except Exception as exc:  # approval UI failure must fail closed
                raise ToolError("approval_failed", type(exc).__name__) from None
            if allowed is not True:
                raise ToolError("approval_denied", f"{name} was not approved")

        cache_key = None
        if spec.idempotent and idempotency_key:
            cache_key = f"{name}:{idempotency_key}"
            if cache_key in self._cache:
                cached_value, cached_text, cached_truncated = self._cache[cache_key]
                if self._trace:
                    self._trace.record("tool_cache_hit", tool=name,
                                       key_sha256=hashlib.sha256(cache_key.encode()).hexdigest()[:16])
                return ToolResult(value=cached_value, text=cached_text, truncated=cached_truncated)

        started = time.monotonic()
        if self._trace:
            self._trace.record("tool_started", tool=name, read_only=spec.read_only,
                               scope=spec.side_effect_scope,
                               args_sha256=hashlib.sha256(_canonical(args).encode()).hexdigest()[:16])
        try:
            value = self._run_handler(spec, self._handlers[name], args, context)
            text, truncated = _result_text(value, spec.max_output_chars)
            if cache_key:
                self._cache[cache_key] = (value, text, truncated)
                if len(self._cache) > 512:
                    self._cache.pop(next(iter(self._cache)))
            if self._trace:
                self._trace.record("tool_finished", tool=name, status="ok",
                                   duration_ms=round((time.monotonic() - started) * 1000, 1),
                                   result_chars=len(text), truncated=truncated)
            return ToolResult(value=value, text=text, truncated=truncated)
        except ToolError as exc:
            if self._trace:
                self._trace.record("tool_finished", tool=name, status="error",
                                   error_code=exc.code,
                                   duration_ms=round((time.monotonic() - started) * 1000, 1))
            raise
        except Exception as exc:  # keep internals out of model-visible errors
            if self._trace:
                self._trace.record("tool_finished", tool=name, status="error",
                                   error_code="tool_failed",
                                   duration_ms=round((time.monotonic() - started) * 1000, 1))
            raise ToolError("tool_failed", type(exc).__name__) from None

    @staticmethod
    def _run_handler(spec: ToolSpec, handler, args: dict, context: ToolContext) -> Any:
        # Thread timeout is advisory: local handlers are expected to be short
        # and side-effect writes are atomic. A timed-out thread may finish, so
        # write handlers must use an idempotency key.
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(handler, args, context)
        try:
            return future.result(timeout=max(0.01, spec.timeout_s))
        except FutureTimeout:
            future.cancel()
            raise ToolError("timeout", f"{spec.name} timed out") from None
        finally:
            pool.shutdown(wait=False, cancel_futures=True)