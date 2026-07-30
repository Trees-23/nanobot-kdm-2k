from nanobot.audit.index import AuditIndexer
from nanobot.audit.query import AuditQuery
from tests.audit.test_query import write_run_fixture


async def test_stats_use_index_without_payload_content(tmp_path) -> None:
    await write_run_fixture(tmp_path)
    AuditIndexer(tmp_path).update()

    report = AuditQuery.from_root(tmp_path, use_index=True).stats(group_by="status")
    rows = {row.group: row for row in report.rows}

    assert report.coverage_complete is True
    assert rows["failed"].error_count == 1
    assert rows["succeeded"].run_count == 0
    assert rows["unknown"].run_count == 3
