"""Spawn tool for creating background subagents."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_audit_tool_call_id, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
)
from nanobot.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


@tool_parameters(
    ObjectSchema(
        properties={
            "task": StringSchema("Legacy text task for the subagent", nullable=True),
            "task_spec": ObjectSchema(
                properties={
                    "schema_version": IntegerSchema(minimum=1, maximum=1),
                    "objective": StringSchema(min_length=1, max_length=8000),
                    "context": StringSchema(max_length=8000),
                    "constraints": ArraySchema(StringSchema(max_length=1000), max_items=32),
                    "deliverables": ArraySchema(StringSchema(max_length=1000), max_items=32),
                    "acceptance_criteria": ArraySchema(
                        StringSchema(max_length=1000), max_items=32
                    ),
                    "dependencies": ArraySchema(StringSchema(max_length=256), max_items=32),
                    "output_mode": StringSchema(enum=["text", "structured_preferred"]),
                },
                required=["objective"],
                additional_properties=False,
                nullable=True,
            ),
            "label": StringSchema("Optional short label for the task (for display)"),
            "temperature": NumberSchema(
                description=(
                    "Optional sampling temperature for the subagent "
                    "(0.0 = deterministic, higher = more creative). "
                    "Defaults to the provider's configured temperature."
                ),
                minimum=0.0,
                maximum=2.0,
            ),
            "required": BooleanSchema(
                description=(
                    "Whether this task must succeed before its owner Run may publish a final "
                    "answer and before the current Goal can complete."
                ),
                default=False,
            ),
            "task_group": StringSchema(
                "Optional required-task barrier group.",
                min_length=1,
                max_length=64,
                nullable=True,
            ),
            "replaces_task_id": StringSchema(
                "Optional failed required task that this task explicitly replaces.",
                min_length=1,
                max_length=64,
                nullable=True,
            ),
        },
        required=[],
        additional_properties=False,
    ).to_json_schema()
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(manager=ctx.subagent_manager)

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful. When required=true, retain the "
            "returned task_id and task_group, then call await_subagents for the complete group "
            "before a final answer. A failed, cancelled, or timed-out required task must be "
            "replaced explicitly or reported by blocking the Goal. required=false remains a "
            "background task and does not delay the owner Run; never call await_subagents for "
            "required=false tasks. Their results are delivered asynchronously."
        )

    async def execute(
        self,
        task: str | None = None,
        task_spec: dict[str, Any] | None = None,
        label: str | None = None,
        temperature: float | None = None,
        required: bool = False,
        task_group: str | None = None,
        replaces_task_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        task = (task or "").strip()
        if bool(task) == bool(task_spec):
            return ToolResult.error("Error: provide exactly one of task or task_spec")
        group = (task_group or "default").strip()
        if not group or len(group) > 64:
            return ToolResult.error("Error: task_group must contain 1 to 64 characters")
        if replaces_task_id and not required:
            return ToolResult.error("Error: replaces_task_id requires required=true")
        running = self._manager.get_running_count()
        limit = self._manager.max_concurrent_subagents
        if running >= limit:
            return ToolResult.error(
                f"Error: Cannot spawn subagent: concurrency limit reached "
                f"({running}/{limit} running). Wait for a running subagent "
                f"to complete before spawning a new one."
            )
        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: spawn requires an active model runtime")
        origin_channel = request_ctx.channel
        origin_chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{origin_channel}:{origin_chat_id}"
        try:
            result = await self._manager.spawn(
                task=task,
                task_spec=task_spec,
                runtime=request_ctx.runtime,
                label=label,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                session_key=session_key,
                origin_message_id=request_ctx.message_id,
                temperature=temperature,
                workspace_scope=current_workspace_scope(),
                required=required,
                task_group=group,
                spawn_tool_call_id=current_audit_tool_call_id(),
                replaces_task_id=(replaces_task_id or "").strip() or None,
                enforce_limit=True,
                structured=True,
                child_depth=int(request_ctx.metadata.get("subagent_depth") or 0),
            )
        except Exception as exc:
            from nanobot.agent.subagent import SubagentAdmissionError

            if isinstance(exc, SubagentAdmissionError):
                return ToolResult.error(json.dumps({
                    "error": "spawn_rejected",
                    "reason": exc.reason,
                    "message": str(exc),
                }, ensure_ascii=True, sort_keys=True))
            if not isinstance(exc, (TypeError, ValueError, RuntimeError)):
                return ToolResult.error(
                    f"Error: failed to schedule subagent: {type(exc).__name__}: {exc}"
                )
            return ToolResult.error(f"Error: spawn rejected: {exc}")
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=True, sort_keys=True)
        return str(result)
