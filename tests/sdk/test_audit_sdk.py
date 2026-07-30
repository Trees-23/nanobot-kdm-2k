from unittest.mock import AsyncMock, MagicMock

from nanobot.bus.events import AUDIT_CONTEXT_META, OutboundMessage
from nanobot.nanobot import Nanobot


class RecordingEmitter:
    def __init__(self) -> None:
        self.events = []
        self.payloads = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        self.payloads.append(payload)


async def test_sdk_records_return_without_channel_delivery() -> None:
    emitter = RecordingEmitter()
    loop = MagicMock()
    loop.runtime_resolver.resolve_override.return_value = None
    loop.audit_runtime.emitter = emitter
    loop.process_direct = AsyncMock(
        return_value=OutboundMessage(
            channel="cli",
            chat_id="direct",
            content="done",
            metadata={
                AUDIT_CONTEXT_META: {
                    "trace_id": "trace",
                    "turn_id": "turn",
                    "run_id": "run",
                }
            },
        )
    )

    result = await Nanobot(loop).run("work")

    assert result.content == "done"
    assert [event.event_type for event in emitter.events] == ["returned_to_caller"]
    assert emitter.events[0].status == "returned"
    assert emitter.payloads[0].content.content == "done"


async def test_sdk_context_manager_starts_and_closes_audit_runtime() -> None:
    loop = MagicMock()
    loop.audit_runtime.ensure_started = AsyncMock()
    loop.close_mcp = AsyncMock()

    async with Nanobot(loop):
        loop.audit_runtime.ensure_started.assert_awaited_once()

    loop.close_mcp.assert_awaited_once()


async def test_sdk_records_error_before_reraising() -> None:
    emitter = RecordingEmitter()
    loop = MagicMock()
    loop.runtime_resolver.resolve_override.return_value = None
    loop.audit_runtime.emitter = emitter
    error = RuntimeError("failed")
    error._audit_context = {"trace_id": "trace", "turn_id": "turn", "run_id": "run"}
    loop.process_direct = AsyncMock(side_effect=error)

    try:
        await Nanobot(loop).run("work")
    except RuntimeError as caught:
        assert caught is error
    else:
        raise AssertionError("SDK error was swallowed")

    assert [event.event_type for event in emitter.events] == ["returned_to_caller"]
    assert emitter.events[0].status == "error"
