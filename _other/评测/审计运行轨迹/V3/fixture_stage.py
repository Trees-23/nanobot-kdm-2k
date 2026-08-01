#!/usr/bin/env python3
"""Run the frozen V2 staging implementation with V3-owned assets and target paths."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
V2_SCRIPT = SCRIPT_DIR.parent / "V2" / "fixture_stage.py"

spec = importlib.util.spec_from_file_location("audit_trace_v3_fixture_stage_impl", V2_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load fixture staging implementation: {V2_SCRIPT}")
_impl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = _impl
spec.loader.exec_module(_impl)

_impl.SCRIPT_DIR = SCRIPT_DIR
_impl.MANIFEST_PATH = SCRIPT_DIR / "fixture-manifest.json"
_impl.SOURCE_ROOT = SCRIPT_DIR / "fixtures"

FixtureError = _impl.FixtureError
Fixture = _impl.Fixture
sha256_file = _impl.sha256_file
load_manifest = _impl.load_manifest
resolve_workspace = _impl.resolve_workspace
target_paths = _impl.target_paths
verify_target = _impl.verify_target
stage = _impl.stage
cleanup = _impl.cleanup
main = _impl.main


if __name__ == "__main__":
    raise SystemExit(main())
