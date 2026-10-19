"""Small durable task runtime: explicit states, checkpoints and tool calls."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .tools import ToolContext, ToolError, ToolRegistry


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskState:
    task_id: str
    kind: str
    status: str = TaskStatus.PENDING.value
    step: int = 0
    max_steps: int = 12
    last_tool: str = ""
    last_error: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AgentRuntime:
    """Persist task state after every step so a crashed job can be inspected/resumed.

    The runtime does not run a free-form model loop. A planner or a deterministic
    workflow decides which tool to call; this class enforces state, approval
    boundaries, step budgets and traces.
    """

    def __init__(self, checkpoint_dir: Path, trace=None, max_steps: int = 12):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.trace = trace
        self.max_steps = max(1, int(max_steps))

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _path(self, task_id: str) -> Path:
        if not task_id or any(c in task_id for c in ("/", "\\", "\x00", "..")):
            raise ValueError("invalid task id")
        return self.checkpoint_dir / f"{task_id}.json"

    def _save(self, task: TaskState) -> None:
        task.updated_at = self._now()
        path = self._path(task.task_id)
        fd, temporary = tempfile.mkstemp(prefix=".task-", dir=str(self.checkpoint_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(task.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    def create(self, kind: str, metadata: dict | None = None) -> TaskState:
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("task kind is required")
        task = TaskState(task_id=uuid.uuid4().hex, kind=kind.strip()[:80],
                         metadata=dict(metadata or {}), max_steps=self.max_steps,
                         created_at=self._now(), updated_at=self._now())
        self._save(task)
        if self.trace:
            self.trace.record("task_created", task_id=task.task_id, kind=task.kind)
        return task

    def load(self, task_id: str) -> TaskState:
        path = self._path(task_id)
        if not path.exists():
            raise FileNotFoundError(task_id)
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
        return TaskState(**data)

    def list_tasks(self, limit: int = 20) -> list[dict]:
        rows = []
        for path in sorted(self.checkpoint_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with path.open(encoding="utf-8") as stream:
                    rows.append(json.load(stream))
            except (OSError, ValueError):
                continue
            if len(rows) >= limit:
                break
        return rows

    def start(self, task: TaskState) -> None:
        if task.status not in {TaskStatus.PENDING.value, TaskStatus.WAITING_APPROVAL.value}:
            raise ValueError(f"cannot start task in state {task.status}")
        task.status = TaskStatus.RUNNING.value
        self._save(task)

    def run_tool(self, task: TaskState, registry: ToolRegistry, name: str, args: dict, *,
                 context: ToolContext | None = None,
                 approval=None, idempotency_key: str | None = None,
                 fail_fast: bool = False) -> dict:
        if task.status == TaskStatus.PENDING.value:
            self.start(task)
        if task.status != TaskStatus.RUNNING.value:
            raise ValueError(f"task is not running: {task.status}")
        if task.step >= task.max_steps:
            self.fail(task, "step_budget_exhausted")
            raise ToolError("step_budget_exhausted", "task step budget exhausted")
        spec = registry.spec(name)
        if spec.requires_approval:
            task.status = TaskStatus.WAITING_APPROVAL.value
            self._save(task)
            if self.trace:
                self.trace.record("task_waiting_approval", task_id=task.task_id, tool=name)
        try:
            result = registry.invoke(name, args, context=context, approval=approval,
                                     idempotency_key=idempotency_key)
        except ToolError as exc:
            task.step += 1
            task.last_tool = name
            task.last_error = exc.code
            task.status = TaskStatus.RUNNING.value
            self._save(task)
            if self.trace:
                self.trace.record("task_tool_error", task_id=task.task_id, tool=name,
                                  error_code=exc.code, step=task.step)
            if fail_fast:
                self.fail(task, exc.code)
                raise
            return {"ok": False, "error_code": exc.code, "error": exc.message}
        task.step += 1
        task.last_tool = name
        task.last_error = ""
        task.status = TaskStatus.RUNNING.value
        self._save(task)
        if self.trace:
            self.trace.record("task_tool_ok", task_id=task.task_id, tool=name, step=task.step)
        return {"ok": True, "result": result}

    def complete(self, task: TaskState, summary: dict | None = None) -> None:
        task.status = TaskStatus.COMPLETED.value
        if summary:
            task.metadata = {**task.metadata, "summary": summary}
        self._save(task)
        if self.trace:
            self.trace.record("task_completed", task_id=task.task_id, kind=task.kind,
                              steps=task.step)

    def fail(self, task: TaskState, error_code: str) -> None:
        task.status = TaskStatus.FAILED.value
        task.last_error = str(error_code)[:120]
        self._save(task)
        if self.trace:
            self.trace.record("task_failed", task_id=task.task_id, kind=task.kind,
                              error_code=task.last_error, steps=task.step)

    def cancel(self, task: TaskState) -> None:
        task.status = TaskStatus.CANCELLED.value
        self._save(task)
        if self.trace:
            self.trace.record("task_cancelled", task_id=task.task_id, kind=task.kind)