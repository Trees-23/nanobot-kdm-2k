"""Contract tests for the durable unified subagent task state machine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from nanobot.session.subagent_tasks import (
    InvalidSubagentTaskTransitionError,
    SubagentDeliveryPhase,
    SubagentTaskDTO,
    SubagentTaskStatus,
    SubagentTaskStore,
    SubagentTerminationState,
    TaskResult,
    TaskSpec,
)


@pytest.mark.asyncio
async def test_create_persists_revision_and_lifecycle_outbox_atomically(tmp_path):
    store = SubagentTaskStore(tmp_path)

    task = await store.create(
        task_id="task-a",
        owner_session_key="websocket:chat-a",
        owner_run_id="run-owner",
        label="bounded label",
    )

    restored = SubagentTaskStore(tmp_path).load("task-a")
    assert restored == task
    assert task.revision == 1
    assert task.lifecycle_outbox[0].idempotency_key == "task-a:1:subagent_created"


@pytest.mark.asyncio
async def test_status_transition_is_monotonic_and_duplicate_is_idempotent(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")

    queued = await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    duplicate = await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    running = await store.transition_status("task-a", SubagentTaskStatus.RUNNING)

    assert queued.revision == 2
    assert duplicate.revision == 2
    assert running.revision == 3
    assert running.started_at is not None
    assert [event.revision for event in running.lifecycle_outbox] == [1, 2, 3]


@pytest.mark.asyncio
async def test_illegal_transition_fails_without_rewriting_record(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")

    with pytest.raises(InvalidSubagentTaskTransitionError, match="created -> running"):
        await store.transition_status("task-a", SubagentTaskStatus.RUNNING)

    restored = store.load("task-a")
    assert restored is not None
    assert restored.status == SubagentTaskStatus.CREATED
    assert restored.revision == 1


@pytest.mark.asyncio
async def test_late_result_cannot_override_terminal_status(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)
    failed = await store.transition_status("task-a", SubagentTaskStatus.FAILED, error="failed")

    late = await store.transition_status("task-a", SubagentTaskStatus.SUCCEEDED)

    assert late.status == SubagentTaskStatus.FAILED
    assert late.revision == failed.revision
    assert late.finished_at == failed.finished_at


@pytest.mark.asyncio
async def test_result_claim_and_delivery_have_exactly_once_effect(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    await store.mark_result_ready("task-a")

    claimed, changed = await store.claim_result("task-a", "run-owner")
    duplicate, duplicate_changed = await store.claim_result("task-a", "run-owner")
    delivered = await store.mark_delivered("task-a")
    delivered_duplicate = await store.mark_delivered("task-a")

    assert changed is True
    assert duplicate_changed is False
    assert duplicate.revision == claimed.revision
    assert delivered.delivery.phase == SubagentDeliveryPhase.DELIVERED
    assert delivered_duplicate.revision == delivered.revision


def test_legacy_record_with_missing_fields_is_explicitly_degraded(tmp_path):
    store = SubagentTaskStore(tmp_path)
    path = store._path("legacy-a")
    path.write_text(
        json.dumps({"task_id": "legacy-a", "owner_session_key": "test:a"}),
        encoding="utf-8",
    )

    task = store.load("legacy-a")

    assert task is not None
    assert task.schema_version == 2
    assert task.revision == 1
    assert task.legacy_inferred is True
    assert task.delivery.phase == SubagentDeliveryPhase.NOT_READY


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        from nanobot.session.subagent_tasks import SubagentTask

        SubagentTask(
            revision=1,
            task_id="task-a",
            owner_session_key="test:a",
            created_at=datetime(2026, 8, 3),
        )


def test_non_utc_timestamp_is_rejected():
    with pytest.raises(ValidationError, match="must use UTC"):
        from nanobot.session.subagent_tasks import SubagentTask

        SubagentTask(
            revision=1,
            task_id="task-a",
            owner_session_key="test:a",
            created_at=datetime(2026, 8, 3, tzinfo=timezone(timedelta(hours=8))),
        )


@pytest.mark.asyncio
async def test_public_dto_exposes_only_safe_usage_and_budget_fields(tmp_path):
    store = SubagentTaskStore(tmp_path)
    task = await store.create(
        task_id="task-a",
        owner_session_key="test:a",
        label="safe label",
    )
    task.executor = {"pid": 123, "secret": "must-not-leak"}
    task.usage = {"prompt_tokens": 100, "provider_secret": "must-not-leak"}
    task.budget = {"max_cost_usd": 1, "internal_reservation": "must-not-leak"}

    payload = SubagentTaskDTO.from_task(task).model_dump(mode="json")

    assert payload["schema_version"] == 1
    assert payload["label"] == "safe label"
    assert "executor" not in payload
    assert payload["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": None,
    }
    assert payload["budget"]["max_cost_usd"] == 1
    assert "provider_secret" not in payload["usage"]
    assert "internal_reservation" not in payload["budget"]
    assert "lifecycle_outbox" not in payload
    assert "owner_session_key" not in payload


def test_test_clock_is_utc_aware():
    assert datetime.now(timezone.utc).utcoffset() is not None


def test_legacy_task_and_result_adapters_do_not_invent_evidence():
    spec = TaskSpec.from_legacy(" inspect the repository ")
    result = TaskResult.from_output("plain result", SubagentTaskStatus.SUCCEEDED)

    assert spec.objective == "inspect the repository"
    assert result.summary == "plain result"
    assert result.evidence == []
    assert result.files_changed == []
    assert result.tests == []


def test_structured_task_result_is_bounded_and_validated():
    result = TaskResult.from_output(
        json.dumps({
            "schema_version": 1,
            "status": "failed",
            "summary": "done",
            "evidence": ["audit event"],
            "tests": ["pytest focused"],
        }),
        SubagentTaskStatus.SUCCEEDED,
    )

    assert result.status == SubagentTaskStatus.SUCCEEDED
    assert result.evidence == ["audit event"]
    assert result.tests == ["pytest focused"]


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (TaskSpec, {"objective": "bounded", "constraints": ["x" * 1001]}),
        (TaskSpec, {"objective": "bounded", "dependencies": ["x" * 257]}),
        (
            TaskResult,
            {"status": "succeeded", "summary": "bounded", "evidence": ["x" * 2001]},
        ),
    ],
)
def test_structured_protocol_rejects_unbounded_list_items(model, payload):
    with pytest.raises(ValidationError, match="string_too_long"):
        model.model_validate(payload)


@pytest.mark.asyncio
async def test_termination_failed_atomically_marks_task_lost(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)

    lost = await store.record_termination(
        "task-a",
        SubagentTerminationState.TERMINATION_FAILED,
        evidence={"exit_observed": False},
    )

    assert lost.status == SubagentTaskStatus.LOST
    assert lost.termination.state == SubagentTerminationState.TERMINATION_FAILED
    assert lost.termination.evidence == {"exit_observed": False}
    assert lost.finished_at is not None
    assert [event.event_type for event in lost.lifecycle_outbox[-2:]] == [
        "subagent_termination_decided",
        "subagent_lost",
    ]


@pytest.mark.asyncio
async def test_cancel_request_has_distinct_lifecycle_evidence(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)

    requested = await store.record_termination(
        "task-a",
        SubagentTerminationState.CANCEL_REQUESTED,
        evidence={"request_sent": True},
    )
    waiting = await store.record_termination(
        "task-a",
        SubagentTerminationState.GRACE_WAITING,
        evidence={"request_sent": True},
    )

    assert requested.lifecycle_outbox[-1].event_type == "subagent_cancel_requested"
    assert waiting.lifecycle_outbox[-1].event_type == "subagent_termination_decided"


@pytest.mark.asyncio
async def test_late_termination_cannot_override_success(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="task-a", owner_session_key="test:a")
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)
    await store.transition_status("task-a", SubagentTaskStatus.RUNNING)
    succeeded = await store.transition_status("task-a", SubagentTaskStatus.SUCCEEDED)

    late = await store.record_termination(
        "task-a",
        SubagentTerminationState.TERMINATION_FAILED,
        evidence={"exit_observed": False},
    )

    assert late.status == SubagentTaskStatus.SUCCEEDED
    assert late.termination.state == SubagentTerminationState.NONE
    assert late.revision == succeeded.revision


@pytest.mark.asyncio
async def test_restart_recovery_is_idempotent_for_queued_and_running_tasks(tmp_path):
    store = SubagentTaskStore(tmp_path)
    await store.create(task_id="queued", owner_session_key="test:a")
    await store.transition_status("queued", SubagentTaskStatus.QUEUED)
    await store.create(task_id="running", owner_session_key="test:a")
    await store.transition_status("running", SubagentTaskStatus.QUEUED)
    await store.transition_status("running", SubagentTaskStatus.RUNNING)

    restarted = SubagentTaskStore(tmp_path)
    assert await restarted.recover_runtime(set()) == 2
    assert await restarted.recover_runtime(set()) == 0

    for task_id in ("queued", "running"):
        task = restarted.load(task_id)
        assert task is not None
        assert task.status == SubagentTaskStatus.LOST
        assert task.termination.state == SubagentTerminationState.TERMINATION_FAILED
        assert task.termination.evidence == {
            "executor_present": False,
            "exit_observed": False,
        }
        assert [event.event_type for event in task.lifecycle_outbox[-3:]] == [
            "subagent_recovered",
            "subagent_termination_decided",
            "subagent_lost",
        ]
