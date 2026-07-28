"""Test cmd_stop drains pending queue to prevent mid-turn injection deadlock."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.command.builtin import cmd_stop
from nanobot.command.router import CommandContext


def _mock_loop(cancelled: int) -> MagicMock:
    loop = MagicMock()
    loop._cancel_active_tasks = AsyncMock(return_value=cancelled)
    loop._pending_queues = {}
    audit = MagicMock()
    audit.response_prepared = AsyncMock()
    audit.finished = AsyncMock()
    turn = MagicMock(trace_id="trace", turn_id="turn")
    loop._audit_request_cancellation = AsyncMock(return_value=(audit, turn))
    return loop


@pytest.mark.asyncio
async def test_cmd_stop_drains_pending_queue():
    """cmd_stop should drain pending queue in addition to cancelling active tasks."""
    mock_loop = _mock_loop(1)

    pending = asyncio.Queue()
    await pending.put("msg1")
    await pending.put("msg2")
    mock_loop._pending_queues["test-session"] = pending

    ctx = CommandContext(
        msg=MagicMock(channel="websocket", chat_id="test-chat", metadata={}),
        session=None,
        key="test-session",
        raw="/stop",
        loop=mock_loop,
    )

    result = await cmd_stop(ctx)

    assert isinstance(result, OutboundMessage)
    assert "Stopped 3 task(s)" in result.content  # 1 cancelled + 2 drained
    assert "test-session" not in mock_loop._pending_queues


@pytest.mark.asyncio
async def test_cmd_stop_with_empty_pending_queue():
    """cmd_stop should work correctly when pending queue is empty."""
    mock_loop = _mock_loop(2)

    pending = asyncio.Queue()
    mock_loop._pending_queues["test-session"] = pending

    ctx = CommandContext(
        msg=MagicMock(channel="websocket", chat_id="test-chat", metadata={}),
        session=None,
        key="test-session",
        raw="/stop",
        loop=mock_loop,
    )

    result = await cmd_stop(ctx)

    assert "Stopped 2 task(s)" in result.content
    assert "test-session" not in mock_loop._pending_queues


@pytest.mark.asyncio
async def test_cmd_stop_no_pending_queue():
    """cmd_stop should work when no pending queue exists."""
    mock_loop = _mock_loop(0)

    ctx = CommandContext(
        msg=MagicMock(channel="websocket", chat_id="test-chat", metadata={}),
        session=None,
        key="test-session",
        raw="/stop",
        loop=mock_loop,
    )

    result = await cmd_stop(ctx)

    assert "No active task to stop" in result.content
