import sqlite3

import pytest

from nanobot.audit.index import AuditIndex, IndexRebuildRequired, SegmentCursor


def test_index_uses_wal_and_schema_version(tmp_path) -> None:
    index = AuditIndex.open(tmp_path / "index.sqlite")
    try:
        assert index.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert index.schema_version == 2
        assert index.connection.execute(
            "SELECT value FROM meta WHERE key = 'source_format'"
        ).fetchone()[0] == "audit-v1"
    finally:
        index.close()


def test_zero_cursor_explicitly_represents_new_segment() -> None:
    cursor = SegmentCursor.zero(
        process_instance_id="process",
        stream_kind="event",
        segment_id="segment",
    )
    assert cursor.durable_offset == 0
    assert cursor.final_hash is None
    assert cursor.durability_epoch == 0


def test_unknown_schema_requires_rebuild(tmp_path) -> None:
    path = tmp_path / "index.sqlite"
    index = AuditIndex.open(path)
    index.connection.execute(
        "UPDATE meta SET value = '99' WHERE key = 'schema_version'"
    )
    index.connection.commit()
    index.close()

    with pytest.raises(IndexRebuildRequired):
        AuditIndex.open(path)
    assert sqlite3.connect(path).execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()[0] == "99"


def test_v1_schema_requires_explicit_rebuild(tmp_path) -> None:
    path = tmp_path / "index.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    connection.commit()
    connection.close()

    with pytest.raises(IndexRebuildRequired):
        AuditIndex.open(path)
