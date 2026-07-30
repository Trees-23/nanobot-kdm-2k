import json
import os
from datetime import UTC, datetime

from nanobot.audit.lease import ProcessLease, ProcessLeaseState


def test_lease_refresh_atomically_replaces_state(tmp_path) -> None:
    path = tmp_path / "state" / "p1.json"
    lease = ProcessLease(path)
    first = ProcessLeaseState("p1", "host", "boot", 12, datetime.now(UTC), datetime.now(UTC))
    lease.refresh(first)
    second = ProcessLeaseState("p1", "host", "boot", 12, first.started_at, datetime.now(UTC))
    lease.refresh(second)

    raw = json.loads(path.read_text())
    assert raw["process_instance_id"] == "p1"
    assert raw["heartbeat_at"] == second.heartbeat_at.isoformat().replace("+00:00", "Z")
    assert list(path.parent.glob("*.tmp")) == []
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
