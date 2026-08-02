"""Real process tests for the killable Child executor supervisor."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from nanobot.agent.child_executor import ProcessChildExecutor

pytestmark = pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="Linux /proc required")


def _executor() -> ProcessChildExecutor:
    return ProcessChildExecutor(worker_module="tests.agent.fixtures.child_executor_worker")


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
async def test_cooperative_cancel_does_not_send_process_signals(monkeypatch) -> None:
    executor = _executor()
    handle = await executor.start({"behavior": "cooperative"})
    signals: list[object] = []
    monkeypatch.setattr("nanobot.agent.child_executor.os.killpg", lambda *_args: signals.append(_args))

    await executor.request_cancel(handle)
    exited = await executor.wait(handle, 2)

    assert exited is not None
    assert exited.result == {"status": "cancelled"}
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
