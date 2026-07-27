"""Vertical acceptance tests for production audit boundaries and read models."""

from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.base import ToolResult
from nanobot.audit.context import AuditRunContext
from nanobot.audit.index import AuditIndexer
from nanobot.audit.query import AuditQuery, TraceFilter
from nanobot.audit.runtime import AuditRuntime
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AuditConfig
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest
from tests.agent.runner_helpers import make_run_spec


def _runtime(root) -> AuditRuntime:
    return AuditRuntime.from_config(
        AuditConfig(fsync_interval_seconds=0.01, fsync_record_interval=1),
        root=root,
    )


async def test_sdk_turn_round_trips_through_writer_index_and_trace_view(tmp_path) -> None:
    root = tmp_path / "audit"
    runtime = _runtime(root)
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_with_retry = AsyncMock(
        return_value=LLMResponse(content="durable answer", usage={})
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path / "workspace",
        model="test-model",
        audit_runtime=runtime,
    )
    loop._connect_mcp = AsyncMock()
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)

    outbound = await loop.process_direct("retain this input")
    await runtime.close()
    update = AuditIndexer(root).update()
    query = AuditQuery.from_root(root, use_index=True)
    page = query.find_traces(TraceFilter(source_type="sdk", limit=10))
    view = query.load_trace(page.trace_ids[0], include_payloads=True)

    assert outbound is not None
    assert update.coverage_complete is True
    assert page.trace_ids == [view.trace_id]
    assert view.integrity.status == "valid"
    assert view.summary.turn_count == 1
    assert view.summary.run_count == 1
    assert view.summary.terminal_run_statuses == ["succeeded"]
    event_types = [event.event_type for event in view.timeline]
    for expected in (
        "trace_created",
        "turn_started",
        "run_started",
        "run_finished",
        "turn_response_prepared",
        "turn_finished",
    ):
        assert event_types.count(expected) == 1
    assert event_types.index("run_started") < event_types.index("run_finished")
    assert event_types.index("turn_started") < event_types.index("turn_finished")
    assert any(
        payload.payload_kind == "turn_input"
        and payload.content.content == "retain this input"
        for payload in (view.payloads or {}).values()
    )


async def test_tool_error_recovery_round_trips_as_one_causal_run(tmp_path) -> None:
    root = tmp_path / "audit"
    runtime = _runtime(root)
    await runtime.start()
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="provider-call", name="read_file", arguments={})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="recovered"),
        ]
    )
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.prepare_call.return_value = (None, {}, None)
    tools.execute = AsyncMock(return_value=ToolResult.error("Error: missing file"))

    result = await AgentRunner(audit_emitter=runtime.emitter).run(
        make_run_spec(
            provider,
            initial_messages=[{"role": "user", "content": "read it"}],
            tools=tools,
            model="test-model",
            max_iterations=2,
            max_tool_result_chars=10_000,
            session_key="cli:direct",
            audit_context=AuditRunContext("trace-recovery", "turn-recovery", "run-recovery"),
        )
    )
    await runtime.close()
    AuditIndexer(root).update()
    view = AuditQuery.from_root(root, use_index=True).load_trace(
        "trace-recovery", include_payloads=True
    )

    tool_terminal = next(
        event for event in view.timeline if event.event_type == "tool_finished"
    )
    assert result.stop_reason == "completed"
    assert tool_terminal.status == "error"
    assert view.summary.terminal_run_statuses == ["succeeded"]
    assert view.summary.run_count == 1
    assert view.integrity.status == "valid"


async def test_indexed_trace_pagination_does_not_scan_evidence_files(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "audit"
    runtime = _runtime(root)
    await runtime.start()
    # Reuse a production Runner to produce a complete indexed Trace summary.
    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))
    tools = MagicMock()
    tools.get_definitions.return_value = []
    await AgentRunner(audit_emitter=runtime.emitter).run(
        make_run_spec(
            provider,
            initial_messages=[],
            tools=tools,
            model="test-model",
            max_iterations=1,
            max_tool_result_chars=100,
            audit_context=AuditRunContext("trace-index", "turn-index", "run-index"),
        )
    )
    await runtime.close()
    AuditIndexer(root).update()
    query = AuditQuery.from_root(root, use_index=True)
    monkeypatch.setattr(query, "_records", MagicMock(side_effect=AssertionError("scan used")))

    page = query.find_traces(TraceFilter(limit=1))

    assert page.trace_ids == ["trace-index"]
    assert page.items[0].run_count == 1
    assert page.items[0].terminal_run_statuses == ["succeeded"]
