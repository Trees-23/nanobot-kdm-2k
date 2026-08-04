"""Durable subagent snapshot, delta, gap, and reconnect protocol tests."""

from __future__ import annotations

import json

import pytest

from nanobot.channels.websocket.runtime import WebSocketChannel
from nanobot.session.subagent_tasks import (
    SubagentExecutionPhase,
    SubagentTaskStatus,
    SubagentTaskStore,
)


class _Connection:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)


def _channel(store: SubagentTaskStore, connection: _Connection) -> WebSocketChannel:
    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._subagent_tasks = store
    channel._subagent_revisions = {}
    channel._subs = {"chat-a": {connection}}
    channel._subagent_subs = {"chat-a": {connection}}
    channel._conn_chats = {connection: {"chat-a"}}
    channel._conn_default = {}
    return channel


async def _queued(store: SubagentTaskStore) -> None:
    await store.create(task_id="task-a", owner_session_key="websocket:chat-a")
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)


@pytest.mark.asyncio
async def test_reconnect_snapshot_is_authoritative_and_payload_closed(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _queued(store)
    connection = _Connection()
    channel = _channel(store, connection)

    await channel.send_subagent_snapshot("chat-a", connection=connection)

    body = json.loads(connection.sent[-1])
    assert body["event"] == "subagent_snapshot"
    assert body["snapshot"]["tasks"][0]["revision"] == 2
    assert channel._subagent_revisions[("chat-a", "task-a")] == 2
    assert "owner_session_key" not in body["snapshot"]["tasks"][0]


@pytest.mark.asyncio
async def test_rehydrate_explicitly_subscribes_and_disconnect_cleans_up(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _queued(store)
    connection = _Connection()
    channel = _channel(store, connection)
    channel._subagent_subs.clear()

    await channel._dispatch_envelope(
        connection,
        "client-a",
        {"type": "subagent_rehydrate", "chat_id": "chat-a"},
    )

    assert connection in channel._subagent_subs["chat-a"]
    assert json.loads(connection.sent[-1])["event"] == "subagent_snapshot"

    channel._cleanup_connection(connection)
    assert "chat-a" not in channel._subagent_subs


@pytest.mark.asyncio
async def test_contiguous_revision_emits_delta_and_duplicate_is_ignored(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _queued(store)
    connection = _Connection()
    channel = _channel(store, connection)
    await channel.send_subagent_snapshot("chat-a")
    connection.sent.clear()

    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)
    await channel._poll_subagent_changes_once()
    await channel._poll_subagent_changes_once()

    assert len(connection.sent) == 1
    body = json.loads(connection.sent[0])
    assert body["event"] == "subagent_status_changed"
    assert body["task"]["revision"] == 3


@pytest.mark.asyncio
async def test_revision_gap_rehydrates_with_snapshot(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _queued(store)
    connection = _Connection()
    channel = _channel(store, connection)
    await channel.send_subagent_snapshot("chat-a")
    connection.sent.clear()

    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)
    await store.update_runtime("task-a", phase=SubagentExecutionPhase.AWAITING_TOOLS)
    await channel._poll_subagent_changes_once()

    assert len(connection.sent) == 1
    body = json.loads(connection.sent[0])
    assert body["event"] == "subagent_snapshot"
    assert body["snapshot"]["tasks"][0]["revision"] == 4
