from nanobot.audit.verify import AuditVerifier
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def test_verifier_reports_missing_cataloged_segment(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=False))
    process_id = writer.process_id
    event_path = writer.event_segment_path
    await writer.close()
    event_path.unlink()

    report = AuditVerifier(tmp_path).verify_process(process_id)
    assert report.status == "invalid"
    assert "missing_cataloged_segment" in report.error_codes


async def test_verifier_accepts_clean_committed_evidence(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    process_id = writer.process_id
    await writer.close()

    report = AuditVerifier(tmp_path).verify_process(process_id)
    assert report.status == "valid"
    assert report.error_codes == ()
