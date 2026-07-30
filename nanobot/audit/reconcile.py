"""Conservative crash reconciliation without fabricated execution outcomes."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nanobot.audit.ids import new_audit_id
from nanobot.audit.lease import STALE_AFTER_S
from nanobot.audit.reader import AuditReader
from nanobot.audit.runtime import _boot_id
from nanobot.audit.schema import (
    OrphanModelCallDetectedDraft,
    OrphanRunDetectedDraft,
    OrphanRunSuspectedDraft,
    OrphanToolDetectedDraft,
)

Liveness = Literal["alive", "dead", "uncertain"]


def _windows_pid_liveness(pid: int) -> Liveness:
    """Check a Windows PID without sending a signal to the process."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            close_handle(handle)
            return "alive"
        error = ctypes.get_last_error()
    except (AttributeError, OSError, ValueError):
        return "uncertain"
    if error == 87:  # ERROR_INVALID_PARAMETER: the PID does not exist.
        return "dead"
    if error == 5:  # ERROR_ACCESS_DENIED: a protected process still exists.
        return "alive"
    return "uncertain"


def _local_pid_liveness(pid: int) -> Liveness:
    if os.name == "nt":
        return _windows_pid_liveness(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (PermissionError, OSError):
        return "uncertain"
    return "alive"


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    emitted_types: tuple[str, ...]
    inspected_processes: int


class AuditReconciler:
    def __init__(self, runtime: Any, *, root: Path | None = None) -> None:
        self.runtime = runtime
        writer = getattr(runtime, "writer", None)
        self.root = root or getattr(writer, "root", None)
        if self.root is None:
            raise ValueError("audit reconciliation requires an audit root")
        self.root = Path(self.root)
        self.reader = AuditReader(self.root)

    def _lease_state(self, process_id: str) -> dict[str, Any] | None:
        path = self.root / "state" / "process-leases" / f"{process_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def classify_liveness(self, process_id: str, *, cleanly_closed: bool) -> Liveness:
        if cleanly_closed:
            return "dead"
        lease = self._lease_state(process_id)
        if lease is None:
            return "uncertain"
        try:
            heartbeat = datetime.fromisoformat(str(lease["heartbeat_at"]).replace("Z", "+00:00"))
            pid = int(lease["pid"])
            host = str(lease["host_fingerprint"])
            boot = str(lease["boot_id"])
        except (KeyError, TypeError, ValueError):
            return "uncertain"
        local_host = platform.node() or "unknown-host"
        if host != local_host:
            age = (datetime.now(UTC) - heartbeat).total_seconds()
            return "alive" if age <= STALE_AFTER_S else "uncertain"
        if boot != _boot_id():
            return "dead"
        return _local_pid_liveness(pid)

    @staticmethod
    def _common(event_type: str, source: Any) -> dict[str, Any]:
        return {
            "event_id": new_audit_id(),
            "event_type": event_type,
            "occurred_at": datetime.now(UTC),
            "monotonic_ns": time.monotonic_ns(),
            "trace_id": source.trace_id,
            "turn_id": source.turn_id,
            "run_id": source.run_id,
            "parent_run_id": source.parent_run_id,
            "resumed_from_run_id": source.resumed_from_run_id,
            "caused_by_event_id": None,
            "model_call_id": source.model_call_id,
            "attempt_id": None,
            "tool_call_id": source.tool_call_id,
            "checkpoint_id": None,
            "goal_id": None,
            "delivery_id": None,
            "session_key": source.session_key,
            "source_type": "reconciler",
            "source_metadata": {},
            "iteration": source.iteration,
        }

    async def reconcile(self) -> ReconcileReport:
        emitted: list[str] = []
        inspected = 0
        for process_id in self.reader.process_ids():
            result = self.reader.read_process(process_id)
            if result.cleanly_closed:
                continue
            inspected += 1
            liveness = self.classify_liveness(
                process_id, cleanly_closed=result.cleanly_closed
            )
            if liveness == "alive":
                continue
            terminal_runs = {
                event.run_id for event in result.events if event.event_type == "run_finished"
            }
            already_orphaned = {
                event.run_id
                for event in result.events
                if event.event_type in {"orphan_run_suspected", "orphan_run_detected"}
            }
            open_runs = [
                event
                for event in result.events
                if event.event_type == "run_started"
                and event.run_id not in terminal_runs
                and event.run_id not in already_orphaned
            ]
            for run in open_runs:
                if liveness == "uncertain":
                    event = OrphanRunSuspectedDraft.model_validate(
                        {
                            **self._common("orphan_run_suspected", run),
                            "owner_process_instance_id": process_id,
                            "evidence_kind": "owner_liveness_uncertain",
                            "observed_at": datetime.now(UTC),
                        }
                    )
                    await self.runtime.emitter.emit(event, critical=True)
                    emitted.append(event.event_type)
                    continue
                event = OrphanRunDetectedDraft.model_validate(
                    {
                        **self._common("orphan_run_detected", run),
                        "owner_process_instance_id": process_id,
                        "evidence_kind": "owner_confirmed_dead",
                        "observed_at": datetime.now(UTC),
                    }
                )
                await self.runtime.emitter.emit(event, critical=True)
                emitted.append(event.event_type)

                terminal_models = {
                    item.model_call_id
                    for item in result.events
                    if item.event_type in {"model_response_received", "model_request_failed"}
                }
                terminal_tools = {
                    item.tool_call_id
                    for item in result.events
                    if item.event_type == "tool_finished"
                }
                for item in result.events:
                    if item.run_id != run.run_id:
                        continue
                    if (
                        item.event_type == "model_request_started"
                        and item.model_call_id not in terminal_models
                    ):
                        nested = OrphanModelCallDetectedDraft.model_validate(
                            {
                                **self._common("orphan_model_call_detected", item),
                                "owner_process_instance_id": process_id,
                                "evidence_kind": "owner_confirmed_dead",
                            }
                        )
                    elif (
                        item.event_type == "tool_started"
                        and item.tool_call_id not in terminal_tools
                    ):
                        nested = OrphanToolDetectedDraft.model_validate(
                            {
                                **self._common("orphan_tool_detected", item),
                                "owner_process_instance_id": process_id,
                                "evidence_kind": "owner_confirmed_dead",
                            }
                        )
                    else:
                        continue
                    await self.runtime.emitter.emit(nested, critical=True)
                    emitted.append(nested.event_type)
        return ReconcileReport(tuple(emitted), inspected)
