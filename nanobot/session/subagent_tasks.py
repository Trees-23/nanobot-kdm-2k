"""Durable contract for unified subagent task state.

This module intentionally does not schedule children or own Goal barriers.  It freezes the
business-state contract that later integration work can use without turning either
``SubagentManager`` or Audit projections into a second writable source of truth.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
from contextlib import suppress
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SUBAGENT_TASK_SCHEMA_VERSION = 2
SUBAGENT_LIFECYCLE_SCHEMA_VERSION = 1
MAX_TASK_LABEL_CHARS = 120
MAX_TASK_ERROR_CHARS = 1000
MAX_TASK_SUMMARY_CHARS = 4000

TaskSpecItem = Annotated[str, Field(max_length=1000)]
TaskDependency = Annotated[str, Field(max_length=256)]
TaskResultItem = Annotated[str, Field(max_length=2000)]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("subagent task timestamps must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("subagent task timestamps must use UTC")
    return value


class SubagentTaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LOST = "lost"


TERMINAL_TASK_STATUSES = frozenset({
    SubagentTaskStatus.SUCCEEDED,
    SubagentTaskStatus.FAILED,
    SubagentTaskStatus.CANCELLED,
    SubagentTaskStatus.TIMED_OUT,
    SubagentTaskStatus.LOST,
})

_STATUS_TRANSITIONS: dict[SubagentTaskStatus, frozenset[SubagentTaskStatus]] = {
    SubagentTaskStatus.CREATED: frozenset({
        SubagentTaskStatus.QUEUED,
        SubagentTaskStatus.FAILED,
        SubagentTaskStatus.CANCELLED,
    }),
    SubagentTaskStatus.QUEUED: frozenset({
        SubagentTaskStatus.RUNNING,
        SubagentTaskStatus.FAILED,
        SubagentTaskStatus.CANCELLED,
        SubagentTaskStatus.TIMED_OUT,
        SubagentTaskStatus.LOST,
    }),
    SubagentTaskStatus.RUNNING: TERMINAL_TASK_STATUSES,
}

class SubagentExecutionPhase(StrEnum):
    INITIALIZING = "initializing"
    RUNNING_MODEL = "running_model"
    AWAITING_TOOLS = "awaiting_tools"
    TOOLS_COMPLETED = "tools_completed"
    FINAL_RESPONSE = "final_response"
    RESULT_PREPARING = "result_preparing"


class SubagentTerminationState(StrEnum):
    NONE = "none"
    CANCEL_REQUESTED = "cancel_requested"
    GRACE_WAITING = "grace_waiting"
    COOPERATIVELY_EXITED = "cooperatively_exited"
    FORCE_KILL_REQUESTED = "force_kill_requested"
    FORCE_KILLED = "force_killed"
    TERMINATION_FAILED = "termination_failed"


class SubagentDeliveryPhase(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"
    CLAIMED_PENDING_DELIVERY = "claimed_pending_delivery"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    objective: str = Field(min_length=1, max_length=8000)
    context: str = Field(default="", max_length=8000)
    constraints: list[TaskSpecItem] = Field(default_factory=list, max_length=32)
    deliverables: list[TaskSpecItem] = Field(default_factory=list, max_length=32)
    acceptance_criteria: list[TaskSpecItem] = Field(default_factory=list, max_length=32)
    dependencies: list[TaskDependency] = Field(default_factory=list, max_length=32)
    output_mode: Literal["text", "structured_preferred"] = "text"

    @classmethod
    def from_legacy(cls, task: str) -> TaskSpec:
        return cls(objective=task.strip())

    def idempotency_key(self, owner_scope: str) -> str:
        canonical = self.model_dump_json(exclude={"schema_version"})
        return hashlib.sha256(f"{owner_scope}\0{canonical}".encode()).hexdigest()


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    status: SubagentTaskStatus
    summary: str = Field(default="", max_length=MAX_TASK_SUMMARY_CHARS)
    evidence: list[TaskResultItem] = Field(default_factory=list, max_length=64)
    artifacts: list[TaskResultItem] = Field(default_factory=list, max_length=64)
    files_changed: list[TaskResultItem] = Field(default_factory=list, max_length=128)
    tests: list[TaskResultItem] = Field(default_factory=list, max_length=128)
    risks: list[TaskResultItem] = Field(default_factory=list, max_length=64)
    error: str | None = Field(default=None, max_length=MAX_TASK_ERROR_CHARS)

    @classmethod
    def from_legacy(
        cls,
        text: str,
        status: SubagentTaskStatus,
        *,
        error: str | None = None,
    ) -> TaskResult:
        return cls(status=status, summary=text[:MAX_TASK_SUMMARY_CHARS], error=error)

    @classmethod
    def from_output(
        cls,
        text: str,
        status: SubagentTaskStatus,
        *,
        error: str | None = None,
    ) -> TaskResult:
        """Accept a versioned JSON result, otherwise preserve legacy text without invented evidence."""
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            raw = None
        if isinstance(raw, dict):
            try:
                return cls.model_validate({**raw, "status": status})
            except ValidationError:
                pass
        return cls.from_legacy(text, status, error=error)


_TERMINATION_TRANSITIONS = {
    SubagentTerminationState.NONE: frozenset({
        SubagentTerminationState.CANCEL_REQUESTED,
        SubagentTerminationState.COOPERATIVELY_EXITED,
        SubagentTerminationState.TERMINATION_FAILED,
    }),
    SubagentTerminationState.CANCEL_REQUESTED: frozenset({
        SubagentTerminationState.GRACE_WAITING,
        SubagentTerminationState.COOPERATIVELY_EXITED,
        SubagentTerminationState.FORCE_KILL_REQUESTED,
        SubagentTerminationState.TERMINATION_FAILED,
    }),
    SubagentTerminationState.GRACE_WAITING: frozenset({
        SubagentTerminationState.COOPERATIVELY_EXITED,
        SubagentTerminationState.FORCE_KILL_REQUESTED,
        SubagentTerminationState.TERMINATION_FAILED,
    }),
    SubagentTerminationState.FORCE_KILL_REQUESTED: frozenset({
        SubagentTerminationState.FORCE_KILLED,
        SubagentTerminationState.TERMINATION_FAILED,
    }),
}


class TerminationState(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    state: SubagentTerminationState = SubagentTerminationState.NONE
    evidence: dict[str, Any] | None = None


class DeliveryState(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    phase: SubagentDeliveryPhase = SubagentDeliveryPhase.NOT_READY
    claim_owner_run_id: str | None = None
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None

    @field_validator("claimed_at", "delivered_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class SubagentLifecycleOutboxEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SUBAGENT_LIFECYCLE_SCHEMA_VERSION
    idempotency_key: str
    event_type: str
    task_id: str
    revision: int = Field(ge=1)
    occurred_at: datetime
    published_at: datetime | None = None
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", "published_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class SubagentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[2] = SUBAGENT_TASK_SCHEMA_VERSION
    revision: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=128)
    owner_session_key: str = Field(min_length=1, max_length=512)
    trace_id: str | None = None
    turn_id: str | None = None
    owner_run_id: str | None = None
    child_run_id: str | None = None
    spawn_tool_call_id: str | None = None
    label: str = Field(default="", max_length=MAX_TASK_LABEL_CHARS)
    required: bool = False
    task_group: str = Field(default="default", min_length=1, max_length=64)
    attempt: int = Field(default=1, ge=1)
    replaces_task_id: str | None = None
    status: SubagentTaskStatus = SubagentTaskStatus.CREATED
    phase: SubagentExecutionPhase = SubagentExecutionPhase.INITIALIZING
    termination: TerminationState = Field(default_factory=TerminationState)
    delivery: DeliveryState = Field(default_factory=DeliveryState)
    executor: dict[str, Any] | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    task_spec: TaskSpec | None = None
    task_result: TaskResult | None = None
    idempotency_key: str | None = None
    child_depth: int = Field(default=0, ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = Field(default=None, max_length=MAX_TASK_ERROR_CHARS)
    legacy_inferred: bool = False
    lifecycle_outbox: list[SubagentLifecycleOutboxEvent] = Field(default_factory=list)

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class SubagentUsageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class SubagentBudgetDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    wall_time_seconds: float | None = Field(default=None, ge=0)
    deadline_at: datetime | None = None
    reservation_state: Literal["reserved", "released", "settled"] | None = None

    @field_validator("deadline_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value)


class SubagentTaskDTO(BaseModel):
    """Versioned, bounded public projection; never serialize runtime status directly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    revision: int
    task_id: str
    owner_run_id: str | None
    child_run_id: str | None
    label: str
    required: bool
    task_group: str
    status: SubagentTaskStatus
    phase: SubagentExecutionPhase
    termination_state: SubagentTerminationState
    delivery_phase: SubagentDeliveryPhase
    usage: SubagentUsageDTO
    budget: SubagentBudgetDTO
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    legacy_inferred: bool

    @classmethod
    def from_task(cls, task: SubagentTask) -> SubagentTaskDTO:
        return cls(
            revision=task.revision,
            task_id=task.task_id,
            owner_run_id=task.owner_run_id,
            child_run_id=task.child_run_id,
            label=task.label,
            required=task.required,
            task_group=task.task_group,
            status=task.status,
            phase=task.phase,
            termination_state=task.termination.state,
            delivery_phase=task.delivery.phase,
            usage=SubagentUsageDTO.model_validate({
                key: task.usage.get(key)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd")
                if task.usage.get(key) is not None
            }),
            budget=SubagentBudgetDTO.model_validate({
                key: task.budget.get(key)
                for key in (
                    "max_tokens",
                    "max_cost_usd",
                    "wall_time_seconds",
                    "deadline_at",
                    "reservation_state",
                )
                if task.budget.get(key) is not None
            }),
            created_at=task.created_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            error=task.error,
            legacy_inferred=task.legacy_inferred,
        )


class SubagentTimelineEventDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    idempotency_key: str
    revision: int = Field(ge=1)
    event_type: str
    occurred_at: datetime
    audit_published: bool
    summary: dict[str, Any]

    @classmethod
    def from_event(cls, event: SubagentLifecycleOutboxEvent) -> SubagentTimelineEventDTO:
        return cls(
            idempotency_key=event.idempotency_key,
            revision=event.revision,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            audit_published=event.published_at is not None,
            summary=dict(event.summary),
        )


class SubagentSnapshotDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    tasks: list[SubagentTaskDTO]
    max_revision: int = Field(default=0, ge=0)


class SubagentTaskDetailDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task: SubagentTaskDTO
    trace_id: str | None
    turn_id: str | None
    spawn_tool_call_id: str | None
    replaces_task_id: str | None
    child_depth: int = Field(ge=0)
    timeline: list[SubagentTimelineEventDTO]

    @classmethod
    def from_task(cls, task: SubagentTask) -> SubagentTaskDetailDTO:
        return cls(
            task=SubagentTaskDTO.from_task(task),
            trace_id=task.trace_id,
            turn_id=task.turn_id,
            spawn_tool_call_id=task.spawn_tool_call_id,
            replaces_task_id=task.replaces_task_id,
            child_depth=task.child_depth,
            timeline=[SubagentTimelineEventDTO.from_event(item) for item in task.lifecycle_outbox],
        )


class InvalidSubagentTaskTransitionError(ValueError):
    """Raised when a caller attempts a non-idempotent illegal transition."""


class SubagentTaskConflictError(ValueError):
    """Raised when an idempotent create key is reused for another logical task."""


class SubagentTaskStore:
    """Atomic per-task JSON store with lifecycle events committed in the same rename."""

    def __init__(self, workspace: Path) -> None:
        self.root = workspace / "subagent_tasks"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, task_id: str) -> asyncio.Lock:
        return self._locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def _storage_key(task_id: str) -> str:
        return base64.urlsafe_b64encode(task_id.encode()).decode().rstrip("=")

    def _path(self, task_id: str) -> Path:
        return self.root / f"{self._storage_key(task_id)}.json"

    @staticmethod
    def _event(
        task: SubagentTask,
        event_type: str,
        occurred_at: datetime,
        summary: dict[str, Any] | None = None,
    ) -> SubagentLifecycleOutboxEvent:
        return SubagentLifecycleOutboxEvent(
            idempotency_key=f"{task.task_id}:{task.revision}:{event_type}",
            event_type=event_type,
            task_id=task.task_id,
            revision=task.revision,
            occurred_at=occurred_at,
            summary={
                "task_status": str(task.status),
                "task_phase": str(task.phase),
                "termination_state": str(task.termination.state),
                "delivery_phase": str(task.delivery.phase),
                **(summary or {}),
            },
        )

    async def create(
        self,
        *,
        task_id: str,
        owner_session_key: str,
        trace_id: str | None = None,
        turn_id: str | None = None,
        label: str = "",
        owner_run_id: str | None = None,
        child_run_id: str | None = None,
        spawn_tool_call_id: str | None = None,
        required: bool = False,
        task_group: str = "default",
        attempt: int = 1,
        replaces_task_id: str | None = None,
        task_spec: TaskSpec | None = None,
        idempotency_key: str | None = None,
        child_depth: int = 0,
        budget: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            existing = self.load(task_id)
            if existing is not None:
                identity = (existing.owner_session_key, existing.owner_run_id, existing.required)
                requested = (owner_session_key, owner_run_id, required)
                if identity != requested:
                    raise SubagentTaskConflictError(
                        f"subagent task {task_id} already exists with another owner"
                    )
                return existing
            occurred_at = now or _utc_now()
            task = SubagentTask(
                revision=1,
                task_id=task_id,
                owner_session_key=owner_session_key,
                trace_id=trace_id,
                turn_id=turn_id,
                owner_run_id=owner_run_id,
                child_run_id=child_run_id,
                spawn_tool_call_id=spawn_tool_call_id,
                label=label[:MAX_TASK_LABEL_CHARS],
                required=required,
                task_group=task_group,
                attempt=attempt,
                replaces_task_id=replaces_task_id,
                task_spec=task_spec,
                idempotency_key=idempotency_key,
                child_depth=child_depth,
                budget=dict(budget or {}),
                created_at=occurred_at,
            )
            task.lifecycle_outbox.append(self._event(task, "subagent_created", occurred_at))
            self._write(task)
            return task.model_copy(deep=True)

    def load(self, task_id: str) -> SubagentTask | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("subagent task record must be a JSON object")
        normalized = self._normalize_legacy(raw)
        task = SubagentTask.model_validate(normalized)
        if task.task_id != task_id:
            raise ValueError("subagent task storage identity mismatch")
        return task

    def list_tasks(self) -> list[SubagentTask]:
        tasks: list[SubagentTask] = []
        for path in sorted(self.root.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("subagent task record must be a JSON object")
            tasks.append(SubagentTask.model_validate(self._normalize_legacy(raw)))
        return tasks

    def snapshot(self, owner_session_key: str) -> SubagentSnapshotDTO:
        tasks = [
            SubagentTaskDTO.from_task(task)
            for task in self.list_tasks()
            if task.owner_session_key == owner_session_key
        ]
        tasks.sort(key=lambda item: (item.created_at, item.task_id))
        return SubagentSnapshotDTO(
            tasks=tasks,
            max_revision=max((item.revision for item in tasks), default=0),
        )

    async def mark_outbox_published(
        self,
        task_id: str,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        async with self._lock(task_id):
            task = self._required(task_id)
            for event in task.lifecycle_outbox:
                if event.idempotency_key != idempotency_key:
                    continue
                if event.published_at is not None:
                    return False
                event.published_at = now or _utc_now()
                self._write(task)
                return True
            return False

    async def transition_status(
        self,
        task_id: str,
        status: SubagentTaskStatus,
        *,
        error: str | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            task = self._required(task_id)
            current = SubagentTaskStatus(task.status)
            target = SubagentTaskStatus(status)
            if current == target:
                return task
            if current in TERMINAL_TASK_STATUSES:
                # Late or duplicate terminal results are rejected without rewriting evidence.
                return task
            if target not in _STATUS_TRANSITIONS.get(current, frozenset()):
                raise InvalidSubagentTaskTransitionError(
                    f"invalid status transition: {current} -> {target}"
                )
            occurred_at = now or _utc_now()
            before = current.value
            task.status = target
            if target == SubagentTaskStatus.RUNNING and task.started_at is None:
                task.started_at = occurred_at
            if target in TERMINAL_TASK_STATUSES:
                task.finished_at = occurred_at
            if error is not None:
                task.error = error[:MAX_TASK_ERROR_CHARS]
            if target == SubagentTaskStatus.LOST:
                event_type = "subagent_lost"
            elif target in TERMINAL_TASK_STATUSES:
                event_type = "subagent_terminal"
            elif target == SubagentTaskStatus.QUEUED:
                event_type = "subagent_admitted"
            else:
                event_type = "subagent_phase_changed"
            self._commit(
                task,
                event_type,
                occurred_at,
                {"from_status": before, "to_status": target.value},
            )
            return task.model_copy(deep=True)

    async def update_runtime(
        self,
        task_id: str,
        *,
        phase: SubagentExecutionPhase | str | None = None,
        executor: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            task = self._required(task_id)
            if SubagentTaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                return task
            changed: dict[str, Any] = {}
            if phase is not None and task.phase != phase:
                task.phase = SubagentExecutionPhase(phase)
                changed["phase"] = str(task.phase)
            if executor is not None and task.executor != executor:
                task.executor = dict(executor)
                changed["executor_recorded"] = True
            if progress is not None and task.progress != progress:
                task.progress = dict(progress)
                changed["progress_updated"] = True
            if usage is not None and task.usage != usage:
                task.usage = dict(usage)
                changed["usage_updated"] = True
            if not changed:
                return task
            event_type = (
                "subagent_usage_updated"
                if set(changed) == {"usage_updated"}
                else "subagent_phase_changed"
            )
            self._commit(task, event_type, now or _utc_now(), changed)
            return task.model_copy(deep=True)

    async def update_budget(
        self,
        task_id: str,
        budget: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> SubagentTask:
        """Persist reservation release/settlement even after terminal status."""
        async with self._lock(task_id):
            task = self._required(task_id)
            if task.budget == budget:
                return task
            task.budget = dict(budget)
            self._commit(
                task,
                "subagent_budget_updated",
                now or _utc_now(),
                {"reservation_state": task.budget.get("reservation_state")},
            )
            return task.model_copy(deep=True)

    async def record_termination(
        self,
        task_id: str,
        state: SubagentTerminationState | str,
        *,
        evidence: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            task = self._required(task_id)
            current = SubagentTerminationState(task.termination.state)
            target = SubagentTerminationState(state)
            if current == target:
                return task
            if SubagentTaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                return task
            if target not in _TERMINATION_TRANSITIONS.get(current, frozenset()):
                raise InvalidSubagentTaskTransitionError(
                    f"invalid termination transition: {current} -> {target}"
                )
            occurred_at = now or _utc_now()
            task.termination.state = target
            task.termination.evidence = dict(evidence) if evidence is not None else None
            if target == SubagentTerminationState.TERMINATION_FAILED:
                task.status = SubagentTaskStatus.LOST
                task.finished_at = task.finished_at or occurred_at
                task.error = task.error or "child termination could not be confirmed"
            task.revision += 1
            summary = {"from_termination": current.value, "to_termination": target.value}
            event_type = (
                "subagent_cancel_requested"
                if target == SubagentTerminationState.CANCEL_REQUESTED
                else "subagent_termination_decided"
            )
            task.lifecycle_outbox.append(self._event(task, event_type, occurred_at, summary))
            if target == SubagentTerminationState.TERMINATION_FAILED:
                task.lifecycle_outbox.append(
                    self._event(
                        task,
                        "subagent_lost",
                        occurred_at,
                        {"to_status": SubagentTaskStatus.LOST.value},
                    )
                )
            self._write(task)
            return task.model_copy(deep=True)

    async def recover_runtime(self, running_task_ids: set[str] | None = None) -> int:
        active = running_task_ids or set()
        recovered = 0
        for task in self.list_tasks():
            if task.task_id in active or task.status not in {
                SubagentTaskStatus.QUEUED,
                SubagentTaskStatus.RUNNING,
            }:
                continue
            await self.record_recovery(
                task.task_id,
                evidence={"executor_present": False, "startup_reconciliation": True},
            )
            await self.record_termination(
                task.task_id,
                SubagentTerminationState.TERMINATION_FAILED,
                evidence={"executor_present": False, "exit_observed": False},
            )
            recovered += 1
        return recovered

    async def record_recovery(
        self,
        task_id: str,
        *,
        evidence: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        """Record one durable startup-reconciliation decision before terminal arbitration."""
        async with self._lock(task_id):
            task = self._required(task_id)
            if SubagentTaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                return task
            occurred_at = now or _utc_now()
            self._commit(
                task,
                "subagent_recovered",
                occurred_at,
                {"recovery_evidence": dict(evidence or {})},
            )
            return task.model_copy(deep=True)

    async def mark_result_ready(
        self,
        task_id: str,
        *,
        result: TaskResult | None = None,
        now: datetime | None = None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            task = self._required(task_id)
            current = SubagentDeliveryPhase(task.delivery.phase)
            if current == SubagentDeliveryPhase.READY:
                return task
            if current != SubagentDeliveryPhase.NOT_READY:
                raise InvalidSubagentTaskTransitionError(
                    f"invalid delivery transition: {current} -> ready"
                )
            occurred_at = now or _utc_now()
            task.task_result = result
            task.delivery.phase = SubagentDeliveryPhase.READY
            self._commit(
                task,
                "subagent_result_ready",
                occurred_at,
                {"from_delivery": current.value, "to_delivery": "ready"},
            )
            return task.model_copy(deep=True)

    async def claim_result(
        self,
        task_id: str,
        owner_run_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[SubagentTask, bool]:
        async with self._lock(task_id):
            task = self._required(task_id)
            phase = SubagentDeliveryPhase(task.delivery.phase)
            if phase in {
                SubagentDeliveryPhase.CLAIMED_PENDING_DELIVERY,
                SubagentDeliveryPhase.DELIVERED,
            }:
                return task, False
            if phase not in {SubagentDeliveryPhase.READY, SubagentDeliveryPhase.DELIVERY_FAILED}:
                raise InvalidSubagentTaskTransitionError(
                    f"cannot claim result in delivery phase {phase}"
                )
            occurred_at = now or _utc_now()
            task.delivery.phase = SubagentDeliveryPhase.CLAIMED_PENDING_DELIVERY
            task.delivery.claim_owner_run_id = owner_run_id
            task.delivery.claimed_at = occurred_at
            self._commit(
                task,
                "subagent_result_claimed",
                occurred_at,
                {"claim_owner_run_id": owner_run_id},
            )
            return task.model_copy(deep=True), True

    async def mark_delivered(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> SubagentTask:
        task = await self._transition_delivery(
            task_id,
            expected={SubagentDeliveryPhase.CLAIMED_PENDING_DELIVERY},
            target=SubagentDeliveryPhase.DELIVERED,
            event_type="subagent_result_delivered",
            now=now,
        )
        return task

    async def mark_delivery_failed(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> SubagentTask:
        return await self._transition_delivery(
            task_id,
            expected={SubagentDeliveryPhase.CLAIMED_PENDING_DELIVERY},
            target=SubagentDeliveryPhase.DELIVERY_FAILED,
            event_type="subagent_delivery_failed",
            now=now,
        )

    async def _transition_delivery(
        self,
        task_id: str,
        *,
        expected: set[SubagentDeliveryPhase],
        target: SubagentDeliveryPhase,
        event_type: str,
        now: datetime | None,
    ) -> SubagentTask:
        async with self._lock(task_id):
            task = self._required(task_id)
            current = SubagentDeliveryPhase(task.delivery.phase)
            if current == target:
                return task
            if current not in expected:
                raise InvalidSubagentTaskTransitionError(
                    f"invalid delivery transition: {current} -> {target}"
                )
            occurred_at = now or _utc_now()
            task.delivery.phase = target
            if target == SubagentDeliveryPhase.DELIVERED:
                task.delivery.delivered_at = occurred_at
            self._commit(
                task,
                event_type,
                occurred_at,
                {"from_delivery": current.value, "to_delivery": target.value},
            )
            return task.model_copy(deep=True)

    def _required(self, task_id: str) -> SubagentTask:
        task = self.load(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _commit(
        self,
        task: SubagentTask,
        event_type: str,
        occurred_at: datetime,
        summary: dict[str, Any],
    ) -> None:
        task.revision += 1
        task.lifecycle_outbox.append(self._event(task, event_type, occurred_at, summary))
        self._write(task)

    def _write(self, task: SubagentTask) -> None:
        path = self._path(task.task_id)
        tmp_path = path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as stream:
                stream.write(task.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, path)
            with suppress(PermissionError):
                fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                except OSError as exc:
                    if exc.errno != errno.EINVAL:
                        raise
                finally:
                    os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _normalize_legacy(raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("schema_version") == SUBAGENT_TASK_SCHEMA_VERSION:
            return raw
        normalized = dict(raw)
        normalized["schema_version"] = SUBAGENT_TASK_SCHEMA_VERSION
        normalized["revision"] = max(1, int(normalized.get("revision") or 1))
        normalized.setdefault("created_at", _utc_now().isoformat())
        normalized.setdefault("termination", {"state": "none", "evidence": None})
        normalized.setdefault("delivery", {"phase": "not_ready"})
        normalized.setdefault("phase", "initializing")
        normalized.setdefault("status", "created")
        normalized.setdefault("required", False)
        normalized.setdefault("task_group", "default")
        normalized.setdefault("attempt", 1)
        normalized.setdefault("label", "")
        normalized.setdefault("progress", {})
        normalized.setdefault("usage", {})
        normalized.setdefault("budget", {})
        normalized.setdefault("task_spec", None)
        normalized.setdefault("task_result", None)
        normalized.setdefault("idempotency_key", None)
        normalized.setdefault("child_depth", 0)
        normalized.setdefault("legacy_inferred", True)
        normalized.setdefault("lifecycle_outbox", [])
        return normalized
