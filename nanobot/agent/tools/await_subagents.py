"""Goal-scoped barrier for required background subagents."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.session.goal_orchestration import deadline_remaining_seconds, obligation_status


@tool_parameters(
    tool_parameters_schema(
        task_ids=ArraySchema(
            StringSchema("Required task ID"),
            description="Task IDs owned by the current Goal.",
            min_items=1,
            max_items=100,
            nullable=True,
        ),
        task_group=StringSchema(
            "Required-task group owned by the current Goal.",
            min_length=1,
            max_length=64,
            nullable=True,
        ),
        timeout_seconds=NumberSchema(
            description="Maximum in-process wait for this call.", minimum=0, maximum=300
        ),
    )
)
class AwaitSubagentsTool(Tool):
    """Wait for a selected current-Goal task set without polling the model."""

    def __init__(self, manager: Any, orchestration: Any) -> None:
        self._manager = manager
        self._orchestration = orchestration

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(ctx.subagent_manager, ctx.goal_orchestration)

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.subagent_manager is not None and ctx.goal_orchestration is not None

    @property
    def name(self) -> str:
        return "await_subagents"

    @property
    def description(self) -> str:
        return (
            "Perform one bounded wait for required subagents owned by the current Goal. Select "
            "exactly one complete task_ids set or task_group. waiting=true means the barrier is "
            "still unresolved, not that a task reached a terminal state. A timeout leaves tasks "
            "and the Goal active; failed tasks must be explicitly replaced or the Goal blocked."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        task_ids: list[str] | None = None,
        task_group: str | None = None,
        timeout_seconds: float = 300,
        **kwargs: Any,
    ) -> str:
        request = current_request_context()
        if request is None or not request.session_key:
            return ToolResult.error("Error: await_subagents requires an active chat session")
        if bool(task_ids) == bool(task_group):
            return ToolResult.error("Error: select exactly one of task_ids or task_group")
        ids = list(dict.fromkeys(task_ids or []))
        try:
            initial = await self._orchestration.select(
                request.session_key,
                task_ids=ids or None,
                task_group=(task_group or "").strip() or None,
            )
            await self._orchestration.set_phase(request.session_key, "waiting_for_children")
            durable_remaining = [
                remaining
                for record in initial.values()
                if (remaining := deadline_remaining_seconds(record.get("deadline_at"))) is not None
            ]
            wait_budget = max(0.0, float(timeout_seconds))
            if durable_remaining:
                wait_budget = min(wait_budget, min(durable_remaining))
            await self._manager.wait_for(list(initial), wait_budget)
            if durable_remaining and min(durable_remaining) <= 0:
                timeout_tasks = getattr(self._manager, "timeout_tasks", None)
                if timeout_tasks is not None:
                    await timeout_tasks(list(initial))
            records = await self._orchestration.select(
                request.session_key,
                task_ids=list(initial),
                running_task_ids=self._manager.running_task_ids(),
            )
        except (TypeError, ValueError) as exc:
            return ToolResult.error(f"Error: await_subagents rejected: {exc}")

        tasks = {
            task_id: {
                "status": record.get("status"),
                "required": record.get("required"),
                "task_group": record.get("group"),
                "child_run_id": record.get("child_run_id"),
                "resolved_by_task_id": record.get("resolved_by_task_id"),
                "error": record.get("error"),
            }
            for task_id, record in records.items()
        }
        all_records = await self._orchestration.select(
            request.session_key,
            task_ids=list(records),
        )
        statuses = [obligation_status(all_records, task_id)[:2] for task_id in records]
        satisfied = all(ok for ok, _status in statuses)
        running = any(status == "running" for _ok, status in statuses)
        payload = {
            "barrier_satisfied": satisfied,
            "waiting": running,
            "tasks": tasks,
        }
        await self._orchestration.set_phase(
            request.session_key,
            "ready" if satisfied else ("waiting_for_children" if running else "failed"),
        )
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        if not satisfied and not running:
            return ToolResult.error(encoded, append_retry_hint=False)
        return encoded
