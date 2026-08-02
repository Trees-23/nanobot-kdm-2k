from pathlib import Path
from types import SimpleNamespace

from nanobot.agent.tool_failure import (
    DIAGNOSTIC_UNAVAILABLE,
    ERROR_MESSAGE_LIMIT,
    normalize_tool_failure,
)
from nanobot.agent.tool_failure import ERROR_SUMMARY_LIMIT as NORMALIZED_SUMMARY_LIMIT
from nanobot.audit.diagnostics import (
    ERROR_SUMMARY_LIMIT,
    safe_error_summary,
    safe_tool_input,
    tool_operation_evidence,
)


class _ReadTool:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def _resolve_read(self, path: str) -> Path:
        value = Path(path)
        return value if value.is_absolute() else self._workspace / value


def test_safe_tool_input_hides_external_path_and_keeps_relative_path(tmp_path) -> None:
    tool = _ReadTool(tmp_path / "workspace")

    external = safe_tool_input(
        "read_file", tool, {"path": "/srv/private/customer/config.json"}
    )
    relative = safe_tool_input("read_file", tool, {"path": "config.json"})

    assert external.summary == "path=<outside-workspace>"
    assert "/srv/private" not in (external.summary or "")
    assert relative.summary == "path=config.json"
    assert external.resource_key != relative.resource_key


def test_read_resource_correction_requires_one_directory_edit(tmp_path) -> None:
    tool = _ReadTool(tmp_path / "workspace")
    failed = safe_tool_input(
        "read_file", tool, {"path": "/home/nanobot/.nanobot/runtime/config.json"}
    )
    corrected = safe_tool_input(
        "read_file", tool, {"path": "/home/nanobot/.nanobot/config.json"}
    )
    unrelated = safe_tool_input("read_file", tool, {"path": "config.json"})

    assert corrected.resource_key in failed.correction_keys
    assert unrelated.resource_key not in failed.correction_keys


def test_web_search_summary_omits_query_and_unknown_provider() -> None:
    tool = SimpleNamespace(config=SimpleNamespace(provider="private-provider"))

    summary = safe_tool_input(
        "web_search", tool, {"query": "Authorization: Bearer top-secret"}
    )

    assert summary.summary == "query omitted; provider=omitted"
    assert "top-secret" not in summary.summary


def test_error_summary_is_allowlisted_redacted_and_bounded() -> None:
    summary = safe_error_summary(
        "unknown_tool" * 30,
        error_code="unknown_failure",
        error_type="Bearer abcdefghijklmnopqrstuvwxyz",
        effective_timeout_ms=None,
        provider=None,
        safe_input_summary=None,
    )

    assert summary is not None
    assert len(summary) <= ERROR_SUMMARY_LIMIT
    assert "abcdefghijklmnopqrstuvwxyz" not in summary


def test_normalized_failure_redacts_secrets_controls_and_retry_hint() -> None:
    failure = normalize_tool_failure(
        "Error: token=top-secret\x00\nBearer abcdefghijklmnopqrstuvwxyz"
        "\n\n[Analyze the error above and try a different approach.]",
        source="tool_result",
    )

    assert "top-secret" not in failure.message
    assert "abcdefghijklmnopqrstuvwxyz" not in failure.message
    assert "Analyze the error" not in failure.message
    assert "\x00" not in failure.message
    assert failure.error_type == "ToolError"
    assert failure.error_code == "tool_error"


def test_normalized_failure_is_non_empty_and_bounded() -> None:
    failure = normalize_tool_failure("\x00" + "x" * 4_000, source="runtime")

    assert failure.message
    assert failure.summary
    assert len(failure.message) <= ERROR_MESSAGE_LIMIT
    assert len(failure.summary) <= NORMALIZED_SUMMARY_LIMIT
    assert DIAGNOSTIC_UNAVAILABLE not in failure.message


def test_operation_evidence_keeps_unknown_plugins_unresolved() -> None:
    plugin = tool_operation_evidence("mcp_private", None, {"token": "secret"})
    builtin = tool_operation_evidence("list_exec_sessions", None, {})

    assert plugin.retry_key
    assert "secret" not in repr(plugin)
    assert plugin.verification_kind is None
    assert plugin.failure_fallback == "unresolved"
    assert builtin.failure_fallback == "continued"


def test_message_has_no_automatic_retry_without_idempotency_receipt() -> None:
    evidence = tool_operation_evidence(
        "message",
        None,
        {"channel": "test", "chat_id": "target", "content": "hello"},
    )

    assert evidence.retry_key is None
    assert evidence.verification_kind is None


def test_exec_session_evidence_separates_retry_from_continuation() -> None:
    failed = tool_operation_evidence(
        "write_stdin", None, {"session_id": "session-1", "chars": "input"}
    )
    polled = tool_operation_evidence(
        "write_stdin", None, {"session_id": "session-1", "chars": ""}
    )

    assert failed.retry_key != polled.retry_key
    assert failed.continuation_key == polled.continuation_key
    assert failed.verification_kind == "session_exit_zero"
