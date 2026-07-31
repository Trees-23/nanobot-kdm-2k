from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "_other"
    / "评测"
    / "审计运行轨迹"
)


def load_fixture_stage(version: str = "V2"):
    script_path = SCRIPT_ROOT / version / "fixture_stage.py"
    spec = importlib.util.spec_from_file_location(
        f"audit_trace_fixture_stage_{version.lower()}", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_context(module, workspace: Path):
    target_root_relative, fixtures = module.load_manifest()
    target_root, targets = module.target_paths(workspace, target_root_relative, fixtures)
    return target_root, targets, fixtures


def test_stage_verify_and_cleanup_are_idempotent(tmp_path: Path) -> None:
    module = load_fixture_stage()
    target_root, targets, fixtures = fixture_context(module, tmp_path)

    module.stage(target_root, targets, fixtures)
    module.stage(target_root, targets, fixtures)
    module.verify_target(target_root, targets, fixtures)

    assert len(list(target_root.rglob("*.*"))) == 3
    module.cleanup(target_root, targets, fixtures)
    assert not target_root.exists()


def test_stage_refuses_to_overwrite_different_content(tmp_path: Path) -> None:
    module = load_fixture_stage()
    target_root, targets, fixtures = fixture_context(module, tmp_path)
    target = targets[fixtures[0].relative_path]
    target.parent.mkdir(parents=True)
    target.write_text("different", encoding="utf-8")

    with pytest.raises(module.FixtureError, match="refusing to overwrite different target"):
        module.stage(target_root, targets, fixtures)

    assert target.read_text(encoding="utf-8") == "different"


def test_cleanup_refuses_a_modified_fixture(tmp_path: Path) -> None:
    module = load_fixture_stage()
    target_root, targets, fixtures = fixture_context(module, tmp_path)
    module.stage(target_root, targets, fixtures)
    target = targets[fixtures[0].relative_path]
    target.chmod(0o600)
    target.write_text("modified", encoding="utf-8")

    with pytest.raises(module.FixtureError, match="staged fixture hash mismatch"):
        module.cleanup(target_root, targets, fixtures)

    assert target.exists()


def test_target_paths_reject_workspace_symlink(tmp_path: Path) -> None:
    module = load_fixture_stage()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "evals").symlink_to(outside, target_is_directory=True)
    target_root_relative, fixtures = module.load_manifest()

    with pytest.raises(module.FixtureError, match="workspace target contains symlink"):
        module.target_paths(tmp_path, target_root_relative, fixtures)


def test_v3_uses_own_manifest_and_complete_lifecycle(tmp_path: Path) -> None:
    module = load_fixture_stage("V3")
    target_root, targets, fixtures = fixture_context(module, tmp_path)

    assert target_root.relative_to(tmp_path).as_posix() == "evals/audit-trace-v3/fixtures"
    module.stage(target_root, targets, fixtures)
    module.verify_target(target_root, targets, fixtures)
    assert len(list(target_root.rglob("*.*"))) == 3

    module.cleanup(target_root, targets, fixtures)
    assert not target_root.exists()
