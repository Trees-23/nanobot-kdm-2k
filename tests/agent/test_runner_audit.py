import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.await_subagents import AwaitSubagentsTool
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.audit.context import AuditRunContext, set_run_cause
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.session.goal_orchestration import GoalOrchestrationStore
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager
from tests.agent.runner_helpers import make_run_spec
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


async def test_list_dir_error_keeps_safe_diagnostics_without_payload_dependency(
    tmp_path,
) -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="missing-directory",
                        name="list_dir",
                        arguments={"path": "missing"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="continued"),
        ]
    )
    registry = ToolRegistry()
    registry.register(
        ListDirTool(
            workspace=tmp_path,
            allowed_dir=tmp_path,
            restrict_to_workspace=True,
        )
    )
    emitter = RecordingEmitter()

    await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=registry,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    terminal = next(event for event in emitter.events if event.event_type == "tool_finished")
    assert terminal.status == "error"
    assert terminal.error_message == "Error: Directory not found: missing"
    assert terminal.error_summary == "Directory not found: missing"
    assert terminal.error_type == "ToolError"
    assert terminal.error_code == "tool_error"
    assert terminal.error_source == "tool_result"
    assert terminal.retryability == "unknown"


async def test_prepare_validation_error_uses_complete_failure_contract() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="invalid-list-dir",
                        name="list_dir",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="continued"),
        ]
    )
    registry = ToolRegistry()
    registry.register(ListDirTool())
    emitter = RecordingEmitter()

    await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=registry,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=10_000,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    terminal = next(event for event in emitter.events if event.event_type == "tool_finished")
    assert terminal.error_message
    assert terminal.error_summary
    assert terminal.error_type == "ValidationError"
    assert terminal.error_code == "invalid_tool_arguments"
    assert terminal.error_source == "validation"
    assert terminal.retryability == "non_retryable"


async def test_fatal_tool_error_keeps_tool_domain_and_precise_event_link() -> None:
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(id="provider-call", name="web_search", arguments={})
            ],
            finish_reason="tool_calls",
        )
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {}, None)
    tools.execute = AsyncMock(
        return_value=ToolResult.error(
            "Error: DuckDuckGo search timed out after 30s",
            error_type="TimeoutError",
            error_code="web_search_timeout",
            effective_timeout_ms=30_000,
            provider="duckduckgo",
        )
    )
    emitter = RecordingEmitter()

    result = await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=10_000,
            fail_on_tool_error=True,
            audit_context=AuditRunContext("trace", "turn", "run"),
        )
    )

    tool_finished = next(
        event for event in emitter.events if event.event_type == "tool_finished"
    )
    run_finished = emitter.events[-1]
    assert result.stop_reason == "tool_error"
    assert tool_finished.status == "timeout"
    assert tool_finished.error_type == "TimeoutError"
    assert tool_finished.error_code == "web_search_timeout"
    assert tool_finished.error_message == "Error: DuckDuckGo search timed out after 30s"
    assert tool_finished.error_summary == "DuckDuckGo search timed out after 30s"
    assert tool_finished.error_source == "timeout"
    assert tool_finished.retryability == "retryable"
    assert tool_finished.effective_timeout_ms == 30_000
    assert tool_finished.provider == "duckduckgo"
    assert run_finished.stop_reason == "tool_error"
    assert run_finished.fatal_event_id == tool_finished.event_id
    assert run_finished.failure_policy == "fail_on_tool_error"
    assert run_finished.fail_on_tool_error is True


async def test_read_file_corrections_emit_explicit_recovery_link(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    config_root = tmp_path / "home" / ".nanobot"
    workspace.mkdir()
    config_root.mkdir(parents=True)
    (config_root / "config.json").write_text('{"ok": true}', encoding="utf-8")
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="bad-absolute",
                    name="read_file",
                    arguments={"path": str(config_root / "runtime" / "config.json")},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="bad-relative",
                    name="read_file",
                    arguments={"path": "config.json"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="correct-absolute",
                    name="read_file",
                    arguments={"path": str(config_root / "config.json")},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="recovered"),
        ]
    )
    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=workspace, restrict_to_workspace=False))
    emitter = RecordingEmitter()

    result = await AgentRunner(audit_emitter=emitter).run(
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

    terminals = [
        event for event in emitter.events if event.event_type == "tool_finished"
    ]
    assert result.stop_reason == "completed"
    assert [event.status for event in terminals] == ["error", "error", "ok"]
    assert terminals[0].error_code == "file_not_found"
    assert terminals[0].safe_input_summary == "path=<outside-workspace>"
    assert terminals[1].safe_input_summary == "path=config.json"
    assert terminals[2].recovery_of_tool_call_ids == [terminals[0].tool_call_id]
    assert terminals[1].tool_call_id not in terminals[2].recovery_of_tool_call_ids


async def test_spawn_concurrency_rejection_is_audited_error_without_child_run() -> None:
    class AtCapacityManager:
        max_concurrent_subagents = 1

        def __init__(self) -> None:
            self.spawn = AsyncMock()

        def get_running_count(self) -> int:
            return 1

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="provider-spawn", name="spawn", arguments={"task": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="capacity rejection handled"),
        ]
    )
    manager = AtCapacityManager()
    tools = ToolRegistry()
    tools.register(SpawnTool(manager))  # type: ignore[arg-type]
    emitter = RecordingEmitter()
    spec = make_run_spec(
        provider,
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=10_000,
        session_key="test:c1",
        audit_context=AuditRunContext("trace", "turn", "main-run"),
    )

    with request_context(
        RequestContext(
            channel="test",
            chat_id="c1",
            session_key="test:c1",
            runtime=spec.runtime,
            metadata={
                "_audit_context": {
                    "trace_id": "trace",
                    "turn_id": "turn",
                    "run_id": "main-run",
                }
            },
        )
    ):
        result = await AgentRunner(audit_emitter=emitter).run(spec)

    terminal = next(
        event
        for event in emitter.events
        if event.event_type == "tool_finished" and event.tool_name == "spawn"
    )
    assert terminal.status == "error"
    assert manager.spawn.await_count == 0
    assert len([event for event in emitter.events if event.event_type == "run_started"]) == 1
    assert result.stop_reason == "completed"


async def test_successful_spawn_output_binds_audit_call_task_and_child_ids() -> None:
    class RecordingManager:
        max_concurrent_subagents = 3

        def __init__(self) -> None:
            self.kwargs = None

        def get_running_count(self) -> int:
            return 0

        async def spawn(self, **kwargs):
            self.kwargs = kwargs
            return {
                "started": True,
                "task_id": "task-a",
                "required": True,
                "task_group": "research",
                "child_run_id": "child-a",
            }

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="provider-spawn",
                        name="spawn",
                        arguments={"task": "x", "required": True, "task_group": "research"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="spawned"),
        ]
    )
    manager = RecordingManager()
    tools = ToolRegistry()
    tools.register(SpawnTool(manager))  # type: ignore[arg-type]
    emitter = RecordingEmitter()
    spec = make_run_spec(
        provider,
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=10_000,
        session_key="test:c1",
        audit_context=AuditRunContext("trace", "turn", "main-run"),
    )
    with request_context(
        RequestContext(
            channel="test",
            chat_id="c1",
            session_key="test:c1",
            runtime=spec.runtime,
            metadata={
                "_audit_context": {
                    "trace_id": "trace",
                    "turn_id": "turn",
                    "run_id": "main-run",
                }
            },
        )
    ):
        result = await AgentRunner(audit_emitter=emitter).run(spec)

    terminal = next(
        event
        for event in emitter.events
        if event.event_type == "tool_finished" and event.tool_name == "spawn"
    )
    output = next(message for message in result.messages if message.get("name") == "spawn")
    assert manager.kwargs["spawn_tool_call_id"] == terminal.tool_call_id
    assert json.loads(output["content"])["task_id"] == "task-a"
    assert json.loads(output["content"])["child_run_id"] == "child-a"


async def test_failed_await_subagents_is_nonfatal_and_structured_for_recovery(tmp_path) -> None:
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("test:c1")
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "deliver"}
    sessions.save(session)
    store = GoalOrchestrationStore(sessions)
    await store.register(
        "test:c1",
        task_id="failed-a",
        label="failed-a",
        group="research",
        child_run_id="child-a",
        spawn_tool_call_id="spawn-a",
    )
    await store.finish("test:c1", "failed-a", "failed", "controlled failure")
    manager = MagicMock()
    manager.wait_for = AsyncMock()
    manager.running_task_ids.return_value = set()
    tools = ToolRegistry()
    tools.register(AwaitSubagentsTool(manager, store))
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="provider-await",
                        name="await_subagents",
                        arguments={"task_group": "research", "timeout_seconds": 0},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="I will replace the failed task or block the Goal."),
        ]
    )
    emitter = RecordingEmitter()
    spec = make_run_spec(
        provider,
        initial_messages=[],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=10_000,
        session_key="test:c1",
        audit_context=AuditRunContext("trace", "turn", "main-run"),
    )

    with request_context(
        RequestContext(
            channel="test", chat_id="c1", session_key="test:c1", runtime=spec.runtime
        )
    ):
        result = await AgentRunner(audit_emitter=emitter).run(spec)

    output = next(
        message for message in result.messages if message.get("name") == "await_subagents"
    )
    payload = json.loads(output["content"])
    assert payload["barrier_satisfied"] is False
    assert payload["tasks"]["failed-a"]["status"] == "failed"
    assert result.stop_reason == "completed"
    assert "replace" in result.final_content
    terminal = next(
        event
        for event in emitter.events
        if event.event_type == "tool_finished" and event.tool_name == "await_subagents"
    )
    assert terminal.status == "error"


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
    set_run_cause("run", "cancel-event")
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
    assert tool_terminal.caused_by_event_id == "cancel-event"
    assert emitter.events[-1].event_type == "run_finished"
    assert emitter.events[-1].status == "cancelled"
    assert emitter.events[-1].caused_by_event_id == "cancel-event"


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
