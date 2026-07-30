from nanobot.audit.reader import AuditReader
from nanobot.audit.runtime import AuditRuntime
from nanobot.config.schema import AuditConfig
from tests.audit.test_writer import _event


async def test_disabled_runtime_is_noop(tmp_path) -> None:
    runtime = AuditRuntime.from_config(AuditConfig(mode="off"), root=tmp_path)
    await runtime.start()
    result = await runtime.emitter.emit(_event(1))
    await runtime.close()
    assert result.disabled is True
    assert list(tmp_path.rglob("*.jsonl")) == []


async def test_full_runtime_starts_once_and_closes_writer(tmp_path) -> None:
    runtime = AuditRuntime.from_config(
        AuditConfig(mode="full", warn_plaintext_payloads=False), root=tmp_path
    )
    await runtime.start()
    await runtime.ensure_started()
    process_id = runtime.writer.process_id
    await runtime.close()
    await runtime.close()

    result = AuditReader(tmp_path).read_process(process_id)
    assert result.cleanly_closed is True
    assert len(list((tmp_path / "catalog" / process_id).glob("*.jsonl"))) == 1


async def test_runtime_refreshes_lease_before_close(tmp_path) -> None:
    runtime = AuditRuntime.from_config(
        AuditConfig(mode="metadata_only", warn_plaintext_payloads=False), root=tmp_path
    )
    await runtime.start()
    assert runtime.lease is not None
    assert runtime.lease.path.exists()
    await runtime.close()
