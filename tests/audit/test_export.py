import json
import zipfile
from io import BytesIO

from nanobot.audit.export import AuditExporter, ExportMode
from nanobot.audit.schema import TurnInputPayloadDraft
from nanobot.audit.writer import AuditWriter, CommitItem
from tests.audit.test_writer import _event, _item


async def fixture(root) -> None:
    writer = AuditWriter(root, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    await writer.close()


async def test_sanitized_and_full_export_payload_disclosure(tmp_path) -> None:
    await fixture(tmp_path)
    exporter = AuditExporter.from_root(tmp_path)
    sanitized = BytesIO()
    full = BytesIO()

    sanitized_report = exporter.export_trace(
        "t1", mode=ExportMode.SANITIZED, output=sanitized
    )
    full_report = exporter.export_trace("t1", mode=ExportMode.FULL, output=full)

    sanitized_value = json.loads(sanitized.getvalue())
    full_value = json.loads(full.getvalue())
    assert "payloads" not in sanitized_value
    assert "message 1" not in sanitized.getvalue().decode()
    assert "d1" in full_value["payloads"]
    assert "message 1" in full.getvalue().decode()
    assert sanitized_report.payload_count == 0
    assert full_report.payload_count == 1


async def test_evidence_bundle_contains_provenance_and_witnesses(tmp_path) -> None:
    await fixture(tmp_path)
    output = BytesIO()

    report = AuditExporter.from_root(tmp_path).export_trace(
        "t1", mode=ExportMode.EVIDENCE_BUNDLE, output=output
    )

    with zipfile.ZipFile(BytesIO(output.getvalue())) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "trace.json",
            "verification.json",
            "catalog-epochs.json",
            "events.json",
            "payloads.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        verification = json.loads(archive.read("verification.json"))
    assert manifest["hash_scope"] == "local_unsigned"
    assert manifest["lineage_witnesses"]
    assert verification["status"] == "valid"
    assert report.event_count == 1


async def test_large_payload_export_writes_bounded_chunks(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    payload = TurnInputPayloadDraft(
        payload_id="d1",
        event_id="e1",
        content={
            "role": "user",
            "content": "x" * 200_000,
            "media_refs": [],
            "source_message_id": None,
        },
    )
    await writer.submit(CommitItem(_event(1), payload, 210_000, True))
    await writer.close()
    output = BytesIO()

    report = AuditExporter.from_root(tmp_path).export_trace(
        "t1", mode=ExportMode.FULL, output=output
    )

    assert len(output.getvalue()) > 200_000
    assert report.max_chunk_bytes <= 64 * 1024
