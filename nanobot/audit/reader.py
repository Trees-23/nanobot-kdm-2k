"""Catalog-first reader for committed audit evidence prefixes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nanobot.audit.integrity import hash_record
from nanobot.audit.schema import (
    AuditEventBase,
    AuditPayloadBase,
    CatalogRecordBase,
    audit_event_adapter,
    audit_payload_adapter,
    catalog_record_adapter,
)


@dataclass(frozen=True, slots=True)
class AuditDiagnostic:
    code: str
    message: str
    segment_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessReadResult:
    process_instance_id: str
    events: tuple[AuditEventBase, ...]
    payloads: tuple[AuditPayloadBase, ...]
    catalog_records: tuple[CatalogRecordBase, ...]
    diagnostics: tuple[AuditDiagnostic, ...]
    last_committed_epoch: int
    cleanly_closed: bool

    @property
    def uncertain_tail(self) -> bool:
        return any(diagnostic.code == "uncataloged_tail" for diagnostic in self.diagnostics)


class AuditReader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def process_ids(self) -> tuple[str, ...]:
        catalog_root = self.root / "catalog"
        if not catalog_root.exists():
            return ()
        return tuple(sorted(path.name for path in catalog_root.iterdir() if path.is_dir()))

    def _read_catalog(
        self, process_id: str
    ) -> tuple[list[CatalogRecordBase], list[AuditDiagnostic]]:
        directory = self.root / "catalog" / process_id
        diagnostics: list[AuditDiagnostic] = []
        by_hash: dict[str, CatalogRecordBase] = {}
        successors: dict[str | None, list[CatalogRecordBase]] = {}
        for path in sorted(directory.glob("*.jsonl")):
            for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
                try:
                    raw = json.loads(line)
                    record = catalog_record_adapter.validate_python(raw)
                except (json.JSONDecodeError, ValidationError):
                    diagnostics.append(
                        AuditDiagnostic("invalid_catalog_record", f"{path.name}:{line_number}")
                    )
                    continue
                if record.catalog_record_hash != hash_record(
                    raw, hash_field="catalog_record_hash"
                ):
                    diagnostics.append(
                        AuditDiagnostic(
                            "catalog_hash_mismatch", f"{path.name}:{line_number}", record.catalog_segment_id
                        )
                    )
                    continue
                by_hash[record.catalog_record_hash] = record
                successors.setdefault(record.previous_catalog_hash, []).append(record)

        roots = [
            record
            for record in successors.get(None, [])
            if record.catalog_record_type == "process_started"
        ]
        if len(roots) != 1:
            diagnostics.append(AuditDiagnostic("catalog_root_invalid", process_id))
            return [], diagnostics
        ordered = [roots[0]]
        seen = {roots[0].catalog_record_hash}
        while True:
            candidates = [
                record
                for record in successors.get(ordered[-1].catalog_record_hash, [])
                if record.catalog_record_hash not in seen
            ]
            if not candidates:
                break
            if len(candidates) != 1:
                diagnostics.append(AuditDiagnostic("catalog_fork", process_id))
                break
            ordered.append(candidates[0])
            seen.add(candidates[0].catalog_record_hash)
        if len(seen) != len(by_hash):
            diagnostics.append(AuditDiagnostic("unlinked_catalog_records", process_id))
        return ordered, diagnostics

    def _read_prefix(
        self,
        *,
        path: Path,
        segment_id: str,
        offset: int,
        adapter: Any,
        missing_code: str,
        diagnostics: list[AuditDiagnostic],
    ) -> list[Any]:
        if not path.exists():
            diagnostics.append(AuditDiagnostic(missing_code, str(path), segment_id))
            return []
        data = path.read_bytes()
        if len(data) < offset:
            diagnostics.append(AuditDiagnostic("catalog_offset_beyond_file", str(path), segment_id))
            return []
        if len(data) > offset:
            diagnostics.append(AuditDiagnostic("uncataloged_tail", str(path), segment_id))
        committed = data[:offset]
        if committed and not committed.endswith(b"\n"):
            diagnostics.append(AuditDiagnostic("committed_prefix_partial_line", str(path), segment_id))
            return []
        records: list[Any] = []
        for line_number, line in enumerate(committed.splitlines(), start=1):
            try:
                records.append(adapter.validate_json(line))
            except ValidationError:
                diagnostics.append(
                    AuditDiagnostic("invalid_committed_record", f"{path}:{line_number}", segment_id)
                )
        return records

    def read_process(self, process_id: str) -> ProcessReadResult:
        catalog_records, diagnostics = self._read_catalog(process_id)
        paths: dict[str, str] = {}
        event_prefixes: dict[str, int] = {}
        payload_prefixes: dict[str, int] = {}
        last_epoch = 0
        cleanly_closed = False
        for record in catalog_records:
            if record.catalog_record_type == "segment_registered":
                paths[record.segment_id] = record.path_token
            elif record.catalog_record_type == "epoch_committed":
                last_epoch = max(last_epoch, record.durability_epoch)
                event_prefixes[record.event_segment_id] = max(
                    event_prefixes.get(record.event_segment_id, 0), record.event_durable_offset
                )
                if record.payload_segment_id is not None:
                    payload_prefixes[record.payload_segment_id] = max(
                        payload_prefixes.get(record.payload_segment_id, 0),
                        record.payload_durable_offset,
                    )
            elif record.catalog_record_type == "process_closed":
                cleanly_closed = True

        events: list[AuditEventBase] = []
        for segment_id, offset in event_prefixes.items():
            path_token = paths.get(segment_id)
            if path_token is None:
                diagnostics.append(AuditDiagnostic("unregistered_cataloged_segment", segment_id))
                continue
            events.extend(
                self._read_prefix(
                    path=self.root / path_token,
                    segment_id=segment_id,
                    offset=offset,
                    adapter=audit_event_adapter,
                    missing_code="missing_cataloged_segment",
                    diagnostics=diagnostics,
                )
            )

        payloads: list[AuditPayloadBase] = []
        for segment_id, offset in payload_prefixes.items():
            path_token = paths.get(segment_id)
            if path_token is None:
                diagnostics.append(AuditDiagnostic("unregistered_cataloged_segment", segment_id))
                continue
            payloads.extend(
                self._read_prefix(
                    path=self.root / path_token,
                    segment_id=segment_id,
                    offset=offset,
                    adapter=audit_payload_adapter,
                    missing_code="missing_cataloged_payload_segment",
                    diagnostics=diagnostics,
                )
            )

        events.sort(key=lambda event: (event.durability_epoch, event.segment_id, event.segment_sequence))
        payloads.sort(key=lambda payload: (payload.payload_segment_id, payload.payload_segment_sequence))
        return ProcessReadResult(
            process_id,
            tuple(events),
            tuple(payloads),
            tuple(catalog_records),
            tuple(diagnostics),
            last_epoch,
            cleanly_closed,
        )
