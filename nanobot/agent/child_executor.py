"""Killable process supervisor for subagent workers."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

IPC_SCHEMA_VERSION = 1
MAX_IPC_FRAME_BYTES = 1_048_576
_CHILD_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TZ",
    "VIRTUAL_ENV",
)


@dataclass(frozen=True, slots=True)
class ChildProcessIdentity:
    """Non-persistent identity used to reject PID reuse while supervising a worker."""

    executor_id: str
    process_instance_id: str
    supervisor_instance_id: str
    pid: int
    pgid: int
    proc_start_ticks: str


@dataclass(frozen=True, slots=True)
class ChildExit:
    """Observed worker exit and bounded termination evidence."""

    returncode: int
    result: dict[str, Any] | None
    exit_observed: bool = True
    reaped: bool = True
    descendants_cleared: bool = True
    forced: bool = False
    term_sent: bool = False
    kill_sent: bool = False
    reason: str = "exited"

    @property
    def termination_confirmed(self) -> bool:
        return self.exit_observed and self.reaped and self.descendants_cleared


@dataclass(slots=True)
class ChildHandle:
    """One live worker owned by a supervisor instance."""

    identity: ChildProcessIdentity
    process: asyncio.subprocess.Process
    exit_future: asyncio.Future[ChildExit]
    reader_task: asyncio.Task[None]
    lifecycle: list[dict[str, Any]] = field(default_factory=list)
    lifecycle_queue: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=asyncio.Queue
    )
    result: dict[str, Any] | None = None
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProcessChildExecutor:
    """Run one subagent per POSIX process group with structured pipe IPC."""

    backend = "process_group_v1"

    def __init__(
        self,
        *,
        worker_module: str = "nanobot.agent.child_worker",
        python_executable: str | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.worker_module = worker_module
        self.python_executable = python_executable or sys.executable
        self.platform_name = platform_name or os.name
        self.supervisor_instance_id = uuid.uuid4().hex
        self._handles: dict[str, ChildHandle] = {}

    @property
    def force_kill_available(self) -> bool:
        return self.platform_name == "posix" and sys.platform.startswith("linux")

    async def start(self, payload: dict[str, Any]) -> ChildHandle:
        """Start a worker and send its versioned execution spec over stdin."""
        if not self.force_kill_available:
            raise RuntimeError("process-group child executor is unavailable on this platform")
        executor_id = uuid.uuid4().hex
        process_instance_id = uuid.uuid4().hex
        envelope = {
            "schema_version": IPC_SCHEMA_VERSION,
            "type": "start",
            "executor_id": executor_id,
            "process_instance_id": process_instance_id,
            "payload": payload,
        }
        self._encode_envelope(envelope)
        process = await asyncio.create_subprocess_exec(
            self.python_executable,
            "-m",
            self.worker_module,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env=self._child_environment(),
        )
        handle: ChildHandle | None = None
        try:
            pid = process.pid
            identity = ChildProcessIdentity(
                executor_id=executor_id,
                process_instance_id=process_instance_id,
                supervisor_instance_id=self.supervisor_instance_id,
                pid=pid,
                pgid=os.getpgid(pid),
                proc_start_ticks=self._proc_start_ticks(pid),
            )
            exit_future: asyncio.Future[ChildExit] = asyncio.get_running_loop().create_future()
            handle = ChildHandle(
                identity=identity,
                process=process,
                exit_future=exit_future,
                reader_task=None,  # type: ignore[arg-type]
            )
            handle.reader_task = asyncio.create_task(self._read_worker(handle))
            self._handles[executor_id] = handle
            await self._write(handle, envelope)
            return handle
        except BaseException:
            if handle is not None:
                await self.force_kill(handle)
                await asyncio.gather(handle.reader_task, return_exceptions=True)
            else:
                self._signal_group(process.pid, signal.SIGKILL)
                await process.wait()
            raise

    async def request_cancel(self, handle: ChildHandle) -> None:
        """Request cooperative cancellation without claiming the worker exited."""
        if handle.process.returncode is not None:
            return
        await self._write(handle, {
            "schema_version": IPC_SCHEMA_VERSION,
            "type": "cancel",
            "executor_id": handle.identity.executor_id,
            "process_instance_id": handle.identity.process_instance_id,
        })

    async def wait(self, handle: ChildHandle, timeout: float | None = None) -> ChildExit | None:
        """Wait without cancelling the shared exit observation on timeout."""
        waiter = asyncio.shield(handle.exit_future)
        if timeout is None:
            return await waiter
        try:
            return await asyncio.wait_for(waiter, timeout=max(0.0, timeout))
        except TimeoutError:
            return None

    async def force_kill(
        self,
        handle: ChildHandle,
        *,
        term_grace_seconds: float = 0.5,
        descendant_grace_seconds: float = 1.0,
    ) -> ChildExit:
        """Terminate a verified process group, reap its root, and check descendants."""
        existing = await self.wait(handle, 0)
        if existing is not None:
            return existing
        if not self._identity_matches(handle.identity):
            return ChildExit(
                returncode=handle.process.returncode or 0,
                result=handle.result,
                exit_observed=handle.process.returncode is not None,
                reaped=handle.process.returncode is not None,
                descendants_cleared=False,
                forced=True,
                reason="identity_mismatch",
            )

        term_sent = self._signal_group(handle.identity.pgid, signal.SIGTERM)
        observed = await self.wait(handle, term_grace_seconds)
        kill_sent = False
        if observed is None or self._process_group_exists(handle.identity.pgid):
            kill_sent = self._signal_group(handle.identity.pgid, signal.SIGKILL)
            observed = await self.wait(handle, max(0.1, descendant_grace_seconds))
        if observed is None:
            return ChildExit(
                returncode=handle.process.returncode or 0,
                result=handle.result,
                exit_observed=False,
                reaped=False,
                descendants_cleared=False,
                forced=True,
                term_sent=term_sent,
                kill_sent=kill_sent,
                reason="reap_timeout",
            )

        deadline = asyncio.get_running_loop().time() + max(0.0, descendant_grace_seconds)
        while self._process_group_exists(handle.identity.pgid):
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.02)
        descendants_cleared = not self._process_group_exists(handle.identity.pgid)
        return replace(
            observed,
            descendants_cleared=descendants_cleared,
            forced=True,
            term_sent=term_sent,
            kill_sent=kill_sent,
            reason="force_killed" if descendants_cleared else "descendants_remain",
        )

    async def close(self) -> None:
        """Boundedly terminate all workers still owned by this supervisor."""
        handles = list(self._handles.values())
        for handle in handles:
            if handle.process.returncode is None:
                await self.force_kill(handle)
        if handles:
            await asyncio.gather(*(handle.reader_task for handle in handles), return_exceptions=True)

    async def _write(self, handle: ChildHandle, envelope: dict[str, Any]) -> None:
        writer = handle.process.stdin
        if writer is None or writer.is_closing():
            return
        encoded = self._encode_envelope(envelope)
        async with handle.write_lock:
            writer.write(encoded)
            await writer.drain()

    @staticmethod
    def _encode_envelope(envelope: dict[str, Any]) -> bytes:
        encoded = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        if len(encoded) > MAX_IPC_FRAME_BYTES:
            raise ValueError("child executor IPC frame exceeds the 1 MiB limit")
        return encoded

    async def _read_worker(self, handle: ChildHandle) -> None:
        stream = handle.process.stdout
        try:
            if stream is not None:
                while line := await stream.readline():
                    if len(line) > MAX_IPC_FRAME_BYTES:
                        continue
                    try:
                        envelope = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not self._envelope_matches(handle, envelope):
                        continue
                    if envelope.get("type") == "lifecycle":
                        handle.lifecycle.append(envelope)
                        handle.lifecycle_queue.put_nowait(envelope)
                    elif envelope.get("type") == "result" and handle.result is None:
                        result = envelope.get("result")
                        if isinstance(result, dict):
                            handle.result = result
            returncode = await handle.process.wait()
            if not handle.exit_future.done():
                handle.exit_future.set_result(ChildExit(returncode=returncode, result=handle.result))
        except BaseException as exc:
            if not handle.exit_future.done():
                handle.exit_future.set_exception(exc)
            raise
        finally:
            handle.lifecycle_queue.put_nowait(None)
            if handle.process.stdin is not None:
                handle.process.stdin.close()
            self._handles.pop(handle.identity.executor_id, None)

    @staticmethod
    def _envelope_matches(handle: ChildHandle, envelope: Any) -> bool:
        return (
            isinstance(envelope, dict)
            and envelope.get("schema_version") == IPC_SCHEMA_VERSION
            and envelope.get("executor_id") == handle.identity.executor_id
            and envelope.get("process_instance_id") == handle.identity.process_instance_id
        )

    @staticmethod
    def _proc_start_ticks(pid: int) -> str:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return stat[stat.rfind(")") + 2:].split()[19]

    @staticmethod
    def _child_environment() -> dict[str, str]:
        """Pass only runtime plumbing; provider credentials travel in pipe IPC."""
        return {key: os.environ[key] for key in _CHILD_ENV_ALLOWLIST if key in os.environ}

    def _identity_matches(self, identity: ChildProcessIdentity) -> bool:
        if identity.supervisor_instance_id != self.supervisor_instance_id:
            return False
        try:
            return (
                os.getpgid(identity.pid) == identity.pgid
                and self._proc_start_ticks(identity.pid) == identity.proc_start_ticks
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            return False

    @staticmethod
    def _signal_group(pgid: int, sent_signal: signal.Signals) -> bool:
        try:
            os.killpg(pgid, sent_signal)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def _process_group_exists(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            return exc.errno != errno.ESRCH
