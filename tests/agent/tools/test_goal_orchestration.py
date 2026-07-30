"""Regression tests for durable required-subagent orchestration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.goal_permission import goal_mutation_permission
from nanobot.agent.tools.await_subagents import AwaitSubagentsTool
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.long_task import UpdateGoalTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import GenerationSettings
from nanobot.session.goal_orchestration import GoalOrchestrationStore, required_gate
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager
from nanobot.utils.llm_runtime import LLMRuntime


def _active(sm: SessionManager, key: str = "test:c1") -> None:
    session = sm.get_or_create(key)
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "deliver"}
    sm.save(session)


def _runtime() -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=100)
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=1000)


async def _register(
    store: GoalOrchestrationStore,
    task_id: str,
    *,
    key: str = "test:c1",
    replaces: str | None = None,
) -> None:
    await store.register(
        key,
        task_id=task_id,
        label=task_id,
        group="research",
        child_run_id=f"run-{task_id}",
        spawn_tool_call_id=f"spawn-{task_id}",
        replaces_task_id=replaces,
    )


@pytest.mark.asyncio
async def test_three_required_children_finish_out_of_order_without_lost_updates(tmp_path):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await asyncio.gather(*(_register(store, task_id) for task_id in ("a", "b", "c")))

    await asyncio.gather(
        store.finish("test:c1", "c", "succeeded"),
        store.finish("test:c1", "a", "succeeded"),
        store.finish("test:c1", "b", "succeeded"),
    )

    goal = sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]
    assert required_gate(goal) == (True, [])
    assert {record["status"] for record in goal["orchestration"]["tasks"].values()} == {
        "succeeded"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "timed_out", "lost"])
async def test_each_non_success_terminal_state_blocks_goal_complete(tmp_path, status):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "a")
    await store.finish("test:c1", "a", status, "bounded failure")
    tool = UpdateGoalTool(sm)

    with request_context(RequestContext(channel="test", chat_id="c1", session_key="test:c1")):
        result = await tool.execute(action="complete", recap="done")

    assert isinstance(result, ToolResult) and result.is_error
    assert f"a={status}" in result
    assert sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]["status"] == "active"


@pytest.mark.asyncio
async def test_failed_task_replacement_preserves_old_evidence_and_satisfies_gate(tmp_path):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "old")
    await store.finish("test:c1", "old", "failed", "first attempt")
    await _register(store, "new", replaces="old")
    await store.finish("test:c1", "new", "succeeded")

    goal = sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]
    tasks = goal["orchestration"]["tasks"]
    assert tasks["old"]["status"] == "failed"
    assert tasks["old"]["resolved_by_task_id"] == "new"
    assert tasks["new"]["attempt"] == 2
    assert required_gate(goal) == (True, [])
    tool = UpdateGoalTool(sm)
    with request_context(RequestContext(channel="test", chat_id="c1", session_key="test:c1")):
        result = await tool.execute(action="complete", recap="verified")
    assert "marked complete" in result
    assert sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]["status"] == "completed"


@pytest.mark.asyncio
async def test_task_selection_rejects_another_session_goal(tmp_path):
    sm = SessionManager(tmp_path)
    _active(sm, "test:a")
    _active(sm, "test:b")
    store = GoalOrchestrationStore(sm)
    await _register(store, "owned", key="test:a")

    with pytest.raises(ValueError, match="not owned"):
        await store.select("test:b", task_ids=["owned"])


@pytest.mark.asyncio
async def test_orchestration_save_failure_rolls_back_memory(tmp_path, monkeypatch):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    session = sm.get_or_create("test:c1")
    monkeypatch.setattr(sm, "save", MagicMock(side_effect=OSError("disk unavailable")))

    with pytest.raises(OSError, match="disk unavailable"):
        await _register(store, "a")

    assert "orchestration" not in session.metadata[GOAL_STATE_KEY]


@pytest.mark.asyncio
async def test_runtime_recovery_marks_missing_running_task_lost(tmp_path):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "a")

    records = await store.select("test:c1", task_ids=["a"], running_task_ids=set())

    assert records["a"]["status"] == "lost"
    persisted = SessionManager(tmp_path).get_or_create("test:c1")
    assert persisted.metadata[GOAL_STATE_KEY]["orchestration"]["tasks"]["a"]["status"] == "lost"


@pytest.mark.asyncio
async def test_await_subagents_timeout_waits_once_and_keeps_goal_active(tmp_path):
    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "a")
    manager = SimpleNamespace(
        wait_for=AsyncMock(),
        running_task_ids=MagicMock(return_value={"a"}),
    )
    tool = AwaitSubagentsTool(manager, store)

    with request_context(RequestContext(channel="test", chat_id="c1", session_key="test:c1")):
        result = await tool.execute(task_group="research", timeout_seconds=0.01)

    payload = json.loads(result)
    assert payload["barrier_satisfied"] is False
    assert payload["waiting"] is True
    manager.wait_for.assert_awaited_once_with(["a"], 0.01)
    assert sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]["status"] == "active"


@pytest.mark.asyncio
async def test_required_spawn_without_active_goal_is_error_and_starts_nothing(tmp_path):
    from nanobot.agent.subagent import SubagentManager

    sm = SessionManager(tmp_path)
    store = GoalOrchestrationStore(sm)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        goal_orchestration=store,
    )
    tool = SpawnTool(manager)
    with request_context(
        RequestContext(
            channel="test", chat_id="c1", session_key="test:c1", runtime=_runtime()
        )
    ):
        result = await tool.execute(task="required work", required=True)

    assert isinstance(result, ToolResult) and result.is_error
    assert "active goal" in result
    assert manager.get_running_count() == 0
    assert manager._task_statuses == {}


@pytest.mark.asyncio
async def test_terminal_manager_status_survives_running_cleanup(tmp_path):
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    )
    manager._announce_result = AsyncMock()
    manager.runner.run = AsyncMock(
        return_value=AgentRunResult(final_content="done", messages=[], stop_reason="completed")
    )
    result = await manager.spawn("work", runtime=_runtime(), structured=True)
    task_id = result["task_id"]
    await asyncio.gather(*manager._running_tasks.values())
    await asyncio.sleep(0)

    assert task_id not in manager._task_statuses
    assert manager.get_status(task_id).terminal_status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "goal_status"), [("cancel", "cancelled"), ("block", "blocked")])
async def test_goal_cancel_and_block_cancel_running_required_children(
    tmp_path, action, goal_status
):
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager

    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        goal_orchestration=store,
    )
    manager._announce_result = AsyncMock()
    release = asyncio.Event()

    async def wait_forever(_spec):
        await release.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=wait_forever)
    result = await manager.spawn(
        "required work",
        runtime=_runtime(),
        session_key="test:c1",
        required=True,
        structured=True,
    )
    tool = UpdateGoalTool(sm, subagent_manager=manager)
    with request_context(RequestContext(channel="test", chat_id="c1", session_key="test:c1")):
        await tool.execute(action=action, recap="stop")

    goal = sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]
    assert goal["status"] == goal_status
    assert goal["orchestration"]["tasks"][result["task_id"]]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_goal_replace_preserves_cancelled_orchestration_snapshot(tmp_path):
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager

    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        goal_orchestration=store,
    )
    manager._announce_result = AsyncMock()
    release = asyncio.Event()

    async def wait_forever(_spec):
        await release.wait()
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=wait_forever)
    spawned = await manager.spawn(
        "old required work",
        runtime=_runtime(),
        session_key="test:c1",
        required=True,
        structured=True,
    )
    tool = UpdateGoalTool(sm, subagent_manager=manager)
    with request_context(
        RequestContext(channel="test", chat_id="c1", session_key="test:c1")
    ), goal_mutation_permission(True):
        await tool.execute(action="replace", objective="new objective")

    goal = sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]
    assert goal["status"] == "active"
    assert "orchestration" not in goal
    assert goal["previous_orchestration"]["tasks"][spawned["task_id"]]["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "error_kind", "expected_status", "error_fragment"),
    [
        ("completed", None, "succeeded", None),
        ("max_iterations", None, "failed", "Iteration budget exhausted"),
        ("empty_final_response", None, "failed", "no final response"),
        ("tool_error", None, "failed", "tool failed"),
        ("error", "timeout", "timed_out", "provider unavailable"),
        ("unexpected_stop", None, "failed", "non-success reason"),
    ],
)
async def test_required_subagent_stop_reason_mapping_uses_real_manager_path(
    tmp_path, stop_reason, error_kind, expected_status, error_fragment
):
    from nanobot.agent.runner import AgentRunResult
    from nanobot.agent.subagent import SubagentManager, SubagentStatus

    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "mapped")
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        goal_orchestration=store,
    )
    manager._announce_result = AsyncMock()
    tool_events = (
        [{"name": "exec", "status": "error", "detail": "tool failed"}]
        if stop_reason == "tool_error"
        else []
    )
    manager.runner.run = AsyncMock(
        return_value=AgentRunResult(
            final_content="verified result" if stop_reason == "completed" else None,
            messages=[],
            stop_reason=stop_reason,
            error="provider unavailable" if stop_reason == "error" else None,
            error_kind=error_kind,
            tool_events=tool_events,
        )
    )
    status = SubagentStatus(
        task_id="mapped",
        label="mapped",
        task_description="sensitive task body",
        started_at=1.0,
        session_key="test:c1",
        required=True,
    )

    await manager._run_subagent(
        "mapped",
        "sensitive task body",
        "mapped",
        {"channel": "test", "chat_id": "c1", "session_key": "test:c1"},
        status,
        _runtime(),
        required=True,
    )

    record = sm.get_or_create("test:c1").metadata[GOAL_STATE_KEY]["orchestration"]["tasks"][
        "mapped"
    ]
    assert record["status"] == expected_status
    if error_fragment is None:
        assert record["error"] is None
        assert manager._announce_result.await_args.args[5] == "ok"
    else:
        assert error_fragment in record["error"]
        assert manager._announce_result.await_args.args[5] == "error"
        tool = UpdateGoalTool(sm)
        with request_context(
            RequestContext(channel="test", chat_id="c1", session_key="test:c1")
        ):
            denied = await tool.execute(action="complete", recap="not done")
        assert isinstance(denied, ToolResult) and denied.is_error


def test_terminal_cache_is_bounded_fifo_and_minimizes_payload(tmp_path):
    from nanobot.agent.subagent import (
        TERMINAL_STATUS_CACHE_LIMIT,
        SubagentManager,
        SubagentStatus,
    )

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    )
    for index in range(TERMINAL_STATUS_CACHE_LIMIT + 1):
        manager._cache_terminal_status(
            SubagentStatus(
                task_id=f"task-{index}",
                label=f"label-{index}",
                task_description="unbounded sensitive body" * 100,
                started_at=float(index),
                phase="done",
                tool_events=[{"result": "unbounded payload" * 100}],
                usage={"large": "payload" * 100},
                error="x" * 1000,
                terminal_status="failed",
                session_key="test:a",
            )
        )

    assert len(manager._terminal_statuses) == TERMINAL_STATUS_CACHE_LIMIT
    assert "task-0" not in manager._terminal_statuses
    cached = manager._terminal_statuses[f"task-{TERMINAL_STATUS_CACHE_LIMIT}"]
    assert cached.task_description == ""
    assert cached.tool_events == []
    assert cached.usage == {}
    assert len(cached.error) == 500


def test_terminal_cache_session_cleanup_is_isolated(tmp_path):
    from nanobot.agent.subagent import SubagentManager, SubagentStatus

    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
    )
    for task_id, session_key in (("a", "test:a"), ("b", "test:b")):
        manager._cache_terminal_status(
            SubagentStatus(
                task_id=task_id,
                label=task_id,
                task_description="body",
                started_at=1.0,
                terminal_status="succeeded",
                session_key=session_key,
            )
        )

    assert manager.clear_terminal_statuses_by_session("test:a") == 1
    assert manager.get_status("a") is None
    assert manager.get_status("b") is not None


@pytest.mark.asyncio
async def test_durable_required_success_survives_terminal_cache_eviction(tmp_path):
    from nanobot.agent.subagent import SubagentManager, SubagentStatus

    sm = SessionManager(tmp_path)
    _active(sm)
    store = GoalOrchestrationStore(sm)
    await _register(store, "durable")
    await store.finish("test:c1", "durable", "succeeded")
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=AgentDefaults().max_tool_result_chars,
        goal_orchestration=store,
    )
    manager._cache_terminal_status(
        SubagentStatus(
            task_id="durable",
            label="durable",
            task_description="body",
            started_at=1.0,
            terminal_status="succeeded",
            session_key="test:c1",
            required=True,
        )
    )
    manager.clear_terminal_statuses_by_session("test:c1")
    barrier = AwaitSubagentsTool(manager, store)
    with request_context(
        RequestContext(channel="test", chat_id="c1", session_key="test:c1")
    ):
        waited = await barrier.execute(task_ids=["durable"], timeout_seconds=0)
        completed = await UpdateGoalTool(sm).execute(action="complete", recap="verified")

    assert json.loads(waited)["barrier_satisfied"] is True
    assert "marked complete" in completed
