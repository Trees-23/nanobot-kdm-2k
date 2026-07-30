"""Recognized-secret redaction for durable audit evidence."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from nanobot.audit.types import JsonValue

REDACTED = "[REDACTED:CREDENTIAL]"

_KNOWN_SECRET_KEYS = {
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "setcookie",
    "token",
    "accesstoken",
}

_BUILTIN_PATTERNS = (
    ("builtin_bearer", r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    ("builtin_openai_key", r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    ("builtin_aws_access_key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


class RedactionError(RuntimeError):
    """A redaction rule failed without exposing candidate content."""


@dataclass(frozen=True, slots=True)
class RedactionReport:
    rule_counts: dict[str, int]

    @property
    def replacement_count(self) -> int:
        return sum(self.rule_counts.values())


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


class AuditRedactor:
    def __init__(
        self,
        *,
        additional_keys: Iterable[str] = (),
        additional_patterns: Iterable[str] = (),
    ) -> None:
        self._secret_keys = _KNOWN_SECRET_KEYS | {
            normalize_key(key) for key in additional_keys
        }
        patterns: list[tuple[str, re.Pattern[str]]] = [
            (rule_id, re.compile(pattern)) for rule_id, pattern in _BUILTIN_PATTERNS
        ]
        for index, pattern in enumerate(additional_patterns):
            rule_id = f"custom_pattern_{index}"
            try:
                compiled = re.compile(pattern)
            except re.error:
                raise ValueError(f"invalid redaction rule {rule_id}") from None
            patterns.append((rule_id, compiled))
        self._patterns = tuple(patterns)

    @staticmethod
    def _substitute(pattern: re.Pattern[str], text: str) -> tuple[str, int]:
        return pattern.subn(REDACTED, text)

    def redact(self, value: JsonValue) -> tuple[JsonValue, RedactionReport]:
        counts: Counter[str] = Counter()
        cleaned = self._walk(value, counts)
        return cleaned, RedactionReport(dict(counts))

    def _walk(self, value: Any, counts: Counter[str]) -> JsonValue:
        if isinstance(value, dict):
            cleaned: dict[str, JsonValue] = {}
            for key, item in value.items():
                if normalize_key(str(key)) in self._secret_keys:
                    cleaned[str(key)] = REDACTED
                    counts["structured_credential"] += 1
                else:
                    cleaned[str(key)] = self._walk(item, counts)
            return cleaned
        if isinstance(value, list):
            return [self._walk(item, counts) for item in value]
        if isinstance(value, str):
            cleaned = value
            for rule_id, pattern in self._patterns:
                try:
                    cleaned, replacements = self._substitute(pattern, cleaned)
                except Exception:
                    raise RedactionError(f"redaction rule failed: {rule_id}") from None
                counts[rule_id] += replacements
            return cleaned
        if value is None or isinstance(value, bool | int | float):
            return value
        raise RedactionError(f"unsupported redaction type: {type(value).__name__}")
