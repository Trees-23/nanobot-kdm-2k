import json

from typer.testing import CliRunner

from nanobot.audit.writer import AuditWriter
from nanobot.cli.commands import app
from tests.audit.test_writer import _item

runner = CliRunner()


async def write_fixture(root) -> None:
    writer = AuditWriter(root, fsync_interval_seconds=0.01)
    await writer.start()
    await writer.submit(_item(1))
    await writer.close()


def test_audit_help_lists_commands() -> None:
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    for command in ("list", "show", "verify", "export", "stats", "index", "doctor"):
        assert command in result.stdout


async def test_list_and_show_default_to_sanitized(tmp_path) -> None:
    await write_fixture(tmp_path)

    listed = runner.invoke(app, ["audit", "list", "--audit-root", str(tmp_path)])
    shown = runner.invoke(app, ["audit", "show", "t1", "--audit-root", str(tmp_path)])

    assert listed.exit_code == 0
    assert "t1" in listed.stdout
    assert shown.exit_code == 0
    assert "message 1" not in shown.stdout
    assert '"payloads"' not in shown.stdout


async def test_show_payloads_requires_explicit_flag_and_warns(tmp_path) -> None:
    await write_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["audit", "show", "t1", "--include-payloads", "--audit-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "permanent plaintext" in result.stdout
    assert "message 1" in result.stdout


async def test_custom_audit_root_does_not_read_other_instance(tmp_path) -> None:
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    await write_fixture(selected)
    await write_fixture(other)

    result = runner.invoke(app, ["audit", "list", "--audit-root", str(selected)])

    assert result.exit_code == 0
    assert str(other) not in result.stdout


async def test_verify_export_and_stats_commands(tmp_path) -> None:
    await write_fixture(tmp_path)
    output = tmp_path / "trace.json"

    verified = runner.invoke(
        app, ["audit", "verify", "t1", "--audit-root", str(tmp_path)]
    )
    exported = runner.invoke(
        app,
        [
            "audit",
            "export",
            "t1",
            "--output",
            str(output),
            "--audit-root",
            str(tmp_path),
        ],
    )
    rejected_stats = runner.invoke(
        app, ["audit", "stats", "--audit-root", str(tmp_path)]
    )
    stats = runner.invoke(
        app, ["audit", "stats", "--all", "--audit-root", str(tmp_path)]
    )

    assert verified.exit_code == 0
    assert '"status": "valid"' in verified.stdout
    assert exported.exit_code == 0
    assert output.exists()
    assert "message 1" not in output.read_text(encoding="utf-8")
    assert rejected_stats.exit_code != 0
    assert stats.exit_code == 0


async def test_config_selects_its_own_audit_path(tmp_path) -> None:
    selected = tmp_path / "selected-audit"
    await write_fixture(selected)
    config_path = tmp_path / "instance" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"audit": {"path": str(selected)}}), encoding="utf-8"
    )

    result = runner.invoke(app, ["audit", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "t1" in result.stdout
