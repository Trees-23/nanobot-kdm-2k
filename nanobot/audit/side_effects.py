"""Capability-specific, verifiable tool side-effect evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanobot.utils.file_edit_events import FileEditTracker, prepare_file_edit_trackers

_EXIT_CODE_RE = re.compile(r"(?:^|\n)Exit code:\s*(-?\d+)(?:\n|$)")


def _file_hash(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass(slots=True)
class SideEffectSnapshot:
    tool_name: str
    file_trackers: list[FileEditTracker] = field(default_factory=list)
    before_hashes: dict[str, str | None] = field(default_factory=dict)
    cwd: str | None = None


def capture_side_effect_before(
    *,
    call_id: str,
    tool_name: str,
    tool: Any,
    params: Any,
    workspace: Path | None = None,
) -> SideEffectSnapshot:
    values = params if isinstance(params, dict) else {}
    trackers = prepare_file_edit_trackers(
        call_id=call_id,
        tool_name=tool_name,
        tool=tool,
        workspace=workspace,
        params=values,
    )
    before_hashes = {
        tracker.path.as_posix(): _file_hash(tracker.path) for tracker in trackers
    }
    cwd: str | None = None
    if tool_name in {"exec", "write_stdin", "run_cli_app"}:
        raw_cwd = values.get("cwd") or getattr(tool, "workspace", None) or getattr(
            tool, "_workspace", None
        )
        if raw_cwd:
            try:
                cwd = Path(raw_cwd).expanduser().resolve(strict=False).as_posix()
            except (OSError, RuntimeError, TypeError, ValueError):
                cwd = str(raw_cwd)
    return SideEffectSnapshot(tool_name, trackers, before_hashes, cwd)


def capture_side_effect_after(
    snapshot: SideEffectSnapshot,
    result: Any,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for tracker in snapshot.file_trackers:
        path = tracker.path.as_posix()
        evidence.append(
            {
                "kind": "filesystem_path",
                "path": tracker.display_path,
                "absolute_path": path,
                "before_exists": tracker.before.exists,
                "after_exists": tracker.path.is_file(),
                "before_sha256": snapshot.before_hashes.get(path),
                "after_sha256": _file_hash(tracker.path),
                "verification_scope": "affected_path_only",
            }
        )
    if snapshot.cwd is not None:
        match = _EXIT_CODE_RE.search(str(result or ""))
        evidence.append(
            {
                "kind": "process_execution",
                "cwd": snapshot.cwd,
                "exit_code": int(match.group(1)) if match else None,
                "verification_scope": "process_result_only",
            }
        )
    return evidence
