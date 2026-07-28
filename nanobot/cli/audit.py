"""Read-oriented `nanobot audit` command surface."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console

from nanobot.audit.export import AuditExporter, ExportMode
from nanobot.audit.index import AuditIndex, AuditIndexer, IndexRebuildRequired
from nanobot.audit.query import AuditQuery, TraceFilter
from nanobot.audit.verify import AuditVerifier
from nanobot.config.loader import load_config
from nanobot.config.paths import get_audit_dir


def _root(config_path: str | None, audit_root: str | None) -> Path:
    if audit_root:
        return Path(audit_root).expanduser().resolve()
    if config_path:
        path = Path(config_path).expanduser().resolve()
        config = load_config(path)
        if config.audit.path:
            return Path(config.audit.path).expanduser().resolve()
        return path.parent / "audit" / "v1"
    config = load_config()
    return get_audit_dir(config.audit.path)


def _options(
    config: str | None,
    audit_root: str | None,
) -> Path:
    return _root(config, audit_root)


def create_audit_app(*, console: Console) -> typer.Typer:
    audit_app = typer.Typer(help="Inspect durable Agent audit evidence", no_args_is_help=True)
    index_app = typer.Typer(help="Manage the disposable SQLite audit index")

    @audit_app.command("list")
    def audit_list(
        limit: int = typer.Option(50, "--limit", min=1, max=500),
        cursor: str | None = typer.Option(None, "--cursor"),
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        page = AuditQuery.from_root(_options(config, audit_root)).find_traces(
            TraceFilter(limit=limit, cursor=cursor)
        )
        console.print_json(json.dumps(page.model_dump(mode="json")))

    @audit_app.command("show")
    def audit_show(
        trace_id: str,
        include_payloads: bool = typer.Option(False, "--include-payloads"),
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        if include_payloads:
            console.print(
                "[yellow]Warning: full audit payloads may contain permanent plaintext.[/yellow]"
            )
        try:
            view = AuditQuery.from_root(_options(config, audit_root)).load_trace(
                trace_id, include_payloads=include_payloads
            )
        except KeyError:
            console.print("[red]Trace not found[/red]")
            raise typer.Exit(1) from None
        value = view.model_dump(mode="json")
        if not include_payloads:
            value.pop("payloads", None)
        console.print_json(json.dumps(value))

    @audit_app.command("verify")
    def audit_verify(
        trace_id: str | None = typer.Argument(None),
        all_processes: bool = typer.Option(False, "--all"),
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        root = _options(config, audit_root)
        if all_processes:
            reports = AuditVerifier(root).verify_all()
            value = {
                key: {
                    "status": report.status,
                    "error_codes": report.error_codes,
                    "warning_codes": report.warning_codes,
                }
                for key, report in reports.items()
            }
            valid = all(report.status == "valid" for report in reports.values())
        elif trace_id:
            try:
                report = AuditQuery.from_root(root).load_trace(trace_id).integrity
            except KeyError:
                console.print("[red]Trace not found[/red]")
                raise typer.Exit(1) from None
            value = {
                "status": report.status,
                "error_codes": report.error_codes,
                "warning_codes": report.warning_codes,
            }
            valid = report.status == "valid"
        else:
            raise typer.BadParameter("provide TRACE_ID or --all")
        console.print_json(json.dumps(value))
        if not valid:
            raise typer.Exit(1)

    @audit_app.command("export")
    def audit_export(
        trace_id: str,
        output: Path = typer.Option(..., "--output", "-o"),
        mode: ExportMode = typer.Option(ExportMode.SANITIZED, "--mode"),
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        if mode is ExportMode.FULL:
            console.print(
                "[yellow]Warning: exporting full permanent plaintext audit payloads.[/yellow]"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as file:
            report = AuditExporter.from_root(_options(config, audit_root)).export_trace(
                trace_id, mode=mode, output=file
            )
        console.print_json(json.dumps(asdict(report), default=str))

    @audit_app.command("stats")
    def audit_stats(
        all_events: bool = typer.Option(False, "--all"),
        group_by: str = typer.Option("source_type", "--group-by"),
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        if not all_events:
            raise typer.BadParameter("stats requires --all or an explicit time range")
        root = _options(config, audit_root)
        if not (root / "state" / "audit-index.sqlite").exists():
            AuditIndexer(root).update()
        report = AuditQuery.from_root(root, use_index=True).stats(group_by=group_by)
        console.print_json(json.dumps(report.model_dump(mode="json")))

    @index_app.command("status")
    def index_status(
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        root = _options(config, audit_root)
        path = root / "state" / "audit-index.sqlite"
        if not path.exists():
            console.print_json(json.dumps({"exists": False}))
            return
        try:
            index = AuditIndex.open(path)
        except IndexRebuildRequired as error:
            console.print_json(json.dumps({"exists": True, "valid": False, "error": str(error)}))
            raise typer.Exit(1) from None
        try:
            event_count = index.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            console.print_json(
                json.dumps({"exists": True, "valid": True, "event_count": event_count})
            )
        finally:
            index.close()

    @index_app.command("rebuild")
    def index_rebuild(
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        root = _options(config, audit_root)
        path = root / "state" / "audit-index.sqlite"
        if path.exists():
            path.unlink()
        report = AuditIndexer(root).update()
        console.print_json(json.dumps(asdict(report)))

    @audit_app.command("doctor")
    def audit_doctor(
        config: str | None = typer.Option(None, "--config", "-c"),
        audit_root: str | None = typer.Option(None, "--audit-root"),
    ) -> None:
        root = _options(config, audit_root)
        usage = shutil.disk_usage(root if root.exists() else root.parent)
        reports = AuditVerifier(root).verify_all()
        value = {
            "root": str(root),
            "exists": root.exists(),
            "free_bytes": usage.free,
            "process_count": len(reports),
            "integrity": {key: report.status for key, report in reports.items()},
            "index_exists": (root / "state" / "audit-index.sqlite").exists(),
        }
        console.print_json(json.dumps(value))

    audit_app.add_typer(index_app, name="index")
    return audit_app
