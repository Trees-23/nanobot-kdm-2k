from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nanobot.audit.schema import (
    CATALOG_MODELS,
    EVENT_DRAFT_MODELS,
    EVENT_MODELS,
    PAYLOAD_DRAFT_MODELS,
    CatalogRecordBase,
    ToolFinishedDraft,
    ToolFinishedEvent,
    audit_event_adapter,
    audit_event_draft_adapter,
    materialize_event,
)
from nanobot.audit.types import CatalogRecordType, EventType, PayloadKind


def _common_event(event_type: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": "e1",
        "event_type": event_type,
        "occurred_at": datetime.now(UTC),
        "monotonic_ns": 1,
        "trace_id": "t1",
        "turn_id": "u1",
        "run_id": "r1",
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


def test_tool_finished_requires_tool_identity() -> None:
    with pytest.raises(ValidationError):
        ToolFinishedEvent.model_validate(_common_event("tool_finished"))


def test_event_adapter_round_trips_unknown_future_field() -> None:
    raw = {
        **_common_event("tool_finished"),
        "tool_call_id": "call-1",
        "tool_name": "exec",
        "elapsed_ms": 12,
        "status": "ok",
        "process_instance_id": "p1",
        "segment_id": "s1",
        "segment_sequence": 1,
        "durability_epoch": 1,
        "previous_event_hash": None,
        "payload_id": None,
        "payload_sha256": None,
        "event_hash": "sha256:abc",
        "future_field": {"kept": True},
    }
    event = audit_event_adapter.validate_python(raw)
    assert event.model_dump()["future_field"] == {"kept": True}


def test_draft_rejects_writer_owned_fields() -> None:
    raw = {
        **_common_event("tool_finished"),
        "tool_call_id": "call-1",
        "tool_name": "exec",
        "elapsed_ms": 12,
        "status": "ok",
        "segment_sequence": 4,
    }
    with pytest.raises(ValidationError, match="segment_sequence"):
        audit_event_draft_adapter.validate_python(raw)


def test_materialize_event_adds_persistence_fields() -> None:
    draft = ToolFinishedDraft.model_validate(
        {
            **_common_event("tool_finished"),
            "tool_call_id": "call-1",
            "tool_name": "exec",
            "elapsed_ms": 12,
            "status": "ok",
        }
    )
    event = materialize_event(
        draft,
        process_instance_id="p1",
        segment_id="s1",
        segment_sequence=1,
        durability_epoch=1,
        previous_event_hash=None,
        payload_id=None,
        payload_sha256=None,
        event_hash="sha256:abc",
    )
    assert isinstance(event, ToolFinishedEvent)
    assert event.segment_sequence == 1


def test_rejects_naive_event_timestamp() -> None:
    raw = {
        **_common_event("tool_finished"),
        "occurred_at": datetime.now(),
        "tool_call_id": "call-1",
        "tool_name": "exec",
        "elapsed_ms": 12,
        "status": "ok",
    }
    with pytest.raises(ValidationError, match="timezone-aware"):
        ToolFinishedDraft.model_validate(raw)


def test_every_closed_discriminator_has_a_model() -> None:
    assert set(EVENT_MODELS) == set(EventType)
    assert set(EVENT_DRAFT_MODELS) == set(EventType)
    assert set(PAYLOAD_DRAFT_MODELS) == set(PayloadKind)
    assert set(CATALOG_MODELS) == set(CatalogRecordType)


def test_catalog_base_is_persisted_only() -> None:
    assert "catalog_record_hash" in CatalogRecordBase.model_fields
