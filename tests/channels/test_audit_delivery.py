import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import AUDIT_CONTEXT_META, OutboundMessage
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import ChannelsConfig


class RecordingEmitter:
    def __init__(self) -> None:
        self.events = []
        self.payloads = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        self.payloads.append(payload)


def manager(emitter: RecordingEmitter) -> ChannelManager:
    value = ChannelManager.__new__(ChannelManager)
    value.config = SimpleNamespace(channels=ChannelsConfig(send_max_retries=2))
    value._audit_emitter = emitter
    return value


def message() -> OutboundMessage:
    return OutboundMessage(
        channel="test",
        chat_id="chat",
        content="hello",
        metadata={
            AUDIT_CONTEXT_META: {
                "trace_id": "trace",
                "turn_id": "turn",
                "run_id": "run",
            },
            "thread_id": "thread",
        },
    )


async def test_delivery_retry_records_real_attempts_and_strips_audit_metadata(
    monkeypatch,
) -> None:
    emitter = RecordingEmitter()
    attempts = 0
    received = []

    class Channel:
        async def send(self, msg):
            nonlocal attempts
            attempts += 1
            received.append(msg)
            if attempts == 1:
                raise RuntimeError("temporary")

    monkeypatch.setattr("nanobot.channels.manager.asyncio.sleep", AsyncMock())
    await manager(emitter)._send_with_retry(Channel(), message())

    assert [event.event_type for event in emitter.events] == [
        "delivery_attempted",
        "delivery_retry_scheduled",
        "delivery_attempted",
        "delivery_finished",
    ]
    assert [event.attempt_ordinal for event in emitter.events if event.event_type == "delivery_attempted"] == [1, 2]
    assert emitter.events[-1].status == "accepted_by_adapter"
    assert len({event.delivery_id for event in emitter.events}) == 1
    assert all(AUDIT_CONTEXT_META not in item.metadata for item in received)
    assert all(item.metadata["thread_id"] == "thread" for item in received)


async def test_delivery_cancellation_records_terminal_and_reraises() -> None:
    emitter = RecordingEmitter()

    class Channel:
        async def send(self, _msg):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await manager(emitter)._send_with_retry(Channel(), message())

    assert [event.event_type for event in emitter.events] == [
        "delivery_attempted",
        "delivery_finished",
    ]
    assert emitter.events[-1].status == "cancelled"


async def test_delivery_exhaustion_records_failed_terminal(monkeypatch) -> None:
    emitter = RecordingEmitter()

    class Channel:
        async def send(self, _msg):
            raise RuntimeError("still failing")

    monkeypatch.setattr("nanobot.channels.manager.asyncio.sleep", AsyncMock())
    await manager(emitter)._send_with_retry(Channel(), message())

    attempts = [event for event in emitter.events if event.event_type == "delivery_attempted"]
    assert len(attempts) == 2
    assert emitter.events[-1].event_type == "delivery_finished"
    assert emitter.events[-1].final_attempt_ordinal == 2
    assert emitter.events[-1].status == "failed"


async def test_delivery_metadata_is_converted_to_json_values() -> None:
    emitter = RecordingEmitter()
    item = message()
    item.metadata["remote"] = ("127.0.0.1", 8765)

    class Channel:
        async def send(self, _msg):
            return None

    await manager(emitter)._send_with_retry(Channel(), item)

    payload = next(payload for payload in emitter.payloads if payload is not None)
    assert payload.content.adapter_metadata["remote"] == ["127.0.0.1", 8765]
