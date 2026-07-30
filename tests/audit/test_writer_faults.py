import pytest

from nanobot.audit.writer import AuditWriter, AuditWriterError
from tests.audit.test_writer import _item


async def test_event_fsync_failure_abandons_segment(tmp_path, monkeypatch) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    failed_segment = writer.event_segment_id
    original = writer._event.segment.fsync
    failures = 0

    def fail_once() -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("injected event fsync failure")
        original()

    monkeypatch.setattr(writer._event.segment, "fsync", fail_once)
    with pytest.raises(AuditWriterError):
        await writer.submit(_item(1, payload=False))

    assert writer.event_segment_id != failed_segment
    assert any(
        record.catalog_record_type == "segment_abandoned" and record.segment_id == failed_segment
        for record in writer.catalog.records
    )
    await writer.submit(_item(2, payload=False))
    await writer.close()


async def test_catalog_fsync_failure_rotates_catalog_and_data_segments(tmp_path, monkeypatch) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    failed_catalog = writer.catalog.segment_id
    failed_event = writer.event_segment_id

    def fail() -> None:
        raise OSError("injected catalog fsync failure")

    monkeypatch.setattr(writer.catalog._segment, "fsync", fail)
    with pytest.raises(AuditWriterError):
        await writer.submit(_item(1, payload=False))

    assert writer.catalog.segment_id != failed_catalog
    assert writer.event_segment_id != failed_event
    await writer.submit(_item(2, payload=False))
    await writer.close()
