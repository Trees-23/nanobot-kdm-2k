"""Single-process audit writer with coordinated durability epochs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.audit.catalog import CatalogWriteUncertainError, EpochCommit, ProcessCatalog
from nanobot.audit.ids import new_audit_id
from nanobot.audit.integrity import hash_record
from nanobot.audit.schema import (
    AuditEventDraftBase,
    AuditPayloadDraftBase,
    materialize_event,
    materialize_payload,
)
from nanobot.audit.segments import JsonlSegment
from nanobot.config.schema import AuditConfig


class AuditWriterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    process_instance_id: str
    durability_epoch: int
    catalog_record_id: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommitItem:
    event: AuditEventDraftBase
    payload: AuditPayloadDraftBase | None
    estimated_bytes: int
    critical: bool


@dataclass(slots=True)
class _QueuedCommit:
    item: CommitItem
    acknowledgement: asyncio.Future[CommitReceipt] | None


@dataclass(slots=True)
class _DataSegment:
    stream_kind: str
    segment_id: str
    path_token: str
    segment: JsonlSegment
    sequence: int = 0
    previous_hash: str | None = None
    committed_offset: int = 0
    committed_hash: str | None = None
    committed_count: int = 0


_STOP = object()


class AuditWriter:
    def __init__(
        self,
        root: Path,
        *,
        process_instance_id: str | None = None,
        queue_capacity: int = 4096,
        queue_max_bytes: int = 268_435_456,
        enqueue_timeout_seconds: float = 0.025,
        critical_ack_timeout_seconds: float = 2.0,
        fsync_interval_seconds: float = 5.0,
        fsync_record_interval: int = 100,
    ) -> None:
        self.root = root
        self.process_id = process_instance_id or new_audit_id()
        self._queue: asyncio.Queue[_QueuedCommit | object] = asyncio.Queue(queue_capacity)
        self._queue_max_bytes = queue_max_bytes
        self._queued_bytes = 0
        self._byte_condition = asyncio.Condition()
        self.enqueue_timeout = enqueue_timeout_seconds
        self.critical_ack_timeout = critical_ack_timeout_seconds
        self.fsync_interval = fsync_interval_seconds
        self.fsync_record_interval = fsync_record_interval
        self.catalog: ProcessCatalog
        self._event: _DataSegment
        self._payload: _DataSegment
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._closing = False
        self._durability_epoch = 0
        self._fsync_observer: Callable[[str], None] | None = None

    @classmethod
    def from_config(cls, *, root: Path, config: AuditConfig) -> AuditWriter:
        return cls(
            root,
            queue_capacity=config.writer_queue_capacity,
            queue_max_bytes=config.writer_queue_max_bytes,
            enqueue_timeout_seconds=config.enqueue_timeout_ms / 1000,
            critical_ack_timeout_seconds=config.critical_ack_timeout_ms / 1000,
            fsync_interval_seconds=config.fsync_interval_seconds,
            fsync_record_interval=config.fsync_record_interval,
        )

    @property
    def event_segment_id(self) -> str:
        return self._event.segment_id

    @property
    def event_segment_path(self) -> Path:
        return self._event.segment.path

    @property
    def last_committed_epoch(self) -> int:
        return self._durability_epoch

    def set_fsync_observer(self, observer: Callable[[str], None] | None) -> None:
        self._fsync_observer = observer

    def _observe(self, operation: str) -> None:
        if self._fsync_observer is not None:
            self._fsync_observer(operation)

    async def start(self) -> None:
        if self._started:
            return
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.catalog = ProcessCatalog.create(self.root, process_instance_id=self.process_id)
        self._event = self._create_data_segment("event")
        self._payload = self._create_data_segment("payload")
        self._started = True
        self._task = asyncio.create_task(self._run(), name=f"audit-writer-{self.process_id}")

    def _create_data_segment(
        self,
        stream_kind: str,
        *,
        previous: _DataSegment | None = None,
    ) -> _DataSegment:
        segment_id = new_audit_id()
        date_token = datetime.now(UTC).date().isoformat()
        directory_name = "events" if stream_kind == "event" else "payloads"
        filename = f"{self.process_id}-{os.getpid()}-{segment_id}.jsonl"
        path_token = f"{directory_name}/{date_token}/{filename}"
        segment = JsonlSegment.create(self.root / path_token)
        self.catalog.register_segment(
            stream_kind=stream_kind,
            segment_id=segment_id,
            path_token=path_token,
            previous_segment_id=previous.segment_id if previous else None,
            previous_segment_hash=previous.committed_hash if previous else None,
            previous_segment_record_count=previous.committed_count if previous else None,
        )
        return _DataSegment(stream_kind, segment_id, path_token, segment)

    async def _acquire_bytes(self, size: int) -> None:
        async def wait_for_capacity() -> None:
            async with self._byte_condition:
                await self._byte_condition.wait_for(
                    lambda: self._queued_bytes == 0
                    if size > self._queue_max_bytes
                    else self._queued_bytes + size <= self._queue_max_bytes
                )
                self._queued_bytes += size

        await asyncio.wait_for(wait_for_capacity(), timeout=self.enqueue_timeout)

    async def _release_bytes(self, size: int) -> None:
        async with self._byte_condition:
            self._queued_bytes -= size
            self._byte_condition.notify_all()

    async def submit(self, item: CommitItem) -> CommitReceipt | None:
        if not self._started or self._closing:
            raise AuditWriterError("audit writer is not accepting items")
        acknowledgement = (
            asyncio.get_running_loop().create_future() if item.critical else None
        )
        queued = _QueuedCommit(item, acknowledgement)
        await self._acquire_bytes(item.estimated_bytes)
        try:
            await asyncio.wait_for(self._queue.put(queued), timeout=self.enqueue_timeout)
        except BaseException:
            await self._release_bytes(item.estimated_bytes)
            if acknowledgement is not None:
                acknowledgement.cancel()
            raise
        if acknowledgement is None:
            return None
        return await asyncio.wait_for(
            asyncio.shield(acknowledgement), timeout=self.critical_ack_timeout
        )

    async def _run(self) -> None:
        stop_after_batch = False
        while True:
            queued = await self._queue.get()
            if queued is _STOP:
                break
            assert isinstance(queued, _QueuedCommit)
            batch = [queued]
            if not queued.item.critical:
                while len(batch) < self.fsync_record_interval:
                    try:
                        candidate = await asyncio.wait_for(
                            self._queue.get(), timeout=self.fsync_interval
                        )
                    except TimeoutError:
                        break
                    if candidate is _STOP:
                        stop_after_batch = True
                        break
                    assert isinstance(candidate, _QueuedCommit)
                    batch.append(candidate)
                    if candidate.item.critical:
                        break
            try:
                receipt = self._commit_epoch(batch)
            except Exception as error:
                try:
                    self._recover_after_failure(error)
                except Exception as recovery_error:
                    error = recovery_error
                wrapped = AuditWriterError(type(error).__name__)
                for entry in batch:
                    if entry.acknowledgement is not None and not entry.acknowledgement.done():
                        entry.acknowledgement.set_exception(wrapped)
            else:
                for entry in batch:
                    if entry.acknowledgement is not None and not entry.acknowledgement.done():
                        entry.acknowledgement.set_result(receipt)
            finally:
                for entry in batch:
                    await self._release_bytes(entry.item.estimated_bytes)
                    self._queue.task_done()
            if stop_after_batch:
                break

    def _materialize_payloads(self, batch: list[_QueuedCommit], epoch: int) -> list[Any]:
        records: list[Any] = []
        for entry in batch:
            draft = entry.item.payload
            if draft is None:
                continue
            self._payload.sequence += 1
            provisional = materialize_payload(
                draft,
                process_instance_id=self.process_id,
                payload_segment_id=self._payload.segment_id,
                payload_segment_sequence=self._payload.sequence,
                previous_payload_hash=self._payload.previous_hash,
                payload_hash="",
            )
            payload_hash = hash_record(provisional, hash_field="payload_hash")
            record = materialize_payload(
                draft,
                process_instance_id=self.process_id,
                payload_segment_id=self._payload.segment_id,
                payload_segment_sequence=self._payload.sequence,
                previous_payload_hash=self._payload.previous_hash,
                payload_hash=payload_hash,
            )
            self._payload.previous_hash = payload_hash
            records.append(record)
        return records

    def _commit_epoch(self, batch: list[_QueuedCommit]) -> CommitReceipt:
        epoch = self._durability_epoch + 1
        payloads = self._materialize_payloads(batch, epoch)
        payload_by_event: dict[str, Any] = {}
        for payload in payloads:
            self._payload.segment.append(payload.model_dump(mode="json"))
            self._observe("payload.append")
            payload_by_event[payload.event_id] = payload
        if payloads:
            self._payload.segment.fsync()
            self._observe("payload.fsync")

        events: list[Any] = []
        for entry in batch:
            draft = entry.item.event
            payload = payload_by_event.get(draft.event_id)
            self._event.sequence += 1
            provisional = materialize_event(
                draft,
                process_instance_id=self.process_id,
                segment_id=self._event.segment_id,
                segment_sequence=self._event.sequence,
                durability_epoch=epoch,
                previous_event_hash=self._event.previous_hash,
                payload_id=payload.payload_id if payload else None,
                payload_sha256=payload.payload_hash if payload else None,
                event_hash="",
            )
            event_hash = hash_record(provisional, hash_field="event_hash")
            event = materialize_event(
                draft,
                process_instance_id=self.process_id,
                segment_id=self._event.segment_id,
                segment_sequence=self._event.sequence,
                durability_epoch=epoch,
                previous_event_hash=self._event.previous_hash,
                payload_id=payload.payload_id if payload else None,
                payload_sha256=payload.payload_hash if payload else None,
                event_hash=event_hash,
            )
            self._event.previous_hash = event_hash
            self._event.segment.append(event.model_dump(mode="json"))
            self._observe("event.append")
            events.append(event)
        self._event.segment.fsync()
        self._observe("event.fsync")

        self._observe("catalog.epoch_committed")
        catalog_receipt = self.catalog.commit_epoch(
            EpochCommit(
                durability_epoch=epoch,
                event_segment_id=self._event.segment_id,
                event_durable_offset=self._event.segment.offset,
                event_final_hash=self._event.previous_hash or "",
                event_record_count=self._event.sequence,
                payload_segment_id=self._payload.segment_id if payloads else None,
                payload_durable_offset=self._payload.segment.offset if payloads else 0,
                payload_final_hash=self._payload.previous_hash if payloads else None,
                payload_record_count=self._payload.sequence if payloads else 0,
            )
        )
        self._observe("catalog.fsync")
        self._durability_epoch = epoch
        self._event.committed_offset = self._event.segment.offset
        self._event.committed_hash = self._event.previous_hash
        self._event.committed_count = self._event.sequence
        if payloads:
            self._payload.committed_offset = self._payload.segment.offset
            self._payload.committed_hash = self._payload.previous_hash
            self._payload.committed_count = self._payload.sequence
        return CommitReceipt(
            self.process_id,
            epoch,
            catalog_receipt.catalog_record_id,
            tuple(event.event_id for event in events),
        )

    def _recover_after_failure(self, error: Exception) -> None:
        old_event = self._event
        old_payload = self._payload
        old_event.segment.close_uncertain()
        old_payload.segment.close_uncertain()
        reason = type(error).__name__
        if isinstance(error, CatalogWriteUncertainError):
            self.catalog.recover_after_uncertain(abandon_reason=reason)
        self.catalog.abandon_segment(
            stream_kind="event",
            segment_id=old_event.segment_id,
            last_committed_offset=old_event.committed_offset,
            last_committed_hash=old_event.committed_hash,
            abandon_reason=reason,
        )
        self.catalog.abandon_segment(
            stream_kind="payload",
            segment_id=old_payload.segment_id,
            last_committed_offset=old_payload.committed_offset,
            last_committed_hash=old_payload.committed_hash,
            abandon_reason=reason,
        )
        self._event = self._create_data_segment("event", previous=old_event)
        self._payload = self._create_data_segment("payload", previous=old_payload)

    async def close(self) -> None:
        if not self._started or self._closing:
            return
        self._closing = True
        await self._queue.put(_STOP)
        if self._task is not None:
            await self._task
        self.catalog.close_process(shutdown_reason="graceful_shutdown")
        self._event.segment.seal()
        self._payload.segment.seal()
        self.catalog.seal()
