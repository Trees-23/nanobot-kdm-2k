"""Integrity and lifecycle verification for committed audit evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from nanobot.audit.integrity import verify_chain
from nanobot.audit.reader import AuditReader, ProcessReadResult
from nanobot.audit.types import IntegrityStatus


@dataclass(frozen=True, slots=True)
class VerificationReport:
    status: IntegrityStatus
    error_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]


_INVALID_DIAGNOSTICS = {
    "catalog_fork",
    "catalog_hash_mismatch",
    "catalog_offset_beyond_file",
    "catalog_root_invalid",
    "committed_prefix_partial_line",
    "invalid_catalog_record",
    "invalid_committed_record",
    "missing_cataloged_payload_segment",
    "missing_cataloged_segment",
    "unlinked_catalog_records",
    "unregistered_cataloged_segment",
}


class AuditVerifier:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.reader = AuditReader(root)

    @staticmethod
    def _chain_errors(result: ProcessReadResult) -> list[str]:
        errors: list[str] = []
        event_segments = defaultdict(list)
        for event in result.events:
            event_segments[event.segment_id].append(event)
        for events in event_segments.values():
            events.sort(key=lambda event: event.segment_sequence)
            report = verify_chain(events, hash_field="event_hash")
            if not report.valid:
                errors.append(f"event_{report.error_code}")
        payload_segments = defaultdict(list)
        for payload in result.payloads:
            payload_segments[payload.payload_segment_id].append(payload)
        for payloads in payload_segments.values():
            payloads.sort(key=lambda payload: payload.payload_segment_sequence)
            report = verify_chain(payloads, hash_field="payload_hash")
            if not report.valid:
                errors.append(f"payload_{report.error_code}")
        return errors

    @staticmethod
    def _reference_errors(result: ProcessReadResult) -> list[str]:
        errors: list[str] = []
        payloads = {payload.payload_id: payload for payload in result.payloads}
        referenced: set[str] = set()
        for event in result.events:
            if event.payload_id is None:
                continue
            referenced.add(event.payload_id)
            payload = payloads.get(event.payload_id)
            if payload is None:
                errors.append("missing_referenced_payload")
            elif payload.event_id != event.event_id or payload.payload_hash != event.payload_sha256:
                errors.append("payload_reference_mismatch")
        if set(payloads) - referenced:
            errors.append("orphan_payload")
        return errors

    @staticmethod
    def _lifecycle_errors(result: ProcessReadResult) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        incomplete: list[str] = []
        pairs = (
            ("run_started", "run_finished", "run_id"),
            ("iteration_started", "iteration_finished", "run_id"),
            ("model_request_started", ("model_response_received", "model_request_failed"), "model_call_id"),
            ("model_attempt_started", "model_attempt_finished", "attempt_id"),
            ("tool_started", "tool_finished", "tool_call_id"),
        )
        for started_type, terminal_types, identifier in pairs:
            terminal_set = {terminal_types} if isinstance(terminal_types, str) else set(terminal_types)
            starts = Counter(
                getattr(event, identifier)
                for event in result.events
                if event.event_type == started_type
            )
            terminals = Counter(
                getattr(event, identifier)
                for event in result.events
                if event.event_type in terminal_set
            )
            for identity, count in starts.items():
                terminal_count = terminals[identity]
                if count != 1 or terminal_count > 1:
                    errors.append("lifecycle_cardinality")
                elif terminal_count == 0:
                    (errors if result.cleanly_closed else incomplete).append("open_lifecycle_span")
        return errors, incomplete

    def verify_process(self, process_id: str) -> VerificationReport:
        result = self.reader.read_process(process_id)
        errors = [
            diagnostic.code
            for diagnostic in result.diagnostics
            if diagnostic.code in _INVALID_DIAGNOSTICS
        ]
        warnings = [
            diagnostic.code
            for diagnostic in result.diagnostics
            if diagnostic.code not in _INVALID_DIAGNOSTICS
        ]
        errors.extend(self._chain_errors(result))
        errors.extend(self._reference_errors(result))
        lifecycle_errors, incomplete = self._lifecycle_errors(result)
        errors.extend(lifecycle_errors)
        warnings.extend(incomplete)
        if errors:
            status = IntegrityStatus.INVALID
        elif incomplete:
            status = IntegrityStatus.INCOMPLETE
        elif any(event.event_type == "audit_degraded" for event in result.events):
            status = IntegrityStatus.DEGRADED
        else:
            status = IntegrityStatus.VALID
        return VerificationReport(status, tuple(sorted(set(errors))), tuple(sorted(set(warnings))))

    def verify_all(self) -> dict[str, VerificationReport]:
        return {
            process_id: self.verify_process(process_id)
            for process_id in self.reader.process_ids()
        }
