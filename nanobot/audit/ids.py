"""Audit identity generation."""

from __future__ import annotations

import secrets
import time
import uuid


def new_audit_id(*, timestamp_ms: int | None = None) -> str:
    """Return a UUIDv7 string with an optional deterministic timestamp."""
    timestamp = int(time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms)
    if not 0 <= timestamp < 1 << 48:
        raise ValueError("UUIDv7 timestamp must fit in 48 bits")
    value = timestamp << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return str(uuid.UUID(int=value))
