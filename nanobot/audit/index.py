"""Disposable SQLite index and its single-writer coordination."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from nanobot.audit.index_schema import SCHEMA_SQL, SCHEMA_VERSION


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
