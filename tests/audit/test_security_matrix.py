from io import BytesIO

from nanobot.audit.emitter import AuditEmitter
from nanobot.audit.export import AuditExporter, ExportMode
from nanobot.audit.redaction import AuditRedactor
from nanobot.audit.schema import TraceCreatedDraft, TurnInputPayloadDraft
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _event


async def test_recognized_secrets_never_reach_evidence_but_opaque_value_does(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    emitter = AuditEmitter(
        writer=writer,
        redactor=AuditRedactor(additional_patterns=[r"ACME_[A-Z0-9]{16}"]),
        mode="full",
    )
    source = _event(1)
    event = TraceCreatedDraft.model_validate(source.model_dump(mode="python"))
    payload = TurnInputPayloadDraft(
        payload_id="d1",
        event_id=event.event_id,
        content={
            "role": "user",
            "content": {
                "headers": {
                    "Authorization": "Bearer AUTH_CANARY",
                    "Cookie": "COOKIE_CANARY",
                },
                "shell_output": "Bearer SHELL_CANARY",
                "mcp_result": "sk-1234567890ABCDEFGHIJ",
                "checkpoint": "ACME_1234567890ABCDEF",
                "opaque": "OPAQUE_UNCONFIGURED_CANARY",
            },
            "media_refs": [],
            "source_message_id": None,
        },
    )
    await emitter.emit(event, payload=payload, critical=True)
    await writer.close()

    evidence = b"".join(path.read_bytes() for path in tmp_path.rglob("*.jsonl"))
    for canary in (
        b"AUTH_CANARY",
        b"COOKIE_CANARY",
        b"SHELL_CANARY",
        b"sk-1234567890ABCDEFGHIJ",
        b"ACME_1234567890ABCDEF",
    ):
        assert canary not in evidence
    assert b"OPAQUE_UNCONFIGURED_CANARY" in evidence

    output = BytesIO()
    AuditExporter.from_root(tmp_path).export_trace(
        "t1", mode=ExportMode.FULL, output=output
    )
    assert b"AUTH_CANARY" not in output.getvalue()
    assert b"OPAQUE_UNCONFIGURED_CANARY" in output.getvalue()
