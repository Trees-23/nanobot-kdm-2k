from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

VERSION_DIR = (
    Path(__file__).resolve().parents[2]
    / "_other"
    / "评测"
    / "审计运行轨迹"
    / "V4"
)


def load_module(filename: str):
    path = VERSION_DIR / filename
    name = f"audit_trace_v4_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def valid_evidence(module) -> dict[str, object]:
    return {
        "container": {"status": "running"},
        "http": {
            "health_status": 200,
            "index_status": 200,
            "audit_status": 401,
            "bundle_status": 200,
        },
        "served": {"index_sha256": "a" * 64, "bundle_sha256": "b" * 64},
        "container_dist": {"index_sha256": "a" * 64, "bundle_sha256": "b" * 64},
        "backend_files": [{"host_sha256": "c" * 64, "container_sha256": "c" * 64}],
        "bundle_markers": {marker: True for marker in module.REQUIRED_BUNDLE_MARKERS},
        "host_dist_reference": {
            "present": True,
            "index_sha256": "d" * 64,
            "matches_deployed": False,
            "gate_effect": "informational_only",
        },
    }


def test_host_dist_mismatch_is_informational_not_a_gate_failure() -> None:
    module = load_module("deployment_gate.py")

    result = module.evaluate(valid_evidence(module))

    assert result["passed"] is True
    assert result["policy"]["host_dist_hash_mismatch_is_failure"] is False


def test_deployed_container_mismatch_or_missing_marker_fails_gate() -> None:
    module = load_module("deployment_gate.py")
    evidence = valid_evidence(module)
    evidence["container_dist"]["bundle_sha256"] = "e" * 64
    evidence["bundle_markers"][module.REQUIRED_BUNDLE_MARKERS[0]] = False

    result = module.evaluate(evidence)

    assert result["passed"] is False
    assert result["checks"]["served_bundle_is_container_bundle"] is False
    assert result["checks"]["required_bundle_markers_present"] is False


def test_authority_hashes_are_machine_valid_and_v4_has_no_update_list() -> None:
    module = load_module("authority_hashes.py")

    report = module.build_report()

    assert report["valid"] is True
    assert "更新清单.md" not in report["files"]
    assert all(module.SHA256_PATTERN.fullmatch(value) for value in report["files"].values())
    assert set(report["dependencies"]) == {"V2/fixture_stage.py", "V3/prompt_send.py"}
    assert all(
        module.SHA256_PATTERN.fullmatch(value) for value in report["dependencies"].values()
    )
