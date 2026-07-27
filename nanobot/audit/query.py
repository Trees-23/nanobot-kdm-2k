"""Typed, scan-backed audit query and causal Trace reconstruction."""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from nanobot.audit.reader import AuditReader
from nanobot.audit.schema import AuditEventBase, AuditPayloadBase
from nanobot.audit.types import IntegrityStatus, RunStatus
from nanobot.audit.verify import AuditVerifier, VerificationReport

_MAX_LIMIT = 500
_DECISION_TYPES = {
    "provider_route_decision",
    "retry_scheduled",
    "policy_blocked",
    "continuation_requested",
    "finalization_requested",
    "cancel_requested",
}
_INTEGRITY_RANK = {
    IntegrityStatus.VALID: 0,
    IntegrityStatus.DEGRADED: 1,
    IntegrityStatus.INCOMPLETE: 2,
    IntegrityStatus.UNKNOWN: 3,
    IntegrityStatus.INVALID: 4,
}


class TraceFilter(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    session_key: str | None = None
    source_type: str | None = None
    run_status: RunStatus | None = None
    model: str | None = None
    tool: str | None = None
    limit: int = Field(default=50, ge=1, le=_MAX_LIMIT)
    cursor: str | None = None


class TraceSummary(BaseModel):
    first_seen: datetime
    last_seen: datetime
    turn_count: int
    run_count: int
    terminal_run_statuses: list[RunStatus]
    integrity_status: IntegrityStatus


class TurnView(BaseModel):
    turn_id: str
    session_key: str | None
    source_type: str | None
    event_ids: list[str]


class RunNode(BaseModel):
    run_id: str
    parent_run_id: str | None
    resumed_from_run_id: str | None
    status: RunStatus | None
    children: list[RunNode] = Field(default_factory=list)


class RunTree(BaseModel):
    roots: list[RunNode]
    resumptions: list[RunNode]


class TraceView(BaseModel):
    trace_id: str
    summary: TraceSummary
    turns: list[TurnView]
    run_tree: RunTree
    timeline: list[AuditEventBase]
    payloads: dict[str, AuditPayloadBase] | None = None
    decisions: list[AuditEventBase]
    integrity: VerificationReport


class TracePage(BaseModel):
    items: list[TraceSummary]
    trace_ids: list[str]
    next_cursor: str | None = None


def _cursor(last_seen: datetime, trace_id: str) -> str:
    raw = f"{last_seen.isoformat()}\0{trace_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        timestamp, trace_id = raw.split("\0", 1)
        return datetime.fromisoformat(timestamp), trace_id
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid audit query cursor") from error


class AuditQuery:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.reader = AuditReader(root)
        self.verifier = AuditVerifier(root)

    @classmethod
    def from_root(cls, root: Path) -> AuditQuery:
        return cls(root)

    def _records(self) -> tuple[list[AuditEventBase], dict[str, AuditPayloadBase], dict[str, str]]:
        events: list[AuditEventBase] = []
        payloads: dict[str, AuditPayloadBase] = {}
        owners: dict[str, str] = {}
        for process_id in self.reader.process_ids():
            result = self.reader.read_process(process_id)
            events.extend(result.events)
            payloads.update((payload.payload_id, payload) for payload in result.payloads)
            for event in result.events:
                owners[event.event_id] = process_id
        return events, payloads, owners

    @staticmethod
    def _event_order(event: AuditEventBase) -> tuple[Any, ...]:
        return (
            event.occurred_at,
            event.process_instance_id,
            event.durability_epoch,
            event.segment_id,
            event.segment_sequence,
            event.event_id,
        )

    def _integrity(
        self,
        process_ids: set[str],
        *,
        causal_cycle: bool = False,
    ) -> VerificationReport:
        reports = [self.verifier.verify_process(process_id) for process_id in process_ids]
        if not reports:
            return VerificationReport(IntegrityStatus.UNKNOWN, (), ())
        status = max((report.status for report in reports), key=lambda item: _INTEGRITY_RANK[item])
        errors = {code for report in reports for code in report.error_codes}
        warnings = {code for report in reports for code in report.warning_codes}
        if causal_cycle:
            status = IntegrityStatus.INVALID
            errors.add("causal_cycle")
        return VerificationReport(status, tuple(sorted(errors)), tuple(sorted(warnings)))

    @staticmethod
    def _run_tree(events: list[AuditEventBase]) -> tuple[RunTree, bool]:
        starts: dict[str, AuditEventBase] = {}
        statuses: dict[str, RunStatus] = {}
        for event in events:
            if event.event_type == "run_started" and event.run_id:
                starts.setdefault(event.run_id, event)
            elif event.event_type == "run_finished" and event.run_id:
                try:
                    statuses[event.run_id] = RunStatus(getattr(event, "status"))
                except ValueError:
                    continue

        children: dict[str, list[str]] = defaultdict(list)
        cycle = False
        for run_id, event in starts.items():
            if event.parent_run_id and event.parent_run_id in starts:
                children[event.parent_run_id].append(run_id)

        visiting: set[str] = set()
        visited: set[str] = set()

        def build(run_id: str) -> RunNode:
            nonlocal cycle
            event = starts[run_id]
            if run_id in visiting:
                cycle = True
                return RunNode(
                    run_id=run_id,
                    parent_run_id=event.parent_run_id,
                    resumed_from_run_id=event.resumed_from_run_id,
                    status=statuses.get(run_id),
                )
            visiting.add(run_id)
            nodes = [build(child) for child in sorted(children.get(run_id, []))]
            visiting.discard(run_id)
            visited.add(run_id)
            return RunNode(
                run_id=run_id,
                parent_run_id=event.parent_run_id,
                resumed_from_run_id=event.resumed_from_run_id,
                status=statuses.get(run_id),
                children=nodes,
            )

        roots = [
            build(run_id)
            for run_id, event in sorted(starts.items())
            if not event.parent_run_id or event.parent_run_id not in starts
        ]
        for run_id in sorted(set(starts) - visited):
            roots.append(build(run_id))
        resumptions = [
            RunNode(
                run_id=run_id,
                parent_run_id=event.parent_run_id,
                resumed_from_run_id=event.resumed_from_run_id,
                status=statuses.get(run_id),
                children=[],
            )
            for run_id, event in sorted(starts.items())
            if event.resumed_from_run_id
        ]
        return RunTree(roots=roots, resumptions=resumptions), cycle

    def load_trace(self, trace_id: str, *, include_payloads: bool = False) -> TraceView:
        all_events, all_payloads, owners = self._records()
        events = sorted(
            (event for event in all_events if event.trace_id == trace_id),
            key=self._event_order,
        )
        if not events:
            raise KeyError(f"audit trace not found: {trace_id}")
        by_turn: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in events:
            if event.turn_id:
                by_turn[event.turn_id].append(event)
        turns = [
            TurnView(
                turn_id=turn_id,
                session_key=next((event.session_key for event in rows if event.session_key), None),
                source_type=next((event.source_type for event in rows if event.source_type), None),
                event_ids=[event.event_id for event in rows],
            )
            for turn_id, rows in sorted(
                by_turn.items(), key=lambda item: self._event_order(item[1][0])
            )
        ]
        run_tree, cycle = self._run_tree(events)
        process_ids = {owners[event.event_id] for event in events}
        integrity = self._integrity(process_ids, causal_cycle=cycle)
        statuses = [
            RunStatus(getattr(event, "status"))
            for event in events
            if event.event_type == "run_finished"
        ]
        referenced_payloads = {
            event.payload_id: all_payloads[event.payload_id]
            for event in events
            if event.payload_id and event.payload_id in all_payloads
        }
        return TraceView(
            trace_id=trace_id,
            summary=TraceSummary(
                first_seen=events[0].occurred_at,
                last_seen=events[-1].occurred_at,
                turn_count=len(by_turn),
                run_count=len({event.run_id for event in events if event.run_id}),
                terminal_run_statuses=statuses,
                integrity_status=integrity.status,
            ),
            turns=turns,
            run_tree=run_tree,
            timeline=events,
            payloads=referenced_payloads if include_payloads else None,
            decisions=[event for event in events if event.event_type in _DECISION_TYPES],
            integrity=integrity,
        )

    def find_traces(self, filters: TraceFilter | None = None) -> TracePage:
        filters = filters or TraceFilter()
        events, _, _ = self._records()
        by_trace: dict[str, list[AuditEventBase]] = defaultdict(list)
        for event in events:
            if event.trace_id:
                by_trace[event.trace_id].append(event)
        rows: list[tuple[datetime, str, TraceSummary]] = []
        for trace_id, trace_events in by_trace.items():
            trace_events.sort(key=self._event_order)
            if filters.since and trace_events[-1].occurred_at < filters.since:
                continue
            if filters.until and trace_events[0].occurred_at > filters.until:
                continue
            if filters.session_key and not any(
                event.session_key == filters.session_key for event in trace_events
            ):
                continue
            if filters.source_type and not any(
                event.source_type == filters.source_type for event in trace_events
            ):
                continue
            if filters.run_status and not any(
                event.event_type == "run_finished"
                and getattr(event, "status", None) == filters.run_status
                for event in trace_events
            ):
                continue
            if filters.model and not any(
                getattr(event, "model", None) == filters.model for event in trace_events
            ):
                continue
            if filters.tool and not any(
                getattr(event, "tool_name", None) == filters.tool for event in trace_events
            ):
                continue
            view = self.load_trace(trace_id)
            rows.append((view.summary.last_seen, trace_id, view.summary))
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        if filters.cursor:
            cursor = _decode_cursor(filters.cursor)
            rows = [row for row in rows if (row[0], row[1]) < cursor]
        selected = rows[: filters.limit]
        next_cursor = None
        if len(rows) > filters.limit and selected:
            next_cursor = _cursor(selected[-1][0], selected[-1][1])
        return TracePage(
            items=[row[2] for row in selected],
            trace_ids=[row[1] for row in selected],
            next_cursor=next_cursor,
        )
