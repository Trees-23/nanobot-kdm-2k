from unittest.mock import AsyncMock, Mock

from nanobot.audit.emitter import AuditEmitter, DisabledAuditEmitter
from nanobot.audit.redaction import AuditRedactor, RedactionError
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _event, _payload


async def test_emitter_never_falls_back_to_unredacted_content() -> None:
    writer = AsyncMock()
    redactor = Mock(spec=AuditRedactor)
    redactor.redact.side_effect = RedactionError("rule_failed")
    emitter = AuditEmitter(writer=writer, redactor=redactor)

    result = await emitter.emit(_event(1), payload=_payload(1))

    assert result.committed is False
    assert result.degraded is True
    writer.submit.assert_not_awaited()
    assert "message 1" not in repr(result)


async def test_metadata_only_never_enqueues_payload(tmp_path) -> None:
    writer = AsyncMock()
    emitter = AuditEmitter(writer=writer, redactor=AuditRedactor(), mode="metadata_only")
    await emitter.emit(_event(1), payload=_payload(1))
    item = writer.submit.await_args.args[0]
    assert item.payload is None


async def test_recovery_emits_loss_window_after_writer_recovers(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    emitter = AuditEmitter(writer=writer, redactor=AuditRedactor())
    original_submit = writer.submit
    failures = 0

    async def fail_once(item):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("disk full")
        return await original_submit(item)

    writer.submit = fail_once
    failed = await emitter.emit(_event(1))
    recovered = await emitter.emit(_event(2), critical=True)
    writer.submit = original_submit
    await writer.close()

    assert failed.degraded is True
    assert recovered.degraded is False
    assert [event.event_type for event in emitter.recovery_events] == [
        "audit_degraded",
        "audit_recovered",
    ]


async def test_disabled_emitter_is_a_noop() -> None:
    result = await DisabledAuditEmitter().emit(_event(1), payload=_payload(1))
    assert result.disabled is True
    assert result.committed is False
