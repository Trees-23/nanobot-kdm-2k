"""Fail-closed diagnostic summaries and deterministic resource identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.audit.redaction import AuditRedactor, RedactionError

ERROR_SUMMARY_LIMIT = 160
SAFE_INPUT_SUMMARY_LIMIT = 128
_SAFE_PROVIDER_LABELS = {"duckduckgo": "DuckDuckGo"}


@dataclass(frozen=True, slots=True)
class ToolOperationEvidence:
    operation_kind: str = "unknown"
    summary: str | None = None
    resource_key: str | None = None
    correction_keys: tuple[str, ...] = ()
    resource_keys: tuple[str, ...] = ()
    continuation_key: str | None = None
    retry_key: str | None = None
    verification_kind: str | None = None
    failure_fallback: str = "unresolved"


SafeToolInput = ToolOperationEvidence

_BUILTIN_TOOLS = {
    "apply_patch", "await_subagents", "create_goal", "cron", "edit_file", "exec",
    "find_files", "generate_image", "grep", "list_dir", "list_exec_sessions", "message",
    "my", "read_file", "run_cli_app", "spawn", "update_goal", "web_fetch", "web_search",
    "write_file", "write_stdin",
}
_READ_TOOLS = {"read_file", "list_dir", "find_files", "grep"}
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch"}


def _runtime_fingerprint(kind: str, value: Any) -> str | None:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    digest = hashlib.sha256(f"{kind}\0{encoded}".encode()).hexdigest()
    return f"runtime-sha256:{digest}"


def _fingerprint(tool_name: str, path: Path) -> str:
    canonical = path.as_posix()
    digest = hashlib.sha256(f"{tool_name}\0{canonical}".encode()).hexdigest()
    return f"sha256:{digest}"


def _path_correction_keys(tool_name: str, path: Path) -> tuple[str, ...]:
    """Fingerprint paths formed by deleting exactly one intermediate directory."""
    parts = path.parts
    if not path.is_absolute() or len(parts) < 4:
        return ()
    variants = {
        Path(*parts[:index], *parts[index + 1 :])
        for index in range(1, len(parts) - 1)
    }
    return tuple(sorted(_fingerprint(tool_name, candidate) for candidate in variants))


def tool_operation_evidence(
    tool_name: str,
    tool: Any,
    params: Any,
) -> ToolOperationEvidence:
    values = params if isinstance(params, dict) else {}
    failure_fallback = "continued" if tool_name in _BUILTIN_TOOLS else "unresolved"
    retry_key = _runtime_fingerprint(tool_name, params)
    if tool_name in {"message", "spawn", "create_goal"}:
        retry_key = None
    if tool_name == "web_search":
        provider = getattr(getattr(tool, "config", None), "provider", None)
        safe_provider = provider if provider in _SAFE_PROVIDER_LABELS else "omitted"
        return ToolOperationEvidence(
            tool_name,
            summary=f"query omitted; provider={safe_provider}",
            retry_key=retry_key,
            verification_kind="provider_response",
            failure_fallback=failure_fallback,
        )
    if tool_name == "web_fetch":
        return ToolOperationEvidence(
            tool_name,
            summary="URL omitted",
            retry_key=retry_key,
            verification_kind="provider_response",
            failure_fallback=failure_fallback,
        )
    if tool_name == "write_stdin":
        session_id = values.get("session_id")
        continuation_key = (
            _runtime_fingerprint("exec_session", session_id)
            if isinstance(session_id, str) and session_id
            else None
        )
        return ToolOperationEvidence(
            tool_name,
            summary="exec session identity recorded" if continuation_key else "session omitted",
            continuation_key=continuation_key,
            retry_key=retry_key,
            verification_kind="session_exit_zero",
            failure_fallback=failure_fallback,
        )
    if tool_name == "await_subagents":
        identity = values.get("task_ids") or values.get("task_group")
        return ToolOperationEvidence(
            tool_name,
            summary="subagent task identity recorded" if identity else "task identity omitted",
            continuation_key=_runtime_fingerprint("subagent_tasks", identity) if identity else None,
            retry_key=retry_key,
            failure_fallback=failure_fallback,
        )
    if tool_name == "exec":
        return ToolOperationEvidence(
            tool_name,
            summary="command omitted",
            retry_key=retry_key,
            verification_kind="process_exit_zero",
            failure_fallback=failure_fallback,
        )
    if tool_name in _WRITE_TOOLS:
        return ToolOperationEvidence(
            tool_name,
            summary="write targets omitted",
            retry_key=retry_key,
            verification_kind="filesystem_after_state",
            failure_fallback=failure_fallback,
        )
    if tool_name == "generate_image":
        return ToolOperationEvidence(
            tool_name,
            summary="image request omitted",
            retry_key=retry_key,
            verification_kind="artifact_reference",
            failure_fallback=failure_fallback,
        )
    if tool_name not in _READ_TOOLS or not isinstance(params, dict):
        return ToolOperationEvidence(
            tool_name,
            retry_key=retry_key,
            failure_fallback=failure_fallback,
        )
    raw_path = values.get("path", ".")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ToolOperationEvidence(
            tool_name,
            summary="path omitted",
            retry_key=retry_key,
            verification_kind="read_success",
            failure_fallback=failure_fallback,
        )
    try:
        resolver = getattr(tool, "_resolve_read", None) or getattr(tool, "_resolve", None)
        resolved = Path(resolver(raw_path) if callable(resolver) else raw_path).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ToolOperationEvidence(
            tool_name,
            summary="path unavailable",
            retry_key=retry_key,
            verification_kind="read_success",
            failure_fallback=failure_fallback,
        )

    workspace_value = getattr(tool, "_workspace", None)
    display = "<outside-workspace>"
    if workspace_value is not None:
        try:
            workspace = Path(workspace_value).resolve(strict=False)
            display = resolved.relative_to(workspace).as_posix() or "."
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    summary = f"path={display}"[:SAFE_INPUT_SUMMARY_LIMIT]
    resource_key = _fingerprint(tool_name, resolved)
    return ToolOperationEvidence(
        tool_name,
        summary=summary,
        resource_key=resource_key,
        correction_keys=_path_correction_keys(tool_name, resolved),
        resource_keys=(resource_key,),
        retry_key=retry_key,
        verification_kind="read_success",
        failure_fallback=failure_fallback,
    )


def safe_tool_input(tool_name: str, tool: Any, params: Any) -> SafeToolInput:
    return tool_operation_evidence(tool_name, tool, params)


def safe_error_summary(
    tool_name: str,
    *,
    error_code: str | None,
    error_type: str | None,
    effective_timeout_ms: int | None,
    provider: str | None,
    safe_input_summary: str | None,
) -> str | None:
    if not error_code and not error_type:
        return None
    if error_code == "web_search_timeout" and effective_timeout_ms is not None:
        label = _SAFE_PROVIDER_LABELS.get(provider or "", "Web search")
        seconds = effective_timeout_ms / 1000
        duration = str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"
        summary = f"{label} search timed out after {duration}s"
    elif error_code == "file_not_found":
        target = safe_input_summary or "path unavailable"
        summary = f"File not found ({target})"
    elif error_code == "web_search_failed":
        label = _SAFE_PROVIDER_LABELS.get(provider or "", "Web search")
        summary = f"{label} search failed ({error_type or 'unknown error'})"
    else:
        summary = f"{tool_name} failed ({error_type or 'unknown error'})"
    try:
        cleaned, _ = AuditRedactor().redact(summary[:ERROR_SUMMARY_LIMIT])
    except RedactionError:
        return "Diagnostic summary unavailable"
    return cleaned if isinstance(cleaned, str) else "Diagnostic summary unavailable"
