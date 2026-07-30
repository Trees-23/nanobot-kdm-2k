import asyncio
import time

from nanobot.audit.index_service import AuditIndexService
from nanobot.audit.writer import AuditWriter
from tests.audit.test_writer import _item


async def test_refresh_runs_blocking_index_work_off_event_loop(tmp_path, monkeypatch) -> None:
    service = AuditIndexService(tmp_path)

    def slow_update() -> None:
        time.sleep(0.05)

    monkeypatch.setattr(service.indexer, "update", slow_update)
    monkeypatch.setattr(service.read_service, "status_from_index", lambda: service._status)
    refresh = asyncio.create_task(service.refresh_now())
    await asyncio.sleep(0.005)

    assert refresh.done() is False
    await refresh


async def test_refresh_keeps_last_good_revision_on_failure(tmp_path, monkeypatch) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1, payload=False))
    await writer.close()
    service = AuditIndexService(tmp_path)

    ready = await service.refresh_now()
    assert ready.state == "ready"
    assert ready.revision is not None

    def fail() -> None:
        raise OSError("private path must not escape")

    monkeypatch.setattr(service.indexer, "update", fail)
    stale = await service.refresh_now()

    assert stale.state == "stale"
    assert stale.revision == ready.revision
    assert stale.last_error is not None
    assert stale.last_error.message == "OSError"


async def test_disabled_service_does_not_start_background_task(tmp_path) -> None:
    service = AuditIndexService(tmp_path, enabled=False)
    await service.start()

    assert service.snapshot_status().state == "disabled"
    assert service._task is None
