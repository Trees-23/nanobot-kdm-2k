"""Fail-open conversion of producer drafts into writer commit items."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from nanobot.audit.ids import new_audit_id
from nanobot.audit.integrity import canonical_json_bytes
from nanobot.audit.redaction import AuditRedactor
from nanobot.audit.schema import (
    AuditDegradedDraft,
    AuditEventDraftBase,
    AuditPayloadDraftBase,
    AuditRecoveredDraft,
    audit_event_draft_adapter,
    audit_payload_draft_adapter,
)
from nanobot.audit.writer import AuditWriter, CommitItem


@dataclass(frozen=True, slots=True)
class EmitResult:
    committed: bool
    degraded: bool = False
    disabled: bool = False
    accepted: bool = False


@dataclass(slots=True)
class _HealthState:
    first_failure_at: datetime | None = None
    last_failure_at: datetime | None = None
    lost_item_count: int = 0
    reasons: set[str] = field(default_factory=set)
    trace_ids: set[str] = field(default_factory=set)

    @property
    def degraded(self) -> bool:
        return self.first_failure_at is not None

    def record(self, trace_id: str | None, reason: str) -> None:
        now = datetime.now(UTC)
        self.first_failure_at = self.first_failure_at or now
        self.last_failure_at = now
        self.lost_item_count += 1
        self.reasons.add(reason)
        if trace_id:
            self.trace_ids.add(trace_id)

    def clear(self) -> None:
        self.first_failure_at = None
        self.last_failure_at = None
        self.lost_item_count = 0
        self.reasons.clear()
        self.trace_ids.clear()


def _common_health_fields(event_type: str) -> dict[str, object]:
    return {
        "event_id": new_audit_id(),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC),
        "monotonic_ns": 0,
        "trace_id": None,
        "turn_id": None,
        "run_id": None,
        "parent_run_id": None,
        "resumed_from_run_id": None,
        "caused_by_event_id": None,
        "model_call_id": None,
        "attempt_id": None,
        "tool_call_id": None,
        "checkpoint_id": None,
        "goal_id": None,
        "delivery_id": None,
        "session_key": None,
        "source_type": "audit",
        "source_metadata": {},
        "iteration": None,
    }


class AuditEmitter:
    def __init__(
        self,
        *,
        writer: AuditWriter,
        redactor: AuditRedactor,
        mode: str = "full",
    ) -> None:
        self._writer = writer
        self._redactor = redactor
        self._mode = mode
        self._health = _HealthState()
        self.recovery_events: list[AuditEventDraftBase] = []

    def record_failure(self, *, trace_id: str | None, error: BaseException) -> None:
        """Record an audit-internal loss without exposing exception content."""
        self._health.record(trace_id, type(error).__name__)

    def _redact_event(self, event: AuditEventDraftBase) -> AuditEventDraftBase:
        cleaned, _ = self._redactor.redact(event.model_dump(mode="json"))
        return audit_event_draft_adapter.validate_python(cleaned)

    def _redact_payload(
        self, payload: AuditPayloadDraftBase | None
    ) -> AuditPayloadDraftBase | None:
        if payload is None or self._mode == "metadata_only":
            return None
        cleaned, _ = self._redactor.redact(payload.model_dump(mode="json"))
        return audit_payload_draft_adapter.validate_python(cleaned)

    @staticmethod
    def _item(
        event: AuditEventDraftBase,
        payload: AuditPayloadDraftBase | None,
        *,
        critical: bool,
    ) -> CommitItem:
        estimate = len(canonical_json_bytes(event.model_dump(mode="json")))
        if payload is not None:
            estimate += len(canonical_json_bytes(payload.model_dump(mode="json")))
        return CommitItem(event, payload, max(estimate, 1), critical)

    async def emit(
        self,
        event: AuditEventDraftBase,
        *,
        payload: AuditPayloadDraftBase | None = None,
        critical: bool = False,
    ) -> EmitResult:
        try:
            redacted_event = self._redact_event(event)
            redacted_payload = self._redact_payload(payload)
            if redacted_payload is not None and redacted_payload.event_id != redacted_event.event_id:
                raise ValueError("payload event_id does not match event")
            receipt = await self._writer.submit(
                self._item(redacted_event, redacted_payload, critical=critical)
            )
        except Exception as error:
            self._health.record(event.trace_id, type(error).__name__)
            return EmitResult(committed=False, degraded=True)

        if self._health.degraded:
            await self._emit_recovery_if_needed()
        return EmitResult(
            committed=critical and receipt is not None,
            degraded=self._health.degraded,
            accepted=True,
        )

    async def _emit_recovery_if_needed(self) -> None:
        if not self._health.degraded:
            return
        assert self._health.first_failure_at is not None
        assert self._health.last_failure_at is not None
        degraded = AuditDegradedDraft.model_validate(
            {
                **_common_health_fields("audit_degraded"),
                "failure_started_at": self._health.first_failure_at,
                "failure_last_seen_at": self._health.last_failure_at,
                "lost_item_count": self._health.lost_item_count,
                "failure_reason": ",".join(sorted(self._health.reasons)),
                "affected_trace_ids": sorted(self._health.trace_ids),
            }
        )
        recovered = AuditRecoveredDraft.model_validate(
            {
                **_common_health_fields("audit_recovered"),
                "degraded_started_at": self._health.first_failure_at,
                "degraded_ended_at": datetime.now(UTC),
                "last_committed_epoch": self._writer.last_committed_epoch,
            }
        )
        try:
            await self._writer.submit(self._item(degraded, None, critical=True))
            await self._writer.submit(self._item(recovered, None, critical=True))
        except Exception as error:
            self._health.record(None, type(error).__name__)
            return
        self.recovery_events.extend((degraded, recovered))
        self._health.clear()


class DisabledAuditEmitter:
    async def emit(
        self,
        event: AuditEventDraftBase,
        *,
        payload: AuditPayloadDraftBase | None = None,
        critical: bool = False,
    ) -> EmitResult:
        del event, payload, critical
        return EmitResult(committed=False, disabled=True)
