"""Process-level lifecycle for audit writer, emitter, and liveness lease."""

from __future__ import annotations

import asyncio
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from nanobot.audit.context import TraceContextResolver
from nanobot.audit.emitter import AuditEmitter, DisabledAuditEmitter
from nanobot.audit.lease import HEARTBEAT_INTERVAL_S, ProcessLease, ProcessLeaseState
from nanobot.audit.redaction import AuditRedactor
from nanobot.audit.writer import AuditWriter
from nanobot.config.schema import AuditConfig


def _boot_id() -> str:
    path = Path("/proc/sys/kernel/random/boot_id")
    try:
        return path.read_text(encoding="utf-8").strip() or "unknown-boot"
    except OSError:
        return "unknown-boot"


class AuditRuntime:
    def __init__(
        self,
        *,
        writer: AuditWriter | None,
        emitter: AuditEmitter | DisabledAuditEmitter,
        lease: ProcessLease | None = None,
        warn_plaintext_payloads: bool = False,
    ) -> None:
        self.writer = writer
        self.emitter = emitter
        self.lease = lease
        self.context_resolver = TraceContextResolver()
        self._warn_plaintext_payloads = warn_plaintext_payloads
        self._start_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_stop = asyncio.Event()
        self._started = False
        self._closed = False
        self._started_at: datetime | None = None
        self._host_fingerprint = platform.node() or "unknown-host"
        self._boot_id = _boot_id()

    @classmethod
    def disabled(cls) -> AuditRuntime:
        return cls(writer=None, emitter=DisabledAuditEmitter())

    @classmethod
    def from_config(cls, config: AuditConfig, *, root: Path) -> AuditRuntime:
        if config.mode == "off":
            return cls.disabled()
        writer = AuditWriter.from_config(root=root, config=config)
        lease = ProcessLease(root / "state" / "process-leases" / f"{writer.process_id}.json")
        redactor = AuditRedactor(
            additional_keys=config.additional_secret_keys,
            additional_patterns=config.additional_secret_patterns,
        )
        return cls(
            writer=writer,
            emitter=AuditEmitter(writer=writer, redactor=redactor, mode=config.mode),
            lease=lease,
            warn_plaintext_payloads=config.mode == "full" and config.warn_plaintext_payloads,
        )

    async def start(self) -> None:
        await self.ensure_started()

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("audit runtime is already closed")
            if self.writer is None:
                self._started = True
                return
            await self.writer.start()
            self._started_at = datetime.now(UTC)
            self._refresh_lease()
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name=f"audit-heartbeat-{self.writer.process_id}"
            )
            self._started = True
            if self._warn_plaintext_payloads:
                logger.warning(
                    "Audit full mode stores permanent plaintext payloads; recognized-secret "
                    "redaction does not identify every opaque credential"
                )

    def _lease_state(self) -> ProcessLeaseState:
        assert self.writer is not None
        assert self._started_at is not None
        return ProcessLeaseState(
            process_instance_id=self.writer.process_id,
            host_fingerprint=self._host_fingerprint,
            boot_id=self._boot_id,
            pid=os.getpid(),
            started_at=self._started_at,
            heartbeat_at=datetime.now(UTC),
        )

    def _refresh_lease(self) -> None:
        if self.lease is not None:
            self.lease.refresh(self._lease_state())

    async def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._heartbeat_stop.wait(), timeout=HEARTBEAT_INTERVAL_S
                )
            except TimeoutError:
                try:
                    self._refresh_lease()
                except Exception as error:
                    if isinstance(self.emitter, AuditEmitter):
                        self.emitter.record_failure(trace_id=None, error=error)

    async def close(self) -> None:
        async with self._start_lock:
            if self._closed:
                return
            self._closed = True
            self._heartbeat_stop.set()
            if self._heartbeat_task is not None:
                await self._heartbeat_task
                self._heartbeat_task = None
            if self.writer is not None and self._started:
                await self.writer.close()

    def __repr__(self) -> str:
        mode = "off" if self.writer is None else "enabled"
        return f"AuditRuntime(mode={mode!r})"
