import json
import platform
from datetime import UTC, datetime
from types import SimpleNamespace

from nanobot.audit.lease import ProcessLease, ProcessLeaseState
from nanobot.audit.reconcile import AuditReconciler
from nanobot.audit.runtime import _boot_id
from nanobot.audit.schema import RunFinishedDraft, RunStartedDraft, ToolStartedDraft
from nanobot.audit.writer import AuditWriter, CommitItem
from tests.audit.test_query import common


class RecordingEmitter:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event, *, payload=None, critical=False):
        self.events.append(event)


async def crashed_fixture(root, *, tool: bool = False):
    writer = AuditWriter(root, fsync_interval_seconds=0.01)
    await writer.start()
    run = RunStartedDraft.model_validate(
        common("run-start", "run_started", turn="turn", run="run", offset=1)
    )
    await writer.submit(CommitItem(run, None, 512, True))
    if tool:
        tool_event = ToolStartedDraft.model_validate({
            **common("tool-start", "tool_started", turn="turn", run="run", offset=2),
            "tool_call_id": "tool",
            "tool_name": "exec",
        })
        await writer.submit(CommitItem(tool_event, None, 512, True))
    process_id = writer.process_id
    await writer.close()
    catalog = next((root / "catalog" / process_id).glob("*.jsonl"))
    lines = catalog.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["catalog_record_type"] == "process_closed"
    catalog.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    emitter = RecordingEmitter()
    runtime = SimpleNamespace(writer=SimpleNamespace(root=root), emitter=emitter)
    return runtime, process_id, emitter


async def test_uncertain_owner_only_emits_suspected(tmp_path) -> None:
    runtime, _process_id, emitter = await crashed_fixture(tmp_path)

    result = await AuditReconciler(runtime).reconcile()

    assert result.emitted_types == ("orphan_run_suspected",)
    assert [event.event_type for event in emitter.events] == ["orphan_run_suspected"]


async def test_confirmed_dead_emits_nested_orphans_without_results(tmp_path) -> None:
    runtime, process_id, emitter = await crashed_fixture(tmp_path, tool=True)
    lease = ProcessLease(
        tmp_path / "state" / "process-leases" / f"{process_id}.json"
    )
    lease.refresh(
        ProcessLeaseState(
            process_id,
            platform.node() or "unknown-host",
            _boot_id(),
            999_999_999,
            datetime.now(UTC),
            datetime.now(UTC),
        )
    )

    result = await AuditReconciler(runtime).reconcile()

    assert result.emitted_types == ("orphan_run_detected", "orphan_tool_detected")
    assert all(getattr(event, "payload_id", None) is None for event in emitter.events)


async def test_cancelled_run_is_not_reclassified_as_orphan(tmp_path) -> None:
    writer = AuditWriter(tmp_path, fsync_interval_seconds=0.01)
    await writer.start()
    started = RunStartedDraft.model_validate(
        common("start", "run_started", turn="turn", run="run", offset=1)
    )
    finished = RunFinishedDraft.model_validate({
        **common("finish", "run_finished", turn="turn", run="run", offset=2),
        "status": "cancelled",
        "stop_reason": "system_cancel",
    })
    await writer.submit(CommitItem(started, None, 512, True))
    await writer.submit(CommitItem(finished, None, 512, True))
    process_id = writer.process_id
    await writer.close()
    catalog = next((tmp_path / "catalog" / process_id).glob("*.jsonl"))
    lines = catalog.read_text(encoding="utf-8").splitlines()
    catalog.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    emitter = RecordingEmitter()
    runtime = SimpleNamespace(writer=SimpleNamespace(root=tmp_path), emitter=emitter)

    result = await AuditReconciler(runtime).reconcile()

    assert result.emitted_types == ()
