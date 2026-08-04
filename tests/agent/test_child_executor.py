"""Real process tests for the killable Child executor supervisor."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import pytest

from nanobot.agent.child_executor import MAX_IPC_FRAME_BYTES, ChildExit, ProcessChildExecutor
from nanobot.agent.child_worker import build_child_config_snapshot
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.providers.factory import make_provider
from nanobot.utils.llm_runtime import LLMRuntime

pytestmark = pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="Linux /proc required")


def _executor() -> ProcessChildExecutor:
    return ProcessChildExecutor(worker_module="tests.agent.fixtures.child_executor_worker")


class _BehaviorExecutor(ProcessChildExecutor):
    def __init__(self, behavior: str, *, fail_force_kill: bool = False) -> None:
        super().__init__(worker_module="tests.agent.fixtures.child_executor_worker")
        self.behavior = behavior
        self.fail_force_kill = fail_force_kill

    async def start(self, _payload: dict[str, Any]):
        return await super().start({"behavior": self.behavior})

    async def force_kill(self, handle, **kwargs) -> ChildExit:
        if not self.fail_force_kill:
            return await super().force_kill(handle, **kwargs)
        return ChildExit(
            returncode=0,
            result=None,
            exit_observed=False,
            reaped=False,
            descendants_cleared=False,
            forced=True,
            reason="injected_termination_failure",
        )


def _runtime(config: Config) -> LLMRuntime:
    provider = make_provider(config)
    return LLMRuntime.capture(provider, "worker-model", context_window_tokens=4096)


async def _wait_for_handle(manager: SubagentManager, task_id: str):
    for _ in range(100):
        handle = manager._executor_handles.get(task_id)
        if handle is not None:
            return handle
        await asyncio.sleep(0.01)
    raise AssertionError("process child handle was not registered")


class _ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps({
            "id": "child-worker-response",
            "object": "chat.completion",
            "created": 0,
            "model": "worker-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "worker completed"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: Any) -> None:
        return


@pytest.mark.asyncio
async def test_worker_natural_exit_is_observed_and_reaped() -> None:
    executor = _executor()
    handle = await executor.start({"behavior": "exit"})

    exited = await executor.wait(handle, 2)

    assert exited is not None
    assert exited.returncode == 0
    assert exited.result == {"status": "ok"}
    assert exited.termination_confirmed is True
    assert handle.process.returncode == 0


@pytest.mark.asyncio
async def test_oversized_start_frame_is_rejected_before_process_creation(monkeypatch) -> None:
    executor = _executor()

    async def unexpected_process_start(*_args, **_kwargs):
        raise AssertionError("oversized IPC payload must not start a worker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_process_start)

    with pytest.raises(ValueError, match="1 MiB"):
        await executor.start({"value": "x" * MAX_IPC_FRAME_BYTES})

    assert executor._handles == {}


@pytest.mark.asyncio
async def test_start_write_failure_kills_and_reaps_created_worker(monkeypatch) -> None:
    executor = _executor()
    created: list[asyncio.subprocess.Process] = []
    create_process = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        process = await create_process(*args, **kwargs)
        created.append(process)
        return process

    async def fail_write(*_args, **_kwargs):
        raise BrokenPipeError("injected start write failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)
    monkeypatch.setattr(executor, "_write", fail_write)

    with pytest.raises(BrokenPipeError, match="injected"):
        await executor.start({"behavior": "stubborn"})

    assert len(created) == 1
    assert created[0].returncode is not None
    assert executor._handles == {}


@pytest.mark.asyncio
async def test_worker_does_not_inherit_unrelated_parent_secrets(monkeypatch) -> None:
    monkeypatch.setenv("CHILD_EXECUTOR_SECRET", "must-not-cross-process-boundary")
    executor = _executor()
    handle = await executor.start({"behavior": "echo_env"})

    exited = await executor.wait(handle, 2)

    assert exited is not None
    assert exited.result == {"secret": None}


@pytest.mark.asyncio
async def test_cooperative_cancel_does_not_send_process_signals(monkeypatch) -> None:
    executor = _executor()
    handle = await executor.start({"behavior": "cooperative"})
    signals: list[object] = []
    monkeypatch.setattr("nanobot.agent.child_executor.os.killpg", lambda *_args: signals.append(_args))

    await executor.request_cancel(handle)
    exited = await executor.wait(handle, 2)

    assert exited is not None
    assert exited.result == {"status": "cancelled", "stop_reason": "cancelled"}
    assert exited.forced is False
    assert signals == []


@pytest.mark.asyncio
async def test_stubborn_worker_and_descendant_are_force_killed() -> None:
    executor = _executor()
    handle = await executor.start({"behavior": "descendant"})
    await executor.request_cancel(handle)
    assert await executor.wait(handle, 0.05) is None

    exited = await executor.force_kill(handle, term_grace_seconds=0.05)

    assert exited.termination_confirmed is True
    assert exited.forced is True
    assert exited.term_sent is True
    assert exited.reason == "force_killed"
    assert handle.process.returncode is not None


@pytest.mark.asyncio
async def test_identity_mismatch_refuses_to_signal_process_group() -> None:
    executor = _executor()
    handle = await executor.start({"behavior": "stubborn"})
    original_identity = handle.identity
    handle.identity = replace(original_identity, proc_start_ticks="reused")

    refused = await executor.force_kill(handle, term_grace_seconds=0.01)

    assert refused.termination_confirmed is False
    assert refused.reason == "identity_mismatch"
    assert handle.process.returncode is None
    handle.identity = original_identity
    cleaned = await executor.force_kill(handle, term_grace_seconds=0.01)
    assert cleaned.termination_confirmed is True


def _process_manager(
    tmp_path,
    executor: ProcessChildExecutor,
) -> tuple[SubagentManager, LLMRuntime]:
    config = Config.model_validate({
        "agents": {
            "defaults": {
                "workspace": str(tmp_path),
                "model": "worker-model",
                "provider": "worker_test",
            },
        },
        "providers": {
            "worker_test": {
                "apiKey": "pipe-only-test-key",
                "apiBase": "http://127.0.0.1:9/v1",
            },
        },
        "audit": {"mode": "off"},
    })
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        child_executor=executor,
        child_runtime_config={},
    )
    return manager, _runtime(config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "expected_termination"),
    [("cooperative", "cooperatively_exited"), ("descendant", "force_killed")],
)
async def test_manager_preserves_confirmed_process_termination_state(
    tmp_path,
    behavior: str,
    expected_termination: str,
) -> None:
    executor = _BehaviorExecutor(behavior)
    manager, runtime = _process_manager(tmp_path, executor)
    spawned = await manager.spawn("bounded child", runtime=runtime, structured=True)
    task_id = spawned["task_id"]
    handle = await _wait_for_handle(manager, task_id)

    assert await manager.timeout_tasks([task_id], grace_seconds=0.05) is True
    await asyncio.gather(*list(manager._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)

    status = manager.get_status(task_id)
    assert status is not None
    assert status.terminal_status == "timed_out"
    assert status.termination_state == expected_termination
    assert handle.process.returncode is not None
    await manager.close()


@pytest.mark.asyncio
async def test_optional_termination_failure_notifies_once_and_rejects_late_success(tmp_path) -> None:
    executor = _BehaviorExecutor("late_success", fail_force_kill=True)
    manager, runtime = _process_manager(tmp_path, executor)
    spawned = await manager.spawn(
        "optional late child",
        runtime=runtime,
        session_key="test:optional",
        structured=True,
    )
    task_id = spawned["task_id"]
    await _wait_for_handle(manager, task_id)

    assert await manager.timeout_tasks([task_id], grace_seconds=0.01) is False
    assert manager.bus.inbound_size == 1
    await manager._mark_termination_failed(task_id)
    assert manager.bus.inbound_size == 1

    await asyncio.gather(*list(manager._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)
    status = manager.get_status(task_id)
    assert status is not None
    assert status.required is False
    assert status.terminal_status == "lost"
    assert status.termination_state == "termination_failed"
    assert status.error == "child termination could not be confirmed"
    assert manager.bus.inbound_size == 1
    await manager.close()


@pytest.mark.asyncio
async def test_subagent_worker_rehydrates_runtime_and_returns_result(tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    config = Config.model_validate({
        "agents": {
            "defaults": {
                "workspace": str(tmp_path),
                "model": "worker-model",
                "provider": "worker_test",
                "maxToolIterations": 2,
            },
        },
        "providers": {
            "worker_test": {
                "apiKey": "pipe-only-test-key",
                "apiBase": f"http://{host}:{port}/v1",
            },
        },
        "audit": {"mode": "off"},
    })
    provider = make_provider(config)
    runtime = LLMRuntime.capture(
        provider,
        "worker-model",
        context_window_tokens=4096,
    )
    bus = MessageBus()
    manager = SubagentManager(
        workspace=tmp_path,
        bus=bus,
        max_tool_result_chars=16_000,
        child_executor=ProcessChildExecutor(),
        child_runtime_config=build_child_config_snapshot(config),
        child_audit_root=str(tmp_path / "audit"),
    )
    try:
        spawned = await manager.spawn("Return the deterministic answer.", runtime=runtime, structured=True)
        tasks = list(manager._running_tasks.values())
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        message = await asyncio.wait_for(bus.consume_inbound(), 1)

        assert "worker completed" in message.content
        status = manager.get_status(spawned["task_id"])
        assert status is not None
        assert status.terminal_status == "succeeded"
        durable = manager._task_store.load(spawned["task_id"])
        assert durable is not None
        assert str(durable.phase) == "final_response"
        assert any(
            event.summary.get("task_phase") == "final_response"
            for event in durable.lifecycle_outbox
        )
        assert manager._executor_handles == {}
    finally:
        await manager.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
