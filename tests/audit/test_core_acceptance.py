from datetime import UTC, datetime

from nanobot.audit import AuditEmitter, AuditVerifier, AuditWriter
from nanobot.audit.redaction import AuditRedactor
from nanobot.audit.schema import EVENT_DRAFT_MODELS
from nanobot.audit.types import EventType


def _base(event_type: EventType) -> dict[str, object]:
    return {
        "event_id": f"event-{event_type.value}",
        "event_type": event_type.value,
        "occurred_at": datetime.now(UTC),
        "monotonic_ns": 1,
        "trace_id": "trace-1",
        "turn_id": "turn-1",
        "run_id": "run-1",
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
        "source_type": "cli",
        "source_metadata": {},
        "iteration": 1,
    }


def _event(event_type: EventType, **fields):
    return EVENT_DRAFT_MODELS[event_type].model_validate({**_base(event_type), **fields})


async def test_core_evidence_round_trip_has_valid_lifecycle(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    emitter = AuditEmitter(writer=writer, redactor=AuditRedactor())
    events = [
        _event(
            EventType.TRACE_CREATED,
            turn_id=None,
            run_id=None,
            iteration=None,
            actor_type="user",
            creation_reason="message",
        ),
        _event(EventType.TURN_STARTED, run_id=None, iteration=None),
        _event(EventType.RUN_STARTED),
        _event(EventType.ITERATION_STARTED),
        _event(
            EventType.MODEL_REQUEST_STARTED,
            model_call_id="model-call-1",
            requested_provider="test",
            requested_model="test-model",
        ),
        _event(
            EventType.MODEL_ATTEMPT_STARTED,
            model_call_id="model-call-1",
            attempt_id="attempt-1",
            attempt_ordinal=1,
            provider="test",
            model="test-model",
            input_variant="original",
        ),
        _event(
            EventType.MODEL_ATTEMPT_FINISHED,
            model_call_id="model-call-1",
            attempt_id="attempt-1",
            attempt_ordinal=1,
            provider="test",
            model="test-model",
            elapsed_ms=2,
            status="ok",
        ),
        _event(
            EventType.MODEL_RESPONSE_RECEIVED,
            model_call_id="model-call-1",
            finish_reason="tool_calls",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ),
        _event(EventType.TOOL_STARTED, tool_call_id="tool-1", tool_name="exec"),
        _event(
            EventType.TOOL_FINISHED,
            tool_call_id="tool-1",
            tool_name="exec",
            elapsed_ms=3,
            status="ok",
        ),
        _event(EventType.ITERATION_FINISHED, iteration_outcome="completed"),
        _event(EventType.RUN_FINISHED, status="succeeded", stop_reason="completed"),
        _event(
            EventType.TURN_RESPONSE_PREPARED,
            run_id=None,
            iteration=None,
            response_kind="assistant",
        ),
        _event(
            EventType.TURN_FINISHED,
            run_id=None,
            iteration=None,
            status="response_prepared",
        ),
    ]
    for event in events:
        result = await emitter.emit(event, critical=True)
        assert result.committed is True
    process_id = writer.process_id
    await writer.close()

    report = AuditVerifier(tmp_path).verify_process(process_id)
    assert report.status == "valid"
    assert report.error_codes == ()
