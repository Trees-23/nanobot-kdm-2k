"""Per-process catalog and committed durability prefixes."""

from __future__ import annotations

import os
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from nanobot import __version__
from nanobot.audit.ids import new_audit_id
from nanobot.audit.integrity import hash_record
from nanobot.audit.schema import CatalogRecordBase, catalog_record_adapter
from nanobot.audit.segments import JsonlSegment
from nanobot.audit.types import CatalogRecordType


class CatalogWriteUncertainError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EpochCommit:
    durability_epoch: int
    event_segment_id: str
    event_durable_offset: int
    event_final_hash: str
    event_record_count: int
    payload_segment_id: str | None
    payload_durable_offset: int
    payload_final_hash: str | None
    payload_record_count: int


@dataclass(frozen=True, slots=True)
class CommittedPrefix:
    durability_epoch: int
    event_segment_id: str
    event_offset: int
    event_final_hash: str
    event_record_count: int
    payload_segment_id: str | None
    payload_offset: int
    payload_final_hash: str | None
    payload_record_count: int


@dataclass(frozen=True, slots=True)
class CatalogReceipt:
    catalog_record_id: str
    catalog_record_hash: str
    catalog_offset: int


def _host_fingerprint() -> str:
    return platform.node() or "unknown-host"


class ProcessCatalog:
    def __init__(
        self,
        *,
        root: Path,
        process_instance_id: str,
        segment_id: str,
        segment: JsonlSegment,
    ) -> None:
        self.root = root
        self.process_instance_id = process_instance_id
        self.segment_id = segment_id
        self._segment = segment
        self._sequence = 0
        self._previous_hash: str | None = None
        self._records: list[CatalogRecordBase] = []
        self._prefix: CommittedPrefix | None = None

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        process_instance_id: str,
        host_fingerprint: str | None = None,
        boot_id: str = "unknown-boot",
        pid: int | None = None,
        writer_version: str = __version__,
    ) -> ProcessCatalog:
        segment_id = new_audit_id()
        directory = root / "catalog" / process_instance_id
        segment = JsonlSegment.create(directory / f"{segment_id}.jsonl")
        catalog = cls(
            root=root,
            process_instance_id=process_instance_id,
            segment_id=segment_id,
            segment=segment,
        )
        now = datetime.now(UTC)
        catalog._append(
            CatalogRecordType.PROCESS_STARTED,
            host_fingerprint=host_fingerprint or _host_fingerprint(),
            pid=os.getpid() if pid is None else pid,
            boot_id=boot_id,
            writer_version=writer_version,
            started_at=now,
        )
        return catalog

    @property
    def records(self) -> tuple[CatalogRecordBase, ...]:
        return tuple(self._records)

    def _append(self, record_type: CatalogRecordType, **fields: Any) -> CatalogReceipt:
        sequence = self._sequence + 1
        raw = {
            "catalog_version": 1,
            "catalog_record_type": record_type.value,
            "catalog_record_id": new_audit_id(),
            "process_instance_id": self.process_instance_id,
            "catalog_segment_id": self.segment_id,
            "catalog_sequence": sequence,
            "previous_catalog_hash": self._previous_hash,
            "occurred_at": datetime.now(UTC),
            "catalog_record_hash": "",
            **fields,
        }
        raw["catalog_record_hash"] = hash_record(raw, hash_field="catalog_record_hash")
        record = catalog_record_adapter.validate_python(raw)
        try:
            append = self._segment.append(record.model_dump(mode="json"))
            self._segment.fsync()
        except Exception as error:
            self._segment.close_uncertain()
            raise CatalogWriteUncertainError(type(error).__name__) from error
        self._sequence = sequence
        self._previous_hash = record.catalog_record_hash
        self._records.append(record)
        return CatalogReceipt(record.catalog_record_id, record.catalog_record_hash, append.end)

    @staticmethod
    def _validate_path_token(path_token: str) -> None:
        token = PurePosixPath(path_token)
        if token.is_absolute() or ".." in token.parts or "\\" in path_token:
            raise ValueError("path_token must be a safe relative audit path")

    def register_segment(
        self,
        *,
        stream_kind: str,
        segment_id: str,
        path_token: str,
        previous_segment_id: str | None = None,
        previous_segment_hash: str | None = None,
        previous_segment_record_count: int | None = None,
    ) -> CatalogReceipt:
        self._validate_path_token(path_token)
        return self._append(
            CatalogRecordType.SEGMENT_REGISTERED,
            stream_kind=stream_kind,
            segment_id=segment_id,
            previous_segment_id=previous_segment_id,
            previous_segment_hash=previous_segment_hash,
            previous_segment_record_count=previous_segment_record_count,
            path_token=path_token,
        )

    def rotate_catalog_segment(self) -> CatalogReceipt:
        previous_segment_id = self.segment_id
        previous_hash = self._previous_hash
        previous_record_count = self._sequence
        self._segment.seal()

        self.segment_id = new_audit_id()
        directory = self.root / "catalog" / self.process_instance_id
        self._segment = JsonlSegment.create(directory / f"{self.segment_id}.jsonl")
        self._sequence = 0
        # The first record in the successor carries the cross-segment predecessor.
        self._previous_hash = previous_hash
        return self.register_segment(
            stream_kind="catalog",
            segment_id=self.segment_id,
            path_token=f"catalog/{self.process_instance_id}/{self.segment_id}.jsonl",
            previous_segment_id=previous_segment_id,
            previous_segment_hash=previous_hash,
            previous_segment_record_count=previous_record_count,
        )

    def recover_after_uncertain(self, *, abandon_reason: str) -> CatalogReceipt:
        previous_segment_id = self.segment_id
        previous_hash = self._previous_hash
        previous_record_count = self._sequence

        self.segment_id = new_audit_id()
        directory = self.root / "catalog" / self.process_instance_id
        self._segment = JsonlSegment.create(directory / f"{self.segment_id}.jsonl")
        self._sequence = 0
        self._previous_hash = previous_hash
        self.register_segment(
            stream_kind="catalog",
            segment_id=self.segment_id,
            path_token=f"catalog/{self.process_instance_id}/{self.segment_id}.jsonl",
            previous_segment_id=previous_segment_id,
            previous_segment_hash=previous_hash,
            previous_segment_record_count=previous_record_count,
        )
        return self.abandon_segment(
            stream_kind="catalog",
            segment_id=previous_segment_id,
            last_committed_offset=0,
            last_committed_hash=previous_hash,
            abandon_reason=abandon_reason,
        )

    def close_segment(
        self,
        *,
        stream_kind: str,
        segment_id: str,
        final_offset: int,
        final_hash: str,
        record_count: int,
        byte_size: int,
    ) -> CatalogReceipt:
        return self._append(CatalogRecordType.SEGMENT_CLOSED, **locals_without_self(locals()))

    def abandon_segment(
        self,
        *,
        stream_kind: str,
        segment_id: str,
        last_committed_offset: int,
        last_committed_hash: str | None,
        abandon_reason: str,
    ) -> CatalogReceipt:
        return self._append(CatalogRecordType.SEGMENT_ABANDONED, **locals_without_self(locals()))

    def commit_epoch(self, epoch: EpochCommit) -> CatalogReceipt:
        receipt = self._append(CatalogRecordType.EPOCH_COMMITTED, **asdict(epoch))
        self._prefix = CommittedPrefix(
            epoch.durability_epoch,
            epoch.event_segment_id,
            epoch.event_durable_offset,
            epoch.event_final_hash,
            epoch.event_record_count,
            epoch.payload_segment_id,
            epoch.payload_durable_offset,
            epoch.payload_final_hash,
            epoch.payload_record_count,
        )
        return receipt

    def close_process(self, *, shutdown_reason: str) -> CatalogReceipt:
        return self._append(
            CatalogRecordType.PROCESS_CLOSED,
            last_committed_epoch=self._prefix.durability_epoch if self._prefix else 0,
            shutdown_reason=shutdown_reason,
            event_lineage_head=self._prefix.event_final_hash if self._prefix else None,
            payload_lineage_head=self._prefix.payload_final_hash if self._prefix else None,
            closed_at=datetime.now(UTC),
        )

    def last_committed_prefix(self) -> CommittedPrefix | None:
        return self._prefix

    def seal(self) -> None:
        self._segment.seal()


def locals_without_self(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key != "self"}
