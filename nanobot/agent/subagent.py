"""Subagent manager for background task execution."""

import asyncio
import json
import time
import uuid
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.child_executor import ChildHandle, ProcessChildExecutor
from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
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
from nanobot.audit.subagent_lifecycle import SubagentLifecyclePublisher
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
from nanobot.session.subagent_tasks import (
    SubagentExecutionPhase,
    SubagentTaskStatus,
    SubagentTaskStore,
    TaskResult,
    TaskSpec,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template

TERMINAL_STATUS_CACHE_LIMIT = 256


class _SubagentTaskPersistenceError(RuntimeError):
    """Durable task truth could not be committed; result delivery must stop."""


class SubagentAdmissionError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    origin_message_id: str | None = None
    termination_state: str = "none"
    cancel_requested_at: float | None = None
    termination_evidence: dict[str, Any] | None = None
    child_depth: int = 0


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
        child_executor: ProcessChildExecutor | None = None,
        child_runtime_config: dict[str, Any] | None = None,
        child_audit_root: str | None = None,
        task_store: SubagentTaskStore | None = None,
        max_children_per_owner_run: int = 16,
        max_children_per_session: int = 64,
        max_child_depth: int = 1,
        max_total_subagent_tokens: int = 0,
        max_total_subagent_cost_usd: float = 0,
        max_subagent_wall_time_seconds: float = 0,
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
        self._child_executor = child_executor
        self._child_runtime_config = child_runtime_config
        self._child_audit_root = child_audit_root
        self._task_store = task_store or SubagentTaskStore(workspace)
        self._lifecycle_publisher = SubagentLifecyclePublisher(
            self._task_store,
            audit_emitter,
        )
        self.max_children_per_owner_run = max_children_per_owner_run
        self.max_children_per_session = max_children_per_session
        self.max_child_depth = max_child_depth
        self.max_total_subagent_tokens = max_total_subagent_tokens
        self.max_total_subagent_cost_usd = max_total_subagent_cost_usd
        self.max_subagent_wall_time_seconds = max_subagent_wall_time_seconds
        self._exec_session_manager = ExecSessionManager()
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._watchdog_tasks: dict[str, asyncio.Task[None]] = {}
        self._wall_timeout_in_progress: set[str] = set()
        self._executor_handles: dict[str, ChildHandle] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._terminal_statuses: OrderedDict[str, SubagentStatus] = OrderedDict()
        self._timeout_task_ids: set[str] = set()
        self._termination_failed_ids: set[str] = set()
        self._termination_outcomes: dict[str, asyncio.Event] = {}
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
        task_spec: TaskSpec | dict[str, Any] | None = None,
        child_depth: int = 0,
    ) -> dict[str, Any] | str:
        """Spawn a subagent to execute a task in the background."""
        if runtime is None:
            runtime = self._compat_spawn_runtime()
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        spec = (
            TaskSpec.model_validate(task_spec)
            if task_spec is not None
            else TaskSpec.from_legacy(task)
        )
        task = spec.objective
        execution_task = (
            task
            if task_spec is None
            else "Complete this versioned TaskSpec:\n" + spec.model_dump_json(indent=2)
        )
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
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            origin_message_id=origin_message_id,
            child_depth=child_depth,
        )
        durable_session_key = session_key or f"{origin_channel}:{origin_chat_id}"
        async with self._spawn_lock:
            if enforce_limit and self.get_running_count() >= self.max_concurrent_subagents:
                raise SubagentAdmissionError(
                    "concurrency_limit", "subagent concurrency limit reached"
                )
            existing_tasks = self._task_store.list_tasks()
            owner_scope = owner_run_id or durable_session_key
            owner_count = sum(
                item.owner_run_id == owner_run_id
                if owner_run_id is not None
                else item.owner_session_key == durable_session_key and item.owner_run_id is None
                for item in existing_tasks
            )
            session_tasks = [
                item for item in existing_tasks
                if item.owner_session_key == durable_session_key
            ]
            if owner_count >= self.max_children_per_owner_run:
                raise SubagentAdmissionError(
                    "child_count_limit", "owner Run child task limit reached"
                )
            if len(session_tasks) >= self.max_children_per_session:
                raise SubagentAdmissionError(
                    "session_child_count_limit", "session child task limit reached"
                )
            if child_depth > self.max_child_depth:
                raise SubagentAdmissionError("depth_limit", "subagent depth limit reached")
            idempotency_key = spec.idempotency_key(owner_scope)
            duplicate_ids = {
                item.task_id for item in existing_tasks
                if item.idempotency_key == idempotency_key
            }
            if duplicate_ids and replaces_task_id not in duplicate_ids:
                raise SubagentAdmissionError("duplicate_task", "duplicate TaskSpec rejected")
            reserved_tokens = int(runtime.generation.max_tokens or 0)
            if self.max_total_subagent_tokens > 0:
                used = sum(
                    self._effective_reserved_tokens(item.budget)
                    for item in session_tasks
                )
                if used + reserved_tokens > self.max_total_subagent_tokens:
                    raise SubagentAdmissionError(
                        "token_budget_exhausted", "session subagent token budget exhausted"
                    )
            if self.max_total_subagent_cost_usd > 0:
                observed_cost = sum(
                    float(item.usage.get("cost_usd") or 0)
                    for item in session_tasks
                )
                if observed_cost >= self.max_total_subagent_cost_usd:
                    raise SubagentAdmissionError(
                        "cost_budget_exhausted", "session subagent cost budget exhausted"
                    )
                raise SubagentAdmissionError(
                    "cost_reservation_unavailable",
                    "provider does not expose a reliable admission-time cost reservation",
                )
            if required:
                if not session_key or self._goal_orchestration is None:
                    raise SubagentAdmissionError(
                        "no_active_goal",
                        "required subagents need an active goal in the current session",
                    )
                try:
                    await self._goal_orchestration.validate_registration(
                        session_key,
                        replaces_task_id=replaces_task_id,
                    )
                except ValueError as exc:
                    reason = (
                        "no_active_goal"
                        if str(exc) == "required subagents need an active goal in the current session"
                        else "required_registration_invalid"
                    )
                    raise SubagentAdmissionError(reason, str(exc)) from exc
            await self._task_store.create(
                task_id=task_id,
                owner_session_key=durable_session_key,
                trace_id=audit_context.trace_id if audit_context is not None else None,
                turn_id=audit_context.turn_id if audit_context is not None else None,
                owner_run_id=owner_run_id,
                child_run_id=status.child_run_id,
                spawn_tool_call_id=spawn_tool_call_id,
                label=display_label,
                required=required,
                task_group=task_group,
                replaces_task_id=replaces_task_id,
                task_spec=spec,
                idempotency_key=idempotency_key,
                child_depth=child_depth,
                budget={
                    "max_tokens": reserved_tokens,
                    "reserved_tokens": reserved_tokens,
                    "reservation_state": "reserved",
                    "wall_time_seconds": self.max_subagent_wall_time_seconds or None,
                    "deadline_at": (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=self.max_subagent_wall_time_seconds)
                    ).isoformat()
                    if self.max_subagent_wall_time_seconds > 0
                    else None,
                },
            )
            await self._task_store.transition_status(task_id, SubagentTaskStatus.QUEUED)
            await self._lifecycle_publisher.flush_task(task_id)
            if required:
                try:
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
                except BaseException as exc:
                    await self._task_store.transition_status(
                        task_id,
                        SubagentTaskStatus.FAILED,
                        error=f"required obligation registration failed: {type(exc).__name__}",
                    )
                    await self._release_budget(task_id)
                    raise
            self._task_statuses[task_id] = status
            try:
                bg_task = asyncio.create_task(
                    self._run_subagent(
                        task_id,
                        execution_task,
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
                await self._task_store.transition_status(
                    task_id,
                    SubagentTaskStatus.FAILED,
                    error="subagent scheduling failed",
                )
                await self._release_budget(task_id)
                if required and session_key:
                    await self._goal_orchestration.remove_registration(session_key, task_id)
                raise
            self._running_tasks[task_id] = bg_task
            if self.max_subagent_wall_time_seconds > 0:
                self._watchdog_tasks[task_id] = asyncio.create_task(
                    self._enforce_wall_time(task_id, self.max_subagent_wall_time_seconds)
                )
            if session_key:
                self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            watchdog = self._watchdog_tasks.pop(task_id, None)
            if watchdog is not None and task_id not in self._wall_timeout_in_progress:
                watchdog.cancel()
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

    @staticmethod
    def _effective_reserved_tokens(budget: dict[str, Any]) -> int:
        state = budget.get("reservation_state")
        if state == "released":
            return 0
        if state == "settled":
            return int(budget.get("consumed_tokens") or 0)
        return int(budget.get("reserved_tokens") or 0)

    async def _release_budget(self, task_id: str) -> None:
        task = self._task_store.load(task_id)
        if task is None or task.budget.get("reservation_state") != "reserved":
            return
        budget = dict(task.budget)
        budget["reservation_state"] = "released"
        budget["released_reason"] = "startup_failed"
        await self._task_store.update_budget(task_id, budget)
        await self._lifecycle_publisher.flush_task(task_id)

    async def _settle_budget(self, task_id: str, usage: dict[str, Any]) -> None:
        task = self._task_store.load(task_id)
        if task is None or task.budget.get("reservation_state") != "reserved":
            return
        budget = dict(task.budget)
        total_tokens = int(
            usage.get("total_tokens")
            or int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        )
        budget["reservation_state"] = "settled"
        budget["consumed_tokens"] = total_tokens
        await self._task_store.update_budget(task_id, budget)
        await self._lifecycle_publisher.flush_task(task_id)

    async def _enforce_wall_time(self, task_id: str, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            task = self._running_tasks.get(task_id)
            if task is not None and not task.done():
                self._wall_timeout_in_progress.add(task_id)
                try:
                    await self.timeout_tasks([task_id], grace_seconds=0.1)
                finally:
                    self._wall_timeout_in_progress.discard(task_id)
        except asyncio.CancelledError:
            return

    def _uses_process_executor(self) -> bool:
        return self._child_executor is not None and self._child_runtime_config is not None

    async def _run_process_child(
        self,
        *,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, Any],
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None,
        workspace_scope: WorkspaceScope | None,
        audit_context: AuditRunContext | None,
        checkpoint_callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> AgentRunResult:
        executor = self._child_executor
        if executor is None or self._child_runtime_config is None:
            raise RuntimeError("process child executor is not configured")
        root = workspace_scope.project_path if workspace_scope is not None else self.workspace
        llm_timeout = (
            self._llm_wall_timeout_for_session(status.session_key)
            if self._llm_wall_timeout_for_session
            else None
        )
        raw_audit = None
        if audit_context is not None:
            raw_audit = {
                "trace_id": audit_context.trace_id,
                "turn_id": audit_context.turn_id,
                "run_id": audit_context.run_id,
                "parent_run_id": audit_context.parent_run_id,
                "resumed_from_run_id": audit_context.resumed_from_run_id,
                "source_type": audit_context.source_type,
                "source_metadata": audit_context.source_metadata,
            }
        handle = await executor.start({
            "task_id": task_id,
            "task": task,
            "label": label,
            "origin": origin,
            "origin_message_id": origin_message_id,
            "workspace": str(root),
            "restrict_to_workspace": (
                workspace_scope.restrict_to_workspace
                if workspace_scope is not None
                else self.restrict_to_workspace
            ),
            "runtime": {
                "model": runtime.model,
                "model_preset": runtime.model_preset,
                "context_window_tokens": runtime.context_window_tokens,
                "generation": {
                    "temperature": runtime.generation.temperature,
                    "max_tokens": runtime.generation.max_tokens,
                    "reasoning_effort": runtime.generation.reasoning_effort,
                },
            },
            "config": self._child_runtime_config,
            "audit_root": self._child_audit_root,
            "audit_context": raw_audit,
            "disabled_skills": sorted(self.disabled_skills),
            "max_iterations": self.max_iterations,
            "max_tool_result_chars": self.max_tool_result_chars,
            "fail_on_tool_error": self.fail_on_tool_error,
            "llm_timeout_s": llm_timeout,
            "child_depth": status.child_depth + 1,
        })
        self._executor_handles[task_id] = handle
        status.termination_evidence = {
            "backend": executor.backend,
            "executor_id": handle.identity.executor_id,
            "process_instance_id": handle.identity.process_instance_id,
            "force_kill_available": executor.force_kill_available,
        }
        if self._task_store.load(task_id) is not None:
            await self._task_store.update_runtime(
                task_id,
                executor={
                    "backend": executor.backend,
                    "executor_id": handle.identity.executor_id,
                    "process_instance_id": handle.identity.process_instance_id,
                    "supervisor_instance_id": handle.identity.supervisor_instance_id,
                    "pid": handle.identity.pid,
                    "pgid": handle.identity.pgid,
                },
            )
            await self._lifecycle_publisher.flush_task(task_id)
        if status.required and status.session_key and self._goal_orchestration is not None:
            await self._goal_orchestration.mark_executor(
                status.session_key,
                task_id,
                {
                    "backend": executor.backend,
                    "executor_id": handle.identity.executor_id,
                    "process_instance_id": handle.identity.process_instance_id,
                    "supervisor_instance_id": handle.identity.supervisor_instance_id,
                    "pid": handle.identity.pid,
                    "pgid": handle.identity.pgid,
                },
            )

        async def _consume_lifecycle() -> None:
            while True:
                envelope = await handle.lifecycle_queue.get()
                if envelope is None:
                    return
                if envelope.get("state") != "checkpoint":
                    continue
                await checkpoint_callback({
                    "phase": envelope.get("phase"),
                    "iteration": envelope.get("iteration"),
                })

        lifecycle_task = asyncio.create_task(_consume_lifecycle())
        try:
            exited = await executor.wait(handle)
            await lifecycle_task
        except BaseException:
            if not lifecycle_task.done():
                lifecycle_task.cancel()
            await asyncio.gather(lifecycle_task, return_exceptions=True)
            raise
        finally:
            self._executor_handles.pop(task_id, None)
        if (
            exited is not None
            and exited.result is None
            and status.termination_state == "force_kill_requested"
        ):
            outcome = self._termination_outcomes.setdefault(task_id, asyncio.Event())
            try:
                await asyncio.wait_for(outcome.wait(), timeout=1.0)
            except TimeoutError:
                status.termination_state = "termination_failed"
        if status.termination_state == "termination_failed":
            raise asyncio.CancelledError
        if exited is None or exited.result is None:
            if status.termination_state in {"cooperatively_exited", "force_killed"}:
                raise asyncio.CancelledError
            raise RuntimeError("child worker exited without a result envelope")
        raw = exited.result
        return AgentRunResult(
            final_content=raw.get("final_content") if isinstance(raw.get("final_content"), str) else None,
            messages=[],
            tools_used=list(raw.get("tools_used") or []),
            usage=dict(raw.get("usage") or {}),
            stop_reason=str(raw.get("stop_reason") or "error"),
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
            error_kind=raw.get("error_kind") if isinstance(raw.get("error_kind"), str) else None,
            tool_events=list(raw.get("tool_events") or []),
            had_injections=bool(raw.get("had_injections")),
        )

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

        durable_task = self._task_store.load(task_id)
        if durable_task is not None:
            await self._task_store.transition_status(task_id, SubagentTaskStatus.RUNNING)
            await self._lifecycle_publisher.flush_task(task_id)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)
            if self._task_store.load(task_id) is not None:
                phase = status.phase
                if phase not in {item.value for item in SubagentExecutionPhase}:
                    phase = SubagentExecutionPhase.INITIALIZING
                await self._task_store.update_runtime(
                    task_id,
                    phase=phase,
                    progress={"iteration": status.iteration},
                )
                await self._lifecycle_publisher.flush_task(task_id)

        terminal_status = "failed"
        terminal_error: str | None = None
        task_terminal_persisted = False
        goal_terminal_persisted = not required or not status.session_key
        runtime_usage: dict[str, Any] = {}

        async def _persist_terminal() -> None:
            nonlocal task_terminal_persisted, goal_terminal_persisted
            if not task_terminal_persisted and self._task_store.load(task_id) is not None:
                try:
                    await self._task_store.update_runtime(
                        task_id,
                        usage=runtime_usage or status.usage,
                    )
                    if status.termination_state != "none":
                        await self._task_store.record_termination(
                            task_id,
                            status.termination_state,
                            evidence=status.termination_evidence,
                        )
                    await self._task_store.transition_status(
                        task_id,
                        SubagentTaskStatus(terminal_status),
                        error=terminal_error,
                    )
                    await self._settle_budget(task_id, runtime_usage or status.usage)
                    await self._lifecycle_publisher.flush_task(task_id)
                except Exception as exc:
                    raise _SubagentTaskPersistenceError(
                        f"failed to persist terminal task {task_id}"
                    ) from exc
                task_terminal_persisted = True
            if (
                not goal_terminal_persisted
                and status.session_key
                and self._goal_orchestration is not None
            ):
                try:
                    if status.termination_state != "none":
                        await self._goal_orchestration.mark_termination(
                            status.session_key,
                            task_id,
                            status.termination_state,
                            evidence=status.termination_evidence,
                        )
                    await self._goal_orchestration.finish(
                        status.session_key,
                        task_id,
                        terminal_status,
                        terminal_error,
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist Goal obligation terminal state for subagent [{}]",
                        task_id,
                    )
                else:
                    goal_terminal_persisted = True

        try:
            if self._uses_process_executor():
                result = await self._run_process_child(
                    task_id=task_id,
                    task=task,
                    label=label,
                    origin=origin,
                    status=status,
                    runtime=runtime,
                    origin_message_id=origin_message_id,
                    workspace_scope=workspace_scope,
                    audit_context=audit_context,
                    checkpoint_callback=_on_checkpoint,
                )
            else:
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
                request_metadata["subagent_depth"] = status.child_depth + 1
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
            result_usage = dict(getattr(result, "usage", {}) or {})
            status.usage = result_usage
            runtime_usage.update(result_usage)

            if result.stop_reason == "completed":
                terminal_status = "succeeded"
                final_result = result.final_content or "Task completed successfully."
                logger.info("Subagent [{}] completed successfully", task_id)
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description, final_result, origin, "ok", origin_message_id
                )
            elif result.stop_reason == "tool_error":
                terminal_error = self._format_partial_progress(result)
                status.tool_events = list(result.tool_events)
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description,
                    self._format_partial_progress(result),
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "error":
                terminal_error = result.error or "subagent execution failed"
                if result.error_kind == "timeout":
                    terminal_status = "timed_out"
                    status.termination_state = "cooperatively_exited"
                    status.termination_evidence = {
                        "backend": "asyncio",
                        "exit_observed": True,
                    }
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description,
                    result.error or "Error: subagent execution failed.",
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "max_iterations":
                terminal_error = "Iteration budget exhausted before task completion."
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description, terminal_error, origin, "error", origin_message_id
                )
            elif result.stop_reason == "empty_final_response":
                terminal_error = "Subagent returned no final response; task completion is unverified."
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description, terminal_error, origin, "error", origin_message_id
                )
            elif result.stop_reason == "cancelled":
                terminal_status = (
                    "timed_out" if task_id in self._timeout_task_ids else "cancelled"
                )
                if status.termination_state not in {
                    "force_kill_requested", "force_killed", "termination_failed"
                }:
                    status.termination_state = "cooperatively_exited"
                    status.termination_evidence = {
                        **(status.termination_evidence or {}),
                        "exit_observed": True,
                        "reaped": True,
                    }
                terminal_error = (
                    "subagent task exceeded the required-join deadline"
                    if terminal_status == "timed_out"
                    else "subagent task was cancelled"
                )
                await _persist_terminal()
            else:
                terminal_error = (
                    f"Subagent stopped with non-success reason {result.stop_reason!r}; "
                    "task completion is unverified."
                )
                await _persist_terminal()
                await self._announce_result(
                    task_id, label, status.task_description, terminal_error, origin, "error", origin_message_id
                )
        except asyncio.CancelledError:
            if (
                task_id in self._termination_failed_ids
                or status.termination_state == "termination_failed"
            ):
                terminal_status = "lost"
            else:
                terminal_status = "timed_out" if task_id in self._timeout_task_ids else "cancelled"
                if status.termination_state not in {"force_killed", "termination_failed"}:
                    status.termination_state = "cooperatively_exited"
                    status.termination_evidence = {"backend": "asyncio", "exit_observed": True}
            terminal_error = (
                "subagent task exceeded the required-join deadline"
                if terminal_status == "timed_out"
                else (
                    "child termination could not be confirmed"
                    if terminal_status == "lost"
                    else "subagent task was cancelled"
                )
            )
            raise
        except _SubagentTaskPersistenceError as exc:
            status.phase = "error"
            status.error = str(exc)
            terminal_error = str(exc)
            logger.exception("Subagent [{}] durable terminal persistence failed", task_id)
        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            terminal_error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            await _persist_terminal()
            await self._announce_result(
                task_id,
                label,
                status.task_description,
                f"Error: {e}",
                origin,
                "error",
                origin_message_id,
            )
        finally:
            status.terminal_status = terminal_status
            if terminal_error:
                status.error = terminal_error
            try:
                await _persist_terminal()
            except Exception:
                logger.exception("Failed to persist terminal state for subagent [{}]", task_id)
            self._timeout_task_ids.discard(task_id)
            self._termination_failed_ids.discard(task_id)
            self._termination_outcomes.pop(task_id, None)

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

        if self._task_store.load(task_id) is not None:
            terminal = self._task_store.load(task_id)
            terminal_status = SubagentTaskStatus(
                terminal.status if terminal is not None else "failed"
            )
            await self._task_store.mark_result_ready(
                task_id,
                result=TaskResult.from_output(
                    result,
                    terminal_status,
                    error=result[:1000] if status != "ok" else None,
                ),
            )
            await self._lifecycle_publisher.flush_task(task_id)

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

    async def cancel_by_session(self, session_key: str, grace_seconds: float = 2.0) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        task_ids = [
            tid
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        for task_id in task_ids:
            await self._request_cancellation(task_id, grace_seconds=grace_seconds)
        process_task_ids = [task_id for task_id in task_ids if task_id in self._executor_handles]
        tasks = [self._running_tasks[tid] for tid in task_ids if tid not in self._executor_handles]
        for task in tasks:
            task.cancel()
        pending: set[asyncio.Task[None]] = set()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=max(0.0, grace_seconds))
        pending_process = await self._wait_or_force_process_tasks(
            process_task_ids,
            grace_seconds=grace_seconds,
        )
        for task_id in task_ids:
            status = self.get_status(task_id)
            if status is None:
                continue
            task = self._running_tasks.get(task_id)
            if task_id in pending_process or (task is not None and task in pending):
                await self._mark_termination_failed(task_id)
            else:
                if status.termination_state != "force_killed":
                    status.termination_state = "cooperatively_exited"
                    status.termination_evidence = {
                        **(status.termination_evidence or {"backend": "asyncio"}),
                        "exit_observed": True,
                        "reaped": True,
                    }
                status.terminal_status = "cancelled"
                status.error = status.error or "subagent task was cancelled"
                if status.required and self._goal_orchestration is not None:
                    termination_state = status.termination_state
                    await self._goal_orchestration.mark_termination(
                        session_key,
                        task_id,
                        termination_state,
                        evidence=status.termination_evidence,
                    )
                    await self._goal_orchestration.finish(
                        session_key, task_id, "cancelled", status.error
                    )
        self.clear_terminal_statuses_by_session(session_key)
        return len(task_ids)

    async def timeout_tasks(self, task_ids: list[str], grace_seconds: float = 2.0) -> bool:
        """Cancel selected children and report whether every task actually exited."""
        active = [
            self._running_tasks[task_id]
            for task_id in task_ids
            if (
                task_id in self._running_tasks
                and task_id not in self._executor_handles
                and not self._running_tasks[task_id].done()
            )
        ]
        process_task_ids = [task_id for task_id in task_ids if task_id in self._executor_handles]
        self._timeout_task_ids.update(task_ids)
        for task_id in task_ids:
            await self._request_cancellation(task_id, grace_seconds=grace_seconds)
        for task in active:
            task.cancel()
        pending: set[asyncio.Task[None]] = set()
        if active:
            _done, pending = await asyncio.wait(active, timeout=max(0.0, grace_seconds))
        pending_process = await self._wait_or_force_process_tasks(
            process_task_ids,
            grace_seconds=grace_seconds,
        )
        if pending:
            pending_ids = [
                task_id for task_id in task_ids
                if self._running_tasks.get(task_id) in pending
            ]
            for task_id in pending_ids:
                await self._mark_termination_failed(task_id)
        if pending_process:
            for task_id in pending_process:
                await self._mark_termination_failed(task_id)
        if pending or pending_process:
            return False
        for task_id in task_ids:
            status = self._task_statuses.get(task_id)
            if status is not None:
                if status.termination_state != "force_killed":
                    status.termination_state = "cooperatively_exited"
                    status.termination_evidence = {
                        **(status.termination_evidence or {"backend": "asyncio"}),
                        "exit_observed": True,
                        "reaped": True,
                    }
                if status.required and status.session_key and self._goal_orchestration is not None:
                    await self._goal_orchestration.mark_termination(
                        status.session_key,
                        task_id,
                        status.termination_state,
                        evidence=status.termination_evidence,
                    )
                    await self._goal_orchestration.finish(
                        status.session_key,
                        task_id,
                        "timed_out",
                        "subagent task exceeded the required-join deadline",
                    )
        return all(task.done() for task in active) and not pending_process

    async def _wait_or_force_process_tasks(
        self,
        task_ids: list[str],
        *,
        grace_seconds: float,
    ) -> set[str]:
        """Escalate only owned workers whose cooperative exit was not observed."""
        executor = self._child_executor
        if executor is None:
            return set(task_ids)
        pending: set[str] = set()
        for task_id in task_ids:
            handle = self._executor_handles.get(task_id)
            if handle is None:
                continue
            exited = await executor.wait(handle, grace_seconds)
            if exited is not None:
                continue
            status = self._task_statuses.get(task_id)
            if status is not None:
                status.termination_state = "force_kill_requested"
                status.termination_evidence = {
                    **(status.termination_evidence or {}),
                    "force_kill_requested": True,
                }
                if self._task_store.load(task_id) is not None:
                    await self._task_store.record_termination(
                        task_id,
                        "force_kill_requested",
                        evidence=status.termination_evidence,
                    )
                if status.required and status.session_key and self._goal_orchestration is not None:
                    await self._goal_orchestration.mark_termination(
                        status.session_key,
                        task_id,
                        "force_kill_requested",
                        evidence=status.termination_evidence,
                    )
            forced = await executor.force_kill(handle)
            if forced.termination_confirmed:
                if status is not None:
                    status.termination_state = "force_killed"
                    status.termination_evidence = {
                        **(status.termination_evidence or {}),
                        "exit_observed": forced.exit_observed,
                        "reaped": forced.reaped,
                        "descendants_cleared": forced.descendants_cleared,
                    }
                    if self._task_store.load(task_id) is not None:
                        await self._task_store.record_termination(
                            task_id,
                            "force_killed",
                            evidence=status.termination_evidence,
                        )
                    if status.required and status.session_key and self._goal_orchestration is not None:
                        await self._goal_orchestration.mark_termination(
                            status.session_key,
                            task_id,
                            "force_killed",
                            evidence=status.termination_evidence,
                        )
                    if outcome := self._termination_outcomes.get(task_id):
                        outcome.set()
                parent_task = self._running_tasks.get(task_id)
                if parent_task is not None:
                    await asyncio.wait({parent_task}, timeout=1.0)
            else:
                pending.add(task_id)
        return pending

    async def _request_cancellation(self, task_id: str, *, grace_seconds: float) -> None:
        status = self._task_statuses.get(task_id)
        if status is None or status.termination_state != "none":
            return
        status.termination_state = "cancel_requested"
        status.cancel_requested_at = time.monotonic()
        status.termination_evidence = {
            **(status.termination_evidence or {"backend": "asyncio"}),
            "request_sent": True,
        }
        if self._task_store.load(task_id) is not None:
            await self._task_store.record_termination(
                task_id,
                "cancel_requested",
                evidence=status.termination_evidence,
            )
            await self._task_store.record_termination(
                task_id,
                "grace_waiting",
                evidence=status.termination_evidence,
            )
        if status.required and status.session_key and self._goal_orchestration is not None:
            await self._goal_orchestration.mark_termination(
                status.session_key,
                task_id,
                "cancel_requested",
                evidence=status.termination_evidence,
                grace_seconds=grace_seconds,
            )
            await self._goal_orchestration.mark_termination(
                status.session_key,
                task_id,
                "grace_waiting",
                evidence=status.termination_evidence,
            )
        handle = self._executor_handles.get(task_id)
        if handle is not None and self._child_executor is not None:
            await self._child_executor.request_cancel(handle)

    async def _mark_termination_failed(self, task_id: str) -> None:
        self._termination_failed_ids.add(task_id)
        status = self._task_statuses.get(task_id)
        if status is None:
            return
        first_failure = status.termination_state != "termination_failed"
        status.termination_state = "termination_failed"
        status.termination_evidence = {
            **(status.termination_evidence or {"backend": "asyncio"}),
            "exit_observed": False,
            "force_kill_available": bool(
                self._child_executor is not None and self._child_executor.force_kill_available
            ),
        }
        status.terminal_status = "lost"
        status.error = "child termination could not be confirmed"
        if self._task_store.load(task_id) is not None:
            await self._task_store.record_termination(
                task_id,
                "termination_failed",
                evidence=status.termination_evidence,
            )
        if status.required and status.session_key and self._goal_orchestration is not None:
            await self._goal_orchestration.mark_termination(
                status.session_key,
                task_id,
                "termination_failed",
                evidence=status.termination_evidence,
            )
        if first_failure:
            await self._announce_result(
                task_id,
                status.label,
                status.task_description,
                status.error,
                {
                    "channel": status.origin_channel,
                    "chat_id": status.origin_chat_id,
                    "session_key": status.session_key,
                },
                "error",
                status.origin_message_id,
            )
        if outcome := self._termination_outcomes.get(task_id):
            outcome.set()

    async def close(self) -> None:
        """Cancel running subagents and close their shared exec sessions."""
        watchdogs = list(self._watchdog_tasks.values())
        for watchdog in watchdogs:
            watchdog.cancel()
        if watchdogs:
            await asyncio.gather(*watchdogs, return_exceptions=True)
        self._watchdog_tasks.clear()
        process_task_ids = list(self._executor_handles)
        await self._wait_or_force_process_tasks(process_task_ids, grace_seconds=0.1)
        tasks = [
            task for task_id, task in self._running_tasks.items()
            if task_id not in self._executor_handles and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._terminal_statuses.clear()
        if self._child_executor is not None:
            await self._child_executor.close()
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

    def has_running_required(self, owner_run_id: str) -> bool:
        return any(
            status.required
            and status.owner_run_id == owner_run_id
            and task_id in self._running_tasks
            and not self._running_tasks[task_id].done()
            for task_id, status in self._task_statuses.items()
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
            owner_run_id=status.owner_run_id,
            origin_channel=status.origin_channel,
            origin_chat_id=status.origin_chat_id,
            origin_message_id=status.origin_message_id,
            termination_state=status.termination_state,
            termination_evidence=status.termination_evidence,
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

    async def recover_runtime(self) -> int:
        """Fail closed durable tasks whose executor is absent after restart."""
        recovered = await self._task_store.recover_runtime(self.running_task_ids())
        await self._lifecycle_publisher.flush_pending()
        return recovered

    async def claim_result(self, task_id: str, owner_run_id: str) -> bool | None:
        if self._task_store.load(task_id) is None:
            return None
        _task, changed = await self._task_store.claim_result(task_id, owner_run_id)
        await self._lifecycle_publisher.flush_task(task_id)
        return changed

    async def mark_result_delivered(self, task_id: str) -> bool:
        if self._task_store.load(task_id) is None:
            return False
        await self._task_store.mark_delivered(task_id)
        await self._lifecycle_publisher.flush_task(task_id)
        return True

    async def mark_result_delivery_failed(self, task_id: str) -> bool:
        if self._task_store.load(task_id) is None:
            return False
        await self._task_store.mark_delivery_failed(task_id)
        await self._lifecycle_publisher.flush_task(task_id)
        return True

    async def wait_for(self, task_ids: list[str], timeout: float) -> None:
        tasks = [self._running_tasks[task_id] for task_id in task_ids if task_id in self._running_tasks]
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, timeout))
