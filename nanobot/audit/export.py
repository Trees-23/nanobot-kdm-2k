"""Streaming Trace exports with explicit provenance and disclosure modes."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from nanobot import __version__
from nanobot.audit.query import AuditQuery

_OUTPUT_CHUNK_BYTES = 64 * 1024


class ExportMode(StrEnum):
    SANITIZED = "sanitized"
    FULL = "full"
    EVIDENCE_BUNDLE = "evidence_bundle"


@dataclass(frozen=True, slots=True)
class ExportReport:
    trace_id: str
    mode: ExportMode
    event_count: int
    payload_count: int
    bytes_written: int
    max_chunk_bytes: int


class AuditExporter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.query = AuditQuery.from_root(root)

    @classmethod
    def from_root(cls, root: Path) -> AuditExporter:
        return cls(root)

    @staticmethod
    def _write_json(value: Any, output: BinaryIO) -> tuple[int, int]:
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
        written = 0
        largest = 0
        for text in encoder.iterencode(value):
            encoded = text.encode("utf-8")
            for offset in range(0, len(encoded), _OUTPUT_CHUNK_BYTES):
                chunk = encoded[offset : offset + _OUTPUT_CHUNK_BYTES]
                output.write(chunk)
                written += len(chunk)
                largest = max(largest, len(chunk))
        output.write(b"\n")
        return written + 1, largest

    def export_trace(
        self,
        trace_id: str,
        *,
        mode: ExportMode,
        output: BinaryIO,
    ) -> ExportReport:
        if mode is ExportMode.EVIDENCE_BUNDLE:
            return self._write_bundle(trace_id, output)
        include_payloads = mode is ExportMode.FULL
        view = self.query.load_trace(trace_id, include_payloads=include_payloads)
        raw = view.model_dump(mode="json")
        if not include_payloads:
            raw.pop("payloads", None)
        written, largest = self._write_json(raw, output)
        return ExportReport(
            trace_id,
            mode,
            len(view.timeline),
            len(view.payloads or {}),
            written,
            largest,
        )

    def _write_bundle(self, trace_id: str, output: BinaryIO) -> ExportReport:
        view = self.query.load_trace(trace_id, include_payloads=True)
        process_ids = sorted({event.process_instance_id for event in view.timeline})
        catalog_epochs: list[dict[str, Any]] = []
        lineage: dict[str, dict[str, Any]] = {}
        for process_id in process_ids:
            result = self.query.reader.read_process(process_id)
            catalog_epochs.extend(
                record.model_dump(mode="json")
                for record in result.catalog_records
                if record.catalog_record_type == "epoch_committed"
            )
        by_segment: dict[str, list[Any]] = {}
        for event in view.timeline:
            by_segment.setdefault(event.segment_id, []).append(event)
        for segment_id, events in by_segment.items():
            events.sort(key=lambda event: event.segment_sequence)
            lineage[segment_id] = {
                "first_event_hash": events[0].event_hash,
                "last_event_hash": events[-1].event_hash,
                "selected_record_count": len(events),
            }
        manifest = {
            "bundle_version": 1,
            "exporter_version": __version__,
            "trace_id": trace_id,
            "hash_scope": "local_unsigned",
            "signature": None,
            "process_instance_ids": process_ids,
            "lineage_witnesses": lineage,
            "catalog_epoch_count": len(catalog_epochs),
        }
        files = {
            "manifest.json": manifest,
            "trace.json": view.model_dump(mode="json"),
            "verification.json": {
                "status": view.integrity.status,
                "error_codes": view.integrity.error_codes,
                "warning_codes": view.integrity.warning_codes,
            },
            "catalog-epochs.json": catalog_epochs,
            "events.json": [event.model_dump(mode="json") for event in view.timeline],
            "payloads.json": {
                key: payload.model_dump(mode="json")
                for key, payload in (view.payloads or {}).items()
            },
        }
        temporary = BytesIO()
        largest = 0
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items():
                encoded = json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                largest = max(largest, len(encoded))
                archive.writestr(name, encoded)
        data = temporary.getvalue()
        output.write(data)
        return ExportReport(
            trace_id,
            ExportMode.EVIDENCE_BUNDLE,
            len(view.timeline),
            len(view.payloads or {}),
            len(data),
            largest,
        )
