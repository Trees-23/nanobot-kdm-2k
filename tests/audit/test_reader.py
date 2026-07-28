import json

from nanobot.audit.reader import AuditReader
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def test_reader_ignores_complete_but_uncataloged_tail(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=False))
    process_id = writer.process_id
    event_path = writer.event_segment_path
    await writer.close()

    committed = json.loads(event_path.read_text().splitlines()[0])
    extra = {**committed, "event_id": "uncataloged", "segment_sequence": 2}
    with event_path.open("a") as file:
        file.write(json.dumps(extra) + "\n")

    result = AuditReader(tmp_path).read_process(process_id)
    assert [event.event_id for event in result.events] == ["e1"]
    assert result.uncertain_tail is True


async def test_reader_loads_committed_payload_by_reference(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    process_id = writer.process_id
    await writer.close()

    result = AuditReader(tmp_path).read_process(process_id)
    assert result.events[0].payload_id == "d1"
    assert result.payloads[0].payload_id == "d1"
    assert result.events[0].payload_sha256 == result.payloads[0].payload_hash
