"""Fail-closed diagnostic summaries and deterministic resource identities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.audit.redaction import AuditRedactor, RedactionError

ERROR_SUMMARY_LIMIT = 160
SAFE_INPUT_SUMMARY_LIMIT = 128
_SAFE_PROVIDER_LABELS = {"duckduckgo": "DuckDuckGo"}


@dataclass(frozen=True, slots=True)
class SafeToolInput:
    summary: str | None = None
    resource_key: str | None = None
    correction_keys: tuple[str, ...] = ()


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


def safe_tool_input(tool_name: str, tool: Any, params: Any) -> SafeToolInput:
    if tool_name == "web_search":
        provider = getattr(getattr(tool, "config", None), "provider", None)
        safe_provider = provider if provider in _SAFE_PROVIDER_LABELS else "omitted"
        return SafeToolInput(summary=f"query omitted; provider={safe_provider}")
    if tool_name != "read_file" or not isinstance(params, dict):
        return SafeToolInput()
    raw_path = params.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return SafeToolInput(summary="path omitted")
    try:
        resolver = getattr(tool, "_resolve_read", None)
        resolved = Path(resolver(raw_path) if callable(resolver) else raw_path).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return SafeToolInput(summary="path unavailable")

    workspace_value = getattr(tool, "_workspace", None)
    display = "<outside-workspace>"
    if workspace_value is not None:
        try:
            workspace = Path(workspace_value).resolve(strict=False)
            display = resolved.relative_to(workspace).as_posix() or "."
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    summary = f"path={display}"[:SAFE_INPUT_SUMMARY_LIMIT]
    return SafeToolInput(
        summary=summary,
        resource_key=_fingerprint(tool_name, resolved),
        correction_keys=_path_correction_keys(tool_name, resolved),
    )


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
