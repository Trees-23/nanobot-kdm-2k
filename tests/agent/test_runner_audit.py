from unittest.mock import AsyncMock, MagicMock

from agent.runner_helpers import make_run_spec
from nanobot.agent.runner import AgentRunner
from nanobot.audit.context import AuditRunContext
from nanobot.providers.base import LLMProvider, LLMResponse


class RecordingEmitter:
    def __init__(self) -> None:
        self.events = []
        self.payloads = []

    @property
    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events]

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        self.payloads.append(payload)
        return MagicMock(committed=critical)


async def test_runner_emits_run_iteration_and_model_events() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", usage={}))
    tools = MagicMock()
    tools.get_definitions.return_value = []
    emitter = RecordingEmitter()
    context = AuditRunContext(trace_id="trace", turn_id="turn", run_id="run")

    result = await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[{"role": "user", "content": "hello"}],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=10_000,
            session_key="cli:direct",
            audit_context=context,
        )
    )

    assert result.stop_reason == "completed"
    assert emitter.event_types == [
        "run_started",
        "iteration_started",
        "model_request_started",
        "model_response_received",
        "iteration_finished",
        "run_finished",
    ]
    model_events = [event for event in emitter.events if event.model_call_id]
    assert len({event.model_call_id for event in model_events}) == 1
    assert all(event.trace_id == "trace" for event in emitter.events)


async def test_runner_model_error_emits_logical_failure_and_failed_run() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="provider unavailable",
            finish_reason="error",
            error_kind="timeout",
        )
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    emitter = RecordingEmitter()

    await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    assert "model_request_failed" in emitter.event_types
    terminal = emitter.events[-1]
    assert terminal.event_type == "run_finished"
    assert terminal.status == "failed"
