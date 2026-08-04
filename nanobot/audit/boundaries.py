"""Typed audit producers for turn-owned runtime boundaries."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from nanobot.audit.context import AuditRunContext, AuditTurnContext
from nanobot.audit.emitter import AuditEmitter, DisabledAuditEmitter
from nanobot.audit.ids import new_audit_id
from nanobot.audit.schema import (
    CancelRequestedDraft,
    CheckpointClearedDraft,
    CheckpointPayloadDraft,
    CheckpointRestoredDraft,
    CheckpointWrittenDraft,
    InputInjectedDraft,
    TraceCreatedDraft,
    TraceLinkedDraft,
    TurnFinishedDraft,
    TurnInputPayloadDraft,
    TurnResponsePreparedDraft,
    TurnStartedDraft,
)


class TurnAuditRecorder:
    def __init__(
        self,
        emitter: AuditEmitter | DisabledAuditEmitter,
        turn: AuditTurnContext,
    ) -> None:
        self.emitter = emitter
        self.turn = turn
        self.disabled = bool(getattr(emitter, "audit_disabled", False))
        self._finished = False

    def common(
        self,
        event_type: str,
        *,
        run: AuditRunContext | None = None,
        **ids: Any,
    ) -> dict[str, Any]:
        return {
            "event_id": new_audit_id(),
            "event_type": event_type,
            "occurred_at": datetime.now(UTC),
            "monotonic_ns": time.monotonic_ns(),
            "trace_id": self.turn.trace_id,
            "turn_id": self.turn.turn_id,
            "run_id": run.run_id if run is not None else None,
            "parent_run_id": run.parent_run_id if run is not None else None,
            "resumed_from_run_id": run.resumed_from_run_id if run is not None else None,
            "caused_by_event_id": None,
            "model_call_id": None,
            "attempt_id": None,
            "tool_call_id": None,
            "checkpoint_id": None,
            "goal_id": None,
            "delivery_id": None,
            "session_key": self.turn.session_key,
            "source_type": self.turn.source_type,
            "source_metadata": {},
            "iteration": None,
            **ids,
        }

    async def started(self, message: Any) -> None:
        if self.disabled:
            return
        if self.turn.link_reason in {"created", "control_trace_created"}:
            trace = TraceCreatedDraft.model_validate(
                {
                    **self.common("trace_created"),
                    "actor_type": self.turn.actor_type,
                    "creation_reason": (
                        "control_request"
                        if self.turn.link_reason == "control_trace_created"
                        else "inbound_message"
                    ),
                }
            )
        else:
            trace = TraceLinkedDraft.model_validate(
                {
                    **self.common("trace_linked"),
                    "actor_type": self.turn.actor_type,
                    "link_reason": self.turn.link_reason,
                    "linked_source_id": self.turn.linked_source_id or self.turn.trace_id,
                }
            )
        await self.emitter.emit(trace, critical=True)

        event = TurnStartedDraft.model_validate(self.common("turn_started"))
        payload = TurnInputPayloadDraft.model_validate(
            {
                "payload_id": new_audit_id(),
                "event_id": event.event_id,
                "payload_kind": "turn_input",
                "content": {
                    "role": "user" if self.turn.actor_type == "user" else "system",
                    "content": message.content,
                    "media_refs": list(message.media or []),
                    "source_message_id": (message.metadata or {}).get("message_id"),
                },
            }
        )
        await self.emitter.emit(event, payload=payload, critical=True)

    async def response_prepared(self, *, response_kind: str) -> None:
        if self.disabled:
            return
        event = TurnResponsePreparedDraft.model_validate(
            {**self.common("turn_response_prepared"), "response_kind": response_kind}
        )
        await self.emitter.emit(event, critical=True)

    async def cancel_requested(
        self,
        *,
        target_run_ids: list[str],
        requested_by: str,
    ) -> str:
        if self.disabled:
            return new_audit_id()
        event = CancelRequestedDraft.model_validate(
            {
                **self.common("cancel_requested"),
                "requested_by": requested_by,
                "target_run_ids": target_run_ids,
            }
        )
        await self.emitter.emit(event, critical=True)
        return event.event_id

    async def input_injected(
        self,
        *,
        run: AuditRunContext,
        injection_source: str,
        subagent_task_id: str | None = None,
    ) -> None:
        if self.disabled:
            return
        event = InputInjectedDraft.model_validate(
            {
                **self.common("input_injected", run=run),
                "injection_source": injection_source,
                "target_run_id": run.run_id,
                "subagent_task_id": subagent_task_id,
            }
        )
        await self.emitter.emit(event)

    async def checkpoint_written(
        self,
        checkpoint: dict[str, Any],
        *,
        run: AuditRunContext,
    ) -> None:
        if self.disabled:
            return
        checkpoint_id = str(checkpoint["_audit_checkpoint_id"])
        version = int(checkpoint["_audit_checkpoint_version"])
        phase = str(checkpoint.get("phase") or "unknown")
        event = CheckpointWrittenDraft.model_validate(
            {
                **self.common(
                    "checkpoint_written",
                    run=run,
                    checkpoint_id=checkpoint_id,
                ),
                "checkpoint_version": version,
                "checkpoint_phase": phase,
            }
        )
        payload = CheckpointPayloadDraft.model_validate(
            {
                "payload_id": new_audit_id(),
                "event_id": event.event_id,
                "payload_kind": "checkpoint",
                "content": {
                    "checkpoint_version": version,
                    "checkpoint_phase": phase,
                    "checkpoint_content": checkpoint,
                },
            }
        )
        await self.emitter.emit(event, payload=payload, critical=True)

    async def checkpoint_restored(
        self,
        checkpoint: dict[str, Any],
        *,
        run: AuditRunContext,
    ) -> None:
        if self.disabled:
            return
        checkpoint_id = str(checkpoint.get("_audit_checkpoint_id") or new_audit_id())
        source_run_id = str(checkpoint.get("_audit_run_id") or checkpoint_id)
        event = CheckpointRestoredDraft.model_validate(
            {
                **self.common(
                    "checkpoint_restored",
                    run=run,
                    checkpoint_id=checkpoint_id,
                ),
                "source_run_id": source_run_id,
                "checkpoint_version": int(checkpoint.get("_audit_checkpoint_version") or 1),
            }
        )
        await self.emitter.emit(event, critical=True)

    async def checkpoint_cleared(
        self,
        checkpoint: dict[str, Any],
        *,
        run: AuditRunContext,
        reason: str,
    ) -> None:
        if self.disabled:
            return
        checkpoint_id = str(checkpoint.get("_audit_checkpoint_id") or new_audit_id())
        event = CheckpointClearedDraft.model_validate(
            {
                **self.common(
                    "checkpoint_cleared",
                    run=run,
                    checkpoint_id=checkpoint_id,
                ),
                "clear_reason": reason,
            }
        )
        await self.emitter.emit(event, critical=True)

    async def finished(self, *, status: str) -> None:
        if self.disabled:
            self._finished = True
            return
        if self._finished:
            return
        event = TurnFinishedDraft.model_validate(
            {**self.common("turn_finished"), "status": status}
        )
        await self.emitter.emit(event, critical=True)
        self._finished = True
