"""Mutable process liveness hints kept outside audit evidence chains."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from nanobot.audit.integrity import canonical_json_bytes
from nanobot.audit.segments import ensure_private_dir, fsync_directory

HEARTBEAT_INTERVAL_S = 5
STALE_AFTER_S = 30


@dataclass(frozen=True, slots=True)
class ProcessLeaseState:
    process_instance_id: str
    host_fingerprint: str
    boot_id: str
    pid: int
    started_at: datetime
    heartbeat_at: datetime


class ProcessLease:
    def __init__(self, path: Path) -> None:
        self.path = path

    def refresh(self, state: ProcessLeaseState) -> None:
        ensure_private_dir(self.path.parent)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as file:
                file.write(canonical_json_bytes(asdict(state)) + b"\n")
                file.flush()
                os.fsync(file.fileno())
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            fsync_directory(self.path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
