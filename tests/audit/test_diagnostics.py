from pathlib import Path
from types import SimpleNamespace

from nanobot.audit.diagnostics import (
    ERROR_SUMMARY_LIMIT,
    safe_error_summary,
    safe_tool_input,
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
