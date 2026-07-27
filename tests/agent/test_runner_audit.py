import asyncio
from unittest.mock import AsyncMock, MagicMock

from agent.runner_helpers import make_run_spec
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.base import ToolResult
from nanobot.audit.context import AuditRunContext
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from tests.providers.test_provider_retry import ScriptedProvider


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


async def test_runner_emits_exactly_one_tool_terminal_for_success() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="provider-call", name="read_file", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done"),
        ]
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {}, None)
    tools.execute = AsyncMock(return_value="contents")
    emitter = RecordingEmitter()

    await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    tool_events = [event for event in emitter.events if event.event_type.startswith("tool_")]
    assert [event.event_type for event in tool_events] == ["tool_started", "tool_finished"]
    assert tool_events[-1].status == "ok"
    assert tool_events[0].tool_call_id == tool_events[1].tool_call_id


async def test_runner_emits_error_terminal_for_returned_tool_error() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="provider-call", name="read_file", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="recovered"),
        ]
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {}, None)
    tools.execute = AsyncMock(return_value=ToolResult.error("Error: missing file"))
    emitter = RecordingEmitter()

    result = await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    terminal = next(event for event in emitter.events if event.event_type == "tool_finished")
    assert terminal.status == "error"
    assert result.stop_reason == "completed"
    assert emitter.events[-1].status == "succeeded"


async def test_runner_cancellation_closes_tool_and_run_spans() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="provider-call", name="wait", arguments={})],
            finish_reason="tool_calls",
        )
    )
    started = asyncio.Event()

    class WaitTool:
        async def execute(self, **_params):
            started.set()
            await asyncio.Event().wait()

    tool = WaitTool()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (tool, {}, None)
    emitter = RecordingEmitter()
    task = asyncio.create_task(
        AgentRunner(audit_emitter=emitter).run(
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
    )
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("runner cancellation was swallowed")

    tool_terminal = next(
        event for event in emitter.events if event.event_type == "tool_finished"
    )
    assert tool_terminal.status == "cancelled"
    assert emitter.events[-1].event_type == "run_finished"
    assert emitter.events[-1].status == "cancelled"


async def test_repeated_lookup_emits_blocked_terminal_and_policy() -> None:
    provider = MagicMock(spec=LLMProvider)
    responses = [
        LLMResponse(
            content="working",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{index}",
                    name="web_fetch",
                    arguments={"url": "https://example.com"},
                )
            ],
        )
        for index in range(3)
    ]
    responses.append(LLMResponse(content="done"))
    provider.chat_with_retry = AsyncMock(side_effect=responses)
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {"url": "https://example.com"}, None)
    tools.execute = AsyncMock(return_value="page")
    emitter = RecordingEmitter()

    await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=4,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    terminals = [event for event in emitter.events if event.event_type == "tool_finished"]
    assert [event.status for event in terminals] == ["ok", "ok", "blocked"]
    policies = [event for event in emitter.events if event.event_type == "policy_blocked"]
    assert len(policies) == 1
    assert policies[0].tool_call_id == terminals[-1].tool_call_id


async def test_runner_links_real_provider_attempts_to_model_call(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="timeout", finish_reason="error", error_kind="timeout"),
            LLMResponse(content="done"),
        ]
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    emitter = RecordingEmitter()

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", no_sleep)
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

    attempts = [
        event for event in emitter.events if event.event_type == "model_attempt_started"
    ]
    assert len(attempts) == 2
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert attempts[0].model_call_id == attempts[1].model_call_id
    assert "retry_scheduled" in emitter.event_types
