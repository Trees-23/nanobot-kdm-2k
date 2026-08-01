"""Subagent manager for background task execution."""

import asyncio
import json
import time
import uuid
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.audit.context import AuditRunContext
from nanobot.bus.events import AUDIT_CONTEXT_META, InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, ToolsConfig
from nanobot.providers.base import LLMProvider
from nanobot.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template

TERMINAL_STATUS_CACHE_LIMIT = 256


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None
    terminal_status: str | None = None
    child_run_id: str | None = None
    session_key: str | None = None
    required: bool = False
    owner_run_id: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        workspace: Path | None = None,
        bus: MessageBus | None = None,
        max_tool_result_chars: int | None = None,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        fail_on_tool_error: bool | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        audit_emitter: Any | None = None,
        goal_orchestration: Any | None = None,
    ):
        if workspace is None:
            raise TypeError("SubagentManager.__init__() missing required argument: 'workspace'")
        if bus is None:
            raise TypeError("SubagentManager.__init__() missing required argument: 'bus'")
        if max_tool_result_chars is None:
            raise TypeError(
                "SubagentManager.__init__() missing required argument: 'max_tool_result_chars'"
            )
        if model is not None and provider is None:
            raise TypeError("SubagentManager model compatibility argument requires provider")

        defaults = AgentDefaults()
        self._compat_runtime: LLMRuntime | None = None
        if provider is not None:
            warnings.warn(
                "SubagentManager provider/model constructor arguments are deprecated; "
                "pass runtime=... to spawn() instead",
                DeprecationWarning,
                stacklevel=2,
            )
            self._compat_runtime = LLMRuntime.capture(
                provider,
                model or provider.get_default_model(),
                context_window_tokens=defaults.context_window_tokens,
            )
        self.workspace = workspace
        self.bus = bus
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self.fail_on_tool_error = (
            fail_on_tool_error
            if fail_on_tool_error is not None
            else defaults.fail_on_tool_error
        )
        self.runner = AgentRunner(audit_emitter=audit_emitter)
        self._audit_emitter = audit_emitter
        self._goal_orchestration = goal_orchestration
        self._exec_session_manager = ExecSessionManager()
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._terminal_statuses: OrderedDict[str, SubagentStatus] = OrderedDict()
        self._timeout_task_ids: set[str] = set()
        self._spawn_lock = asyncio.Lock()

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Update the deprecated runtime source used by legacy ``spawn`` calls."""
        warnings.warn(
            "SubagentManager.set_provider() is deprecated; pass runtime=... to spawn() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        context_window_tokens = (
            self._compat_runtime.context_window_tokens
            if self._compat_runtime is not None
            else AgentDefaults().context_window_tokens
        )
        self._compat_runtime = LLMRuntime.capture(
            provider,
            model,
            context_window_tokens=context_window_tokens,
        )

    def _compat_spawn_runtime(self) -> LLMRuntime:
        runtime = self._compat_runtime
        if runtime is None:
            raise TypeError(
                "SubagentManager.spawn() missing required keyword-only argument: 'runtime'"
            )
        warnings.warn(
            "SubagentManager.spawn() without runtime is deprecated; pass runtime=... explicitly",
            DeprecationWarning,
            stacklevel=3,
        )
        return LLMRuntime.capture(
            runtime.provider,
            runtime.model,
            context_window_tokens=runtime.context_window_tokens,
        )

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use."""
        return ToolsConfig(
            exec=self.tools_config.exec,
            web=self.tools_config.web,
            file=self.tools_config.file,
            restrict_to_workspace=self.restrict_to_workspace,
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            exec_session_manager=self._exec_session_manager,
            file_state_store=FileStates(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        return registry

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        *,
        runtime: LLMRuntime | None = None,
        required: bool = False,
        task_group: str = "default",
        spawn_tool_call_id: str | None = None,
        replaces_task_id: str | None = None,
        enforce_limit: bool = False,
        structured: bool = False,
    ) -> dict[str, Any] | str:
        """Spawn a subagent to execute a task in the background."""
        if runtime is None:
            runtime = self._compat_spawn_runtime()
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin: dict[str, Any] = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            "session_key": session_key,
        }
        request = current_request_context()
        raw_audit = request.metadata.get(AUDIT_CONTEXT_META) if request is not None else None
        audit_context: AuditRunContext | None = None
        owner_run_id: str | None = None
        if isinstance(raw_audit, dict) and all(
            isinstance(raw_audit.get(name), str) and raw_audit[name]
            for name in ("trace_id", "turn_id", "run_id")
        ):
            parent = AuditRunContext(
                trace_id=raw_audit["trace_id"],
                turn_id=raw_audit["turn_id"],
                run_id=raw_audit["run_id"],
            )
            owner_run_id = parent.run_id
            audit_context = parent.child_run(
                source_type="subagent",
                source_metadata={
                    "subagent_task_id": task_id,
                    "spawn_tool_call_id": spawn_tool_call_id,
                    "task_group": task_group,
                    "required": required,
                },
            )
            origin[AUDIT_CONTEXT_META] = {
                "trace_id": audit_context.trace_id,
                "turn_id": audit_context.turn_id,
                "run_id": audit_context.run_id,
            }

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            child_run_id=audit_context.run_id if audit_context is not None else None,
            session_key=session_key,
            required=required,
            owner_run_id=owner_run_id,
        )
        async with self._spawn_lock:
            if enforce_limit and self.get_running_count() >= self.max_concurrent_subagents:
                raise RuntimeError("subagent concurrency limit reached")
            if required:
                if not session_key or self._goal_orchestration is None:
                    raise ValueError("required subagents need an active goal in the current session")
                await self._goal_orchestration.register(
                    session_key,
                    task_id=task_id,
                    label=display_label,
                    group=task_group,
                    child_run_id=status.child_run_id,
                    spawn_tool_call_id=spawn_tool_call_id,
                    owner_run_id=owner_run_id,
                    replaces_task_id=replaces_task_id,
                )
            self._task_statuses[task_id] = status
            try:
                bg_task = asyncio.create_task(
                    self._run_subagent(
                        task_id,
                        task,
                        display_label,
                        origin,
                        status,
                        runtime,
                        origin_message_id,
                        workspace_scope,
                        audit_context,
                        required,
                    )
                )
            except BaseException:
                self._task_statuses.pop(task_id, None)
                if required and session_key:
                    await self._goal_orchestration.remove_registration(session_key, task_id)
                raise
            self._running_tasks[task_id] = bg_task
            if session_key:
                self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            completed = self._task_statuses.pop(task_id, None)
            if completed is not None:
                self._cache_terminal_status(completed)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        result = {
            "started": True,
            "task_id": task_id,
            "required": required,
            "task_group": task_group,
            "child_run_id": status.child_run_id,
        }
        if structured:
            return result
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, Any],
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        audit_context: AuditRunContext | None = None,
        required: bool = False,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        terminal_status = "failed"
        terminal_error: str | None = None
        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            tools = self._build_tools(workspace=root, tools_config=cfg)
            system_prompt = self._build_subagent_prompt(workspace=root)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            request_metadata = {}
            if audit_context is not None:
                request_metadata[AUDIT_CONTEXT_META] = {
                    "trace_id": audit_context.trace_id,
                    "turn_id": audit_context.turn_id,
                    "run_id": audit_context.run_id,
                }
            request_token = bind_request_context(RequestContext(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                message_id=origin_message_id,
                session_key=sess_key,
                runtime=runtime,
                metadata=request_metadata,
            ))
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            try:
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    runtime=runtime,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status),
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
                    fail_on_tool_error=self.fail_on_tool_error,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                    audit_context=audit_context,
                ))
            finally:
                if token is not None:
                    reset_workspace_scope(token)
                reset_request_context(request_token)
            status.phase = "done"
            status.stop_reason = result.stop_reason

            if result.stop_reason == "completed":
                terminal_status = "succeeded"
                final_result = result.final_content or "Task completed successfully."
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(
                    task_id, label, task, final_result, origin, "ok", origin_message_id
                )
            elif result.stop_reason == "tool_error":
                terminal_error = self._format_partial_progress(result)
                status.tool_events = list(result.tool_events)
                await self._announce_result(
                    task_id, label, task,
                    self._format_partial_progress(result),
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "error":
                terminal_error = result.error or "subagent execution failed"
                if result.error_kind == "timeout":
                    terminal_status = "timed_out"
                await self._announce_result(
                    task_id, label, task,
                    result.error or "Error: subagent execution failed.",
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "max_iterations":
                terminal_error = "Iteration budget exhausted before task completion."
                await self._announce_result(
                    task_id, label, task, terminal_error, origin, "error", origin_message_id
                )
            elif result.stop_reason == "empty_final_response":
                terminal_error = "Subagent returned no final response; task completion is unverified."
                await self._announce_result(
                    task_id, label, task, terminal_error, origin, "error", origin_message_id
                )
            else:
                terminal_error = (
                    f"Subagent stopped with non-success reason {result.stop_reason!r}; "
                    "task completion is unverified."
                )
                await self._announce_result(
                    task_id, label, task, terminal_error, origin, "error", origin_message_id
                )
        except asyncio.CancelledError:
            terminal_status = "timed_out" if task_id in self._timeout_task_ids else "cancelled"
            terminal_error = (
                "subagent task exceeded the required-join deadline"
                if terminal_status == "timed_out"
                else "subagent task was cancelled"
            )
            raise
        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            terminal_error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error", origin_message_id)
        finally:
            status.terminal_status = terminal_status
            if terminal_error:
                status.error = terminal_error
            if required and status.session_key and self._goal_orchestration is not None:
                try:
                    await self._goal_orchestration.finish(
                        status.session_key, task_id, terminal_status, terminal_error
                    )
                except Exception:
                    logger.exception("Failed to persist terminal state for subagent [{}]", task_id)
            self._timeout_task_ids.discard(task_id)

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, Any],
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
        }
        if isinstance(origin.get(AUDIT_CONTEXT_META), dict):
            metadata[AUDIT_CONTEXT_META] = dict(origin[AUDIT_CONTEXT_META])
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self, workspace: Path | None = None) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.skills import SkillsLoader

        root = workspace or self.workspace
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            workspace=str(root),
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        task_ids = [
            tid
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        tasks = [self._running_tasks[tid] for tid in task_ids]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task_id in task_ids:
            status = self.get_status(task_id)
            if status is None:
                continue
            status.terminal_status = "cancelled"
            status.error = status.error or "subagent task was cancelled"
            if status.required and self._goal_orchestration is not None:
                await self._goal_orchestration.finish(
                    session_key, task_id, "cancelled", status.error
                )
        self.clear_terminal_statuses_by_session(session_key)
        return len(tasks)

    async def timeout_tasks(self, task_ids: list[str], grace_seconds: float = 2.0) -> bool:
        """Cancel selected children and report whether every task actually exited."""
        active = [
            self._running_tasks[task_id]
            for task_id in task_ids
            if task_id in self._running_tasks and not self._running_tasks[task_id].done()
        ]
        self._timeout_task_ids.update(task_ids)
        for task in active:
            task.cancel()
        if not active:
            return True
        try:
            await asyncio.wait_for(asyncio.gather(*active, return_exceptions=True), grace_seconds)
        except asyncio.TimeoutError:
            return False
        return all(task.done() for task in active)

    async def close(self) -> None:
        """Cancel running subagents and close their shared exec sessions."""
        tasks = [task for task in self._running_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._terminal_statuses.clear()
        await self._exec_session_manager.close_all()

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def get_status(self, task_id: str) -> SubagentStatus | None:
        status = self._task_statuses.get(task_id)
        if status is not None:
            return status
        status = self._terminal_statuses.get(task_id)
        if status is not None:
            self._terminal_statuses.move_to_end(task_id)
        return status

    def _cache_terminal_status(self, status: SubagentStatus) -> None:
        minimal = SubagentStatus(
            task_id=status.task_id,
            label=status.label[:120],
            task_description="",
            started_at=status.started_at,
            phase=status.phase,
            stop_reason=status.stop_reason,
            error=(status.error or "")[:500] or None,
            terminal_status=status.terminal_status,
            child_run_id=status.child_run_id,
            session_key=status.session_key,
            required=status.required,
        )
        self._terminal_statuses[status.task_id] = minimal
        self._terminal_statuses.move_to_end(status.task_id)
        while len(self._terminal_statuses) > TERMINAL_STATUS_CACHE_LIMIT:
            self._terminal_statuses.popitem(last=False)

    def clear_terminal_statuses_by_session(self, session_key: str) -> int:
        task_ids = [
            task_id
            for task_id, status in self._terminal_statuses.items()
            if status.session_key == session_key
        ]
        for task_id in task_ids:
            self._terminal_statuses.pop(task_id, None)
        return len(task_ids)

    def running_task_ids(self) -> set[str]:
        return {task_id for task_id, task in self._running_tasks.items() if not task.done()}

    async def wait_for(self, task_ids: list[str], timeout: float) -> None:
        tasks = [self._running_tasks[task_id] for task_id in task_ids if task_id in self._running_tasks]
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, timeout))
