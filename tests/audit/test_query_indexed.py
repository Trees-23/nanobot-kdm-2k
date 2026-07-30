from nanobot.audit.index import AuditIndexer
from nanobot.audit.query import AuditQuery, TraceFilter
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def test_indexed_and_scan_trace_pages_are_identical(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    for index in range(1, 5):
        await writer.submit(_item(index, payload=False))
    await writer.close()
    indexer = AuditIndexer(tmp_path)
    indexer.update()

    scan = AuditQuery.from_root(tmp_path, use_index=False)
    indexed = AuditQuery.from_root(tmp_path, use_index=True)
    first_filter = TraceFilter(limit=2)
    scan_first = scan.find_traces(first_filter)
    indexed_first = indexed.find_traces(first_filter)

    assert indexed_first == scan_first
    assert indexed.find_traces(
        TraceFilter(limit=2, cursor=indexed_first.next_cursor)
    ) == scan.find_traces(TraceFilter(limit=2, cursor=scan_first.next_cursor))
    assert indexed.load_trace(indexed_first.trace_ids[0]) == scan.load_trace(
        scan_first.trace_ids[0]
    )
