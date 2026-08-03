"""Tests for SubagentManager lifecycle — spawn, run, announce, cancel."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent import SubagentManager
from nanobot.agent.hook import AgentHookContext
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.subagent import (
    SubagentAdmissionError,
    SubagentStatus,
    _SubagentHook,
)
from nanobot.agent.tools.context import RequestContext, current_request_context, request_context
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.session.goal_orchestration import GoalOrchestrationStore
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager
from nanobot.session.subagent_tasks import (
    SubagentDeliveryPhase,
    SubagentTaskStatus,
    SubagentTaskStore,
)
from nanobot.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manager(tmp_path: Path, **kw) -> SubagentManager:
    defaults = dict(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    defaults.update(kw)
    return SubagentManager(**defaults)


def _runtime(*, model: str = "test-model", temperature: float = 0.1) -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings(temperature=temperature, max_tokens=4096)
    return LLMRuntime.capture(
        provider,
        model,
        context_window_tokens=128_000,
    )


def _make_hook_context(**overrides) -> AgentHookContext:
    defaults = dict(
        iteration=1,
        tool_calls=[],
        tool_events=[],
        messages=[],
        usage={},
        error=None,
        stop_reason="completed",
        final_content="ok",
    )
    defaults.update(overrides)
    return AgentHookContext(**defaults)


async def _drain_subagent_tasks(sm: SubagentManager) -> None:
    tasks = list(sm._running_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_close_cancels_tasks_before_closing_exec_sessions(tmp_path):
    sm = _manager(tmp_path)
    task = asyncio.create_task(asyncio.Event().wait())
    sm._running_tasks["t1"] = task

    async def close_exec_sessions() -> int:
        assert task.done()
        return 0

    sm._exec_session_manager.close_all = AsyncMock(side_effect=close_exec_sessions)

    await sm.close()

    assert task.cancelled()
    sm._exec_session_manager.close_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_records_cooperative_exit(tmp_path):
    sm = _manager(tmp_path)
    status = SubagentStatus(
        task_id="cooperative",
        label="cooperative",
        task_description="",
        started_at=time.monotonic(),
    )

    async def cooperative() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(cooperative())
    sm._running_tasks[status.task_id] = task
    sm._task_statuses[status.task_id] = status
    await asyncio.sleep(0)

    assert await sm.timeout_tasks([status.task_id], grace_seconds=0.1) is True
    assert task.cancelled()


@pytest.mark.asyncio
async def test_timeout_that_cannot_confirm_exit_is_fail_closed(tmp_path):
    from nanobot.session.goal_orchestration import GoalOrchestrationStore
    from nanobot.session.goal_state import GOAL_STATE_KEY
    from nanobot.session.manager import SessionManager

    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("test:c1")
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "deliver"}
    sessions.save(session)
    store = GoalOrchestrationStore(sessions)
    await store.register(
        "test:c1",
        task_id="stubborn",
        label="stubborn",
        group="default",
        child_run_id="child",
        spawn_tool_call_id="spawn",
        owner_run_id="owner",
    )
    sm = _manager(tmp_path, goal_orchestration=store)
    release = asyncio.Event()
    cancel_seen = asyncio.Event()
    status = SubagentStatus(
        task_id="stubborn",
        label="stubborn",
        task_description="",
        started_at=time.monotonic(),
        session_key="test:c1",
        required=True,
        owner_run_id="owner",
    )

    async def stubborn() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release.wait()

    task = asyncio.create_task(stubborn())
    sm._running_tasks[status.task_id] = task
    sm._task_statuses[status.task_id] = status
    await asyncio.sleep(0)

    assert await sm.timeout_tasks([status.task_id], grace_seconds=0.01) is False
    assert cancel_seen.is_set()
    record = sessions.get_or_create("test:c1").metadata[GOAL_STATE_KEY]["orchestration"]["tasks"]["stubborn"]
    assert record["status"] == "lost"
    assert record["termination_state"] == "termination_failed"
    assert record["termination_evidence"]["exit_observed"] is False

    release.set()
    await task


# ---------------------------------------------------------------------------
# SubagentStatus defaults
# ---------------------------------------------------------------------------


class TestSubagentStatus:
    def test_defaults(self):
        s = SubagentStatus(
            task_id="abc", label="test", task_description="do stuff",
            started_at=time.monotonic(),
        )
        assert s.phase == "initializing"
        assert s.iteration == 0
        assert s.tool_events == []
        assert s.usage == {}
        assert s.stop_reason is None
        assert s.error is None


# ---------------------------------------------------------------------------
# Runtime ownership
# ---------------------------------------------------------------------------


class TestRuntimeOwnership:
    def test_manager_has_no_provider_model_mirrors(self, tmp_path):
        sm = _manager(tmp_path)
        assert not hasattr(sm, "provider")
        assert not hasattr(sm, "model")
        assert not hasattr(sm, "context_window_tokens")
        assert not hasattr(sm.runner, "provider")


class TestLegacyCompatibility:
    def test_accepts_exported_legacy_constructor_positionally(self, tmp_path):
        provider = MagicMock(spec=LLMProvider)
        provider.generation = GenerationSettings(temperature=0.2, max_tokens=2048)

        with pytest.warns(DeprecationWarning, match="provider"):
            sm = SubagentManager(
                provider,
                tmp_path,
                MessageBus(),
                16_000,
                "legacy-model",
            )

        assert sm.workspace == tmp_path
        assert sm.max_tool_result_chars == 16_000
        assert not hasattr(sm, "provider")
        assert not hasattr(sm, "model")

    @pytest.mark.asyncio
    async def test_legacy_spawn_captures_runtime_at_admission(self, tmp_path):
        provider = MagicMock(spec=LLMProvider)
        provider.generation = GenerationSettings(temperature=0.2, max_tokens=2048)
        with pytest.warns(DeprecationWarning, match="provider"):
            sm = SubagentManager(
                provider=provider,
                workspace=tmp_path,
                bus=MessageBus(),
                max_tool_result_chars=16_000,
                model="legacy-model",
            )
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        provider.generation = GenerationSettings(temperature=0.8, max_tokens=512)

        with pytest.warns(DeprecationWarning, match="runtime"):
            await sm.spawn("legacy task")
        await _drain_subagent_tasks(sm)

        runtime = sm.runner.run.await_args.args[0].runtime
        assert runtime.provider is provider
        assert runtime.model == "legacy-model"
        assert runtime.generation == GenerationSettings(0.8, 512, None)

    @pytest.mark.asyncio
    async def test_set_provider_supports_future_legacy_spawns(self, tmp_path):
        sm = _manager(tmp_path)
        provider = MagicMock(spec=LLMProvider)
        provider.generation = GenerationSettings(temperature=0.3, max_tokens=1024)
        with pytest.warns(DeprecationWarning, match="set_provider"):
            sm.set_provider(provider, "replacement-model")
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))

        with pytest.warns(DeprecationWarning, match="runtime"):
            await sm.spawn("legacy task")
        await _drain_subagent_tasks(sm)

        runtime = sm.runner.run.await_args.args[0].runtime
        assert runtime.provider is provider
        assert runtime.model == "replacement-model"

    @pytest.mark.asyncio
    async def test_new_constructor_still_requires_explicit_spawn_runtime(self, tmp_path):
        sm = _manager(tmp_path)

        with pytest.raises(TypeError, match="runtime"):
            await sm.spawn("task")


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------


class TestSpawn:
    @pytest.mark.asyncio
    async def test_returns_string_with_task_id(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        result = await sm.spawn("do something", runtime=_runtime())
        assert "started" in result
        assert "id:" in result

    @pytest.mark.asyncio
    async def test_structured_task_reaches_child_without_leaking_context_in_announce(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        sm._announce_result = AsyncMock()

        spawned = await sm.spawn(
            "",
            task_spec={
                "objective": "inspect state",
                "context": "private bounded context",
                "acceptance_criteria": ["tests pass"],
            },
            runtime=_runtime(),
            session_key="test:structured",
            structured=True,
        )
        await _drain_subagent_tasks(sm)

        run_spec = sm.runner.run.await_args.args[0]
        assert "private bounded context" in run_spec.initial_messages[-1]["content"]
        assert sm._announce_result.await_args.args[2] == "inspect state"
        task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        assert task is not None and task.task_spec is not None
        assert task.task_spec.acceptance_criteria == ["tests pass"]

    @pytest.mark.asyncio
    async def test_duplicate_requires_explicit_terminal_replacement(self, tmp_path):
        sm = _manager(tmp_path, max_concurrent_subagents=3)
        release = asyncio.Event()

        async def slow_run(_spec):
            await release.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

        sm.runner.run = slow_run
        first = await sm.spawn(
            "same task",
            runtime=_runtime(),
            session_key="test:duplicate",
            structured=True,
        )
        with pytest.raises(SubagentAdmissionError, match="duplicate") as rejected:
            await sm.spawn("same task", runtime=_runtime(), session_key="test:duplicate")
        assert rejected.value.reason == "duplicate_task"

        release.set()
        await _drain_subagent_tasks(sm)
        with pytest.raises(SubagentAdmissionError) as terminal_duplicate:
            await sm.spawn(
                "same task",
                runtime=_runtime(),
                session_key="test:duplicate",
                structured=True,
            )
        assert terminal_duplicate.value.reason == "duplicate_task"

        retried = await sm.spawn(
            "same task",
            runtime=_runtime(),
            session_key="test:duplicate",
            replaces_task_id=first["task_id"],
            structured=True,
        )
        await _drain_subagent_tasks(sm)
        assert retried["started"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("manager_kwargs", "spawn_kwargs", "reason"),
        [
            ({"max_children_per_owner_run": 1}, {}, "child_count_limit"),
            ({"max_children_per_session": 1}, {}, "session_child_count_limit"),
            ({"max_child_depth": 0}, {"child_depth": 1}, "depth_limit"),
            ({"max_total_subagent_tokens": 4095}, {}, "token_budget_exhausted"),
            ({"max_total_subagent_cost_usd": 1}, {}, "cost_reservation_unavailable"),
        ],
    )
    async def test_admission_budget_reasons(
        self, tmp_path, manager_kwargs, spawn_kwargs, reason
    ):
        sm = _manager(tmp_path, **manager_kwargs)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        if reason in {"child_count_limit", "session_child_count_limit"}:
            await sm.spawn("first", runtime=_runtime(), session_key="test:budget")
            await _drain_subagent_tasks(sm)

        with pytest.raises(SubagentAdmissionError) as rejected:
            await sm.spawn(
                "next", runtime=_runtime(), session_key="test:budget", **spawn_kwargs
            )
        assert rejected.value.reason == reason

    @pytest.mark.asyncio
    async def test_terminal_budget_settles_to_observed_usage(self, tmp_path):
        sm = _manager(tmp_path, max_total_subagent_tokens=5000)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done",
            messages=[],
            stop_reason="completed",
            usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        ))

        spawned = await sm.spawn(
            "budgeted", runtime=_runtime(), session_key="test:settle", structured=True
        )
        await _drain_subagent_tasks(sm)

        task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        assert task is not None
        assert task.budget["reservation_state"] == "settled"
        assert task.budget["consumed_tokens"] == 15

    @pytest.mark.asyncio
    async def test_failed_required_registration_releases_reserved_budget(self, tmp_path):
        goal_store = MagicMock()
        goal_store.validate_registration = AsyncMock()
        goal_store.register = AsyncMock(side_effect=OSError("write failed"))
        sm = _manager(tmp_path, goal_orchestration=goal_store)

        with pytest.raises(OSError, match="write failed"):
            await sm.spawn(
                "required",
                runtime=_runtime(),
                session_key="test:release",
                required=True,
            )

        [task] = SubagentTaskStore(tmp_path).list_tasks()
        assert task.status == SubagentTaskStatus.FAILED
        assert task.budget["reservation_state"] == "released"
        assert task.budget["released_reason"] == "startup_failed"

    @pytest.mark.asyncio
    async def test_wall_time_budget_uses_termination_pipeline(self, tmp_path):
        sm = _manager(tmp_path, max_subagent_wall_time_seconds=0.01)

        async def never_finishes(_spec):
            await asyncio.Event().wait()

        sm.runner.run = never_finishes
        spawned = await sm.spawn(
            "bounded", runtime=_runtime(), session_key="test:wall", structured=True
        )
        async def terminal_task():
            while True:
                task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
                if task is not None and task.status == SubagentTaskStatus.TIMED_OUT:
                    return task
                await asyncio.sleep(0.02)

        task = await asyncio.wait_for(terminal_task(), timeout=2)
        assert task.status == SubagentTaskStatus.TIMED_OUT
        assert task.termination.evidence is not None
        assert task.termination.evidence["exit_observed"] is True
        await sm.close()

    @pytest.mark.asyncio
    async def test_inherits_trace_and_creates_child_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        parent = {"trace_id": "trace", "turn_id": "turn", "run_id": "parent-run"}

        with request_context(RequestContext(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            runtime=_runtime(),
            metadata={"_audit_context": parent},
        )):
            await sm.spawn("task", runtime=_runtime(), session_key="cli:direct")
        await _drain_subagent_tasks(sm)

        child = sm.runner.run.await_args.args[0].audit_context
        assert child.trace_id == "trace"
        assert child.turn_id == "turn"
        assert child.parent_run_id == "parent-run"
        assert child.run_id != "parent-run"
        assert child.source_type == "subagent"

    @pytest.mark.asyncio
    async def test_creates_task_in_running_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", runtime=_runtime(), session_key="s1")
        assert len(sm._running_tasks) == 1

        block.set()
        await _drain_subagent_tasks(sm)
        assert len(sm._running_tasks) == 0

    @pytest.mark.asyncio
    async def test_creates_status(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("my task", runtime=_runtime())
        await _drain_subagent_tasks(sm)
        # Status cleaned up after task completes
        assert len(sm._task_statuses) == 0

    @pytest.mark.asyncio
    async def test_registers_in_session_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", runtime=_runtime(), session_key="s1")
        assert "s1" in sm._session_tasks
        assert len(sm._session_tasks["s1"]) == 1

        block.set()
        await _drain_subagent_tasks(sm)
        assert "s1" not in sm._session_tasks

    @pytest.mark.asyncio
    async def test_no_session_key_no_registration(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn("task", runtime=_runtime())
        assert len(sm._session_tasks) == 0

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_label_defaults_to_truncated_task(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        long_label_source = "A" * 50
        await sm.spawn(long_label_source, runtime=_runtime(), session_key="s1")
        status = next(iter(sm._task_statuses.values()))
        assert status.label == long_label_source[:30] + "..."

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_custom_label(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        await sm.spawn(
            "task", runtime=_runtime(), label="Custom Label", session_key="s1"
        )
        status = next(iter(sm._task_statuses.values()))
        assert status.label == "Custom Label"

        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_cleanup_callback_removes_all_entries(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("task", runtime=_runtime(), session_key="s1")
        await _drain_subagent_tasks(sm)
        assert len(sm._running_tasks) == 0
        assert len(sm._task_statuses) == 0
        assert len(sm._session_tasks) == 0

    @pytest.mark.asyncio
    async def test_runtime_is_captured_before_background_task_starts(self, tmp_path):
        sm = _manager(tmp_path)
        runtime = _runtime(temperature=0.2)
        entered = asyncio.Event()
        release = asyncio.Event()
        seen: dict[str, object] = {}

        async def observe(spec):
            seen["spec_runtime"] = spec.runtime
            request_ctx = current_request_context()
            seen["context_runtime"] = request_ctx.runtime if request_ctx else None
            entered.set()
            await release.wait()
            return AgentRunResult(
                final_content="done",
                messages=[],
                stop_reason="completed",
            )

        sm.runner.run = observe
        await sm.spawn("task", runtime=runtime, session_key="s1")
        runtime.provider.generation = GenerationSettings(
            temperature=0.9,
            max_tokens=128,
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

        assert seen["spec_runtime"] is runtime
        assert seen["context_runtime"] is runtime
        assert runtime.generation.temperature == 0.2

        release.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_background_task_is_durable_through_success(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done",
            messages=[],
            stop_reason="completed",
            usage={"prompt_tokens": 12, "completion_tokens": 3},
        ))

        spawned = await sm.spawn(
            "durable background",
            runtime=_runtime(),
            session_key="test:background",
            structured=True,
        )
        await _drain_subagent_tasks(sm)

        task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        assert task is not None
        assert task.required is False
        assert task.owner_session_key == "test:background"
        assert task.status == SubagentTaskStatus.SUCCEEDED
        assert task.usage == {"prompt_tokens": 12, "completion_tokens": 3}
        assert task.delivery.phase == SubagentDeliveryPhase.READY

        assert await sm.claim_result(spawned["task_id"], "continuation-run") is True
        assert await sm.claim_result(spawned["task_id"], "continuation-run") is False
        assert await sm.mark_result_delivered(spawned["task_id"]) is True
        delivered = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        assert delivered is not None
        assert delivered.delivery.phase == SubagentDeliveryPhase.DELIVERED
        assert delivered.delivery.claim_owner_run_id == "continuation-run"

    @pytest.mark.asyncio
    async def test_required_task_uses_task_store_without_replacing_goal_barrier(self, tmp_path):
        sessions = SessionManager(tmp_path)
        session = sessions.get_or_create("test:goal")
        session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "deliver"}
        sessions.save(session)
        goal_store = GoalOrchestrationStore(sessions)
        sm = _manager(tmp_path, goal_orchestration=goal_store)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))

        spawned = await sm.spawn(
            "required durable",
            runtime=_runtime(),
            session_key="test:goal",
            required=True,
            task_group="research",
            structured=True,
        )
        await _drain_subagent_tasks(sm)

        task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        goal = sessions.get_or_create("test:goal").metadata[GOAL_STATE_KEY]
        obligation = goal["orchestration"]["tasks"][spawned["task_id"]]
        assert task is not None
        assert task.required is True
        assert task.task_group == "research"
        assert task.status == SubagentTaskStatus.SUCCEEDED
        assert obligation["status"] == "succeeded"
        assert obligation["group"] == "research"

    @pytest.mark.asyncio
    async def test_goal_mirror_retry_does_not_downgrade_durable_success(self, tmp_path):
        sessions = SessionManager(tmp_path)
        session = sessions.get_or_create("test:goal")
        session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "deliver"}
        sessions.save(session)
        goal_store = GoalOrchestrationStore(sessions)
        original_finish = goal_store.finish
        attempts = 0

        async def flaky_finish(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary goal persistence failure")
            return await original_finish(*args, **kwargs)

        goal_store.finish = AsyncMock(side_effect=flaky_finish)
        sm = _manager(tmp_path, goal_orchestration=goal_store)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))

        spawned = await sm.spawn(
            "required durable",
            runtime=_runtime(),
            session_key="test:goal",
            required=True,
            structured=True,
        )
        await _drain_subagent_tasks(sm)

        task = SubagentTaskStore(tmp_path).load(spawned["task_id"])
        obligation = sessions.get_or_create("test:goal").metadata[GOAL_STATE_KEY][
            "orchestration"
        ]["tasks"][spawned["task_id"]]
        assert attempts == 2
        assert task is not None and task.status == SubagentTaskStatus.SUCCEEDED
        assert obligation["status"] == "succeeded"


# ---------------------------------------------------------------------------
# _run_subagent
# ---------------------------------------------------------------------------


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_successful_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="Task done!", messages=[], stop_reason="completed",
        ))
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent(
                "t1", "do task", "label",
                {"channel": "cli", "chat_id": "direct"},
                SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic()),
                _runtime(),
            )
            mock_announce.assert_called_once()
            assert mock_announce.call_args.args[-2] == "ok"

    @pytest.mark.asyncio
    async def test_tool_error_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content=None, messages=[], stop_reason="tool_error",
            tool_events=[{"name": "read_file", "status": "error", "detail": "not found"}],
        ))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent(
                "t1", "do task", "label",
                {"channel": "cli", "chat_id": "direct"}, status, _runtime(),
            )
            assert mock_announce.call_args.args[-2] == "error"

    @pytest.mark.asyncio
    async def test_exception_run(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock) as mock_announce:
            await sm._run_subagent(
                "t1", "do task", "label",
                {"channel": "cli", "chat_id": "direct"}, status, _runtime(),
            )
            assert status.phase == "error"
            assert "LLM down" in status.error
            assert mock_announce.call_args.args[-2] == "error"

    @pytest.mark.asyncio
    async def test_status_updated_on_success(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="ok", messages=[], stop_reason="completed",
        ))
        status = SubagentStatus(task_id="t1", label="label", task_description="do task", started_at=time.monotonic())
        with patch.object(sm, "_announce_result", new_callable=AsyncMock):
            await sm._run_subagent(
                "t1", "do task", "label",
                {"channel": "cli", "chat_id": "direct"}, status, _runtime(),
            )
            assert status.phase == "done"
            assert status.stop_reason == "completed"


# ---------------------------------------------------------------------------
# _announce_result
# ---------------------------------------------------------------------------


class TestAnnounceResult:
    @pytest.mark.asyncio
    async def test_publishes_inbound_message(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result text",
            {"channel": "cli", "chat_id": "direct"}, "ok",
        )

        assert len(published) == 1
        msg = published[0]
        assert msg.channel == "system"
        assert msg.sender_id == "subagent"
        assert msg.metadata["injected_event"] == "subagent_result"
        assert msg.metadata["subagent_task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_session_key_override(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "telegram", "chat_id": "123", "session_key": "s1"}, "ok",
        )

        assert published[0].session_key_override == "s1"

    @pytest.mark.asyncio
    async def test_session_key_override_fallback(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "telegram", "chat_id": "123"}, "ok",
        )

        assert published[0].session_key_override == "telegram:123"

    @pytest.mark.asyncio
    async def test_ok_status_text(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "cli", "chat_id": "direct"}, "ok",
        )

        assert "completed successfully" in published[0].content

    @pytest.mark.asyncio
    async def test_error_status_text(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "error details",
            {"channel": "cli", "chat_id": "direct"}, "error",
        )

        assert "failed" in published[0].content

    @pytest.mark.asyncio
    async def test_origin_message_id_in_metadata(self, tmp_path):
        sm = _manager(tmp_path)
        published = []
        sm.bus.publish_inbound = AsyncMock(side_effect=lambda msg: published.append(msg))

        await sm._announce_result(
            "t1", "label", "task", "result",
            {"channel": "cli", "chat_id": "direct"}, "ok",
            origin_message_id="msg-123",
        )

        assert published[0].metadata["origin_message_id"] == "msg-123"


# ---------------------------------------------------------------------------
# _format_partial_progress
# ---------------------------------------------------------------------------


class TestFormatPartialProgress:
    def _make_result(self, tool_events=None, error=None):
        return MagicMock(tool_events=tool_events or [], error=error)

    def test_completed_only(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "ok", "detail": "file content"},
            {"name": "exec", "status": "ok", "detail": "output"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Completed steps:" in text
        assert "read_file" in text
        assert "exec" in text

    def test_failure_only(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "error", "detail": "not found"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Failure:" in text
        assert "not found" in text

    def test_completed_and_failure(self):
        result = self._make_result(tool_events=[
            {"name": "read_file", "status": "ok", "detail": "content"},
            {"name": "exec", "status": "error", "detail": "timeout"},
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "Completed steps:" in text
        assert "Failure:" in text

    def test_limited_to_last_three(self):
        result = self._make_result(tool_events=[
            {"name": f"tool_{i}", "status": "ok", "detail": f"result_{i}"}
            for i in range(5)
        ])
        text = SubagentManager._format_partial_progress(result)
        assert "tool_2" in text
        assert "tool_3" in text
        assert "tool_4" in text
        assert "tool_0" not in text
        assert "tool_1" not in text

    def test_error_without_failure_event(self):
        result = self._make_result(
            tool_events=[{"name": "read_file", "status": "ok", "detail": "ok"}],
            error="Something went wrong",
        )
        text = SubagentManager._format_partial_progress(result)
        assert "Something went wrong" in text

    def test_empty_events_with_error(self):
        result = self._make_result(error="Total failure")
        text = SubagentManager._format_partial_progress(result)
        assert "Total failure" in text

    def test_empty_no_error_returns_fallback(self):
        result = self._make_result()
        text = SubagentManager._format_partial_progress(result)
        assert "Error" in text


# ---------------------------------------------------------------------------
# cancel_by_session
# ---------------------------------------------------------------------------


class TestCancelBySession:
    @pytest.mark.asyncio
    async def test_cancels_running_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        runtime = _runtime()
        await sm.spawn("task1", runtime=runtime, session_key="s1")
        await sm.spawn("task2", runtime=runtime, session_key="s1")
        assert len(sm._session_tasks.get("s1", set())) == 2

        count = await sm.cancel_by_session("s1")
        assert count == 2
        block.set()
        await _drain_subagent_tasks(sm)

    @pytest.mark.asyncio
    async def test_no_tasks_returns_zero(self, tmp_path):
        sm = _manager(tmp_path)
        count = await sm.cancel_by_session("nonexistent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_already_done_not_counted(self, tmp_path):
        sm = _manager(tmp_path)
        sm.runner.run = AsyncMock(return_value=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("task1", runtime=_runtime(), session_key="s1")
        await _drain_subagent_tasks(sm)

        count = await sm.cancel_by_session("s1")
        assert count == 0


# ---------------------------------------------------------------------------
# get_running_count / get_running_count_by_session
# ---------------------------------------------------------------------------


class TestRunningCounts:
    @pytest.mark.asyncio
    async def test_running_count_zero(self, tmp_path):
        sm = _manager(tmp_path)
        assert sm.get_running_count() == 0

    @pytest.mark.asyncio
    async def test_running_count_tracks_tasks(self, tmp_path):
        sm = _manager(tmp_path)
        block = asyncio.Event()
        async def _slow_run(spec):
            await block.wait()
            return AgentRunResult(final_content="done", messages=[], stop_reason="completed")
        sm.runner.run = _slow_run

        runtime = _runtime()
        await sm.spawn("t1", runtime=runtime, session_key="s1")
        await sm.spawn("t2", runtime=runtime, session_key="s1")
        assert sm.get_running_count() == 2
        assert sm.get_running_count_by_session("s1") == 2

        block.set()
        await _drain_subagent_tasks(sm)
        assert sm.get_running_count() == 0

    @pytest.mark.asyncio
    async def test_running_count_by_session_nonexistent(self, tmp_path):
        sm = _manager(tmp_path)
        assert sm.get_running_count_by_session("nonexistent") == 0


# ---------------------------------------------------------------------------
# _SubagentHook
# ---------------------------------------------------------------------------


class TestSubagentHook:
    @pytest.mark.asyncio
    async def test_before_execute_tools_logs(self, tmp_path):
        hook = _SubagentHook("t1")
        tool_call = MagicMock()
        tool_call.name = "read_file"
        tool_call.arguments = {"path": "/tmp/test"}
        ctx = _make_hook_context(tool_calls=[tool_call])
        result = await hook.before_execute_tools(ctx)
        assert result is None
        assert ctx.tool_calls == [tool_call]

    @pytest.mark.asyncio
    async def test_after_iteration_updates_status(self):
        status = SubagentStatus(
            task_id="t1", label="test", task_description="do", started_at=time.monotonic(),
        )
        hook = _SubagentHook("t1", status)
        ctx = _make_hook_context(
            iteration=3,
            tool_events=[{"name": "read_file", "status": "ok", "detail": ""}],
            usage={"prompt_tokens": 100},
        )
        await hook.after_iteration(ctx)
        assert status.iteration == 3
        assert len(status.tool_events) == 1
        assert status.usage == {"prompt_tokens": 100}

    @pytest.mark.asyncio
    async def test_after_iteration_no_status_noop(self):
        hook = _SubagentHook("t1", status=None)
        ctx = _make_hook_context(iteration=5)
        result = await hook.after_iteration(ctx)
        assert result is None
        assert ctx.iteration == 5

    @pytest.mark.asyncio
    async def test_after_iteration_sets_error(self):
        status = SubagentStatus(
            task_id="t1", label="test", task_description="do", started_at=time.monotonic(),
        )
        hook = _SubagentHook("t1", status)
        ctx = _make_hook_context(error="something broke")
        await hook.after_iteration(ctx)
        assert status.error == "something broke"
