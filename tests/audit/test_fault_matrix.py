import json

from nanobot.audit.index import AuditIndexer
from nanobot.audit.reader import AuditReader
from nanobot.audit.verify import AuditVerifier
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def fixture(root):
    writer = AuditWriter(root, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=False))
    process_id = writer.process_id
    event_path = writer.event_segment_path
    await writer.close()
    return process_id, event_path


async def test_entire_cataloged_segment_deletion_is_invalid(tmp_path) -> None:
    process_id, event_path = await fixture(tmp_path)
    event_path.unlink()

    report = AuditVerifier(tmp_path).verify_process(process_id)

    assert report.status == "invalid"
    assert "missing_cataloged_segment" in report.error_codes


async def test_partial_uncataloged_tail_is_ignored_with_warning(tmp_path) -> None:
    process_id, event_path = await fixture(tmp_path)
    with event_path.open("ab") as file:
        file.write(b'{"partial":')

    result = AuditReader(tmp_path).read_process(process_id)
    report = AuditVerifier(tmp_path).verify_process(process_id)

    assert [event.event_id for event in result.events] == ["e1"]
    assert result.uncertain_tail is True
    assert report.status == "valid"
    assert "uncataloged_tail" in report.warning_codes


async def test_index_corrupt_source_marks_coverage_incomplete(tmp_path) -> None:
    _process_id, event_path = await fixture(tmp_path)
    raw = event_path.read_text(encoding="utf-8")
    event_path.write_text(raw.replace('"event_id":"e1"', '"event_id":"x1"'), encoding="utf-8")

    report = AuditIndexer(tmp_path).update()

    assert report.coverage_complete is False
    assert report.failures
    assert json.loads(
        __import__("sqlite3").connect(
            tmp_path / "state" / "audit-index.sqlite"
        ).execute("SELECT value FROM meta WHERE key='coverage_complete'").fetchone()[0]
    ) is False
