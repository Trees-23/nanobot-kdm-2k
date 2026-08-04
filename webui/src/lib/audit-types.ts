export type TraceDisplayStatus =
  | "running"
  | "failed"
  | "interrupted"
  | "cancelled"
  | "incomplete"
  | "warning"
  | "succeeded"
  | "unknown";

export type AuditNodeType =
  | "run"
  | "task"
  | "model_call"
  | "model_attempt"
  | "tool_call"
  | "decision"
  | "checkpoint"
  | "goal"
  | "turn_result"
  | "delivery"
  | "anomaly"
  | "external_reference";

export type TraceEdgeType =
  | "sequence"
  | "spawn_branch"
  | "result_return"
  | "caused_by"
  | "retry"
  | "parent_run"
  | "resumed_from"
  | "retry_of"
  | "tool_retry"
  | "tool_continuation"
  | "tool_recovery"
  | "task_execution"
  | "task_replacement"
  | "task_recovery";

export type AuditRunKind = "main" | "child_agent" | "continuation" | "unknown";
export type AuditLaneSide = "left" | "center" | "right";
export type AuditCaptureMode = "full" | "metadata_only" | "off";

export interface AuditIndexStatus {
  state: "ready" | "building" | "stale" | "disabled" | "unavailable";
  revision: number | null;
  coverage_complete: boolean;
  updated_at: string | null;
  lag_ms: number | null;
  last_error: { code: string; message: string; at: string | null } | null;
  audit_mode?: AuditCaptureMode;
}

export interface AuditTraceListItem {
  trace_id: string;
  title: string;
  source_types: string[];
  primary_source_type: string;
  first_seen: string;
  last_seen: string;
  display_status: TraceDisplayStatus;
  turn_count: number;
  run_count: number;
  anomaly_count: number;
  integrity_status: string;
  active: boolean;
  session_key: string | null;
  event_count: number;
}

export interface AuditTraceListResponse {
  items: AuditTraceListItem[];
  next_cursor: string | null;
  index: AuditIndexStatus;
}

export interface AuditSessionListItem {
  session_key: string;
  title: string;
  source_types: string[];
  first_seen: string;
  last_seen: string;
  trace_count: number;
  active_trace_count: number;
  warning_count: number;
  error_count: number;
  integrity_status: string;
  latest_trace_id: string;
}

export interface AuditSessionListResponse {
  items: AuditSessionListItem[];
  next_cursor: string | null;
  index: AuditIndexStatus;
}

export interface AuditTraceFilters {
  query: string;
  since: string;
  until: string;
  status: TraceDisplayStatus | "all";
  anomaliesOnly: boolean;
  sourceType: string;
  model: string;
  tool: string;
}

export interface AuditNodeSummary {
  kind: AuditNodeType;
  actor_type?: string | null;
  provider?: string | null;
  model?: string | null;
  tool_name?: string | null;
  stop_reason?: string | null;
  decision_type?: string | null;
  reason?: string | null;
  subtype?: string | null;
  iteration_count?: number | null;
  model_call_count?: number | null;
  tool_call_count?: number | null;
  attempt_count?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  identifier?: string | null;
  checkpoint_phase?: string | null;
  checkpoint_version?: number | null;
  checkpoint_restored?: boolean | null;
  checkpoint_cleared?: boolean | null;
  transitions?: Array<{ event_type: string; occurred_at: string; version: number | null }>;
  delivery_result?: string | null;
  suppression_reason?: string | null;
  failure_kind?: string | null;
  error_type?: string | null;
  error_code?: string | null;
  error_summary?: string | null;
  error_message?: string | null;
  error_source?: string | null;
  retryability?: string | null;
  operation_evidence_kind?: string | null;
  failed_event_id?: string | null;
  effective_timeout_ms?: number | null;
  safe_input_summary?: string | null;
  impact?: "run_failed" | "run_continued" | "unknown" | "pending" | null;
  recovery_status?: "recovered" | "unrecovered" | "continued" | "unresolved" | "pending" | null;
  recovered_by_event_id?: string | null;
  recovery_evidence_kind?: string | null;
  evidence_source?: "recorded" | "legacy_inferred" | "unknown" | null;
  fatal_event_id?: string | null;
  failure_policy?: string | null;
  fail_on_tool_error?: boolean | null;
  failure_count?: number | null;
  fatal_failure_count?: number | null;
  recovered_failure_count?: number | null;
  continued_failure_count?: number | null;
  task_id?: string | null;
  task_label?: string | null;
  task_revision?: number | null;
  task_status?: string | null;
  task_phase?: string | null;
  termination_state?: string | null;
  delivery_phase?: string | null;
  required_task?: boolean | null;
  lifecycle_event_count?: number | null;
  owner_run_id?: string | null;
  child_run_id?: string | null;
  replaces_task_id?: string | null;
}

export interface AuditNodeEventRef {
  event_id: string;
  event_type: string;
  occurred_at: string;
  status: string | null;
  payload_id: string | null;
}

export interface AuditGraphNode {
  id: string;
  type: AuditNodeType;
  status: TraceDisplayStatus;
  label: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_ms: number | null;
  raw_event_ids: string[];
  raw_events?: AuditNodeEventRef[];
  region_id: string;
  parent_node_id: string | null;
  child_node_ids: string[];
  expandable: boolean;
  relations: Array<{
    type: TraceEdgeType;
    raw_source_event_id: string;
    raw_target_event_id: string;
    resolution: string;
    other_semantic_node_id: string | null;
  }>;
  summary: AuditNodeSummary;
  order: number;
  run_id?: string | null;
  iteration?: number | null;
  lane_id?: string | null;
  lane_kind?: AuditRunKind | null;
  lane_order?: number | null;
  lane_depth?: number | null;
  lane_side?: AuditLaneSide | null;
  run_kind?: AuditRunKind | null;
  terminal_status?: TraceDisplayStatus | null;
  health_status?: TraceDisplayStatus | null;
  anomaly_count?: number;
  first_anomaly_event_id?: string | null;
  spawn_tool_call_id?: string | null;
  continuation_of_run_id?: string | null;
  injection_source?: string | null;
  task_id?: string | null;
}

export interface AuditGraphResponse {
  trace: {
    trace_id: string;
    title: string;
    display_status: TraceDisplayStatus;
    first_seen: string;
    last_seen: string;
    session_key: string | null;
    source_types: string[];
    active: boolean;
    event_count: number;
  };
  level: "trace" | "trace_full" | "run";
  focus: { turn_id: string | null; run_id: string | null };
  regions: Array<{
    id: string;
    type: "turn" | "iteration" | "unscoped" | "lane" | "task";
    label: string;
    status: TraceDisplayStatus;
    parent_region_id: string | null;
    member_node_ids: string[];
    order: number;
    run_id?: string | null;
    lane_id?: string | null;
    lane_kind?: AuditRunKind | null;
    lane_order?: number | null;
    lane_depth?: number | null;
    lane_side?: AuditLaneSide | null;
    terminal_status?: TraceDisplayStatus | null;
    health_status?: TraceDisplayStatus | null;
    task_id?: string | null;
  }>;
  nodes: AuditGraphNode[];
  edges: Array<{
    id: string;
    type: TraceEdgeType;
    relation?: TraceEdgeType | null;
    source: string;
    target: string;
    anchor?: { source_event_id?: string | null; target_event_id?: string | null } | null;
    evidence_kind?: string | null;
  }>;
  first_anomaly: {
    node_id: string;
    event_id: string | null;
    category: string;
    rule: string;
  } | null;
  collapse_groups: Array<{
    id: string;
    member_node_ids: string[];
    status: TraceDisplayStatus;
    label: string;
    elapsed_ms: number | null;
  }>;
  expansion_groups: Array<{
    id: string;
    kind: "model_attempts";
    owner_node_id: string;
    member_node_ids: string[];
    default_expanded: boolean;
  }>;
  ignored_event_ids: string[];
  integrity: { status: string; error_codes: string[]; warning_codes: string[] };
  index: { revision: number; coverage_complete: boolean; lag_ms: number | null; audit_mode?: AuditCaptureMode };
}

export interface AuditGraphEdge {
  id: string;
  type: TraceEdgeType;
  relation?: TraceEdgeType | null;
  source: string;
  target: string;
  anchor?: { source_event_id?: string | null; target_event_id?: string | null } | null;
  evidence_count?: number | null;
  evidence_kind?: string | null;
}

export interface AuditEventItem {
  event_id: string;
  event_type: string;
  occurred_at: string;
  process_instance_id: string;
  durability_epoch: number;
  segment_id: string;
  segment_sequence: number;
  trace_id: string | null;
  turn_id: string | null;
  run_id: string | null;
  model_call_id: string | null;
  attempt_id: string | null;
  tool_call_id: string | null;
  iteration: number | null;
  caused_by_event_id: string | null;
  status: string | null;
  elapsed_ms: number | null;
  payload_id: string | null;
  semantic_node_id: string | null;
  summary: string;
}

export interface AuditEventPage {
  items: AuditEventItem[];
  next_cursor: string | null;
  total: number;
  index: { revision: number; audit_mode?: AuditCaptureMode };
}

export interface AuditPayloadResponse {
  payload_id: string;
  event_id: string | null;
  payload_kind: string | null;
  available: boolean;
  reason: string | null;
  content: unknown;
  truncated: boolean;
}
