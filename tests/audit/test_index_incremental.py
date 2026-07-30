import json

from nanobot.audit.index import AuditIndex, AuditIndexer
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def test_index_only_reads_cataloged_prefix_and_is_idempotent(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=False))
    event_path = writer.event_segment_path
    await writer.close()

    committed = json.loads(event_path.read_text().splitlines()[0])
    with event_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({**committed, "event_id": "uncataloged"}) + "\n")

    indexer = AuditIndexer(tmp_path)
    first = indexer.update()
    second = indexer.update()
    index = AuditIndex.open(indexer.index_path)
    try:
        rows = index.connection.execute("SELECT event_id FROM events").fetchall()
        cursor = index.get_cursor(committed["segment_id"])
    finally:
        index.close()

    assert first.indexed_events == 1
    assert second.indexed_events == 0
    assert [row[0] for row in rows] == ["e1"]
    assert cursor is not None
    assert cursor.durable_offset < event_path.stat().st_size


async def test_deleted_index_rebuilds_to_same_rows(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    for index in range(1, 4):
        await writer.submit(_item(index, payload=False))
    await writer.close()
    indexer = AuditIndexer(tmp_path)
    indexer.update()

    index = AuditIndex.open(indexer.index_path)
    expected = index.connection.execute(
        "SELECT event_id, trace_id FROM events ORDER BY event_id"
    ).fetchall()
    index.close()
    indexer.index_path.unlink()
    indexer.update()
    rebuilt = AuditIndex.open(indexer.index_path)
    try:
        actual = rebuilt.connection.execute(
            "SELECT event_id, trace_id FROM events ORDER BY event_id"
        ).fetchall()
    finally:
        rebuilt.close()

    assert [tuple(row) for row in actual] == [tuple(row) for row in expected]


async def test_v2_projects_typed_event_revision_integrity_and_payload_locator(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=True))
    await writer.close()

    indexer = AuditIndexer(tmp_path)
    report = indexer.update()
    index = AuditIndex.open(indexer.index_path)
    try:
        event = index.connection.execute(
            "SELECT durability_epoch, event_json, payload_id FROM events WHERE event_id = 'e1'"
        ).fetchone()
        locator = index.connection.execute(
            "SELECT payload_id, path_token, line_offset, line_length FROM payload_locators"
        ).fetchone()
        integrity = index.connection.execute("SELECT status FROM process_integrity").fetchone()
        revision = index.connection.execute(
            "SELECT value FROM meta WHERE key = 'revision'"
        ).fetchone()[0]
    finally:
        index.close()

    assert report.coverage_complete is True
    assert event[0] == 1
    assert json.loads(event[1])["event_id"] == "e1"
    assert "content" not in event[1]
    assert locator[0] == event[2]
    assert locator[1].startswith("payloads/")
    assert locator[2] == 0
    assert locator[3] > 0
    assert integrity[0] in {"valid", "degraded"}
    assert int(revision) >= 1
