"""Task lifecycle outbox publication and degraded retry tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.audit.subagent_lifecycle import SubagentLifecyclePublisher
from nanobot.session.subagent_tasks import SubagentTaskStatus, SubagentTaskStore


class RecordingEmitter:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.events = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_committed_lifecycle_outbox_is_acknowledged_once(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(
        task_id="task-a",
        owner_session_key="test:a",
        trace_id="trace-a",
        turn_id="turn-a",
        owner_run_id="owner-a",
        child_run_id="child-a",
        spawn_tool_call_id="spawn-a",
        label="检查一级目录",
    )
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    emitter = RecordingEmitter([
        SimpleNamespace(committed=True, disabled=False),
        SimpleNamespace(committed=True, disabled=False),
    ])
    publisher = SubagentLifecyclePublisher(store, emitter)

    assert await publisher.flush_task("task-a") == 2
    assert await publisher.flush_task("task-a") == 0
    assert [event.event_type for event in emitter.events] == [
        "subagent_created",
        "subagent_admitted",
    ]
    assert all(event.subagent_task_id == "task-a" for event in emitter.events)
    assert all(event.task_label == "检查一级目录" for event in emitter.events)
    assert [event.task_revision for event in emitter.events] == [1, 2]
    assert all(event.trace_id == "trace-a" for event in emitter.events)
    assert all(event.tool_call_id == "spawn-a" for event in emitter.events)


@pytest.mark.asyncio
async def test_degraded_audit_keeps_outbox_for_idempotent_retry(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    first = RecordingEmitter([SimpleNamespace(committed=False, disabled=False)])

    assert await SubagentLifecyclePublisher(store, first).flush_task("task-a") == 0
    pending = store.load("task-a")
    assert pending is not None and pending.lifecycle_outbox[0].published_at is None

    second = RecordingEmitter([SimpleNamespace(committed=True, disabled=False)])
    assert await SubagentLifecyclePublisher(store, second).flush_pending() == 1
    assert first.events[0].idempotency_key == second.events[0].idempotency_key
    published = store.load("task-a")
    assert published is not None and published.lifecycle_outbox[0].published_at is not None


@pytest.mark.asyncio
async def test_budget_settlement_uses_versioned_lifecycle_event(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(
        task_id="task-a",
        owner_session_key="test:a",
        budget={"reserved_tokens": 100, "reservation_state": "reserved"},
    )
    await store.update_budget(
        "task-a",
        {
            "reserved_tokens": 100,
            "consumed_tokens": 12,
            "reservation_state": "settled",
        },
    )
    emitter = RecordingEmitter([
        SimpleNamespace(committed=True, disabled=False),
        SimpleNamespace(committed=True, disabled=False),
    ])

    assert await SubagentLifecyclePublisher(store, emitter).flush_task("task-a") == 2
    assert emitter.events[-1].event_type == "subagent_budget_updated"
    assert emitter.events[-1].idempotency_key == "task-a:2:subagent_budget_updated"
