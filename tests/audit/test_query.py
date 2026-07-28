from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nanobot.audit.query import AuditQuery, TraceFilter
from nanobot.audit.schema import RunFinishedDraft, RunStartedDraft
from nanobot.audit.writer import AuditWriter, CommitItem
from tests.audit.test_writer import _item


def common(event_id: str, event_type: str, *, turn: str, run: str, offset: int):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": datetime.now(UTC) + timedelta(microseconds=offset),
        "monotonic_ns": offset,
        "trace_id": "trace",
        "turn_id": turn,
        "run_id": run,
        "parent_run_id": None,
        "resumed_from_run_id": None,
        "caused_by_event_id": None,
        "model_call_id": None,
        "attempt_id": None,
        "tool_call_id": None,
        "checkpoint_id": None,
        "goal_id": None,
        "delivery_id": None,
        "session_key": "cli:direct",
        "source_type": "user",
        "source_metadata": {},
        "iteration": None,
    }


async def write_run_fixture(root) -> None:
    writer = AuditWriter(root, fsync_interval_seconds=0.01)
    await writer.start()
    rows = [
        RunStartedDraft.model_validate(common("e1", "run_started", turn="u1", run="r1", offset=1)),
        RunStartedDraft.model_validate({
            **common("e2", "run_started", turn="u1", run="r2", offset=2),
            "parent_run_id": "r1",
        }),
        RunFinishedDraft.model_validate({
            **common("e3", "run_finished", turn="u1", run="r2", offset=3),
            "status": "failed",
            "stop_reason": "model_error",
        }),
        RunFinishedDraft.model_validate({
            **common("e4", "run_finished", turn="u1", run="r1", offset=4),
            "status": "succeeded",
            "stop_reason": "completed",
        }),
        RunStartedDraft.model_validate({
            **common("e5", "run_started", turn="u2", run="r3", offset=5),
            "resumed_from_run_id": "r1",
        }),
        RunFinishedDraft.model_validate({
            **common("e6", "run_finished", turn="u2", run="r3", offset=6),
            "status": "succeeded",
            "stop_reason": "completed",
        }),
    ]
    for event in rows:
        await writer.submit(CommitItem(event, None, 512, True))
    await writer.close()


async def test_trace_view_builds_parent_and_resume_tree(tmp_path) -> None:
    await write_run_fixture(tmp_path)

    view = AuditQuery.from_root(tmp_path).load_trace("trace")

    assert view.run_tree.roots[0].run_id == "r1"
    assert view.run_tree.roots[0].children[0].run_id == "r2"
    assert view.run_tree.resumptions[0].run_id == "r3"
    assert view.summary.turn_count == 2
    assert view.summary.run_count == 3
    assert view.integrity.status == "valid"


async def test_query_does_not_load_payloads_by_default(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    await writer.close()

    query = AuditQuery.from_root(tmp_path)
    assert query.load_trace("t1").payloads is None
    assert list(query.load_trace("t1", include_payloads=True).payloads) == ["d1"]


async def test_paginated_trace_cursor_is_stable_and_bounded(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    for index in range(1, 4):
        await writer.submit(_item(index, payload=False))
    await writer.close()
    query = AuditQuery.from_root(tmp_path)

    first = query.find_traces(TraceFilter(limit=2))
    repeated = query.find_traces(TraceFilter(limit=2))
    second = query.find_traces(TraceFilter(limit=2, cursor=first.next_cursor))

    assert first == repeated
    assert len(first.trace_ids) == 2
    assert len(second.trace_ids) == 1
    assert set(first.trace_ids).isdisjoint(second.trace_ids)
    with pytest.raises(ValidationError):
        TraceFilter(limit=501)
