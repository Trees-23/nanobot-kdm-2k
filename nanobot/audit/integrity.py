"""Canonical serialization and hash-chain verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashing and JSONL storage."""
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def hash_record(record: Mapping[str, Any] | BaseModel, *, hash_field: str) -> str:
    """Hash a record while excluding its own hash field."""
    raw = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    payload = {key: value for key, value in raw.items() if key != hash_field}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class ChainVerification:
    valid: bool
    checked_records: int
    error_code: str | None = None
    error_sequence: int | None = None


_CHAIN_FIELDS = {
    "event_hash": ("segment_sequence", "previous_event_hash"),
    "payload_hash": ("payload_segment_sequence", "previous_payload_hash"),
    "catalog_record_hash": ("catalog_sequence", "previous_catalog_hash"),
}


def verify_chain(
    records: Iterable[Mapping[str, Any] | BaseModel],
    *,
    hash_field: str,
    sequence_field: str | None = None,
    previous_hash_field: str | None = None,
) -> ChainVerification:
    """Verify sequence, predecessor, and record hashes for one segment."""
    inferred = _CHAIN_FIELDS.get(hash_field)
    if sequence_field is None or previous_hash_field is None:
        if inferred is None:
            raise ValueError(f"chain fields are required for {hash_field}")
        sequence_field = sequence_field or inferred[0]
        previous_hash_field = previous_hash_field or inferred[1]

    previous_hash: str | None = None
    expected_sequence = 1
    seen: set[int] = set()
    checked = 0
    for model in records:
        record = model.model_dump(mode="json") if isinstance(model, BaseModel) else dict(model)
        sequence = record.get(sequence_field)
        if not isinstance(sequence, int):
            return ChainVerification(False, checked, "invalid_sequence", None)
        if sequence in seen:
            return ChainVerification(False, checked, "duplicate_sequence", sequence)
        if sequence != expected_sequence:
            return ChainVerification(False, checked, "sequence_gap", sequence)
        if record.get(previous_hash_field) != previous_hash:
            return ChainVerification(False, checked, "previous_hash_mismatch", sequence)
        actual_hash = record.get(hash_field)
        if actual_hash != hash_record(record, hash_field=hash_field):
            return ChainVerification(False, checked, "record_hash_mismatch", sequence)
        seen.add(sequence)
        previous_hash = str(actual_hash)
        expected_sequence += 1
        checked += 1
    return ChainVerification(True, checked)
