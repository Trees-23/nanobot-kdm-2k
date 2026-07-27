import uuid

import pytest

from nanobot.audit.ids import new_audit_id


def test_new_audit_id_is_uuid7() -> None:
    value = uuid.UUID(new_audit_id())
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_new_audit_id_embeds_requested_timestamp() -> None:
    value = uuid.UUID(new_audit_id(timestamp_ms=1_700_000_000_123))
    assert value.int >> 80 == 1_700_000_000_123


@pytest.mark.parametrize("timestamp", [-1, 1 << 48])
def test_new_audit_id_rejects_out_of_range_timestamp(timestamp: int) -> None:
    with pytest.raises(ValueError, match="48 bits"):
        new_audit_id(timestamp_ms=timestamp)
