#!/usr/bin/env python3
"""Stage immutable audit-trace fixtures into an explicit Agent workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "fixture-manifest.json"
SOURCE_ROOT = SCRIPT_DIR / "fixtures"


class FixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fixture:
    relative_path: PurePosixPath
    sha256: str
    source: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(raw: Any, field: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw:
        raise FixtureError(f"{field} must be a non-empty string")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise FixtureError(f"{field} must be a normalized relative path")
    return path


def load_manifest() -> tuple[PurePosixPath, tuple[Fixture, ...]]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read manifest: {exc}") from exc

    if manifest.get("schema_version") != 1:
        raise FixtureError("unsupported manifest schema_version")
    target_root = safe_relative_path(manifest.get("target_root"), "target_root")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise FixtureError("manifest files must be a non-empty list")

    fixtures: list[Fixture] = []
    seen: set[PurePosixPath] = set()
    for index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            raise FixtureError(f"files[{index}] must be an object")
        relative_path = safe_relative_path(item.get("path"), f"files[{index}].path")
        expected_hash = item.get("sha256")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)
        ):
            raise FixtureError(f"files[{index}].sha256 must be lowercase SHA-256")
        if relative_path in seen:
            raise FixtureError(f"duplicate fixture path: {relative_path}")
        seen.add(relative_path)

        source = SOURCE_ROOT.joinpath(*relative_path.parts)
        if source.is_symlink() or not source.is_file():
            raise FixtureError(f"fixture source is missing or unsafe: {relative_path}")
        actual_hash = sha256_file(source)
        if actual_hash != expected_hash:
            raise FixtureError(
                f"fixture source hash mismatch: {relative_path} "
                f"expected={expected_hash} actual={actual_hash}"
            )
        fixtures.append(Fixture(relative_path, expected_hash, source))
    return target_root, tuple(fixtures)


def resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise FixtureError("workspace must be an existing directory")
    return workspace


def ensure_no_symlink(workspace: Path, relative_path: PurePosixPath) -> None:
    current = workspace
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise FixtureError(f"workspace target contains symlink: {current}")


def target_paths(
    workspace: Path,
    target_root_relative: PurePosixPath,
    fixtures: tuple[Fixture, ...],
) -> tuple[Path, dict[PurePosixPath, Path]]:
    ensure_no_symlink(workspace, target_root_relative)
    target_root = workspace.joinpath(*target_root_relative.parts)
    resolved_root = target_root.resolve(strict=False)
    if not resolved_root.is_relative_to(workspace):
        raise FixtureError("target_root escapes workspace")

    targets: dict[PurePosixPath, Path] = {}
    for fixture in fixtures:
        combined = PurePosixPath(*target_root_relative.parts, *fixture.relative_path.parts)
        ensure_no_symlink(workspace, combined)
        target = workspace.joinpath(*combined.parts)
        if not target.resolve(strict=False).is_relative_to(workspace):
            raise FixtureError(f"fixture target escapes workspace: {fixture.relative_path}")
        targets[fixture.relative_path] = target
    return target_root, targets


def assert_no_unexpected_files(
    target_root: Path,
    expected_paths: set[Path],
) -> None:
    if not target_root.exists():
        return
    if target_root.is_symlink() or not target_root.is_dir():
        raise FixtureError("target_root exists but is not a safe directory")
    discovered = {
        path
        for path in target_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    unexpected = sorted(str(path.relative_to(target_root)) for path in discovered - expected_paths)
    if unexpected:
        raise FixtureError(f"unexpected files in target_root: {unexpected}")


def verify_target(
    target_root: Path,
    targets: dict[PurePosixPath, Path],
    fixtures: tuple[Fixture, ...],
) -> None:
    assert_no_unexpected_files(target_root, set(targets.values()))
    for fixture in fixtures:
        target = targets[fixture.relative_path]
        if target.is_symlink() or not target.is_file():
            raise FixtureError(f"staged fixture is missing or unsafe: {fixture.relative_path}")
        actual_hash = sha256_file(target)
        if actual_hash != fixture.sha256:
            raise FixtureError(
                f"staged fixture hash mismatch: {fixture.relative_path} "
                f"expected={fixture.sha256} actual={actual_hash}"
            )


def stage(
    target_root: Path,
    targets: dict[PurePosixPath, Path],
    fixtures: tuple[Fixture, ...],
) -> None:
    assert_no_unexpected_files(target_root, set(targets.values()))
    for fixture in fixtures:
        target = targets[fixture.relative_path]
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise FixtureError(f"refusing unsafe existing target: {fixture.relative_path}")
            actual_hash = sha256_file(target)
            if actual_hash != fixture.sha256:
                raise FixtureError(f"refusing to overwrite different target: {fixture.relative_path}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as output, fixture.source.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    verify_target(target_root, targets, fixtures)


def cleanup(
    target_root: Path,
    targets: dict[PurePosixPath, Path],
    fixtures: tuple[Fixture, ...],
) -> None:
    verify_target(target_root, targets, fixtures)
    for target in targets.values():
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
        target.unlink()
    directories = sorted(
        (path for path in target_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.rmdir()
    target_root.rmdir()


def summary(
    action: str,
    workspace: Path,
    target_root: Path,
    fixtures: tuple[Fixture, ...],
) -> dict[str, Any]:
    return {
        "action": action,
        "workspace": str(workspace),
        "target_root": str(target_root),
        "files": [
            {"path": str(fixture.relative_path), "sha256": fixture.sha256}
            for fixture in fixtures
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("source-verify", "stage", "verify", "cleanup"))
    parser.add_argument("--workspace", required=True, help="Host path mounted as Agent workspace")
    args = parser.parse_args()

    try:
        target_root_relative, fixtures = load_manifest()
        workspace = resolve_workspace(args.workspace)
        target_root, targets = target_paths(workspace, target_root_relative, fixtures)
        if args.action == "stage":
            stage(target_root, targets, fixtures)
        elif args.action == "verify":
            verify_target(target_root, targets, fixtures)
        elif args.action == "cleanup":
            cleanup(target_root, targets, fixtures)
        print(json.dumps(summary(args.action, workspace, target_root, fixtures), sort_keys=True))
        return 0
    except (FixtureError, OSError) as exc:
        print(f"fixture staging failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
