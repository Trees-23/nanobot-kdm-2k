"""Internal AgentRunner hook that emits typed audit drafts."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    ModelRequestSnapshot,
    RuntimeDecision,
)
from nanobot.audit.context import AuditRunContext, clear_run_cause, run_cause
from nanobot.audit.ids import new_audit_id
from nanobot.audit.schema import (
    ContinuationRequestedDraft,
    FinalizationRequestedDraft,
    IterationFinishedDraft,
    IterationStartedDraft,
    ModelAttemptFinishedDraft,
    ModelAttemptStartedDraft,
    ModelFirstOutputDraft,
    ModelRequestFailedDraft,
    ModelRequestPayloadDraft,
    ModelRequestStartedDraft,
    ModelResponsePayloadDraft,
    ModelResponseReceivedDraft,
    PolicyBlockedDraft,
    ProviderRouteDecisionDraft,
    ReasoningSummaryPayloadDraft,
    ReasoningSummaryReceivedDraft,
    RetryScheduledDraft,
    RunConfigPayloadDraft,
    RunFinishedDraft,
    RunStartedDraft,
    ToolFinishedDraft,
    ToolInputPayloadDraft,
    ToolOutputPayloadDraft,
    ToolStartedDraft,
)
from nanobot.audit.side_effects import (
    SideEffectSnapshot,
    capture_side_effect_after,
    capture_side_effect_before,
)
from nanobot.providers.base import LLMResponse
from nanobot.providers.observed_call import (
    ProviderAttemptResult,
    ProviderAttemptSnapshot,
    ProviderRetryDecision,
    ProviderRouteDecision,
)
from nanobot.utils.llm_runtime import LLMRuntime


class RunnerAuditHook(AgentHook):
    def __init__(
        self,
        emitter: Any,
        run: AuditRunContext,
        *,
        runtime: LLMRuntime,
        session_key: str | None,
    ) -> None:
        super().__init__()
        self._emitter = emitter
        self._run = run
        self._runtime = runtime
        self._session_key = session_key
        self._iteration_started: set[int] = set()
        self._model_started_ns: dict[str, int] = {}
        self._first_output: set[tuple[str, str]] = set()
        self._current_model_call_id: str | None = None
        self._run_finished = False
        self._tool_ids: dict[str, str] = {}
        self._tool_started_ns: dict[str, int] = {}
        self._tool_params: dict[str, Any] = {}
        self._tool_side_effects: dict[str, SideEffectSnapshot] = {}
        self._attempt_counts: dict[str, int] = {}

    def _common(
        self,
        event_type: str,
        *,
        iteration: int | None = None,
        model_call_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event_id": new_audit_id(),
            "event_type": event_type,
            "occurred_at": datetime.now(UTC),
            "monotonic_ns": time.monotonic_ns(),
            "trace_id": self._run.trace_id,
            "turn_id": self._run.turn_id,
            "run_id": self._run.run_id,
            "parent_run_id": self._run.parent_run_id,
            "resumed_from_run_id": self._run.resumed_from_run_id,
            "caused_by_event_id": run_cause(self._run.run_id),
            "model_call_id": model_call_id,
            "attempt_id": None,
            "tool_call_id": None,
            "checkpoint_id": None,
            "goal_id": None,
            "delivery_id": None,
            "session_key": self._session_key,
            "source_type": self._run.source_type,
            "source_metadata": {},
            "iteration": iteration,
        }

    async def before_run(self, context: AgentRunHookContext) -> None:
        event = RunStartedDraft.model_validate(self._common("run_started"))
        generation = self._runtime.generation
        payload = RunConfigPayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "provider": type(self._runtime.provider).__name__,
                "model": self._runtime.model,
                "generation_settings": {
                    "temperature": generation.temperature,
                    "max_tokens": generation.max_tokens,
                    "reasoning_effort": generation.reasoning_effort,
                },
                "context_limits": {
                    "context_window_tokens": self._runtime.context_window_tokens,
                },
                "goal_snapshot": None,
            },
        )
        await self._emitter.emit(event, payload=payload, critical=True)

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._iteration_started.add(context.iteration)
        event = IterationStartedDraft.model_validate(
            self._common("iteration_started", iteration=context.iteration)
        )
        await self._emitter.emit(event)

    async def before_model_request(
        self,
        context: AgentHookContext,
        request: ModelRequestSnapshot,
    ) -> None:
        self._current_model_call_id = request.model_call_id
        self._model_started_ns[request.model_call_id] = time.monotonic_ns()
        context.provider_attempt_observer = RunnerProviderAttemptObserver(
            self,
            model_call_id=request.model_call_id,
            iteration=context.iteration,
        )
        event = ModelRequestStartedDraft.model_validate(
            {
                **self._common(
                    "model_request_started",
                    iteration=context.iteration,
                    model_call_id=request.model_call_id,
                ),
                "requested_provider": type(request.runtime.provider).__name__,
                "requested_model": request.runtime.model,
            }
        )
        generation = request.runtime.generation
        payload = ModelRequestPayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "messages": request.messages,
                "tool_schemas": request.tools,
                "generation_settings": {
                    "temperature": generation.temperature,
                    "max_tokens": generation.max_tokens,
                    "reasoning_effort": generation.reasoning_effort,
                },
                "system_prompt_hash": "",
                "context_governance_actions": [],
            },
        )
        await self._emitter.emit(event, payload=payload)

    async def after_model_response(
        self,
        context: AgentHookContext,
        response: LLMResponse,
    ) -> None:
        model_call_id = context.model_call_id or self._current_model_call_id
        if model_call_id is None:
            return
        if response.finish_reason == "error":
            status = "timeout" if response.error_kind == "timeout" else "error"
            event = ModelRequestFailedDraft.model_validate(
                {
                    **self._common(
                        "model_request_failed",
                        iteration=context.iteration,
                        model_call_id=model_call_id,
                    ),
                    "status": status,
                    "error_kind": response.error_kind or response.error_type or "provider_error",
                    "attempt_count": self._attempt_counts.get(model_call_id, 0),
                }
            )
            await self._emitter.emit(event)
            return
        event = ModelResponseReceivedDraft.model_validate(
            {
                **self._common(
                    "model_response_received",
                    iteration=context.iteration,
                    model_call_id=model_call_id,
                ),
                "finish_reason": response.finish_reason or "unknown",
                "usage": {key: int(value) for key, value in (response.usage or {}).items()},
            }
        )
        payload = ModelResponsePayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "content": response.content,
                "tool_calls": [call.to_openai_tool_call() for call in response.tool_calls],
                "finish_reason": response.finish_reason,
                "usage": {key: int(value) for key, value in (response.usage or {}).items()},
                "provider_metadata": {},
            },
        )
        await self._emitter.emit(event, payload=payload)

    async def on_model_request_error(
        self,
        context: AgentHookContext,
        error: BaseException,
    ) -> None:
        model_call_id = context.model_call_id or self._current_model_call_id
        if model_call_id is None:
            return
        if isinstance(error, asyncio.CancelledError):
            status = "cancelled"
        elif isinstance(error, TimeoutError):
            status = "timeout"
        else:
            status = "error"
        event = ModelRequestFailedDraft.model_validate(
            {
                **self._common(
                    "model_request_failed",
                    iteration=context.iteration,
                    model_call_id=model_call_id,
                ),
                "status": status,
                "error_kind": type(error).__name__,
                "attempt_count": self._attempt_counts.get(model_call_id, 0),
            }
        )
        await self._emitter.emit(event)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        if delta:
            await self._emit_first_output(context, "content")

    async def _emit_first_output(self, context: AgentHookContext, output_kind: str) -> None:
        model_call_id = context.model_call_id or self._current_model_call_id
        if model_call_id is None or (model_call_id, output_kind) in self._first_output:
            return
        self._first_output.add((model_call_id, output_kind))
        started = self._model_started_ns.get(model_call_id, time.monotonic_ns())
        event = ModelFirstOutputDraft.model_validate(
            {
                **self._common(
                    "model_first_output",
                    iteration=context.iteration,
                    model_call_id=model_call_id,
                ),
                "output_kind": output_kind,
                "elapsed_ms": max(0, (time.monotonic_ns() - started) // 1_000_000),
            }
        )
        await self._emitter.emit(event)

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        if not reasoning_content or self._current_model_call_id is None:
            return
        event = ReasoningSummaryReceivedDraft.model_validate(
            {
                **self._common(
                    "reasoning_summary_received",
                    model_call_id=self._current_model_call_id,
                ),
                "reasoning_source": "public_summary",
            }
        )
        payload = ReasoningSummaryPayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "content": reasoning_content,
                "reasoning_source": "public_summary",
                "streamed": True,
            },
        )
        await self._emitter.emit(event, payload=payload)

    async def after_iteration(self, context: AgentHookContext) -> None:
        if context.iteration not in self._iteration_started:
            return
        self._iteration_started.remove(context.iteration)
        if context.stop_reason == "completed":
            outcome = "completed"
        elif context.stop_reason in {"error", "tool_error"} or context.error:
            outcome = "failed"
        else:
            outcome = "continued"
        event = IterationFinishedDraft.model_validate(
            {
                **self._common("iteration_finished", iteration=context.iteration),
                "iteration_outcome": outcome,
            }
        )
        await self._emitter.emit(event)

    async def on_runtime_decision(
        self,
        context: AgentHookContext,
        decision: RuntimeDecision,
    ) -> None:
        if decision.decision_type == "continuation_requested":
            model_call_id = context.model_call_id or self._current_model_call_id
            if model_call_id is None:
                return
            event = ContinuationRequestedDraft.model_validate(
                {
                    **self._common(
                        "continuation_requested",
                        iteration=context.iteration,
                        model_call_id=model_call_id,
                    ),
                    **decision.fields,
                }
            )
            await self._emitter.emit(event)
        elif decision.decision_type == "finalization_requested":
            event = FinalizationRequestedDraft.model_validate(
                {
                    **self._common(
                        "finalization_requested", iteration=context.iteration
                    ),
                    **decision.fields,
                }
            )
            await self._emitter.emit(event)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            return repr(value)
        return value

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
    ) -> None:
        if tool_call.id in self._tool_ids:
            return
        audit_tool_id = new_audit_id()
        self._tool_ids[tool_call.id] = audit_tool_id
        self._tool_started_ns[tool_call.id] = time.monotonic_ns()
        self._tool_params[tool_call.id] = params
        workspace = getattr(tool, "_workspace", None)
        self._tool_side_effects[tool_call.id] = capture_side_effect_before(
            call_id=tool_call.id,
            tool_name=tool_call.name,
            tool=tool,
            params=params,
            workspace=Path(workspace) if workspace is not None else None,
        )
        event = ToolStartedDraft.model_validate(
            {
                **self._common("tool_started", iteration=context.iteration),
                "tool_call_id": audit_tool_id,
                "tool_name": tool_call.name,
            }
        )
        payload = ToolInputPayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "tool_name": tool_call.name,
                "arguments": (
                    self._json_safe(params)
                    if isinstance(params, dict)
                    else {"value": self._json_safe(params)}
                ),
                "tool_schema_hash": "",
            },
        )
        await self._emitter.emit(event, payload=payload)

    async def after_execute_tool_terminal(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        outcome: Any,
    ) -> None:
        if tool_call.id not in self._tool_ids:
            await self.before_execute_tool(context, tool_call, tool, params)
        audit_tool_id = self._tool_ids.pop(tool_call.id)
        started = self._tool_started_ns.pop(tool_call.id, time.monotonic_ns())
        self._tool_params.pop(tool_call.id, None)
        side_effect_snapshot = self._tool_side_effects.pop(tool_call.id, None)
        status = outcome.status
        if status == "error" and outcome.error_kind in {"TimeoutError", "Timeout"}:
            status = "timeout"
        if status == "blocked":
            policy = PolicyBlockedDraft.model_validate(
                {
                    **self._common("policy_blocked", iteration=context.iteration),
                    "tool_call_id": audit_tool_id,
                    "policy_name": outcome.error_kind or "tool_boundary",
                    "policy_version": "v1",
                    "threshold": 1,
                    "observed_count": 1,
                }
            )
            await self._emitter.emit(policy)
        event = ToolFinishedDraft.model_validate(
            {
                **self._common("tool_finished", iteration=context.iteration),
                "tool_call_id": audit_tool_id,
                "tool_name": tool_call.name,
                "elapsed_ms": max(0, (time.monotonic_ns() - started) // 1_000_000),
                "status": status,
            }
        )
        payload = ToolOutputPayloadDraft(
            payload_id=new_audit_id(),
            event_id=event.event_id,
            content={
                "tool_name": tool_call.name,
                "result": self._json_safe(outcome.result),
                "normalized_error": (
                    {"kind": outcome.error_kind} if outcome.error_kind else None
                ),
                "side_effects": (
                    capture_side_effect_after(side_effect_snapshot, outcome.result)
                    if side_effect_snapshot is not None
                    else []
                ),
            },
        )
        await self._emitter.emit(event, payload=payload, critical=True)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        if self._run_finished:
            return
        for iteration in sorted(self._iteration_started):
            outcome = "cancelled" if context.stop_reason == "cancelled" else "failed"
            event = IterationFinishedDraft.model_validate(
                {
                    **self._common("iteration_finished", iteration=iteration),
                    "iteration_outcome": outcome,
                }
            )
            await self._emitter.emit(event)
        self._iteration_started.clear()
        if context.stop_reason == "cancelled":
            status, stop_reason = "cancelled", "system_cancel"
        elif context.stop_reason == "max_iterations":
            status, stop_reason = "exhausted", "max_iterations"
        elif context.error or context.stop_reason in {"error", "tool_error"}:
            status, stop_reason = "failed", "model_error"
        else:
            status, stop_reason = "succeeded", "completed"
        event = RunFinishedDraft.model_validate(
            {
                **self._common("run_finished"),
                "status": status,
                "stop_reason": stop_reason,
            }
        )
        await self._emitter.emit(event, critical=True)
        clear_run_cause(self._run.run_id)
        self._run_finished = True


class RunnerProviderAttemptObserver:
    def __init__(
        self,
        hook: RunnerAuditHook,
        *,
        model_call_id: str,
        iteration: int,
    ) -> None:
        self._hook = hook
        self._model_call_id = model_call_id
        self._iteration = iteration
        self._last_attempt_id: str | None = None

    async def attempt_started(self, snapshot: ProviderAttemptSnapshot) -> None:
        self._last_attempt_id = snapshot.attempt_id
        self._hook._attempt_counts[self._model_call_id] = (
            self._hook._attempt_counts.get(self._model_call_id, 0) + 1
        )
        event = ModelAttemptStartedDraft.model_validate(
            {
                **self._hook._common(
                    "model_attempt_started",
                    iteration=self._iteration,
                    model_call_id=self._model_call_id,
                ),
                "attempt_id": snapshot.attempt_id,
                "attempt_ordinal": snapshot.attempt_ordinal,
                "provider": snapshot.provider,
                "model": snapshot.model,
                "input_variant": snapshot.input_variant,
            }
        )
        await self._hook._emitter.emit(event)

    async def attempt_finished(self, snapshot: ProviderAttemptResult) -> None:
        event = ModelAttemptFinishedDraft.model_validate(
            {
                **self._hook._common(
                    "model_attempt_finished",
                    iteration=self._iteration,
                    model_call_id=self._model_call_id,
                ),
                "attempt_id": snapshot.attempt_id,
                "attempt_ordinal": snapshot.attempt_ordinal,
                "provider": snapshot.provider,
                "model": snapshot.model,
                "elapsed_ms": snapshot.elapsed_ms,
                "status": snapshot.status,
            }
        )
        await self._hook._emitter.emit(event)

    async def route_decision(self, decision: ProviderRouteDecision) -> None:
        event = ProviderRouteDecisionDraft.model_validate(
            {
                **self._hook._common(
                    "provider_route_decision",
                    iteration=self._iteration,
                    model_call_id=self._model_call_id,
                ),
                "route_action": decision.action,
                "provider": decision.provider,
                "model": decision.model,
                "input_variant": decision.input_variant,
            }
        )
        await self._hook._emitter.emit(event)

    async def retry_scheduled(self, retry: ProviderRetryDecision) -> None:
        prior_attempt_id = retry.prior_attempt_id or self._last_attempt_id
        if prior_attempt_id is None:
            return
        event = RetryScheduledDraft.model_validate(
            {
                **self._hook._common(
                    "retry_scheduled",
                    iteration=self._iteration,
                    model_call_id=self._model_call_id,
                ),
                "prior_attempt_id": prior_attempt_id,
                "delay_ms": retry.delay_ms,
                "policy_name": retry.policy_name,
            }
        )
        await self._hook._emitter.emit(event)
