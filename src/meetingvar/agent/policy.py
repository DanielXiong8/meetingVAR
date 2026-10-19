"""Enterprise policy primitives: principal, scope, approvals, budgets, injection guard.

The model is never the authority for permissions. A tool call is allowed only if
the application-owned Principal has the required scope and, for writes, a
one-time approval bound to the exact arguments.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .tools import ToolError, ToolSpec


@dataclass(frozen=True)
class Principal:
    """Application identity passed to every tool call."""
    actor_id: str
    roles: frozenset[str] = frozenset({"agent:read"})
    project_scopes: tuple[str, ...] = ()
    session_id: str = ""

    def allows_project(self, project_root: Path | str) -> bool:
        if not self.project_scopes:
            return True
        target = str(Path(project_root).resolve()).casefold()
        for scope in self.project_scopes:
            allowed = str(Path(scope).resolve()).casefold()
            if target == allowed or target.startswith(allowed.rstrip("\\/") + "\\") \
                    or target.startswith(allowed.rstrip("\\/") + "/"):
                return True
        return False

    def has_role(self, role: str) -> bool:
        return not role or role in self.roles


def local_principal(project_root: Path | str, *, actor_id: str = "local-user") -> Principal:
    """Desktop/CLI default: the local operator owns this project, read-only by default."""
    return Principal(actor_id=actor_id, roles=frozenset({"agent:read"}),
                     project_scopes=(str(Path(project_root).resolve()),))


def args_hash(args: dict) -> str:
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalTicket:
    token: str
    tool: str
    actor_id: str
    args_sha256: str
    scope: str
    expires_at: float
    risk: str = "write"


class ApprovalError(ToolError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code, message or code)


class ApprovalBroker:
    """One-time, argument-bound approvals. Fail closed on any mismatch."""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._tickets: dict[str, ApprovalTicket] = {}

    def issue(self, *, tool: str, actor_id: str, args: dict, scope: str,
              risk: str = "write") -> ApprovalTicket:
        ticket = ApprovalTicket(
            token=secrets.token_urlsafe(24), tool=tool, actor_id=actor_id,
            args_sha256=args_hash(args), scope=scope,
            expires_at=time.time() + self.ttl_seconds, risk=risk)
        self._tickets[ticket.token] = ticket
        return ticket

    def consume(self, token: str, *, tool: str, actor_id: str, args: dict,
                scope: str) -> bool:
        ticket = self._tickets.pop(str(token or ""), None)
        if ticket is None:
            raise ApprovalError("approval_required", "approval token is missing or already used")
        if ticket.expires_at < time.time():
            raise ApprovalError("approval_expired", "approval token expired")
        if ticket.tool != tool or ticket.actor_id != actor_id or ticket.scope != scope:
            raise ApprovalError("approval_mismatch", "approval token does not match this action")
        if ticket.args_sha256 != args_hash(args):
            raise ApprovalError("approval_mismatch", "approval token was issued for different arguments")
        return True

    def pending(self, token: str) -> ApprovalTicket | None:
        return self._tickets.get(str(token or ""))


def enforce_tool_policy(spec: ToolSpec, *, principal: Principal | None,
                        project_root: Path | str | None) -> None:
    """Least-privilege checks that do not depend on model output."""
    if principal is None:
        return  # legacy callers retain their existing local-only behavior
    if project_root is not None and not principal.allows_project(project_root):
        raise ApprovalError("project_forbidden", "principal is not scoped to this project")
    if spec.side_effect_scope == "external" and not principal.has_role("agent:external"):
        raise ApprovalError("external_forbidden", "external tools require agent:external")
    if not spec.read_only and not principal.has_role("agent:write"):
        raise ApprovalError("write_forbidden", "write tools require agent:write")
    if spec.required_role and not principal.has_role(spec.required_role):
        raise ApprovalError("role_forbidden", f"missing role: {spec.required_role}")


@dataclass
class AgentBudget:
    max_steps: int = 8
    max_tool_calls: int = 12
    max_input_tokens: int = 60000
    max_output_tokens: int = 16000
    max_seconds: float = 180.0
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def can_step(self) -> tuple[bool, str]:
        if self.steps >= self.max_steps:
            return False, "step_budget_exhausted"
        if self.tool_calls >= self.max_tool_calls:
            return False, "tool_budget_exhausted"
        if time.monotonic() - self.started_at > self.max_seconds:
            return False, "time_budget_exhausted"
        if self.input_tokens >= self.max_input_tokens:
            return False, "input_budget_exhausted"
        if self.output_tokens >= self.max_output_tokens:
            return False, "output_budget_exhausted"
        return True, ""

    def consume(self, usage: dict | None = None, *, tool: bool = False) -> None:
        if tool:
            self.tool_calls += 1
        usage = usage or {}
        self.input_tokens += int(usage.get("prompt_tokens") or 0)
        self.output_tokens += int(usage.get("completion_tokens") or 0)

    def as_dict(self) -> dict:
        return {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_seconds": self.max_seconds, "steps": self.steps,
                "tool_calls": self.tool_calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}


_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|developer)\s+(prompt|message)", re.I),
    re.compile(r"(reveal|print|show)\s+(the\s+)?(system\s+prompt|api\s*key|secret|token)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"忽略(之前|以上|前面)(的)?(所有)?(指令|提示)"),
    re.compile(r"(泄露|输出|打印).{0,8}(系统提示|密钥|token|api\s*key)", re.I),
    re.compile(r"(调用|执行).{0,6}(工具|命令|shell|脚本)", re.I),
)


def injection_signals(text: str, limit: int = 5) -> list[str]:
    """Flag likely prompt-injection instructions in untrusted content."""
    if not isinstance(text, str) or not text:
        return []
    signals = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            signals.append(pattern.pattern[:80])
            if len(signals) >= limit:
                break
    return signals


def untrusted_block(text: str, *, source: str, max_chars: int = 6000) -> str:
    """Wrap retrieved content so the model sees it as data, not instructions."""
    body = str(text or "")[:max_chars]
    signals = injection_signals(body)
    header = f"[UNTRUSTED SOURCE: {source}]"
    if signals:
        header += f" [INJECTION_SIGNALS={len(signals)}]"
    return f"{header}\n{body}\n[/UNTRUSTED SOURCE]"


def tool_contracts_for_model(specs: Iterable[ToolSpec]) -> list[dict]:
    """OpenAI tools schema generated only from application-owned ToolSpecs."""
    tools = []
    for spec in specs:
        tools.append({"type": "function", "function": {
            "name": spec.name, "description": spec.description,
            "parameters": spec.input_schema}})
    return tools