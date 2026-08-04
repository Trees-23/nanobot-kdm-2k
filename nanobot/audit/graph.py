"""Deterministic compaction of typed audit events into semantic graphs."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from nanobot.audit.graph_types import (
    AuditEdgeAnchor,
    AuditGraph,
    AuditGraphEdge,
    AuditGraphNode,
    AuditGraphRegion,
    AuditNodeEventRef,
    AuditNodeRelation,
    AuditNodeSummary,
    CollapseGroup,
    ExpansionGroup,
    FirstAnomaly,
    GraphIntegrity,
    GraphTraceSummary,
)
from nanobot.audit.read_service import DisplayStatus, expected_delivery_suppression
from nanobot.audit.schema import AuditEventBase

GRAPH_BUILDER_VERSION = 7
_ABNORMAL = {"error", "failed", "timeout", "blocked", "cancelled", "interrupted", "exhausted"}
_DECISIONS = {
    "provider_route_decision",
    "retry_scheduled",
    "delivery_retry_scheduled",
    "policy_blocked",
    "continuation_requested",
    "finalization_requested",
    "cancel_requested",
}
_TURN_RESULTS = {"turn_response_prepared", "turn_finished", "returned_to_caller"}
_PROCESS_EVENTS = {
    "process_instance_started",
    "process_instance_closed",
    "segment_started",
    "segment_closed",
    "trace_created",
    "trace_linked",
    "turn_started",
    "iteration_started",
    "iteration_finished",
    "input_injected",
    "reasoning_summary_received",
}
_SUBAGENT_EVENTS = {
    "subagent_created",
    "subagent_admitted",
    "subagent_phase_changed",
    "subagent_usage_updated",
    "subagent_budget_updated",
    "subagent_cancel_requested",
    "subagent_termination_decided",
    "subagent_result_ready",
    "subagent_result_claimed",
    "subagent_result_delivered",
    "subagent_delivery_failed",
    "subagent_terminal",
    "subagent_recovered",
    "subagent_lost",
}


def _task_status(events: Sequence[AuditEventBase]) -> DisplayStatus:
    latest = str(getattr(events[-1], "task_status", "")) if events else ""
    if latest == "running":
        return "running"
    if latest == "succeeded":
        return "succeeded"
    if latest == "cancelled":
        return "cancelled"
    if latest in {"failed", "lost"}:
        return "failed"
    if latest == "timed_out":
        return "warning"
    return "incomplete"


def _tool_failure_summary(
    grouped: Sequence[AuditEventBase], trace_events: Sequence[AuditEventBase]
) -> dict[str, Any]:
    failure = next(
        (
            event
            for event in reversed(grouped)
            if getattr(event, "status", None) in _ABNORMAL
        ),
        None,
    )
    if failure is None:
        return {}
    run_events = [event for event in trace_events if event.run_id == failure.run_id]
    finish = next(
        (event for event in reversed(run_events) if event.event_type == "run_finished"),
        None,
    )
    recovered_by = next(
        (
            event
            for event in run_events
            if failure.tool_call_id
            in (getattr(event, "recovery_of_tool_call_ids", None) or [])
        ),
        None,
    )
    fatal = finish is not None and getattr(finish, "fatal_event_id", None) == failure.event_id
    if fatal:
        impact = "run_failed"
    elif finish is None:
        impact = "pending"
    elif getattr(finish, "status", None) == "succeeded":
        impact = "run_continued"
    else:
        impact = "unknown"
    fallback = getattr(failure, "recovery_fallback", None)
    if recovered_by is not None:
        recovery_status = "recovered"
    elif fatal:
        recovery_status = "unrecovered"
    elif finish is None:
        recovery_status = "pending"
    elif getattr(finish, "status", None) == "succeeded":
        recovery_status = (
            fallback
            if fallback in {"continued", "unresolved"}
            else "continued" if getattr(failure, "resource_key", None) else "unresolved"
        )
    else:
        recovery_status = "unresolved"
    status = getattr(failure, "status", None)
    failure_kind = "policy_error" if status == "blocked" else "tool_error"
    recorded = any(
        getattr(failure, field, None) is not None
        for field in ("error_type", "error_code", "error_summary")
    )
    return {
        "failure_kind": failure_kind,
        "error_type": getattr(failure, "error_type", None),
        "error_code": getattr(failure, "error_code", None),
        "error_summary": getattr(failure, "error_summary", None),
        "error_message": getattr(failure, "error_message", None),
        "error_source": getattr(failure, "error_source", None),
        "retryability": getattr(failure, "retryability", None),
        "operation_evidence_kind": getattr(failure, "operation_evidence_kind", None),
        "failed_event_id": failure.event_id,
        "effective_timeout_ms": getattr(failure, "effective_timeout_ms", None),
        "safe_input_summary": getattr(failure, "safe_input_summary", None),
        "impact": impact,
        "recovery_status": recovery_status,
        "recovered_by_event_id": recovered_by.event_id if recovered_by else None,
        "recovery_evidence_kind": (
            getattr(recovered_by, "recovery_evidence_kind", None) if recovered_by else None
        ),
        "evidence_source": "recorded" if recorded else "unknown",
    }


def _run_failure_summary(grouped: Sequence[AuditEventBase]) -> dict[str, Any]:
    finish = next(
        (event for event in reversed(grouped) if event.event_type == "run_finished"),
        None,
    )
    tool_groups: dict[str, list[AuditEventBase]] = defaultdict(list)
    for event in grouped:
        if event.tool_call_id and event.event_type in {"tool_started", "tool_finished"}:
            tool_groups[event.tool_call_id].append(event)
    failures = [
        _tool_failure_summary(events, grouped)
        for events in tool_groups.values()
        if any(getattr(event, "status", None) in _ABNORMAL for event in events)
    ]
    fatal_event_id = getattr(finish, "fatal_event_id", None) if finish else None
    primary = next(
        (item for item in failures if item.get("failed_event_id") == fatal_event_id),
        failures[0] if failures else {},
    )
    summary = {
        **primary,
        "fatal_event_id": fatal_event_id,
        "failure_policy": getattr(finish, "failure_policy", None) if finish else None,
        "fail_on_tool_error": getattr(finish, "fail_on_tool_error", None) if finish else None,
        "failure_count": len(failures),
        "fatal_failure_count": sum(item.get("impact") == "run_failed" for item in failures),
        "recovered_failure_count": sum(
            item.get("recovery_status") == "recovered" for item in failures
        ),
        "continued_failure_count": sum(
            item.get("recovery_status") == "continued" for item in failures
        ),
    }
    if (
        finish is not None
        and getattr(finish, "stop_reason", None) == "model_error"
        and len(failures) == 1
        and not any(event.event_type == "model_request_failed" for event in grouped)
    ):
        summary["failure_kind"] = "tool_error"
        summary["evidence_source"] = "legacy_inferred"
    return summary


def _order(event: AuditEventBase) -> tuple[Any, ...]:
    return (
        event.occurred_at,
        event.process_instance_id,
        event.durability_epoch,
        event.segment_id,
        event.segment_sequence,
        event.event_id,
    )


def _event_refs(events: Sequence[AuditEventBase]) -> list[AuditNodeEventRef]:
    return [
        AuditNodeEventRef(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            status=getattr(event, "status", None),
            payload_id=event.payload_id,
        )
        for event in events
    ]


def _status(
    events: Iterable[AuditEventBase],
    *,
    active: bool = False,
    trace_events: Sequence[AuditEventBase] | None = None,
) -> DisplayStatus:
    selected = list(events)
    values = {
        str(getattr(event, "status"))
        for event in selected
        if getattr(event, "status", None)
    }
    if active:
        return "running"
    if values & {"failed", "exhausted", "error"}:
        return "failed"
    if "interrupted" in values:
        return "interrupted"
    if "cancelled" in values:
        return "cancelled"
    if values & {"timeout", "blocked"}:
        return "warning"
    if "suppressed" in values:
        suppressed = [
            event for event in selected if getattr(event, "status", None) == "suppressed"
        ]
        context = list(trace_events or selected)
        return (
            "succeeded"
            if suppressed
            and all(expected_delivery_suppression(event, context) for event in suppressed)
            else "warning"
        )
    if not values or values & {
        "ok",
        "succeeded",
        "returned",
        "response_prepared",
        "command_completed",
        "accepted_by_adapter",
        "clean",
    }:
        return "succeeded"
    return "incomplete"


def _elapsed(events: Sequence[AuditEventBase]) -> int | None:
    values = [getattr(event, "elapsed_ms", None) for event in events]
    explicit = [value for value in values if isinstance(value, int)]
    if explicit:
        return max(explicit)
    if len(events) > 1:
        return max(0, int((events[-1].occurred_at - events[0].occurred_at).total_seconds() * 1000))
    return None


def _primary_source(events: Sequence[AuditEventBase]) -> str:
    sources = [event.source_type for event in events if event.source_type]
    for source in sources:
        if source in {"user", "websocket"}:
            return source
    for source in sources:
        if source not in {"system", "delivery"}:
            return source
    return sources[0] if sources else "system"


@dataclass(slots=True)
class _BuildState:
    nodes: list[AuditGraphNode]
    owners: dict[str, str]
    ignored: list[str]

    def add(self, node: AuditGraphNode, events: Sequence[AuditEventBase]) -> None:
        self.nodes.append(node)
        for event in events:
            if event.event_id in self.owners:
                raise ValueError(f"event has multiple semantic owners: {event.event_id}")
            self.owners[event.event_id] = node.id


@dataclass(frozen=True, slots=True)
class _RunEvidence:
    kind: Literal["main", "child_agent", "continuation", "unknown"]
    parent_run_id: str | None
    spawn_event_id: str | None = None
    spawn_tool_call_id: str | None = None
    continuation_of_run_id: str | None = None
    injection_source: str | None = None
    lane_order: int = 0
    lane_depth: int = 0

    @property
    def lane_side(self) -> Literal["left", "center", "right"]:
        if self.lane_order < 0:
            return "left"
        if self.lane_order > 0:
            return "right"
        return "center"


class AuditGraphBuilder:
    def build(
        self,
        *,
        trace_id: str,
        level: Literal["trace", "trace_full", "run"],
        events: Sequence[AuditEventBase],
        run_id: str | None = None,
        active_run_ids: set[str] | None = None,
        integrity_status: str = "unknown",
        integrity_error_codes: Sequence[str] = (),
        integrity_warning_codes: Sequence[str] = (),
    ) -> AuditGraph:
        ordered = sorted((event for event in events if event.trace_id == trace_id), key=_order)
        if not ordered:
            raise KeyError(trace_id)
        active = active_run_ids or set()
        if level == "run" and not run_id:
            raise ValueError("run_id is required for run graph")
        if level == "run" and not any(event.run_id == run_id for event in ordered):
            raise KeyError(run_id)

        if level == "trace":
            state, regions, expansion = self._trace_nodes(trace_id, ordered, active)
            edges = self._edges(trace_id, ordered, state)
        elif level == "trace_full":
            state, regions, expansion, evidence = self._trace_full_nodes(
                trace_id, ordered, active
            )
            edges = self._trace_full_edges(trace_id, ordered, state, evidence)
        else:
            selected = [event for event in ordered if event.run_id == run_id]
            state, regions, expansion = self._run_nodes(
                trace_id, run_id or "", selected, trace_events=ordered
            )
            edges = self._edges(trace_id, ordered, state)
        collapse = self._collapse_groups(state.nodes, edges)
        first = self._first_anomaly(ordered, state.owners, state.nodes, integrity_status)
        trace_status = self._trace_status(ordered, active)
        if integrity_status in {"invalid", "incomplete"} and trace_status == "succeeded":
            trace_status = "incomplete"
        elif integrity_status == "degraded" and trace_status == "succeeded":
            trace_status = "warning"
        sources = sorted({event.source_type for event in ordered if event.source_type})
        trace = GraphTraceSummary(
            trace_id=trace_id,
            title=f"{_primary_source(ordered)} / {ordered[0].occurred_at:%H:%M} / {trace_id[:8]}",
            display_status=trace_status,
            first_seen=ordered[0].occurred_at,
            last_seen=ordered[-1].occurred_at,
            session_key=next((event.session_key for event in ordered if event.session_key), None),
            source_types=sources,
            active=bool(active & {event.run_id for event in ordered if event.run_id}),
            event_count=len(ordered),
        )
        self._validate_membership(regions, state.nodes)
        return AuditGraph(
            trace=trace,
            level=level,
            focus={"turn_id": None, "run_id": run_id if level == "run" else None},
            regions=regions,
            nodes=sorted(state.nodes, key=lambda node: (node.order, node.id)),
            edges=sorted(edges, key=lambda edge: (edge.type, edge.source, edge.target, edge.id)),
            first_anomaly=first,
            collapse_groups=collapse,
            expansion_groups=expansion,
            event_owners=dict(sorted(state.owners.items())),
            ignored_event_ids=sorted(state.ignored),
            integrity=GraphIntegrity(
                status=integrity_status,
                error_codes=sorted(integrity_error_codes),
                warning_codes=sorted(integrity_warning_codes),
            ),
        )

    def _trace_nodes(
        self, trace_id: str, events: list[AuditEventBase], active: set[str]
    ) -> tuple[_BuildState, list[AuditGraphRegion], list[ExpansionGroup]]:
        state = _BuildState([], {}, [])
        by_run: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in events:
            if event.run_id:
                by_run[event.run_id].append(event)
            else:
                state.ignored.append(event.event_id)
        turn_ids = list(dict.fromkeys(event.turn_id or "unscoped" for event in events))
        regions: list[AuditGraphRegion] = []
        for order, turn_id in enumerate(turn_ids):
            region_id = f"turn:{trace_id}:{turn_id}"
            members: list[str] = []
            for run_order, (current_run, grouped) in enumerate(by_run.items()):
                if (grouped[0].turn_id or "unscoped") != turn_id:
                    continue
                starts = [event for event in grouped if event.event_type == "run_started"]
                finishes = [event for event in grouped if event.event_type == "run_finished"]
                node_id = f"run:{trace_id}:{current_run}"
                abnormal = not starts or not finishes or len(starts) > 1 or len(finishes) > 1
                node_status = "incomplete" if abnormal else _status(
                    grouped, active=current_run in active, trace_events=events
                )
                parent = getattr(starts[0], "parent_run_id", None) if starts else None
                child_ids = [
                    f"run:{trace_id}:{child}"
                    for child, child_events in by_run.items()
                    if any(event.parent_run_id == current_run for event in child_events)
                ]
                state.add(
                    AuditGraphNode(
                        id=node_id,
                        type="run",
                        status=node_status,
                        label="Subagent" if parent else "Main run",
                        started_at=starts[0].occurred_at if starts else grouped[0].occurred_at,
                        finished_at=finishes[-1].occurred_at if finishes else None,
                        elapsed_ms=_elapsed(grouped),
                        raw_event_ids=[event.event_id for event in grouped],
                        region_id=region_id,
                        parent_node_id=f"run:{trace_id}:{parent}" if parent else None,
                        child_node_ids=sorted(child_ids),
                        expandable=True,
                        summary=AuditNodeSummary(
                            kind="run",
                            actor_type="subagent" if parent else "main",
                            stop_reason=getattr(finishes[-1], "stop_reason", None) if finishes else None,
                            iteration_count=len({event.iteration for event in grouped if event.iteration}),
                            model_call_count=len({event.model_call_id for event in grouped if event.model_call_id}),
                            tool_call_count=len({event.tool_call_id for event in grouped if event.tool_call_id}),
                            subtype="lifecycle_mismatch" if abnormal else None,
                            identifier=current_run,
                        ),
                        order=run_order,
                    ),
                    grouped,
                )
                members.append(node_id)
            regions.append(
                AuditGraphRegion(
                    id=region_id,
                    type="turn",
                    label=f"Turn {order + 1}",
                    status=_status(
                        [event for event in events if (event.turn_id or "unscoped") == turn_id],
                        trace_events=events,
                    ),
                    member_node_ids=members,
                    order=order,
                )
            )
        return state, regions, []

    def _run_evidence(
        self, events: list[AuditEventBase]
    ) -> dict[str, _RunEvidence]:
        by_run: dict[str, list[AuditEventBase]] = defaultdict(list)
        starts: dict[str, AuditEventBase] = {}
        for event in events:
            if event.run_id:
                by_run[event.run_id].append(event)
            if event.event_type == "run_started" and event.run_id:
                starts.setdefault(event.run_id, event)

        continuation_links: dict[tuple[str, str], AuditEventBase] = {}
        for event in events:
            if (
                event.event_type == "trace_linked"
                and getattr(event, "link_reason", None) == "active_run_injection"
                and event.turn_id
                and getattr(event, "linked_source_id", None)
            ):
                continuation_links[(event.turn_id, str(event.linked_source_id))] = event

        evidence: dict[str, _RunEvidence] = {}
        for run_id, start in starts.items():
            parent = start.parent_run_id
            metadata = start.source_metadata if isinstance(start.source_metadata, dict) else {}
            continuation_of = metadata.get("continuation_of_run_id")
            link = continuation_links.get((start.turn_id or "", parent or ""))
            if isinstance(continuation_of, str) and continuation_of:
                evidence[run_id] = _RunEvidence(
                    kind="continuation",
                    parent_run_id=None,
                    continuation_of_run_id=continuation_of,
                    injection_source=str(metadata.get("injection_source") or "subagent_result"),
                )
            elif link is not None:
                evidence[run_id] = _RunEvidence(
                    kind="continuation",
                    parent_run_id=parent,
                    continuation_of_run_id=parent,
                    injection_source="subagent_result",
                )
            elif parent is None:
                evidence[run_id] = _RunEvidence(kind="main", parent_run_id=None)
            else:
                evidence[run_id] = _RunEvidence(kind="unknown", parent_run_id=parent)

        spawn_events = {
            event.event_id: event
            for event in events
            if event.event_type in {"tool_started", "tool_finished"}
            and getattr(event, "tool_name", None) == "spawn"
        }
        candidates: dict[str, list[AuditEventBase]] = defaultdict(list)
        for parent_run_id, grouped in by_run.items():
            parent_events = sorted(grouped, key=_order)
            for index, event in enumerate(parent_events):
                if (
                    event.event_type != "tool_finished"
                    or getattr(event, "tool_name", None) != "spawn"
                    or getattr(event, "status", None) != "ok"
                ):
                    continue
                next_parent = parent_events[index + 1] if index + 1 < len(parent_events) else None
                explicit = [
                    start
                    for start in starts.values()
                    if start.parent_run_id == parent_run_id
                    and start.caused_by_event_id in spawn_events
                    and spawn_events[start.caused_by_event_id].tool_call_id == event.tool_call_id
                ]
                window = [
                    start
                    for start in starts.values()
                    if start.parent_run_id == parent_run_id
                    and start.source_type == "subagent"
                    and evidence[start.run_id].kind != "continuation"
                    and _order(start) > _order(event)
                    and (next_parent is None or _order(start) < _order(next_parent))
                ]
                matched = explicit or window
                if len(matched) == 1:
                    candidates[matched[0].run_id].append(event)

        for run_id, matched in candidates.items():
            if len(matched) != 1:
                continue
            spawn = matched[0]
            current = evidence[run_id]
            evidence[run_id] = replace(
                current,
                kind="child_agent",
                spawn_event_id=spawn.event_id,
                spawn_tool_call_id=spawn.tool_call_id,
            )

        # New runs carry an exact spawn -> task -> child binding. Keep the legacy
        # evidence matcher above for historical traces only.
        for run_id, start in starts.items():
            metadata = start.source_metadata if isinstance(start.source_metadata, dict) else {}
            spawn_tool_call_id = metadata.get("spawn_tool_call_id")
            task_id = metadata.get("subagent_task_id")
            if not all(isinstance(value, str) and value for value in (spawn_tool_call_id, task_id)):
                continue
            matching = [
                event
                for event in events
                if event.tool_call_id == spawn_tool_call_id
                and event.event_type == "tool_finished"
                and getattr(event, "tool_name", None) == "spawn"
                and getattr(event, "status", None) == "ok"
            ]
            if len(matching) == 1:
                evidence[run_id] = replace(
                    evidence[run_id],
                    kind="child_agent",
                    spawn_event_id=matching[0].event_id,
                    spawn_tool_call_id=spawn_tool_call_id,
                )

        children: dict[str, list[str]] = defaultdict(list)
        for run_id, item in evidence.items():
            if item.kind == "child_agent" and item.parent_run_id:
                children[item.parent_run_id].append(run_id)
        for values in children.values():
            values.sort(key=lambda run_id: _order(starts[run_id]))

        unresolved = set(evidence)
        while unresolved:
            progressed = False
            for run_id in sorted(unresolved, key=lambda value: _order(starts[value])):
                item = evidence[run_id]
                parent = evidence.get(item.parent_run_id or "")
                if item.kind == "main":
                    evidence[run_id] = replace(item, lane_order=0, lane_depth=0)
                elif item.kind == "child_agent":
                    if parent is None or item.parent_run_id in unresolved:
                        continue
                    siblings = children[item.parent_run_id or ""]
                    sibling_index = siblings.index(run_id)
                    if parent.lane_order:
                        direction = -1 if parent.lane_order < 0 else 1
                        lane_order = direction * (abs(parent.lane_order) + 1)
                    else:
                        ordinal = sibling_index // 2 + 1
                        lane_order = -ordinal if sibling_index % 2 == 0 else ordinal
                    evidence[run_id] = replace(
                        item, lane_order=lane_order, lane_depth=abs(lane_order)
                    )
                elif item.kind == "continuation":
                    if parent is not None and item.parent_run_id in unresolved:
                        continue
                    lane_order = parent.lane_order if parent and parent.lane_order else 1
                    evidence[run_id] = replace(
                        item, lane_order=lane_order, lane_depth=abs(lane_order)
                    )
                else:
                    if parent is not None and item.parent_run_id in unresolved:
                        continue
                    direction = -1 if parent and parent.lane_order < 0 else 1
                    depth = (abs(parent.lane_order) if parent else 0) + 1
                    evidence[run_id] = replace(
                        item, lane_order=direction * depth, lane_depth=depth
                    )
                unresolved.remove(run_id)
                progressed = True
                break
            if not progressed:
                for run_id in unresolved:
                    evidence[run_id] = replace(evidence[run_id], lane_order=1, lane_depth=1)
                break
        return evidence

    @staticmethod
    def _terminal_status(
        events: Sequence[AuditEventBase], *, active: bool = False
    ) -> DisplayStatus:
        if active:
            return "running"
        terminals = [
            event
            for event in events
            if event.event_type.endswith(("_finished", "_received", "_failed"))
        ]
        return _status(terminals[-1:] or events[-1:]) if events else "incomplete"

    @staticmethod
    def _health_status(
        events: Sequence[AuditEventBase], terminal: DisplayStatus
    ) -> DisplayStatus:
        if terminal == "running":
            return "running"
        abnormal = any(
            getattr(event, "status", None) in _ABNORMAL
            or event.event_type in {"retry_scheduled", "policy_blocked", "audit_degraded"}
            or event.event_type.startswith("orphan_")
            for event in events
        )
        if terminal in {"failed", "cancelled", "interrupted", "incomplete"}:
            return terminal
        return "warning" if abnormal else terminal

    @staticmethod
    def _anomalies(events: Sequence[AuditEventBase]) -> list[AuditEventBase]:
        return [
            event
            for event in events
            if getattr(event, "status", None) in _ABNORMAL
            or event.event_type in {"retry_scheduled", "policy_blocked", "audit_degraded"}
            or event.event_type.startswith("orphan_")
        ]

    def _trace_full_nodes(
        self, trace_id: str, events: list[AuditEventBase], active: set[str]
    ) -> tuple[
        _BuildState,
        list[AuditGraphRegion],
        list[ExpansionGroup],
        dict[str, _RunEvidence],
    ]:
        state = _BuildState([], {}, [])
        evidence = self._run_evidence(events)
        task_events: list[AuditEventBase] = []
        lifecycle_keys: set[str] = set()
        for event in events:
            if event.event_type not in _SUBAGENT_EVENTS:
                continue
            idempotency_key = getattr(event, "idempotency_key", None)
            if isinstance(idempotency_key, str) and idempotency_key in lifecycle_keys:
                state.ignored.append(event.event_id)
                continue
            if isinstance(idempotency_key, str):
                lifecycle_keys.add(idempotency_key)
            task_events.append(event)
        by_run: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in events:
            if event.run_id and event.event_type not in _SUBAGENT_EVENTS:
                by_run[event.run_id].append(event)
        run_ids = sorted(by_run, key=lambda value: _order(by_run[value][0]))
        regions: list[AuditGraphRegion] = []
        expansions: list[ExpansionGroup] = []

        for region_order, run_id in enumerate(run_ids):
            grouped = sorted(by_run[run_id], key=_order)
            item = evidence.get(run_id, _RunEvidence(kind="unknown", parent_run_id=None))
            run_state, _run_regions, run_expansions = self._run_nodes(
                trace_id,
                run_id,
                grouped,
                trace_events=events,
                include_injections=True,
            )
            lane_id = f"lane:{item.kind}:{run_id}"
            starts = [event for event in grouped if event.event_type == "run_started"]
            finishes = [event for event in grouped if event.event_type == "run_finished"]
            lifecycle = [*starts, *finishes]
            terminal = self._terminal_status(lifecycle, active=run_id in active)
            health = self._health_status(grouped, terminal)
            anomalies = self._anomalies(grouped)
            run_node_id = f"run:{trace_id}:{run_id}"
            run_label = {
                "main": "Main agent",
                "child_agent": "Child agent",
                "continuation": "Result continuation",
                "unknown": "Unlinked run",
            }[item.kind]
            state.add(
                AuditGraphNode(
                    id=run_node_id,
                    type="run",
                    status=terminal,
                    label=run_label,
                    started_at=starts[0].occurred_at if starts else grouped[0].occurred_at,
                    finished_at=finishes[-1].occurred_at if finishes else None,
                    elapsed_ms=_elapsed(grouped),
                    raw_event_ids=[event.event_id for event in lifecycle],
                    raw_events=_event_refs(lifecycle),
                    region_id=lane_id,
                    parent_node_id=(
                        f"run:{trace_id}:{item.parent_run_id}"
                        if item.parent_run_id
                        else None
                    ),
                    child_node_ids=[],
                    expandable=False,
                    summary=AuditNodeSummary(
                        kind="run",
                        actor_type=item.kind,
                        stop_reason=(
                            getattr(finishes[-1], "stop_reason", None) if finishes else None
                        ),
                        iteration_count=len(
                            {event.iteration for event in grouped if event.iteration is not None}
                        ),
                        model_call_count=len(
                            {event.model_call_id for event in grouped if event.model_call_id}
                        ),
                        tool_call_count=len(
                            {event.tool_call_id for event in grouped if event.tool_call_id}
                        ),
                        subtype="lifecycle_mismatch" if len(starts) != 1 or len(finishes) > 1 else None,
                        identifier=run_id,
                        **_run_failure_summary(grouped),
                    ),
                    order=0,
                    run_id=run_id,
                    lane_id=lane_id,
                    lane_kind=item.kind,
                    lane_order=item.lane_order,
                    lane_depth=item.lane_depth,
                    lane_side=item.lane_side,
                    run_kind=item.kind,
                    terminal_status=terminal,
                    health_status=health,
                    anomaly_count=len(anomalies),
                    first_anomaly_event_id=anomalies[0].event_id if anomalies else None,
                    spawn_tool_call_id=item.spawn_tool_call_id,
                    continuation_of_run_id=item.continuation_of_run_id,
                    injection_source=item.injection_source,
                ),
                lifecycle,
            )
            members = [run_node_id]
            for node in run_state.nodes:
                node.region_id = lane_id
                node.run_id = run_id
                node.iteration = next(
                    (
                        event.iteration
                        for event in grouped
                        if event.event_id in node.raw_event_ids
                        and event.iteration is not None
                    ),
                    None,
                )
                node.lane_id = lane_id
                node.lane_kind = item.kind
                node.lane_order = item.lane_order
                node.lane_depth = item.lane_depth
                node.lane_side = item.lane_side
                node.run_kind = item.kind
                owned_events = [event for event in grouped if event.event_id in node.raw_event_ids]
                node.terminal_status = self._terminal_status(owned_events)
                node.health_status = self._health_status(owned_events, node.terminal_status)
                anomalies = self._anomalies(owned_events)
                node.anomaly_count = len(anomalies)
                node.first_anomaly_event_id = anomalies[0].event_id if anomalies else None
                node.spawn_tool_call_id = item.spawn_tool_call_id
                node.continuation_of_run_id = item.continuation_of_run_id
                injection_source = next(
                    (
                        str(value)
                        for event in owned_events
                        if (value := getattr(event, "injection_source", None))
                    ),
                    None,
                )
                node.injection_source = injection_source or item.injection_source
                state.add(node, owned_events)
                members.append(node.id)
            expansions.extend(run_expansions)
            regions.append(
                AuditGraphRegion(
                    id=lane_id,
                    type="lane",
                    label=run_label,
                    status=terminal,
                    member_node_ids=members,
                    order=region_order,
                    run_id=run_id,
                    lane_id=lane_id,
                    lane_kind=item.kind,
                    lane_order=item.lane_order,
                    lane_depth=item.lane_depth,
                    lane_side=item.lane_side,
                    terminal_status=terminal,
                    health_status=health,
                )
            )

        by_task: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in task_events:
            task_id = getattr(event, "subagent_task_id", None)
            if isinstance(task_id, str) and task_id:
                by_task[task_id].append(event)
        for task_id, grouped_events in sorted(
            by_task.items(), key=lambda item: _order(item[1][0])
        ):
            grouped = sorted(grouped_events, key=_order)
            first = grouped[0]
            latest = grouped[-1]
            metadata = first.source_metadata if isinstance(first.source_metadata, dict) else {}
            owner_run_id = next(
                (event.parent_run_id for event in grouped if event.parent_run_id),
                None,
            )
            child_run_id = next(
                (
                    event.run_id
                    for event in reversed(grouped)
                    if event.run_id and event.run_id != owner_run_id
                ),
                None,
            )
            child_evidence = evidence.get(
                child_run_id or "",
                _RunEvidence(kind="unknown", parent_run_id=owner_run_id),
            )
            node_id = f"task:{trace_id}:{task_id}"
            child_region = next(
                (
                    region
                    for region in regions
                    if region.run_id == child_run_id
                    and region.lane_id == f"lane:{child_evidence.kind}:{child_run_id}"
                ),
                None,
            )
            region_id = child_region.id if child_region is not None else f"task-region:{trace_id}:{task_id}"
            status = _task_status(grouped)
            task_label = next(
                (
                    value
                    for event in grouped
                    if isinstance((value := getattr(event, "task_label", None)), str)
                    and value
                ),
                None,
            )
            display_label = task_label or f"Task {task_id[:12]}"
            state.add(
                AuditGraphNode(
                    id=node_id,
                    type="task",
                    status=status,
                    label=display_label,
                    started_at=first.occurred_at,
                    finished_at=(
                        latest.occurred_at
                        if str(getattr(latest, "task_status", ""))
                        in {"succeeded", "failed", "cancelled", "timed_out", "lost"}
                        else None
                    ),
                    elapsed_ms=_elapsed(grouped),
                    raw_event_ids=[event.event_id for event in grouped],
                    raw_events=_event_refs(grouped),
                    region_id=region_id,
                    relations=[],
                    summary=AuditNodeSummary(
                        kind="task",
                        identifier=task_id,
                        task_id=task_id,
                        task_label=task_label,
                        task_revision=int(getattr(latest, "task_revision", 0)),
                        task_status=str(getattr(latest, "task_status", "")),
                        task_phase=str(getattr(latest, "task_phase", "")),
                        termination_state=str(getattr(latest, "termination_state", "")),
                        delivery_phase=str(getattr(latest, "delivery_phase", "")),
                        required_task=bool(getattr(latest, "required_task", False)),
                        lifecycle_event_count=len(grouped),
                        owner_run_id=owner_run_id,
                        child_run_id=child_run_id,
                        replaces_task_id=(
                            str(metadata["replaces_task_id"])
                            if metadata.get("replaces_task_id")
                            else None
                        ),
                        evidence_source=(
                            "legacy_inferred"
                            if bool(getattr(latest, "legacy_inferred", False))
                            else "recorded"
                        ),
                    ),
                    order=0,
                    lane_id=region_id,
                    lane_kind=child_evidence.kind,
                    lane_order=child_evidence.lane_order,
                    lane_depth=child_evidence.lane_depth,
                    lane_side=child_evidence.lane_side,
                    run_kind=child_evidence.kind,
                    task_id=task_id,
                    terminal_status=status,
                    health_status=status,
                ),
                grouped,
            )
            if child_region is not None:
                child_region.member_node_ids.append(node_id)
            else:
                regions.append(
                    AuditGraphRegion(
                        id=region_id,
                        type="task",
                        label=display_label,
                        status=status,
                        member_node_ids=[node_id],
                        order=len(regions),
                        lane_id=region_id,
                        lane_kind=child_evidence.kind,
                        lane_order=child_evidence.lane_order,
                        lane_depth=child_evidence.lane_depth,
                        lane_side=child_evidence.lane_side,
                        terminal_status=status,
                        health_status=status,
                        task_id=task_id,
                    )
                )

        for event in events:
            if event.event_id not in state.owners:
                state.ignored.append(event.event_id)
        global_order = {
            event.event_id: order for order, event in enumerate(sorted(events, key=_order))
        }
        for node in state.nodes:
            node.order = min((global_order[event_id] for event_id in node.raw_event_ids), default=0)
        return state, regions, expansions, evidence

    def _run_nodes(
        self,
        trace_id: str,
        run_id: str,
        events: list[AuditEventBase],
        *,
        trace_events: Sequence[AuditEventBase],
        include_injections: bool = False,
    ) -> tuple[_BuildState, list[AuditGraphRegion], list[ExpansionGroup]]:
        state = _BuildState([], {}, [])
        iterations = list(dict.fromkeys(event.iteration for event in events if event.iteration is not None))
        region_keys: list[int | None] = [*iterations]
        if any(event.iteration is None for event in events):
            region_keys.append(None)
        regions = [
            AuditGraphRegion(
                id=f"iteration:{trace_id}:{run_id}:{value if value is not None else 'unscoped'}",
                type="iteration" if value is not None else "unscoped",
                label=f"Iteration {value}" if value is not None else "Run context",
                status=_status(
                    [event for event in events if event.iteration == value],
                    trace_events=trace_events,
                ),
                member_node_ids=[],
                order=order,
            )
            for order, value in enumerate(region_keys)
        ]
        region_by_iteration = {value: region for value, region in zip(region_keys, regions)}

        groups: list[tuple[str, str, list[AuditEventBase], str]] = []
        consumed: set[str] = set()

        def add_group(node_type: str, key: str, selected: list[AuditEventBase], label: str) -> None:
            if selected:
                groups.append((node_type, key, selected, label))
                consumed.update(event.event_id for event in selected)

        for model_id in dict.fromkeys(event.model_call_id for event in events if event.model_call_id):
            selected = [
                event
                for event in events
                if event.model_call_id == model_id
                and event.event_type
                in {"model_request_started", "model_first_output", "model_response_received", "model_request_failed"}
            ]
            add_group("model_call", model_id or "", selected, "Model call")
        for attempt_id in dict.fromkeys(event.attempt_id for event in events if event.attempt_id):
            selected = [event for event in events if event.attempt_id == attempt_id]
            add_group("model_attempt", attempt_id or "", selected, "Model attempt")
        for tool_id in dict.fromkeys(event.tool_call_id for event in events if event.tool_call_id):
            selected = [
                event
                for event in events
                if event.tool_call_id == tool_id and event.event_type in {"tool_started", "tool_finished"}
            ]
            add_group("tool_call", tool_id or "", selected, getattr(selected[0], "tool_name", "Tool") if selected else "Tool")
        for event in events:
            if event.event_id in consumed:
                continue
            if event.event_type in _DECISIONS:
                add_group("decision", event.event_id, [event], event.event_type.replace("_", " "))
        for checkpoint_id in dict.fromkeys(
            event.checkpoint_id for event in events if event.checkpoint_id
        ):
            selected = [
                event
                for event in events
                if event.checkpoint_id == checkpoint_id and event.event_id not in consumed
            ]
            add_group("checkpoint", checkpoint_id or "", selected, "Checkpoint")
        for event in events:
            if event.event_id not in consumed and event.event_type.startswith("checkpoint_"):
                add_group("anomaly", event.event_id, [event], "Checkpoint")
        for goal_id in dict.fromkeys(event.goal_id for event in events if event.goal_id):
            selected = [event for event in events if event.goal_id == goal_id and event.event_id not in consumed]
            add_group("goal", goal_id or "", selected, "Goal")
        for delivery_id in dict.fromkeys(event.delivery_id for event in events if event.delivery_id):
            selected = [event for event in events if event.delivery_id == delivery_id and event.event_id not in consumed]
            add_group("delivery", delivery_id or "", selected, "Delivery")
        result_events = [event for event in events if event.event_type in _TURN_RESULTS and event.event_id not in consumed]
        add_group("turn_result", events[0].turn_id or run_id, result_events, "Turn result")
        if include_injections:
            for event in events:
                if event.event_id in consumed or event.event_type != "input_injected":
                    continue
                label = (
                    "Subagent result"
                    if getattr(event, "injection_source", None) == "subagent_result"
                    else "Input injected"
                )
                add_group("decision", event.event_id, [event], label)
        for event in events:
            if event.event_id in consumed:
                continue
            if event.event_type.startswith("orphan_") or event.event_type == "audit_degraded":
                add_group("anomaly", event.event_id, [event], "Audit anomaly")
            elif event.event_type in _PROCESS_EVENTS or event.event_type in {"run_started", "run_finished"}:
                state.ignored.append(event.event_id)
            else:
                add_group("decision", event.event_id, [event], event.event_type.replace("_", " "))

        groups.sort(key=lambda item: (_order(item[2][0]), item[0], item[1]))
        expansions: list[ExpansionGroup] = []
        by_model: dict[str, list[str]] = defaultdict(list)
        for order, (node_type, key, grouped, label) in enumerate(groups):
            first = grouped[0]
            region = region_by_iteration.get(first.iteration) or region_by_iteration[None]
            prefixes = {
                "model_call": "model",
                "model_attempt": "attempt",
                "tool_call": "tool",
                "decision": "decision",
                "checkpoint": "checkpoint",
                "goal": "goal",
                "turn_result": "result",
                "delivery": "delivery",
                "anomaly": "anomaly",
            }
            scoped_key = (
                f"{first.model_call_id}:{key}"
                if node_type == "model_attempt" and first.model_call_id
                else key
            )
            node_id = f"{prefixes[node_type]}:{trace_id}:{run_id}:{scoped_key}"
            parent_id = None
            if node_type == "model_attempt" and first.model_call_id:
                parent_id = f"model:{trace_id}:{run_id}:{first.model_call_id}"
                by_model[parent_id].append(node_id)
            node_status = _status(grouped, trace_events=trace_events)
            if node_type == "tool_call" and any(
                getattr(event, "status", None) in {"error", "timeout", "blocked"}
                for event in grouped
            ):
                node_status = "failed"
            if node_type == "checkpoint":
                transition_types = {event.event_type for event in grouped}
                node_status = (
                    "succeeded" if "checkpoint_cleared" in transition_types else "incomplete"
                )
            starts = [event for event in grouped if event.event_type.endswith("_started")]
            terminals = [
                event
                for event in grouped
                if event.event_type.endswith(("_finished", "_received", "_failed"))
            ]
            lifecycle_type = node_type in {"model_call", "model_attempt", "tool_call"}
            if lifecycle_type and (not starts or not terminals):
                node_status = "incomplete"
            summary = AuditNodeSummary(
                kind=node_type,
                provider=getattr(first, "provider", None) or getattr(first, "requested_provider", None),
                model=getattr(first, "model", None) or getattr(first, "requested_model", None),
                tool_name=getattr(first, "tool_name", None),
                decision_type=first.event_type if node_type == "decision" else None,
                subtype=("lifecycle_mismatch" if lifecycle_type and node_status == "incomplete" else None),
                attempt_count=(len(grouped) if node_type == "model_attempt" else None),
                identifier=key,
                checkpoint_phase=next(
                    (
                        getattr(event, "checkpoint_phase", None)
                        for event in reversed(grouped)
                        if getattr(event, "checkpoint_phase", None)
                    ),
                    None,
                ),
                checkpoint_version=max(
                    (
                        int(getattr(event, "checkpoint_version"))
                        for event in grouped
                        if getattr(event, "checkpoint_version", None) is not None
                    ),
                    default=None,
                ),
                checkpoint_restored=(
                    any(event.event_type == "checkpoint_restored" for event in grouped)
                    if node_type == "checkpoint"
                    else None
                ),
                checkpoint_cleared=(
                    any(event.event_type == "checkpoint_cleared" for event in grouped)
                    if node_type == "checkpoint"
                    else None
                ),
                transitions=(
                    [
                        {
                            "event_type": event.event_type,
                            "occurred_at": event.occurred_at.isoformat(),
                            "version": getattr(event, "checkpoint_version", None),
                        }
                        for event in grouped
                    ]
                    if node_type == "checkpoint"
                    else []
                ),
                delivery_result=(
                    str(getattr(grouped[-1], "status", "")) or None
                    if node_type == "delivery"
                    else None
                ),
                suppression_reason=(
                    getattr(grouped[-1], "suppression_reason", None)
                    if node_type == "delivery"
                    else None
                ),
                **(
                    _tool_failure_summary(grouped, trace_events)
                    if node_type == "tool_call"
                    else {}
                ),
            )
            state.add(
                AuditGraphNode(
                    id=node_id,
                    type=node_type,
                    status=node_status,
                    label=label,
                    started_at=starts[0].occurred_at if starts else first.occurred_at,
                    finished_at=terminals[-1].occurred_at if terminals else None,
                    elapsed_ms=_elapsed(grouped),
                    raw_event_ids=[event.event_id for event in grouped],
                    raw_events=_event_refs(grouped),
                    region_id=region.id,
                    parent_node_id=parent_id,
                    expandable=node_type == "model_call",
                    summary=summary,
                    order=order,
                ),
                grouped,
            )
            region.member_node_ids.append(node_id)
        by_id = {node.id: node for node in state.nodes}
        for owner_id, members in sorted(by_model.items()):
            if owner_id not in by_id:
                continue
            by_id[owner_id].child_node_ids = sorted(members)
            expansions.append(
                ExpansionGroup(
                    id=f"attempts:{owner_id}",
                    owner_node_id=owner_id,
                    member_node_ids=sorted(members),
                    default_expanded=(
                        len(members) > 1
                        or any(by_id[member].status != "succeeded" for member in members)
                    ),
                )
            )
        return state, regions, expansions

    @staticmethod
    def _trace_status(events: list[AuditEventBase], active: set[str]) -> DisplayStatus:
        if active & {event.run_id for event in events if event.run_id}:
            return "running"
        run_finishes = [event for event in events if event.event_type == "run_finished"]
        primary = _status(run_finishes or events, trace_events=events)
        if primary != "succeeded":
            return primary
        unknown_suppression = any(
            event.event_type == "delivery_finished"
            and getattr(event, "status", None) == "suppressed"
            and not expected_delivery_suppression(event, events)
            for event in events
        )
        auxiliary_warning = any(
            getattr(event, "status", None) in {"error", "timeout", "blocked"}
            for event in events
        )
        return "warning" if unknown_suppression or auxiliary_warning else "succeeded"

    def _edges(
        self, trace_id: str, events: list[AuditEventBase], state: _BuildState
    ) -> list[AuditGraphEdge]:
        edges: dict[tuple[str, str, str], AuditGraphEdge] = {}
        by_id = {event.event_id: event for event in events}
        node_by_id = {node.id: node for node in state.nodes}
        if all(node.type == "run" for node in state.nodes):
            for event in events:
                owner = state.owners.get(event.event_id)
                if not owner or not event.run_id:
                    continue
                for edge_type, related in (
                    ("parent_run", event.parent_run_id),
                    ("resumed_from", event.resumed_from_run_id),
                ):
                    source = f"run:{trace_id}:{related}" if related else None
                    if source and source in node_by_id and source != owner:
                        key = (edge_type, source, owner)
                        edges[key] = AuditGraphEdge(
                            id=f"{edge_type}:{source}:{owner}", type=edge_type, source=source, target=owner
                        )
        else:
            per_run = sorted(state.nodes, key=lambda node: (node.order, node.id))
            for earlier, later in zip(per_run, per_run[1:]):
                if earlier.parent_node_id == later.id or later.parent_node_id == earlier.id:
                    continue
                key = ("sequence", earlier.id, later.id)
                edges[key] = AuditGraphEdge(
                    id=f"sequence:{earlier.id}:{later.id}",
                    type="sequence",
                    source=earlier.id,
                    target=later.id,
                )
        for event in events:
            if not event.caused_by_event_id:
                continue
            declaring = state.owners.get(event.event_id)
            cause = state.owners.get(event.caused_by_event_id)
            if declaring is None:
                continue
            if cause is None:
                relation = AuditNodeRelation(
                    type="caused_by",
                    raw_source_event_id=event.caused_by_event_id,
                    raw_target_event_id=event.event_id,
                    resolution="unresolved" if event.caused_by_event_id not in by_id else "external",
                )
                node_by_id[declaring].relations.append(relation)
            elif cause == declaring:
                node_by_id[declaring].relations.append(
                    AuditNodeRelation(
                        type="caused_by",
                        raw_source_event_id=event.caused_by_event_id,
                        raw_target_event_id=event.event_id,
                        resolution="suppressed_same_node",
                        other_semantic_node_id=cause,
                    )
                )
            else:
                key = ("caused_by", cause, declaring)
                edges[key] = AuditGraphEdge(
                    id=f"caused_by:{cause}:{declaring}",
                    type="caused_by",
                    source=cause,
                    target=declaring,
                )
        attempt_nodes = {
            node.summary.identifier: node
            for node in state.nodes
            if node.type == "model_attempt" and node.summary.identifier
        }
        for event in events:
            if event.event_type != "retry_scheduled":
                continue
            prior = attempt_nodes.get(getattr(event, "prior_attempt_id", None))
            declaring = state.owners.get(event.event_id)
            decision = node_by_id.get(declaring or "")
            if prior is None or decision is None:
                continue
            candidates = [
                node
                for node in state.nodes
                if node.type == "model_attempt"
                and node.parent_node_id == prior.parent_node_id
                and node.order > decision.order
            ]
            if candidates:
                target = min(candidates, key=lambda node: (node.order, node.id))
                key = ("retry_of", prior.id, target.id)
                edges[key] = AuditGraphEdge(
                    id=f"retry_of:{prior.id}:{target.id}",
                    type="retry_of",
                    source=prior.id,
                    target=target.id,
                )
        self._add_tool_relation_edges(trace_id, events, state, edges)
        return list(edges.values())

    @staticmethod
    def _add_tool_relation_edges(
        trace_id: str,
        events: Sequence[AuditEventBase],
        state: _BuildState,
        edges: dict[tuple[str, str, str], AuditGraphEdge],
    ) -> None:
        """Project only explicit Tool relation IDs into safe semantic edges.

        Recovery is evidence, not causal inference: both terminal events must
        belong to the same trace and run, and the referenced call must have an
        abnormal terminal status while the declaring event succeeded.
        """
        node_by_id = {node.id: node for node in state.nodes}
        finished_by_call: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in events:
            if event.event_type == "tool_finished" and event.tool_call_id:
                finished_by_call[event.tool_call_id].append(event)
        relation_specs = (
            ("tool_retry", "retry_of_tool_call_ids"),
            ("tool_continuation", "continuation_of_tool_call_ids"),
            ("tool_recovery", "recovery_of_tool_call_ids"),
        )
        for target_event in events:
            if target_event.event_type != "tool_finished":
                continue
            target_call_id = target_event.tool_call_id
            if not target_call_id:
                continue
            target_node_id = state.owners.get(target_event.event_id)
            if target_node_id not in node_by_id:
                continue
            for relation, field_name in relation_specs:
                if relation == "tool_recovery" and getattr(target_event, "status", None) != "ok":
                    continue
                for source_call_id in getattr(target_event, field_name, None) or []:
                    if not isinstance(source_call_id, str) or not source_call_id:
                        continue
                    source_events = [
                        event
                        for event in finished_by_call.get(source_call_id, [])
                        if getattr(event, "status", None) in _ABNORMAL
                    ]
                    if not source_events:
                        continue
                    source_event = max(source_events, key=_order)
                    if source_event.trace_id != trace_id or target_event.trace_id != trace_id:
                        continue
                    if source_event.run_id != target_event.run_id:
                        continue
                    source_node_id = state.owners.get(source_event.event_id)
                    if source_node_id is None or source_node_id == target_node_id:
                        continue
                    if source_node_id not in node_by_id:
                        continue
                    key = (relation, source_node_id, target_node_id)
                    edges[key] = AuditGraphEdge(
                        id=f"{relation}:{source_node_id}:{target_node_id}",
                        type=relation,
                        relation=relation,
                        source=source_node_id,
                        target=target_node_id,
                        anchor=AuditEdgeAnchor(
                            source_event_id=source_event.event_id,
                            target_event_id=target_event.event_id,
                        ),
                        evidence_count=1,
                        evidence_kind=(
                            getattr(target_event, "recovery_evidence_kind", None)
                            if relation == "tool_recovery"
                            else "explicit_runtime_relation"
                        ),
                    )

    def _trace_full_edges(
        self,
        trace_id: str,
        events: list[AuditEventBase],
        state: _BuildState,
        evidence: dict[str, _RunEvidence],
    ) -> list[AuditGraphEdge]:
        edges: dict[tuple[str, str, str], AuditGraphEdge] = {}
        nodes_by_run: dict[str, list[AuditGraphNode]] = defaultdict(list)
        node_by_id = {node.id: node for node in state.nodes}
        event_by_id = {event.event_id: event for event in events}
        task_nodes = {node.task_id: node for node in state.nodes if node.type == "task"}
        task_by_child_run = {
            node.summary.child_run_id: node
            for node in task_nodes.values()
            if node.summary.child_run_id
        }
        for node in state.nodes:
            if node.run_id:
                nodes_by_run[node.run_id].append(node)

        for run_id, nodes in nodes_by_run.items():
            ordered_nodes = sorted(
                nodes,
                key=lambda node: (0 if node.type == "run" else 1, node.order, node.id),
            )
            for source, target in zip(ordered_nodes, ordered_nodes[1:]):
                key = ("sequence", source.id, target.id)
                edges[key] = AuditGraphEdge(
                    id=f"sequence:{source.id}:{target.id}",
                    type="sequence",
                    relation="sequence",
                    source=source.id,
                    target=target.id,
                    anchor=AuditEdgeAnchor(
                        source_event_id=source.raw_event_ids[-1] if source.raw_event_ids else None,
                        target_event_id=target.raw_event_ids[0] if target.raw_event_ids else None,
                    ),
                )

        for run_id, item in evidence.items():
            if item.kind != "child_agent" or not item.parent_run_id or not item.spawn_tool_call_id:
                continue
            source = f"tool:{trace_id}:{item.parent_run_id}:{item.spawn_tool_call_id}"
            task_node = task_by_child_run.get(run_id)
            target = task_node.id if task_node is not None else f"run:{trace_id}:{run_id}"
            if source not in node_by_id or target not in node_by_id:
                continue
            start = next(
                (
                    event
                    for event in events
                    if event.run_id == run_id and event.event_type == "run_started"
                ),
                None,
            )
            key = ("spawn_branch", source, target)
            edges[key] = AuditGraphEdge(
                id=f"spawn_branch:{source}:{target}",
                type="spawn_branch",
                relation="spawn_branch",
                source=source,
                target=target,
                anchor=AuditEdgeAnchor(
                    source_event_id=item.spawn_event_id,
                    target_event_id=(
                        task_node.raw_event_ids[0]
                        if task_node is not None and task_node.raw_event_ids
                        else start.event_id if start else None
                    ),
                ),
                evidence_kind="recorded_task_binding" if task_node is not None else None,
            )

        for task_id, task_node in task_nodes.items():
            child_run_id = task_node.summary.child_run_id
            child_node_id = f"run:{trace_id}:{child_run_id}" if child_run_id else None
            if child_node_id and child_node_id in node_by_id:
                start = next(
                    (
                        event
                        for event in events
                        if event.run_id == child_run_id and event.event_type == "run_started"
                    ),
                    None,
                )
                key = ("task_execution", task_node.id, child_node_id)
                edges[key] = AuditGraphEdge(
                    id=f"task_execution:{task_node.id}:{child_node_id}",
                    type="task_execution",
                    relation="task_execution",
                    source=task_node.id,
                    target=child_node_id,
                    anchor=AuditEdgeAnchor(
                        source_event_id=task_node.raw_event_ids[0],
                        target_event_id=start.event_id if start else None,
                    ),
                    evidence_kind="recorded_task_binding",
                )
                recovered = next(
                    (
                        event
                        for event in events
                        if getattr(event, "subagent_task_id", None) == task_id
                        and event.event_type == "subagent_recovered"
                    ),
                    None,
                )
                if recovered is not None:
                    recovery_key = ("task_recovery", task_node.id, child_node_id)
                    edges[recovery_key] = AuditGraphEdge(
                        id=f"task_recovery:{task_node.id}:{child_node_id}",
                        type="task_recovery",
                        relation="task_recovery",
                        source=task_node.id,
                        target=child_node_id,
                        anchor=AuditEdgeAnchor(
                            source_event_id=recovered.event_id,
                            target_event_id=start.event_id if start else None,
                        ),
                        evidence_kind="recorded_lifecycle_event",
                    )
            replaces_task_id = task_node.summary.replaces_task_id
            replaced = task_nodes.get(replaces_task_id or "")
            if replaced is not None:
                key = ("task_replacement", replaced.id, task_node.id)
                edges[key] = AuditGraphEdge(
                    id=f"task_replacement:{replaced.id}:{task_node.id}",
                    type="task_replacement",
                    relation="task_replacement",
                    source=replaced.id,
                    target=task_node.id,
                    anchor=AuditEdgeAnchor(
                        source_event_id=replaced.raw_event_ids[-1],
                        target_event_id=task_node.raw_event_ids[0],
                    ),
                    evidence_kind="recorded_replacement_id",
                )

        def terminal_source(run_id: str) -> tuple[str | None, str | None]:
            finish = next(
                (
                    event
                    for event in reversed(events)
                    if event.run_id == run_id and event.event_type == "run_finished"
                ),
                None,
            )
            candidates = [node for node in nodes_by_run.get(run_id, []) if node.type != "run"]
            if finish is not None:
                before_finish = [
                    node
                    for node in candidates
                    if node.started_at is not None and node.started_at <= finish.occurred_at
                ]
                if before_finish:
                    source = max(before_finish, key=lambda node: (node.started_at, node.id))
                    return source.id, finish.event_id
            run_node_id = f"run:{trace_id}:{run_id}"
            return (run_node_id if run_node_id in node_by_id else None), (
                finish.event_id if finish else None
            )

        ordered_events = sorted(events, key=_order)
        for index, event in enumerate(ordered_events):
            if (
                event.event_type != "input_injected"
                or getattr(event, "injection_source", None) != "subagent_result"
                or index == 0
            ):
                continue
            task_id = getattr(event, "subagent_task_id", None)
            task_node = task_nodes.get(task_id) if isinstance(task_id, str) else None
            target = state.owners.get(event.event_id)
            if task_node is not None and target is not None:
                key = ("result_return", task_node.id, target)
                edges[key] = AuditGraphEdge(
                    id=f"result_return:{task_node.id}:{target}",
                    type="result_return",
                    relation="result_return",
                    source=task_node.id,
                    target=target,
                    anchor=AuditEdgeAnchor(
                        source_event_id=task_node.raw_event_ids[-1],
                        target_event_id=event.event_id,
                    ),
                    evidence_kind="recorded_injection_task_id",
                )
                continue
            previous = ordered_events[index - 1]
            if (
                previous.event_type != "run_finished"
                or not previous.run_id
                or evidence.get(previous.run_id, _RunEvidence("unknown", None)).kind
                != "child_agent"
                or evidence[previous.run_id].parent_run_id
                != getattr(event, "target_run_id", event.run_id)
            ):
                continue
            source, source_event = terminal_source(previous.run_id)
            target = state.owners.get(event.event_id)
            if not source or not target:
                continue
            key = ("result_return", source, target)
            edges[key] = AuditGraphEdge(
                id=f"result_return:{source}:{target}",
                type="result_return",
                relation="result_return",
                source=source,
                target=target,
                anchor=AuditEdgeAnchor(
                    source_event_id=source_event,
                    target_event_id=event.event_id,
                ),
                evidence_kind="legacy_inferred",
            )

        for run_id, item in evidence.items():
            if item.kind != "continuation" or not item.continuation_of_run_id:
                continue
            source, source_event = terminal_source(item.continuation_of_run_id)
            target = f"run:{trace_id}:{run_id}"
            start = next(
                (
                    event
                    for event in events
                    if event.run_id == run_id and event.event_type == "run_started"
                ),
                None,
            )
            if not source or target not in node_by_id:
                continue
            key = ("result_return", source, target)
            edges[key] = AuditGraphEdge(
                id=f"result_return:{source}:{target}",
                type="result_return",
                relation="result_return",
                source=source,
                target=target,
                anchor=AuditEdgeAnchor(
                    source_event_id=source_event,
                    target_event_id=start.event_id if start else None,
                ),
            )
            task_node = task_by_child_run.get(item.continuation_of_run_id)
            if task_node is not None:
                task_key = ("result_return", task_node.id, target)
                delivered = next(
                    (
                        event
                        for event in events
                        if getattr(event, "subagent_task_id", None) == task_node.task_id
                        and event.event_type == "subagent_result_delivered"
                    ),
                    None,
                )
                edges[task_key] = AuditGraphEdge(
                    id=f"result_return:{task_node.id}:{target}",
                    type="result_return",
                    relation="result_return",
                    source=task_node.id,
                    target=target,
                    anchor=AuditEdgeAnchor(
                        source_event_id=(
                            delivered.event_id if delivered else task_node.raw_event_ids[-1]
                        ),
                        target_event_id=start.event_id if start else None,
                    ),
                    evidence_kind=(
                        "recorded_delivery_event" if delivered else "recorded_task_binding"
                    ),
                )

        derived = self._edges(trace_id, events, state)
        for edge in derived:
            if edge.type not in {"caused_by", "retry_of"}:
                continue
            relation = "retry" if edge.type == "retry_of" else edge.type
            key = (relation, edge.source, edge.target)
            source_event = node_by_id[edge.source].raw_event_ids[-1]
            target_event = node_by_id[edge.target].raw_event_ids[0]
            edges[key] = AuditGraphEdge(
                id=f"{relation}:{edge.source}:{edge.target}",
                type=relation,
                relation=relation,
                source=edge.source,
                target=edge.target,
                anchor=AuditEdgeAnchor(
                    source_event_id=source_event if source_event in event_by_id else None,
                    target_event_id=target_event if target_event in event_by_id else None,
                ),
            )
        self._add_tool_relation_edges(trace_id, events, state, edges)
        return list(edges.values())

    @staticmethod
    def _first_anomaly(
        events: list[AuditEventBase],
        owners: dict[str, str],
        nodes: list[AuditGraphNode],
        integrity_status: str,
    ) -> FirstAnomaly | None:
        node_by_id = {node.id: node for node in nodes}
        event_by_id = {event.event_id: event for event in events}
        lifecycle_pairs = {
            "run": ("run_started", "run_finished"),
            "model_call": ("model_request_started", ("model_response_received", "model_request_failed")),
            "model_attempt": ("model_attempt_started", "model_attempt_finished"),
            "tool_call": ("tool_started", "tool_finished"),
        }
        mismatch_candidates: list[tuple[tuple[Any, ...], AuditGraphNode, AuditEventBase]] = []
        for node in nodes:
            if node.summary.subtype != "lifecycle_mismatch" or node.type not in lifecycle_pairs:
                continue
            grouped = [event_by_id[event_id] for event_id in node.raw_event_ids if event_id in event_by_id]
            start_type, terminal_types = lifecycle_pairs[node.type]
            terminal_set = {terminal_types} if isinstance(terminal_types, str) else set(terminal_types)
            starts = [event for event in grouped if event.event_type == start_type]
            terminals = [event for event in grouped if event.event_type in terminal_set]
            if len(starts) > 1:
                mismatch = starts[1]
            elif len(terminals) > 1:
                mismatch = terminals[1]
            else:
                mismatch = grouped[0]
            mismatch_candidates.append((_order(mismatch), node, mismatch))
        if mismatch_candidates:
            _, node, event = min(mismatch_candidates, key=lambda item: item[0])
            return FirstAnomaly(
                node_id=node.id,
                event_id=event.event_id,
                category="lifecycle_mismatch",
                rule="earliest_qualifying_event",
            )
        for event in events:
            status = str(getattr(event, "status", ""))
            qualifying = (
                event.event_type.startswith("orphan_")
                or event.event_type in {"audit_degraded", "policy_blocked", "retry_scheduled"}
                or status in _ABNORMAL
            )
            if qualifying and event.event_id in owners:
                return FirstAnomaly(
                    node_id=owners[event.event_id],
                    event_id=event.event_id,
                    category=event.event_type,
                    rule="earliest_qualifying_event",
                )
        if integrity_status in {"invalid", "incomplete", "degraded"} and events:
            owner = owners.get(events[0].event_id)
            if owner and owner in node_by_id:
                return FirstAnomaly(
                    node_id=owner,
                    event_id=events[0].event_id,
                    category="integrity",
                    rule="integrity_anchor",
                )
        return None

    @staticmethod
    def _collapse_groups(
        nodes: list[AuditGraphNode], edges: list[AuditGraphEdge]
    ) -> list[CollapseGroup]:
        endpoints = {
            value
            for edge in edges
            if edge.type != "sequence"
            for value in (edge.source, edge.target)
        }
        eligible = [
            node
            for node in sorted(nodes, key=lambda item: item.order)
            if node.status == "succeeded"
            and node.type not in {"run", "anomaly", "external_reference"}
            and node.id not in endpoints
        ]
        groups: list[list[AuditGraphNode]] = []
        current: list[AuditGraphNode] = []
        for node in eligible:
            if current and (node.region_id != current[-1].region_id or node.order != current[-1].order + 1):
                if len(current) >= 3:
                    groups.append(current)
                current = []
            current.append(node)
        if len(current) >= 3:
            groups.append(current)
        result: list[CollapseGroup] = []
        for members in groups:
            member_ids = [node.id for node in members]
            digest = hashlib.sha256("\0".join(member_ids).encode()).hexdigest()[:16]
            elapsed = [node.elapsed_ms for node in members if node.elapsed_ms is not None]
            result.append(
                CollapseGroup(
                    id=f"success-chain:{digest}",
                    member_node_ids=member_ids,
                    status="succeeded",
                    label=f"{len(members)} successful operations",
                    elapsed_ms=sum(elapsed) if elapsed else None,
                )
            )
        return result

    @staticmethod
    def _validate_membership(
        regions: list[AuditGraphRegion], nodes: list[AuditGraphNode]
    ) -> None:
        region_members = {region.id: set(region.member_node_ids) for region in regions}
        node_ids = {node.id for node in nodes}
        if set().union(*region_members.values()) != node_ids:
            raise ValueError("graph region membership does not cover nodes")
        for node in nodes:
            if node.id not in region_members.get(node.region_id, set()):
                raise ValueError("node region_id disagrees with region membership")
