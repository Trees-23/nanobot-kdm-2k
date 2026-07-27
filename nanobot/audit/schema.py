"""Typed audit V1 event, payload, and catalog contracts."""

from __future__ import annotations

from datetime import datetime
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model, field_validator

from nanobot.audit.types import (
    CatalogRecordType,
    EventType,
    JsonValue,
    PayloadKind,
)


class _UtcModel(BaseModel):
    @field_validator("occurred_at", check_fields=False)
    @classmethod
    def _timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class AuditEventDraftBase(_UtcModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    event_id: str
    event_type: EventType
    occurred_at: datetime
    monotonic_ns: int
    trace_id: str | None
    turn_id: str | None
    run_id: str | None
    parent_run_id: str | None
    resumed_from_run_id: str | None
    caused_by_event_id: str | None
    model_call_id: str | None
    attempt_id: str | None
    tool_call_id: str | None
    checkpoint_id: str | None
    goal_id: str | None
    delivery_id: str | None
    session_key: str | None
    source_type: str | None
    source_metadata: dict[str, JsonValue]
    iteration: int | None


class AuditEventBase(AuditEventDraftBase):
    model_config = ConfigDict(extra="allow", use_enum_values=True)

    process_instance_id: str
    segment_id: str
    segment_sequence: int
    durability_epoch: int
    previous_event_hash: str | None
    payload_id: str | None
    payload_sha256: str | None
    event_hash: str


def _required(annotation: Any) -> tuple[Any, Any]:
    return annotation, ...


def _optional(annotation: Any, default: Any = None) -> tuple[Any, Any]:
    return annotation | None, default


_ID_FIELDS = {
    "T": "trace_id",
    "U": "turn_id",
    "R": "run_id",
    "M": "model_call_id",
    "A": "attempt_id",
    "C": "tool_call_id",
    "K": "checkpoint_id",
    "G": "goal_id",
    "D": "delivery_id",
}


def _ids(codes: str) -> dict[str, tuple[Any, Any]]:
    return {_ID_FIELDS[code]: _required(str) for code in codes}


_EVENT_SPECS: dict[EventType, tuple[str, dict[str, tuple[Any, Any]]]] = {
    EventType.PROCESS_INSTANCE_STARTED: ("", {
        "host_fingerprint": _required(str), "pid": _required(int), "boot_id": _required(str),
        "writer_version": _required(str),
    }),
    EventType.PROCESS_INSTANCE_CLOSED: ("", {
        "last_committed_epoch": _required(int), "shutdown_reason": _required(str),
        "status": (Literal["clean"], "clean"),
    }),
    EventType.SEGMENT_STARTED: ("", {
        "stream_kind": _required(str), "previous_segment_id": _optional(str),
        "previous_segment_hash": _optional(str), "previous_segment_record_count": _optional(int),
    }),
    EventType.SEGMENT_CLOSED: ("", {
        "close_reason": _required(str), "pre_close_record_count": _required(int),
        "pre_close_hash": _required(str), "status": (Literal["clean"], "clean"),
    }),
    EventType.AUDIT_DEGRADED: ("", {
        "failure_started_at": _required(datetime), "failure_last_seen_at": _required(datetime),
        "lost_item_count": _required(int), "failure_reason": _required(str),
        "affected_trace_ids": _required(list[str]),
    }),
    EventType.AUDIT_RECOVERED: ("", {
        "degraded_started_at": _required(datetime), "degraded_ended_at": _required(datetime),
        "last_committed_epoch": _required(int),
    }),
    EventType.TRACE_CREATED: ("T", {"actor_type": _required(str), "creation_reason": _required(str)}),
    EventType.TRACE_LINKED: ("TU", {
        "actor_type": _required(str), "link_reason": _required(str), "linked_source_id": _required(str),
    }),
    EventType.TURN_STARTED: ("TU", {}),
    EventType.INPUT_INJECTED: ("TUR", {
        "injection_source": _required(str), "target_run_id": _required(str),
    }),
    EventType.CANCEL_REQUESTED: ("TU", {
        "requested_by": _required(str), "target_run_ids": _required(list[str]),
    }),
    EventType.TURN_RESPONSE_PREPARED: ("TU", {"response_kind": _required(str)}),
    EventType.TURN_FINISHED: ("TU", {
        "status": _required(Literal["response_prepared", "command_completed", "suppressed", "failed"]),
    }),
    EventType.RETURNED_TO_CALLER: ("TU", {
        "status": _required(Literal["returned", "error"]),
    }),
    EventType.DELIVERY_ATTEMPTED: ("TUD", {
        "channel": _required(str), "attempt_ordinal": _required(int),
    }),
    EventType.DELIVERY_RETRY_SCHEDULED: ("TUD", {
        "failed_attempt_ordinal": _required(int), "delay_ms": _required(int),
        "policy_name": _required(str),
    }),
    EventType.DELIVERY_FINISHED: ("TUD", {
        "final_attempt_ordinal": _required(int),
        "status": _required(Literal["accepted_by_adapter", "failed", "cancelled", "suppressed"]),
        "remote_receipt_id": _optional(str),
    }),
    EventType.RUN_STARTED: ("TUR", {}),
    EventType.RUN_FINISHED: ("TUR", {
        "status": _required(Literal["succeeded", "failed", "cancelled", "interrupted", "exhausted"]),
        "stop_reason": _required(str),
    }),
    EventType.ORPHAN_RUN_SUSPECTED: ("TUR", {
        "owner_process_instance_id": _required(str), "evidence_kind": _required(str),
        "observed_at": _required(datetime),
    }),
    EventType.ORPHAN_RUN_DETECTED: ("TUR", {
        "owner_process_instance_id": _required(str), "evidence_kind": _required(str),
        "observed_at": _required(datetime),
    }),
    EventType.ORPHAN_MODEL_CALL_DETECTED: ("TURM", {
        "owner_process_instance_id": _required(str), "evidence_kind": _required(str),
    }),
    EventType.ORPHAN_TOOL_DETECTED: ("TURC", {
        "owner_process_instance_id": _required(str), "evidence_kind": _required(str),
    }),
    EventType.ITERATION_STARTED: ("TUR", {}),
    EventType.ITERATION_FINISHED: ("TUR", {
        "iteration_outcome": _required(Literal["continued", "completed", "failed", "cancelled"]),
    }),
    EventType.MODEL_REQUEST_STARTED: ("TURM", {
        "requested_provider": _required(str), "requested_model": _required(str),
    }),
    EventType.MODEL_FIRST_OUTPUT: ("TURM", {
        "output_kind": _required(str), "elapsed_ms": _required(int),
    }),
    EventType.MODEL_RESPONSE_RECEIVED: ("TURM", {
        "finish_reason": _required(str), "usage": _required(dict[str, int]),
        "status": (Literal["ok"], "ok"),
    }),
    EventType.MODEL_REQUEST_FAILED: ("TURM", {
        "status": _required(Literal["error", "timeout", "cancelled", "exhausted"]),
        "error_kind": _required(str), "attempt_count": _required(int),
    }),
    EventType.PROVIDER_ROUTE_DECISION: ("TURM", {
        "route_action": _required(str), "provider": _required(str), "model": _required(str),
        "input_variant": _required(str),
    }),
    EventType.MODEL_ATTEMPT_STARTED: ("TURMA", {
        "attempt_ordinal": _required(int), "provider": _required(str), "model": _required(str),
        "input_variant": _required(str),
    }),
    EventType.MODEL_ATTEMPT_FINISHED: ("TURMA", {
        "attempt_ordinal": _required(int), "provider": _required(str), "model": _required(str),
        "elapsed_ms": _required(int),
        "status": _required(Literal["ok", "error", "timeout", "cancelled"]),
    }),
    EventType.RETRY_SCHEDULED: ("TURM", {
        "prior_attempt_id": _required(str), "delay_ms": _required(int),
        "policy_name": _required(str),
    }),
    EventType.REASONING_SUMMARY_RECEIVED: ("TURM", {"reasoning_source": _required(str)}),
    EventType.TOOL_STARTED: ("TURC", {"tool_name": _required(str)}),
    EventType.TOOL_FINISHED: ("TURC", {
        "tool_name": _required(str), "elapsed_ms": _required(int),
        "status": _required(Literal["ok", "error", "cancelled", "timeout", "blocked"]),
    }),
    EventType.POLICY_BLOCKED: ("TUR", {
        "policy_name": _required(str), "policy_version": _required(str),
        "threshold": _required(int), "observed_count": _required(int),
    }),
    EventType.CONTINUATION_REQUESTED: ("TURM", {
        "continuation_reason": _required(Literal["length", "goal", "injection", "empty_response"]),
        "attempt_count": _required(int), "attempt_limit": _required(int),
    }),
    EventType.FINALIZATION_REQUESTED: ("TUR", {
        "finalization_reason": _required(str), "remaining_iteration_budget": _required(int),
    }),
    EventType.CHECKPOINT_WRITTEN: ("TURK", {
        "checkpoint_version": _required(int), "checkpoint_phase": _required(str),
    }),
    EventType.CHECKPOINT_RESTORED: ("TURK", {
        "source_run_id": _required(str), "checkpoint_version": _required(int),
    }),
    EventType.CHECKPOINT_CLEARED: ("TURK", {"clear_reason": _required(str)}),
    EventType.GOAL_CREATED: ("TUG", {"actor_type": _required(str), "goal_version": _required(int)}),
    EventType.GOAL_UPDATED: ("TUG", {
        "actor_type": _required(str), "previous_goal_version": _required(int),
        "goal_version": _required(int),
    }),
    EventType.GOAL_COMPLETED: ("TUG", {"actor_type": _required(str), "goal_version": _required(int)}),
    EventType.GOAL_BLOCKED: ("TUG", {
        "actor_type": _required(str), "blocker_kind": _required(str), "goal_version": _required(int),
    }),
    EventType.GOAL_CANCELLED: ("TUG", {"actor_type": _required(str), "goal_version": _required(int)}),
}


def _stem(value: str) -> str:
    return "".join(part.title() for part in value.split("_"))


EVENT_DRAFT_MODELS: dict[EventType, type[AuditEventDraftBase]] = {}
EVENT_MODELS: dict[EventType, type[AuditEventBase]] = {}

for _event_type, (_required_ids, _semantic_fields) in _EVENT_SPECS.items():
    _fields = {
        "event_type": (Literal[_event_type.value], _event_type.value),
        **_ids(_required_ids),
        **_semantic_fields,
    }
    _name = _stem(_event_type.value)
    _draft = create_model(f"{_name}Draft", __base__=AuditEventDraftBase, **_fields)
    _event = create_model(f"{_name}Event", __base__=AuditEventBase, **_fields)
    globals()[_draft.__name__] = _draft
    globals()[_event.__name__] = _event
    EVENT_DRAFT_MODELS[_event_type] = _draft
    EVENT_MODELS[_event_type] = _event

AuditEventDraft = Annotated[
    reduce(or_, EVENT_DRAFT_MODELS.values()), Field(discriminator="event_type")
]
AuditEvent = Annotated[reduce(or_, EVENT_MODELS.values()), Field(discriminator="event_type")]
audit_event_draft_adapter = TypeAdapter(AuditEventDraft)
audit_event_adapter = TypeAdapter(AuditEvent)


def materialize_event(
    draft: AuditEventDraftBase,
    **persistence_fields: JsonValue,
) -> AuditEventBase:
    raw = {**draft.model_dump(mode="json"), **persistence_fields}
    return audit_event_adapter.validate_python(raw)


class ProcessPayload(BaseModel):
    runtime_version: str
    python_version: str
    platform: str
    config_hash: str


class AuditHealthPayload(BaseModel):
    failure_reason: str
    failure_window: dict[str, JsonValue]
    lost_item_count: int
    affected_trace_ids: list[str]


class TurnInputPayload(BaseModel):
    role: str
    content: JsonValue
    media_refs: list[JsonValue]
    source_message_id: str | None


class TurnOutputPayload(BaseModel):
    content: JsonValue
    media_refs: list[JsonValue]
    response_kind: str


class RunConfigPayload(BaseModel):
    provider: str
    model: str
    generation_settings: dict[str, JsonValue]
    context_limits: dict[str, JsonValue]
    goal_snapshot: JsonValue


class ModelRequestPayload(BaseModel):
    messages: list[dict[str, JsonValue]]
    tool_schemas: list[dict[str, JsonValue]]
    generation_settings: dict[str, JsonValue]
    system_prompt_hash: str
    context_governance_actions: list[JsonValue]


class ModelResponsePayload(BaseModel):
    content: JsonValue
    tool_calls: list[dict[str, JsonValue]]
    finish_reason: str | None
    usage: dict[str, int]
    provider_metadata: dict[str, JsonValue]


class ReasoningSummaryPayload(BaseModel):
    content: str
    reasoning_source: str
    streamed: bool


class ToolInputPayload(BaseModel):
    tool_name: str
    arguments: dict[str, JsonValue]
    tool_schema_hash: str


class ToolOutputPayload(BaseModel):
    tool_name: str
    result: JsonValue
    normalized_error: JsonValue
    side_effects: list[JsonValue]


class CheckpointPayload(BaseModel):
    checkpoint_version: int
    checkpoint_phase: str
    checkpoint_content: JsonValue


class GoalStatePayload(BaseModel):
    goal_version: int
    goal_status: str
    objective: str
    budget: JsonValue
    blocker: JsonValue


class DeliveryPayload(BaseModel):
    channel: str
    content_fingerprint: str
    byte_count: int
    adapter_metadata: dict[str, JsonValue]


_PAYLOAD_CONTENT_MODELS: dict[PayloadKind, type[BaseModel]] = {
    PayloadKind.PROCESS: ProcessPayload,
    PayloadKind.AUDIT_HEALTH: AuditHealthPayload,
    PayloadKind.TURN_INPUT: TurnInputPayload,
    PayloadKind.TURN_OUTPUT: TurnOutputPayload,
    PayloadKind.RUN_CONFIG: RunConfigPayload,
    PayloadKind.MODEL_REQUEST: ModelRequestPayload,
    PayloadKind.MODEL_RESPONSE: ModelResponsePayload,
    PayloadKind.REASONING_SUMMARY: ReasoningSummaryPayload,
    PayloadKind.TOOL_INPUT: ToolInputPayload,
    PayloadKind.TOOL_OUTPUT: ToolOutputPayload,
    PayloadKind.CHECKPOINT: CheckpointPayload,
    PayloadKind.GOAL_STATE: GoalStatePayload,
    PayloadKind.DELIVERY: DeliveryPayload,
}


class AuditPayloadDraftBase(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    schema_version: Literal[1] = 1
    payload_id: str
    event_id: str
    payload_kind: PayloadKind


class AuditPayloadBase(AuditPayloadDraftBase):
    model_config = ConfigDict(extra="allow", use_enum_values=True)
    process_instance_id: str
    payload_segment_id: str
    payload_segment_sequence: int
    previous_payload_hash: str | None
    payload_hash: str


PAYLOAD_DRAFT_MODELS: dict[PayloadKind, type[AuditPayloadDraftBase]] = {}
PAYLOAD_MODELS: dict[PayloadKind, type[AuditPayloadBase]] = {}
for _kind, _content_model in _PAYLOAD_CONTENT_MODELS.items():
    _name = _stem(_kind.value)
    _fields = {
        "payload_kind": (Literal[_kind.value], _kind.value),
        "content": (_content_model, ...),
    }
    _draft = create_model(f"{_name}PayloadDraft", __base__=AuditPayloadDraftBase, **_fields)
    _payload = create_model(f"{_name}PayloadRecord", __base__=AuditPayloadBase, **_fields)
    globals()[_draft.__name__] = _draft
    globals()[_payload.__name__] = _payload
    PAYLOAD_DRAFT_MODELS[_kind] = _draft
    PAYLOAD_MODELS[_kind] = _payload

AuditPayloadDraft = Annotated[
    reduce(or_, PAYLOAD_DRAFT_MODELS.values()), Field(discriminator="payload_kind")
]
AuditPayload = Annotated[reduce(or_, PAYLOAD_MODELS.values()), Field(discriminator="payload_kind")]
audit_payload_draft_adapter = TypeAdapter(AuditPayloadDraft)
audit_payload_adapter = TypeAdapter(AuditPayload)


def materialize_payload(
    draft: AuditPayloadDraftBase,
    **persistence_fields: JsonValue,
) -> AuditPayloadBase:
    raw = {**draft.model_dump(mode="json"), **persistence_fields}
    return audit_payload_adapter.validate_python(raw)


class CatalogRecordBase(_UtcModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    catalog_version: Literal[1] = 1
    catalog_record_type: CatalogRecordType
    catalog_record_id: str
    process_instance_id: str
    catalog_segment_id: str
    catalog_sequence: int
    previous_catalog_hash: str | None
    occurred_at: datetime
    catalog_record_hash: str


_CATALOG_SPECS: dict[CatalogRecordType, dict[str, tuple[Any, Any]]] = {
    CatalogRecordType.PROCESS_STARTED: {
        "host_fingerprint": _required(str), "pid": _required(int), "boot_id": _required(str),
        "writer_version": _required(str), "started_at": _required(datetime),
    },
    CatalogRecordType.SEGMENT_REGISTERED: {
        "stream_kind": _required(str), "segment_id": _required(str),
        "previous_segment_id": _optional(str), "previous_segment_hash": _optional(str),
        "previous_segment_record_count": _optional(int), "path_token": _required(str),
    },
    CatalogRecordType.SEGMENT_CLOSED: {
        "stream_kind": _required(str), "segment_id": _required(str),
        "final_offset": _required(int), "final_hash": _required(str),
        "record_count": _required(int), "byte_size": _required(int),
    },
    CatalogRecordType.SEGMENT_ABANDONED: {
        "stream_kind": _required(str), "segment_id": _required(str),
        "last_committed_offset": _required(int), "last_committed_hash": _optional(str),
        "abandon_reason": _required(str),
    },
    CatalogRecordType.EPOCH_COMMITTED: {
        "durability_epoch": _required(int), "event_segment_id": _required(str),
        "event_durable_offset": _required(int), "event_final_hash": _required(str),
        "event_record_count": _required(int), "payload_segment_id": _optional(str),
        "payload_durable_offset": _required(int), "payload_final_hash": _optional(str),
        "payload_record_count": _required(int),
    },
    CatalogRecordType.PROCESS_CLOSED: {
        "last_committed_epoch": _required(int), "shutdown_reason": _required(str),
        "event_lineage_head": _optional(str), "payload_lineage_head": _optional(str),
        "closed_at": _required(datetime),
    },
}

CATALOG_MODELS: dict[CatalogRecordType, type[CatalogRecordBase]] = {}
for _record_type, _fields in _CATALOG_SPECS.items():
    _name = _stem(_record_type.value)
    _model = create_model(
        f"{_name}CatalogRecord",
        __base__=CatalogRecordBase,
        catalog_record_type=(Literal[_record_type.value], _record_type.value),
        **_fields,
    )
    globals()[_model.__name__] = _model
    CATALOG_MODELS[_record_type] = _model

CatalogRecord = Annotated[
    reduce(or_, CATALOG_MODELS.values()), Field(discriminator="catalog_record_type")
]
catalog_record_adapter = TypeAdapter(CatalogRecord)


del _event_type, _required_ids, _semantic_fields, _fields, _name, _draft, _event
del _kind, _content_model, _payload, _record_type, _model
