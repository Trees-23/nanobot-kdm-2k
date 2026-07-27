"""Disposable SQLite index and its single-writer coordination."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from nanobot.audit.index_schema import SCHEMA_SQL, SCHEMA_VERSION
from nanobot.audit.integrity import verify_chain
from nanobot.audit.reader import AuditReader
from nanobot.audit.schema import audit_event_adapter


class IndexRebuildRequired(RuntimeError):  # noqa: N818 - public design contract
    pass


class IndexWriterBusy(RuntimeError):  # noqa: N818 - public design contract
    pass


@dataclass(frozen=True, slots=True)
class SegmentCursor:
    process_instance_id: str
    stream_kind: str
    segment_id: str
    durable_offset: int
    final_hash: str | None
    durability_epoch: int

    @classmethod
    def zero(
        cls,
        *,
        process_instance_id: str,
        stream_kind: str,
        segment_id: str,
    ) -> SegmentCursor:
        return cls(process_instance_id, stream_kind, segment_id, 0, None, 0)


class AuditIndex:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self.connection = connection

    @classmethod
    def open(cls, path: Path) -> AuditIndex:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA_SQL)
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                (("schema_version", str(SCHEMA_VERSION)), ("source_format", "audit-v1")),
            )
            connection.commit()
        elif int(row[0]) != SCHEMA_VERSION:
            connection.close()
            raise IndexRebuildRequired(
                f"unsupported audit index schema version {row[0]}"
            )
        return cls(path, connection)

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0])

    def get_cursor(self, segment_id: str) -> SegmentCursor | None:
        row = self.connection.execute(
            "SELECT * FROM segment_cursors WHERE segment_id = ?", (segment_id,)
        ).fetchone()
        return SegmentCursor(**dict(row)) if row is not None else None

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> AuditIndex:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @classmethod
    def rebuild(cls, path: Path) -> AuditIndex:
        if path.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            path.rename(path.with_name(f"{path.name}.invalid-{stamp}"))
        return cls.open(path)


class AuditIndexWriter:
    def __init__(self, lock: FileLock) -> None:
        self._lock = lock
        self._closed = False

    @classmethod
    def acquire(cls, root: Path, *, timeout: float = 0) -> AuditIndexWriter:
        state = root / "state"
        state.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(state / "index.lock"))
        try:
            lock.acquire(timeout=timeout)
        except Timeout as error:
            raise IndexWriterBusy("another audit index writer is active") from error
        return cls(lock)

    def close(self) -> None:
        if self._closed:
            return
        self._lock.release()
        self._closed = True

    def __enter__(self) -> AuditIndexWriter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class IndexUpdateReport:
    indexed_events: int
    indexed_segments: int
    coverage_complete: bool
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _EventPrefix:
    process_instance_id: str
    segment_id: str
    path: Path
    durable_offset: int
    final_hash: str
    durability_epoch: int


class AuditIndexer:
    def __init__(self, root: Path, *, index_path: Path | None = None) -> None:
        self.root = root
        self.reader = AuditReader(root)
        self.index_path = index_path or root / "state" / "audit-index.sqlite"

    def _prefixes(self) -> list[_EventPrefix]:
        prefixes: dict[tuple[str, str], _EventPrefix] = {}
        for process_id in self.reader.process_ids():
            records, _ = self.reader._read_catalog(process_id)
            paths: dict[str, str] = {}
            for record in records:
                if record.catalog_record_type == "segment_registered":
                    paths[record.segment_id] = record.path_token
                elif record.catalog_record_type == "epoch_committed":
                    path_token = paths.get(record.event_segment_id)
                    if path_token is None:
                        continue
                    key = (process_id, record.event_segment_id)
                    candidate = _EventPrefix(
                        process_id,
                        record.event_segment_id,
                        self.root / path_token,
                        record.event_durable_offset,
                        record.event_final_hash,
                        record.durability_epoch,
                    )
                    current = prefixes.get(key)
                    if current is None or candidate.durability_epoch > current.durability_epoch:
                        prefixes[key] = candidate
        return sorted(
            prefixes.values(),
            key=lambda item: (item.process_instance_id, item.durability_epoch, item.segment_id),
        )

    @staticmethod
    def _event_row(event) -> tuple[object, ...]:
        usage = getattr(event, "usage", {}) or {}
        return (
            event.event_id,
            event.event_type,
            event.occurred_at.isoformat(),
            event.trace_id,
            event.turn_id,
            event.run_id,
            event.parent_run_id,
            event.resumed_from_run_id,
            event.caused_by_event_id,
            event.model_call_id,
            event.attempt_id,
            event.tool_call_id,
            event.checkpoint_id,
            event.goal_id,
            event.delivery_id,
            event.session_key,
            event.source_type,
            event.iteration,
            getattr(event, "status", None),
            getattr(event, "stop_reason", None),
            getattr(event, "provider", None) or getattr(event, "requested_provider", None),
            getattr(event, "model", None) or getattr(event, "requested_model", None),
            getattr(event, "tool_name", None),
            getattr(event, "elapsed_ms", None),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            event.payload_id,
            event.process_instance_id,
            event.segment_id,
            event.segment_sequence,
        )

    def update(self) -> IndexUpdateReport:
        inserted = 0
        indexed_segments = 0
        failures: list[str] = []
        blocked_processes: set[str] = set()
        with AuditIndexWriter.acquire(self.root):
            index = AuditIndex.open(self.index_path)
            try:
                for prefix in self._prefixes():
                    if prefix.process_instance_id in blocked_processes:
                        continue
                    cursor = index.get_cursor(prefix.segment_id) or SegmentCursor.zero(
                        process_instance_id=prefix.process_instance_id,
                        stream_kind="event",
                        segment_id=prefix.segment_id,
                    )
                    if cursor.durable_offset > prefix.durable_offset:
                        raise IndexRebuildRequired("source prefix moved backwards")
                    if (
                        cursor.durable_offset == prefix.durable_offset
                        and cursor.final_hash == prefix.final_hash
                    ):
                        continue
                    try:
                        data = prefix.path.read_bytes()
                        committed = data[: prefix.durable_offset]
                        if len(data) < prefix.durable_offset or not committed.endswith(b"\n"):
                            raise ValueError("invalid_committed_prefix")
                        events = [
                            audit_event_adapter.validate_python(json.loads(line))
                            for line in committed.splitlines()
                        ]
                        if not events or events[-1].event_hash != prefix.final_hash:
                            raise ValueError("committed_prefix_hash_mismatch")
                        chain = verify_chain(events, hash_field="event_hash")
                        if not chain.valid:
                            raise ValueError(chain.error_code or "event_chain_invalid")
                    except Exception as error:
                        failures.append(
                            f"{prefix.process_instance_id}:{prefix.segment_id}:{type(error).__name__}"
                        )
                        blocked_processes.add(prefix.process_instance_id)
                        continue
                    before = index.connection.total_changes
                    index.connection.executemany(
                        """
                        INSERT OR IGNORE INTO events VALUES (
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (self._event_row(event) for event in events),
                    )
                    inserted += index.connection.total_changes - before
                    index.connection.execute(
                        """
                        INSERT INTO segment_cursors VALUES (?, 'event', ?, ?, ?, ?)
                        ON CONFLICT(process_instance_id, stream_kind, segment_id) DO UPDATE SET
                          durable_offset=excluded.durable_offset,
                          final_hash=excluded.final_hash,
                          durability_epoch=excluded.durability_epoch
                        """,
                        (
                            prefix.process_instance_id,
                            prefix.segment_id,
                            prefix.durable_offset,
                            prefix.final_hash,
                            prefix.durability_epoch,
                        ),
                    )
                    indexed_segments += 1
                index.connection.commit()
            except BaseException:
                index.connection.rollback()
                raise
            finally:
                index.close()
        return IndexUpdateReport(inserted, indexed_segments, not failures, tuple(failures))
