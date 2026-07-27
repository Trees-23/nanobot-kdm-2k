"""Deterministic Trace, Turn, and Run identity propagation."""

from __future__ import annotations

from dataclasses import dataclass, replace

from nanobot.audit.ids import new_audit_id


@dataclass(frozen=True, slots=True)
class TraceTurnInput:
    session_key: str
    source_type: str = "user"
    actor_type: str = "user"
    active_goal_trace_id: str | None = None
    checkpoint_trace_id: str | None = None
    checkpoint_run_id: str | None = None
    injected_trace_id: str | None = None
    injected_run_id: str | None = None
    reply_trace_id: str | None = None
    reply_source_id: str | None = None
    explicit_trace_id: str | None = None
    explicit_link_source_id: str | None = None
    stop_target_trace_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditRunContext:
    trace_id: str
    turn_id: str
    run_id: str
    parent_run_id: str | None = None
    resumed_from_run_id: str | None = None
    source_type: str = "agent"

    def child_run(self, *, source_type: str) -> AuditRunContext:
        return replace(
            self,
            run_id=new_audit_id(),
            parent_run_id=self.run_id,
            resumed_from_run_id=None,
            source_type=source_type,
        )


@dataclass(frozen=True, slots=True)
class AuditTurnContext:
    trace_id: str
    turn_id: str
    session_key: str
    source_type: str
    actor_type: str
    link_reason: str
    linked_source_id: str | None = None

    def new_run(
        self,
        *,
        source_type: str = "agent",
        resumed_from_run_id: str | None = None,
    ) -> AuditRunContext:
        return AuditRunContext(
            trace_id=self.trace_id,
            turn_id=self.turn_id,
            run_id=new_audit_id(),
            parent_run_id=None,
            resumed_from_run_id=resumed_from_run_id,
            source_type=source_type,
        )


class TraceContextResolver:
    """Resolve only explicit evidence links; Session identity never links Traces."""

    def resolve_turn(self, value: TraceTurnInput) -> AuditTurnContext:
        trace_id: str
        reason: str
        source_id: str | None

        if value.active_goal_trace_id:
            trace_id, reason, source_id = (
                value.active_goal_trace_id,
                "active_goal",
                value.active_goal_trace_id,
            )
        elif value.checkpoint_trace_id:
            trace_id, reason, source_id = (
                value.checkpoint_trace_id,
                "checkpoint_restored",
                value.checkpoint_run_id,
            )
        elif value.injected_trace_id:
            trace_id, reason, source_id = (
                value.injected_trace_id,
                "active_run_injection",
                value.injected_run_id,
            )
        elif value.reply_trace_id:
            trace_id, reason, source_id = (
                value.reply_trace_id,
                "explicit_parent_reference",
                value.reply_source_id,
            )
        elif value.explicit_trace_id:
            trace_id, reason, source_id = (
                value.explicit_trace_id,
                "explicit_operator_link",
                value.explicit_link_source_id,
            )
        elif value.stop_target_trace_ids:
            targets = set(value.stop_target_trace_ids)
            if len(targets) == 1:
                trace_id, reason, source_id = (
                    next(iter(targets)),
                    "stop_shared_trace",
                    None,
                )
            else:
                trace_id, reason, source_id = new_audit_id(), "control_trace_created", None
        else:
            trace_id, reason, source_id = new_audit_id(), "created", None

        return AuditTurnContext(
            trace_id=trace_id,
            turn_id=new_audit_id(),
            session_key=value.session_key,
            source_type=value.source_type,
            actor_type=value.actor_type,
            link_reason=reason,
            linked_source_id=source_id,
        )
