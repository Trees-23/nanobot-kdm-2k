"""Safe, stable normalization for terminal tool failures."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from nanobot.audit.redaction import AuditRedactor, RedactionError

ToolFailureSource = Literal[
    "validation",
    "tool_result",
    "exception",
    "timeout",
    "cancelled",
    "policy",
    "provider",
    "runtime",
]
ToolRetryability = Literal["retryable", "non_retryable", "unknown"]

ERROR_MESSAGE_LIMIT = 1024
ERROR_SUMMARY_LIMIT = 160
DIAGNOSTIC_UNAVAILABLE = "Diagnostic summary unavailable"
_RETRY_HINT = "[Analyze the error above and try a different approach.]"
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_DEFAULT_CODES: dict[ToolFailureSource, str] = {
    "validation": "invalid_tool_arguments",
    "tool_result": "tool_error",
    "exception": "tool_exception",
    "timeout": "tool_timeout",
    "cancelled": "tool_cancelled",
    "policy": "policy_blocked",
    "provider": "provider_error",
    "runtime": "runtime_error",
}


@dataclass(frozen=True, slots=True)
class NormalizedToolFailure:
    message: str
    summary: str
    error_type: str
    error_code: str
    source: ToolFailureSource
    retryability: ToolRetryability


def _strip_retry_hint(value: str) -> str:
    before, marker, _after = value.partition(_RETRY_HINT)
    return before.rstrip() if marker else value


def _remove_controls(value: str) -> str:
    return "".join(
        " " if char in "\r\n\t" else char
        for char in value
        if not unicodedata.category(char).startswith("C") or char in "\r\n\t"
    )


def _safe_text(value: str, *, limit: int) -> str:
    candidate = _strip_retry_hint(str(value or "")).strip()
    candidate = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED:CREDENTIAL]", candidate)
    try:
        cleaned, _ = AuditRedactor().redact(candidate)
    except RedactionError:
        return DIAGNOSTIC_UNAVAILABLE
    if not isinstance(cleaned, str):
        return DIAGNOSTIC_UNAVAILABLE
    cleaned = _remove_controls(cleaned).strip()
    return cleaned[:limit].rstrip() or DIAGNOSTIC_UNAVAILABLE


def normalize_tool_failure(
    message: str,
    *,
    source: ToolFailureSource,
    error_type: str | None = None,
    error_code: str | None = None,
    retryability: ToolRetryability | None = None,
) -> NormalizedToolFailure:
    """Normalize one terminal failure without inspecting error prose for semantics."""
    safe_message = _safe_text(message, limit=ERROR_MESSAGE_LIMIT)
    summary_source = re.sub(r"(?i)^error:\s*", "", safe_message, count=1)
    summary_source = re.sub(r"\s+", " ", summary_source).strip()
    if source == "policy":
        default_retryability: ToolRetryability = "non_retryable"
    elif source == "validation":
        default_retryability = "non_retryable"
    elif source == "timeout":
        default_retryability = "retryable"
    else:
        default_retryability = "unknown"
    return NormalizedToolFailure(
        message=safe_message,
        summary=_safe_text(summary_source, limit=ERROR_SUMMARY_LIMIT),
        error_type=_safe_text(error_type or "ToolError", limit=128),
        error_code=_safe_text(error_code or _DEFAULT_CODES[source], limit=128),
        source=source,
        retryability=retryability or default_retryability,
    )
