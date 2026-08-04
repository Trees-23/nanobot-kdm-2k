"""Reliable publication of durable subagent lifecycle outbox events."""

from __future__ import annotations

import time
from typing import Any

from nanobot.audit.ids import new_audit_id
from nanobot.audit.schema import EVENT_DRAFT_MODELS
from nanobot.audit.types import EventType
from nanobot.session.subagent_tasks import SubagentTask, SubagentTaskStore


class SubagentLifecyclePublisher:
    def __init__(self, store: SubagentTaskStore, emitter: Any | None) -> None:
        self._store = store
        self._emitter = emitter

    async def flush_task(self, task_id: str) -> int:
        task = self._store.load(task_id)
        return 0 if task is None else await self._flush(task)

    async def flush_pending(self) -> int:
        published = 0
        for task in self._store.list_tasks():
            published += await self._flush(task)
        return published

    async def _flush(self, task: SubagentTask) -> int:
        published = 0
        for pending in task.lifecycle_outbox:
            if pending.published_at is not None:
                continue
            if self._emitter is None:
                await self._store.mark_outbox_published(task.task_id, pending.idempotency_key)
                published += 1
                continue
            event_type = EventType(pending.event_type)
            model = EVENT_DRAFT_MODELS[event_type]
            summary = pending.summary
            event = model.model_validate({
                "event_id": new_audit_id(),
                "event_type": event_type,
                "occurred_at": pending.occurred_at,
                "monotonic_ns": time.monotonic_ns(),
                "trace_id": task.trace_id,
                "turn_id": task.turn_id,
                "run_id": task.child_run_id or task.owner_run_id,
                "parent_run_id": task.owner_run_id,
                "resumed_from_run_id": None,
                "caused_by_event_id": None,
                "model_call_id": None,
                "attempt_id": None,
                "tool_call_id": task.spawn_tool_call_id,
                "checkpoint_id": None,
                "goal_id": None,
                "delivery_id": None,
                "session_key": task.owner_session_key,
                "source_type": "subagent_task",
                "source_metadata": {
                    "task_group": task.task_group,
                    "replaces_task_id": task.replaces_task_id,
                },
                "iteration": None,
                "subagent_task_id": task.task_id,
                "task_label": task.label or None,
                "task_revision": pending.revision,
                "idempotency_key": pending.idempotency_key,
                "task_status": str(summary.get("task_status") or task.status),
                "task_phase": str(summary.get("task_phase") or task.phase),
                "termination_state": str(
                    summary.get("termination_state") or task.termination.state
                ),
                "delivery_phase": str(summary.get("delivery_phase") or task.delivery.phase),
                "required_task": task.required,
                "legacy_inferred": task.legacy_inferred,
            })
            result = await self._emitter.emit(event, critical=True)
            if not (
                getattr(result, "committed", False)
                or getattr(result, "disabled", False)
            ):
                break
            await self._store.mark_outbox_published(task.task_id, pending.idempotency_key)
            published += 1
        return published
