"""Audit producers for SDK returns and channel delivery ownership."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from nanobot.audit.ids import new_audit_id
from nanobot.audit.schema import (
    DeliveryAttemptedDraft,
    DeliveryFinishedDraft,
    DeliveryPayloadDraft,
    DeliveryRetryScheduledDraft,
    ReturnedToCallerDraft,
    TurnOutputPayloadDraft,
)
from nanobot.bus.events import AUDIT_CONTEXT_META, OutboundMessage


def _context(metadata: dict[str, Any]) -> dict[str, str] | None:
    value = metadata.get(AUDIT_CONTEXT_META)
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(key), str) and value[key] for key in ("trace_id", "turn_id")):
        return None
    return value


def _common(
    event_type: str,
    context: dict[str, str],
    *,
    delivery_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": new_audit_id(),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC),
        "monotonic_ns": time.monotonic_ns(),
        "trace_id": context["trace_id"],
        "turn_id": context["turn_id"],
        "run_id": context.get("run_id"),
        "parent_run_id": None,
        "resumed_from_run_id": None,
        "caused_by_event_id": None,
        "model_call_id": None,
        "attempt_id": None,
        "tool_call_id": None,
        "checkpoint_id": None,
        "goal_id": None,
        "delivery_id": delivery_id,
        "session_key": None,
        "source_type": "delivery" if delivery_id else "sdk",
        "source_metadata": {},
        "iteration": None,
    }


async def emit_returned_to_caller(
    emitter: Any,
    response: OutboundMessage | None,
    result: Any,
    *,
    status: str,
    context_override: dict[str, Any] | None = None,
) -> None:
    if getattr(emitter, "audit_disabled", False):
        return
    context = (
        _context({AUDIT_CONTEXT_META: context_override})
        if context_override is not None
        else (_context(response.metadata) if response is not None else None)
    )
    if context is None:
        return
    event = ReturnedToCallerDraft.model_validate(
        {**_common("returned_to_caller", context), "status": status}
    )
    content = getattr(result, "content", "") if result is not None else ""
    payload = TurnOutputPayloadDraft.model_validate(
        {
            "payload_id": new_audit_id(),
            "event_id": event.event_id,
            "payload_kind": "turn_output",
            "content": {
                "content": content,
                "media_refs": list(getattr(result, "media", []) or []),
                "response_kind": "sdk_result",
            },
        }
    )
    await emitter.emit(event, payload=payload, critical=True)


class DeliveryAuditRecorder:
    def __init__(self, emitter: Any, msg: OutboundMessage) -> None:
        self.emitter = emitter
        self.msg = msg
        self.context = (
            None
            if getattr(emitter, "audit_disabled", False)
            else _context(msg.metadata)
        )
        self.delivery_id = new_audit_id()

    @property
    def enabled(self) -> bool:
        return self.context is not None

    async def attempted(self, ordinal: int) -> None:
        if self.context is None:
            return
        event = DeliveryAttemptedDraft.model_validate(
            {
                **_common(
                    "delivery_attempted",
                    self.context,
                    delivery_id=self.delivery_id,
                ),
                "channel": self.msg.channel,
                "attempt_ordinal": ordinal,
            }
        )
        adapter_metadata = {
            key: value
            for key, value in self.msg.metadata.items()
            if key != AUDIT_CONTEXT_META
        }
        payload = DeliveryPayloadDraft.model_validate(
            {
                "payload_id": new_audit_id(),
                "event_id": event.event_id,
                "payload_kind": "delivery",
                "content": {
                    "channel": self.msg.channel,
                    "content_fingerprint": hashlib.sha256(
                        self.msg.content.encode("utf-8")
                    ).hexdigest(),
                    "byte_count": len(self.msg.content.encode("utf-8")),
                    "adapter_metadata": adapter_metadata,
                },
            }
        )
        await self.emitter.emit(event, payload=payload)

    async def retry(self, ordinal: int, delay: float) -> None:
        if self.context is None:
            return
        event = DeliveryRetryScheduledDraft.model_validate(
            {
                **_common(
                    "delivery_retry_scheduled",
                    self.context,
                    delivery_id=self.delivery_id,
                ),
                "failed_attempt_ordinal": ordinal,
                "delay_ms": max(0, int(round(delay * 1000))),
                "policy_name": "channel_exponential_backoff",
            }
        )
        await self.emitter.emit(event)

    async def finished(self, ordinal: int, status: str) -> None:
        if self.context is None:
            return
        event = DeliveryFinishedDraft.model_validate(
            {
                **_common(
                    "delivery_finished",
                    self.context,
                    delivery_id=self.delivery_id,
                ),
                "final_attempt_ordinal": ordinal,
                "status": status,
                "remote_receipt_id": None,
            }
        )
        await self.emitter.emit(event, critical=True)
