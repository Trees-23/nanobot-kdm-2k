from io import BytesIO
from pathlib import Path

import pytest

from nanobot.audit.export import AuditExporter, ExportMode
from nanobot.audit.verify import AuditVerifier

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "audit_v1"


@pytest.mark.parametrize(
    ("name", "trace_id"),
    [
        ("normal", "t1"),
        ("failure_recovery", "trace"),
        ("cancel_resume", "trace"),
    ],
)
def test_v1_fixture_remains_readable(name: str, trace_id: str) -> None:
    root = FIXTURE_ROOT / name
    reports = AuditVerifier(root).verify_all()
    assert reports
    assert all(report.status == "valid" for report in reports.values())
    exporter = AuditExporter.from_root(root)
    sanitized = BytesIO()
    full = BytesIO()
    exporter.export_trace(trace_id, mode=ExportMode.SANITIZED, output=sanitized)
    exporter.export_trace(trace_id, mode=ExportMode.FULL, output=full)
    assert sanitized.getvalue() == (root / "expected-sanitized.json").read_bytes()
    assert full.getvalue() == (root / "expected-full.json").read_bytes()
