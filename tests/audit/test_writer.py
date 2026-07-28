import asyncio
import json
from datetime import UTC, datetime

from nanobot.audit.schema import TraceCreatedDraft, TurnInputPayloadDraft
from nanobot.audit.writer import AuditWriter, CommitItem


def _event(index: int) -> TraceCreatedDraft:
    return TraceCreatedDraft(
        event_id=f"e{index}",
        occurred_at=datetime.now(UTC),
        monotonic_ns=index,
        trace_id=f"t{index}",
        turn_id=None,
        run_id=None,
        parent_run_id=None,
        resumed_from_run_id=None,
        caused_by_event_id=None,
        model_call_id=None,
        attempt_id=None,
        tool_call_id=None,
        checkpoint_id=None,
        goal_id=None,
        delivery_id=None,
        session_key="cli:direct",
        source_type="cli",
        source_metadata={},
        iteration=None,
        actor_type="user",
        creation_reason="message",
    )


def _payload(index: int) -> TurnInputPayloadDraft:
    return TurnInputPayloadDraft(
        payload_id=f"d{index}",
        event_id=f"e{index}",
        content={
            "role": "user",
            "content": f"message {index}",
            "media_refs": [],
            "source_message_id": None,
        },
    )


def _item(index: int, *, payload: bool = True, critical: bool = True) -> CommitItem:
    return CommitItem(
        event=_event(index),
        payload=_payload(index) if payload else None,
        estimated_bytes=512,
        critical=critical,
    )


async def test_epoch_fsyncs_payload_before_event_and_catalog(tmp_path) -> None:
    calls: list[str] = []
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    writer.set_fsync_observer(calls.append)

    receipt = await writer.submit(_item(1))
    await writer.close()

    assert receipt is not None
    assert calls.index("payload.fsync") < calls.index("event.append")
    assert calls.index("event.fsync") < calls.index("catalog.epoch_committed")
    assert calls.index("catalog.epoch_committed") < calls.index("catalog.fsync")


async def test_concurrent_submissions_have_one_sequence_chain(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await asyncio.gather(*(writer.submit(_item(index, payload=False)) for index in range(1, 51)))
    event_path = writer.event_segment_path
    await writer.close()

    records = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [record["segment_sequence"] for record in records] == list(range(1, 51))
    for previous, current in zip(records, records[1:]):
        assert current["previous_event_hash"] == previous["event_hash"]


async def test_noncritical_submit_returns_after_queue_acceptance(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    assert await writer.submit(_item(1, critical=False)) is None
    await writer.close()
    assert writer.last_committed_epoch == 1
