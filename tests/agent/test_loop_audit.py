"""End-to-end audit coverage for AgentLoop-owned turn boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.audit.runtime import AuditRuntime
from nanobot.bus.events import AUDIT_CONTEXT_META
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMResponse


class RecordingEmitter:
    def __init__(self) -> None:
        self.events = []
        self.payloads = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        self.payloads.append(payload)
        return MagicMock(committed=True)

    @property
    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events]


def make_loop(tmp_path):
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", usage={}))
    runtime = AuditRuntime.disabled()
    emitter = RecordingEmitter()
    runtime.emitter = emitter
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        audit_runtime=runtime,
    )
    loop._connect_mcp = AsyncMock()
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=None)
    return loop, emitter


async def test_ordinary_turn_links_trace_turn_run_and_prepared_response(tmp_path) -> None:
    loop, emitter = make_loop(tmp_path)

    outbound = await loop.process_direct("hello")

    assert outbound is not None
    assert emitter.event_types[:3] == ["trace_created", "turn_started", "run_started"]
    assert emitter.event_types[-2:] == ["turn_response_prepared", "turn_finished"]
    trace_ids = {event.trace_id for event in emitter.events if event.trace_id is not None}
    turn_ids = {event.turn_id for event in emitter.events if event.turn_id is not None}
    run_ids = {event.run_id for event in emitter.events if event.run_id is not None}
    assert len(trace_ids) == len(turn_ids) == len(run_ids) == 1
    assert outbound.metadata[AUDIT_CONTEXT_META] == {
        "trace_id": next(iter(trace_ids)),
        "turn_id": next(iter(turn_ids)),
        "run_id": next(iter(run_ids)),
    }
    turn_input = next(
        payload for payload in emitter.payloads if payload and payload.payload_kind == "turn_input"
    )
    assert turn_input.content.content == "hello"
    assert emitter.events[-1].status == "response_prepared"


async def test_ordinary_messages_create_distinct_traces(tmp_path) -> None:
    loop, emitter = make_loop(tmp_path)

    await loop.process_direct("first")
    await loop.process_direct("second")

    traces = [event.trace_id for event in emitter.events if event.event_type == "trace_created"]
    assert len(traces) == 2
    assert traces[0] != traces[1]


async def test_checkpoint_restoration_inherits_trace_and_resumes_run(tmp_path) -> None:
    loop, emitter = make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata[loop._RUNTIME_CHECKPOINT_KEY] = {
        "phase": "awaiting_tools",
        "assistant_message": None,
        "completed_tool_results": [],
        "pending_tool_calls": [],
        "_audit_checkpoint_id": "checkpoint-source",
        "_audit_checkpoint_version": 3,
        "_audit_trace_id": "trace-source",
        "_audit_turn_id": "turn-source",
        "_audit_run_id": "run-source",
    }
    loop.sessions.save(session)

    await loop.process_direct("resume")

    assert emitter.events[0].event_type == "trace_linked"
    assert emitter.events[0].trace_id == "trace-source"
    restored = next(event for event in emitter.events if event.event_type == "checkpoint_restored")
    assert restored.checkpoint_id == "checkpoint-source"
    assert restored.source_run_id == "run-source"
    run_started = next(event for event in emitter.events if event.event_type == "run_started")
    assert run_started.resumed_from_run_id == "run-source"
