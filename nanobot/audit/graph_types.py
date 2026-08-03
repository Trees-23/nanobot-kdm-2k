"""Typed wire contracts for audit semantic graphs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from nanobot.audit.read_service import DisplayStatus

AuditNodeType = Literal[
    "run",
    "task",
    "model_call",
    "model_attempt",
    "tool_call",
    "decision",
    "checkpoint",
    "goal",
    "turn_result",
    "delivery",
    "anomaly",
    "external_reference",
]
AuditEdgeType = Literal[
    "sequence",
    "spawn_branch",
    "result_return",
    "caused_by",
    "retry",
    "parent_run",
    "resumed_from",
    "retry_of",
    "tool_retry",
    "tool_continuation",
    "tool_recovery",
    "task_execution",
    "task_replacement",
    "task_recovery",
]
AuditRunKind = Literal["main", "child_agent", "continuation", "unknown"]
AuditLaneSide = Literal["left", "center", "right"]
RegionType = Literal["turn", "iteration", "unscoped", "lane", "task"]


class AuditNodeSummary(BaseModel):
    kind: AuditNodeType
    actor_type: str | None = None
    provider: str | None = None
    model: str | None = None
    tool_name: str | None = None
    stop_reason: str | None = None
    decision_type: str | None = None
    reason: str | None = None
    subtype: str | None = None
    iteration_count: int | None = None
    model_call_count: int | None = None
    tool_call_count: int | None = None
    attempt_count: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    identifier: str | None = None
    checkpoint_phase: str | None = None
    checkpoint_version: int | None = None
    checkpoint_restored: bool | None = None
    checkpoint_cleared: bool | None = None
    transitions: list[dict[str, str | int | None]] = Field(default_factory=list)
    delivery_result: str | None = None
    suppression_reason: str | None = None
    failure_kind: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    error_message: str | None = None
    error_source: str | None = None
    retryability: str | None = None
    operation_evidence_kind: str | None = None
    failed_event_id: str | None = None
    effective_timeout_ms: int | None = None
    safe_input_summary: str | None = None
    impact: Literal["run_failed", "run_continued", "unknown", "pending"] | None = None
    recovery_status: Literal["recovered", "unrecovered", "continued", "unresolved", "pending"] | None = None
    recovered_by_event_id: str | None = None
    recovery_evidence_kind: str | None = None
    evidence_source: Literal["recorded", "legacy_inferred", "unknown"] | None = None
    fatal_event_id: str | None = None
    failure_policy: str | None = None
    fail_on_tool_error: bool | None = None
    failure_count: int | None = None
    fatal_failure_count: int | None = None
    recovered_failure_count: int | None = None
    continued_failure_count: int | None = None
    task_id: str | None = None
    task_label: str | None = None
    task_revision: int | None = None
    task_status: str | None = None
    task_phase: str | None = None
    termination_state: str | None = None
    delivery_phase: str | None = None
    required_task: bool | None = None
    lifecycle_event_count: int | None = None
    owner_run_id: str | None = None
    child_run_id: str | None = None
    replaces_task_id: str | None = None


class AuditNodeRelation(BaseModel):
    type: AuditEdgeType
    raw_source_event_id: str
    raw_target_event_id: str
    resolution: Literal["visible", "suppressed_same_node", "unresolved", "external"]
    other_semantic_node_id: str | None = None


class AuditNodeEventRef(BaseModel):
    event_id: str
    event_type: str
    occurred_at: datetime
    status: str | None = None
    payload_id: str | None = None


class AuditGraphNode(BaseModel):
    id: str
    type: AuditNodeType
    status: DisplayStatus
    label: str
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_ms: int | None
    raw_event_ids: list[str]
    raw_events: list[AuditNodeEventRef] = Field(default_factory=list)
    region_id: str
    parent_node_id: str | None = None
    child_node_ids: list[str] = Field(default_factory=list)
    expandable: bool = False
    relations: list[AuditNodeRelation] = Field(default_factory=list)
    summary: AuditNodeSummary
    order: int
    run_id: str | None = None
    iteration: int | None = None
    lane_id: str | None = None
    lane_kind: AuditRunKind | None = None
    lane_order: int | None = None
    lane_depth: int | None = None
    lane_side: AuditLaneSide | None = None
    run_kind: AuditRunKind | None = None
    terminal_status: DisplayStatus | None = None
    health_status: DisplayStatus | None = None
    anomaly_count: int = 0
    first_anomaly_event_id: str | None = None
    spawn_tool_call_id: str | None = None
    continuation_of_run_id: str | None = None
    injection_source: str | None = None
    task_id: str | None = None


class AuditEdgeAnchor(BaseModel):
    source_event_id: str | None = None
    target_event_id: str | None = None


class AuditGraphEdge(BaseModel):
    id: str
    type: AuditEdgeType
    source: str
    target: str
    relation: AuditEdgeType | None = None
    anchor: AuditEdgeAnchor | None = None
    evidence_count: int | None = None
    evidence_kind: str | None = None


class AuditGraphRegion(BaseModel):
    id: str
    type: RegionType
    label: str
    status: DisplayStatus
    parent_region_id: str | None = None
    member_node_ids: list[str]
    order: int
    run_id: str | None = None
    lane_id: str | None = None
    lane_kind: AuditRunKind | None = None
    lane_order: int | None = None
    lane_depth: int | None = None
    lane_side: AuditLaneSide | None = None
    terminal_status: DisplayStatus | None = None
    health_status: DisplayStatus | None = None
    task_id: str | None = None


class CollapseGroup(BaseModel):
    id: str
    member_node_ids: list[str]
    status: DisplayStatus
    label: str
    elapsed_ms: int | None


class ExpansionGroup(BaseModel):
    id: str
    kind: Literal["model_attempts"] = "model_attempts"
    owner_node_id: str
    member_node_ids: list[str]
    default_expanded: bool


class FirstAnomaly(BaseModel):
    node_id: str
    event_id: str | None
    category: str
    rule: Literal["earliest_qualifying_event", "integrity_anchor"]


class GraphTraceSummary(BaseModel):
    trace_id: str
    title: str
    display_status: DisplayStatus
    first_seen: datetime
    last_seen: datetime
    session_key: str | None
    source_types: list[str]
    active: bool
    event_count: int


class GraphIntegrity(BaseModel):
    status: str
    error_codes: list[str]
    warning_codes: list[str]


class AuditGraph(BaseModel):
    trace: GraphTraceSummary
    level: Literal["trace", "trace_full", "run"]
    focus: dict[Literal["turn_id", "run_id"], str | None]
    regions: list[AuditGraphRegion]
    nodes: list[AuditGraphNode]
    edges: list[AuditGraphEdge]
    first_anomaly: FirstAnomaly | None
    collapse_groups: list[CollapseGroup]
    expansion_groups: list[ExpansionGroup]
    event_owners: dict[str, str]
    ignored_event_ids: list[str]
    integrity: GraphIntegrity
