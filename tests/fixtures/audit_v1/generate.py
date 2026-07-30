"""One-time generator for immutable audit V1 compatibility fixtures."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from nanobot.audit.export import AuditExporter, ExportMode
from nanobot.audit.schema import RunFinishedDraft, RunStartedDraft
from nanobot.audit.verify import AuditVerifier
from nanobot.audit.writer import AuditWriter, CommitItem
from tests.audit.test_query import common
from tests.audit.test_writer import _item

ROOT = Path(__file__).resolve().parent


async def write_runs(path: Path, terminals: list[tuple[str, str, str | None]]) -> None:
    writer = AuditWriter(path, fsync_interval_seconds=0.01)
    await writer.start()
    offset = 1
    for run_id, status, resumed_from in terminals:
        started = RunStartedDraft.model_validate({
            **common(
                f"{run_id}-started",
                "run_started",
                turn=f"turn-{run_id}",
                run=run_id,
                offset=offset,
            ),
            "resumed_from_run_id": resumed_from,
        })
        offset += 1
        finished = RunFinishedDraft.model_validate({
            **common(
                f"{run_id}-finished",
                "run_finished",
                turn=f"turn-{run_id}",
                run=run_id,
                offset=offset,
            ),
            "resumed_from_run_id": resumed_from,
            "status": status,
            "stop_reason": status,
        })
        offset += 1
        await writer.submit(CommitItem(started, None, 512, True))
        await writer.submit(CommitItem(finished, None, 512, True))
    await writer.close()


def expected(path: Path, trace_id: str) -> None:
    exporter = AuditExporter.from_root(path)
    with (path / "expected-sanitized.json").open("wb") as output:
        exporter.export_trace(trace_id, mode=ExportMode.SANITIZED, output=output)
    with (path / "expected-full.json").open("wb") as output:
        exporter.export_trace(trace_id, mode=ExportMode.FULL, output=output)
    reports = AuditVerifier(path).verify_all()
    (path / "expected-verification.json").write_text(
        json.dumps(
            {
                key: {
                    "status": report.status,
                    "error_codes": report.error_codes,
                    "warning_codes": report.warning_codes,
                }
                for key, report in reports.items()
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


async def main() -> None:
    for name in ("normal", "failure_recovery", "cancel_resume"):
        path = ROOT / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    writer = AuditWriter(ROOT / "normal", fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    await writer.close()
    await write_runs(
        ROOT / "failure_recovery",
        [("failed-run", "failed", None), ("recovered-run", "succeeded", "failed-run")],
    )
    await write_runs(
        ROOT / "cancel_resume",
        [("cancelled-run", "cancelled", None), ("resumed-run", "succeeded", "cancelled-run")],
    )
    expected(ROOT / "normal", "t1")
    expected(ROOT / "failure_recovery", "trace")
    expected(ROOT / "cancel_resume", "trace")


if __name__ == "__main__":
    asyncio.run(main())
