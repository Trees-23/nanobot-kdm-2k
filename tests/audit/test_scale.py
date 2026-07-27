"""Opt-in deterministic scale acceptance for the disposable audit index."""

import sqlite3
import time

import pytest

from nanobot.audit.index import AuditIndexer
from nanobot.audit.query import AuditQuery, TraceFilter
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


@pytest.mark.slow
async def test_one_million_event_index_and_paginated_filters(
    tmp_path, record_property
) -> None:
    event_count = 1_000_000
    root = tmp_path / "million-event-audit"
    started = time.perf_counter()
    writer = AuditWriter(
        root,
        fsync_interval_seconds=30,
        fsync_record_interval=10_000,
        queue_capacity=20_000,
        queue_max_bytes=32 * 1024 * 1024,
    )
    await writer.start()
    for index in range(1, event_count + 1):
        await writer.submit(_item(index, payload=False, critical=False))
    await writer.close()
    write_seconds = time.perf_counter() - started

    started = time.perf_counter()
    report = AuditIndexer(root).update()
    index_seconds = time.perf_counter() - started

    query = AuditQuery.from_root(root, use_index=True)
    started = time.perf_counter()
    first = query.find_traces(TraceFilter(source_type="cli", limit=100))
    second = query.find_traces(
        TraceFilter(source_type="cli", limit=100, cursor=first.next_cursor)
    )
    query_seconds = time.perf_counter() - started
    with sqlite3.connect(root / "state" / "audit-index.sqlite") as connection:
        indexed_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    record_property("audit_write_seconds", round(write_seconds, 3))
    record_property("audit_index_seconds", round(index_seconds, 3))
    record_property("audit_two_page_query_seconds", round(query_seconds, 3))
    assert report.coverage_complete is True
    assert report.indexed_events == event_count
    assert indexed_count == event_count
    assert len(first.trace_ids) == len(second.trace_ids) == 100
    assert set(first.trace_ids).isdisjoint(second.trace_ids)
    assert query.use_index is True
