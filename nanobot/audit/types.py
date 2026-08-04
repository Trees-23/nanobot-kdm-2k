"""Closed values used by the audit V1 evidence contract."""

from __future__ import annotations

from enum import StrEnum

from pydantic import JsonValue as JsonValue

JsonScalar = str | int | float | bool | None


class EventType(StrEnum):
    PROCESS_INSTANCE_STARTED = "process_instance_started"
    PROCESS_INSTANCE_CLOSED = "process_instance_closed"
    SEGMENT_STARTED = "segment_started"
    SEGMENT_CLOSED = "segment_closed"
    AUDIT_DEGRADED = "audit_degraded"
    AUDIT_RECOVERED = "audit_recovered"
    TRACE_CREATED = "trace_created"
    TRACE_LINKED = "trace_linked"
    TURN_STARTED = "turn_started"
    INPUT_INJECTED = "input_injected"
    CANCEL_REQUESTED = "cancel_requested"
    TURN_RESPONSE_PREPARED = "turn_response_prepared"
    TURN_FINISHED = "turn_finished"
    RETURNED_TO_CALLER = "returned_to_caller"
    DELIVERY_ATTEMPTED = "delivery_attempted"
    DELIVERY_RETRY_SCHEDULED = "delivery_retry_scheduled"
    DELIVERY_FINISHED = "delivery_finished"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    ORPHAN_RUN_SUSPECTED = "orphan_run_suspected"
    ORPHAN_RUN_DETECTED = "orphan_run_detected"
    ORPHAN_MODEL_CALL_DETECTED = "orphan_model_call_detected"
    ORPHAN_TOOL_DETECTED = "orphan_tool_detected"
    ITERATION_STARTED = "iteration_started"
    ITERATION_FINISHED = "iteration_finished"
    MODEL_REQUEST_STARTED = "model_request_started"
    MODEL_FIRST_OUTPUT = "model_first_output"
    MODEL_RESPONSE_RECEIVED = "model_response_received"
    MODEL_REQUEST_FAILED = "model_request_failed"
    PROVIDER_ROUTE_DECISION = "provider_route_decision"
    MODEL_ATTEMPT_STARTED = "model_attempt_started"
    MODEL_ATTEMPT_FINISHED = "model_attempt_finished"
    RETRY_SCHEDULED = "retry_scheduled"
    REASONING_SUMMARY_RECEIVED = "reasoning_summary_received"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    POLICY_BLOCKED = "policy_blocked"
    CONTINUATION_REQUESTED = "continuation_requested"
    FINALIZATION_REQUESTED = "finalization_requested"
    CHECKPOINT_WRITTEN = "checkpoint_written"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    CHECKPOINT_CLEARED = "checkpoint_cleared"
    GOAL_CREATED = "goal_created"
    GOAL_UPDATED = "goal_updated"
    GOAL_COMPLETED = "goal_completed"
    GOAL_BLOCKED = "goal_blocked"
    GOAL_CANCELLED = "goal_cancelled"
    SUBAGENT_CREATED = "subagent_created"
    SUBAGENT_ADMITTED = "subagent_admitted"
    SUBAGENT_PHASE_CHANGED = "subagent_phase_changed"
    SUBAGENT_USAGE_UPDATED = "subagent_usage_updated"
    SUBAGENT_BUDGET_UPDATED = "subagent_budget_updated"
    SUBAGENT_CANCEL_REQUESTED = "subagent_cancel_requested"
    SUBAGENT_TERMINATION_DECIDED = "subagent_termination_decided"
    SUBAGENT_RESULT_READY = "subagent_result_ready"
    SUBAGENT_RESULT_CLAIMED = "subagent_result_claimed"
    SUBAGENT_RESULT_DELIVERED = "subagent_result_delivered"
    SUBAGENT_DELIVERY_FAILED = "subagent_delivery_failed"
    SUBAGENT_TERMINAL = "subagent_terminal"
    SUBAGENT_RECOVERED = "subagent_recovered"
    SUBAGENT_LOST = "subagent_lost"


class PayloadKind(StrEnum):
    PROCESS = "process"
    AUDIT_HEALTH = "audit_health"
    TURN_INPUT = "turn_input"
    TURN_OUTPUT = "turn_output"
    RUN_CONFIG = "run_config"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    REASONING_SUMMARY = "reasoning_summary"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"
    CHECKPOINT = "checkpoint"
    GOAL_STATE = "goal_state"
    DELIVERY = "delivery"


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    EXHAUSTED = "exhausted"


class IntegrityStatus(StrEnum):
    VALID = "valid"
    DEGRADED = "degraded"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"
    INVALID = "invalid"


class AuditMode(StrEnum):
    FULL = "full"
    METADATA_ONLY = "metadata_only"
    OFF = "off"


class ProviderRouteAction(StrEnum):
    PRIMARY_SELECTED = "primary_selected"
    FALLBACK_SELECTED = "fallback_selected"
    CIRCUIT_SKIPPED = "circuit_skipped"
    IMAGE_STRIPPED_RETRY = "image_stripped_retry"
    STREAM_RECOVERY = "stream_recovery"
    FAILOVER_SKIPPED_AFTER_STREAM = "failover_skipped_after_stream"
    FALLBACK_EXHAUSTED = "fallback_exhausted"


class DeliveryStatus(StrEnum):
    ACCEPTED_BY_ADAPTER = "accepted_by_adapter"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPPRESSED = "suppressed"


class CatalogRecordType(StrEnum):
    PROCESS_STARTED = "process_started"
    SEGMENT_REGISTERED = "segment_registered"
    SEGMENT_CLOSED = "segment_closed"
    SEGMENT_ABANDONED = "segment_abandoned"
    EPOCH_COMMITTED = "epoch_committed"
    PROCESS_CLOSED = "process_closed"


class StreamKind(StrEnum):
    EVENT = "event"
    PAYLOAD = "payload"
    CATALOG = "catalog"
