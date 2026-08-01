"""Seed a real audit writer/index for the Gateway Chromium acceptance test."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nanobot.audit.index import AuditIndexer
from nanobot.audit.read_service import AuditReadService
from nanobot.audit.runtime import AuditRuntime
from nanobot.audit.schema import audit_event_draft_adapter
from nanobot.config.schema import AuditConfig

TRACE_ID = "trace-real-tool-recovery"
TURN_ID = "turn-real-tool-recovery"
RUN_ID = "run-real-tool-recovery"
SESSION_KEY = "websocket:real-tool-recovery"


def _event(
    sequence: int,
    event_type: str,
    *,
    tool_call_id: str | None = None,
    **fields: Any,
):
    occurred_at = datetime(2026, 8, 2, tzinfo=UTC) + timedelta(milliseconds=sequence)
    common = {
        "event_id": f"real-event-{sequence:04d}",
        "event_type": event_type,
        "occurred_at": occurred_at,
        "monotonic_ns": time.monotonic_ns() + sequence,
        "trace_id": TRACE_ID,
        "turn_id": TURN_ID,
        "run_id": RUN_ID if event_type not in {"trace_created", "turn_started", "turn_finished", "turn_response_prepared"} else None,
        "parent_run_id": None,
        "resumed_from_run_id": None,
        "caused_by_event_id": None,
        "model_call_id": None,
        "attempt_id": None,
        "tool_call_id": tool_call_id,
        "checkpoint_id": None,
        "goal_id": None,
        "delivery_id": None,
        "session_key": SESSION_KEY,
        "source_type": "websocket",
        "source_metadata": {},
        "iteration": 1 if event_type.startswith("tool_") else None,
        **fields,
    }
    return audit_event_draft_adapter.validate_python(common)


async def _seed(root: Path) -> int:
    runtime = AuditRuntime.from_config(
        AuditConfig(
            mode="metadata_only",
            path=str(root),
            fsync_interval_seconds=0.01,
            fsync_record_interval=1,
        ),
        root=root,
    )
    await runtime.start()
    events = [
        _event(1, "trace_created", actor_type="user", creation_reason="created"),
        _event(2, "turn_started"),
        _event(3, "run_started"),
        _event(4, "tool_started", tool_call_id="failed-read", tool_name="read_file"),
        _event(
            5,
            "tool_finished",
            tool_call_id="failed-read",
            tool_name="read_file",
            elapsed_ms=1,
            status="error",
            error_type="FileNotFoundError",
            error_code="file_not_found",
            error_summary="File not found (path=<outside-workspace>)",
            safe_input_summary="path=<outside-workspace>",
            recovery_of_tool_call_ids=[],
        ),
        _event(6, "tool_started", tool_call_id="recovered-read", tool_name="read_file"),
        _event(
            7,
            "tool_finished",
            tool_call_id="recovered-read",
            tool_name="read_file",
            elapsed_ms=1,
            status="ok",
            safe_input_summary="path=<outside-workspace>",
            recovery_of_tool_call_ids=["failed-read"],
        ),
        _event(8, "run_finished", status="succeeded", stop_reason="completed"),
        _event(9, "turn_response_prepared", response_kind="assistant"),
        _event(10, "turn_finished", status="response_prepared"),
    ]
    for event in events:
        result = await runtime.emitter.emit(event, critical=True)
        if not result.accepted:
            raise RuntimeError(f"audit event was not accepted: {event.event_type}")
    await runtime.close()
    update = AuditIndexer(root).update()
    if not update.coverage_complete:
        raise RuntimeError("audit index coverage is incomplete")
    return AuditReadService(root / "state" / "audit-index.sqlite").status().revision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--websocket-port", type=int, required=True)
    parser.add_argument("--gateway-port", type=int, required=True)
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    args.workspace.mkdir(parents=True, exist_ok=True)
    revision = asyncio.run(_seed(args.root))
    config = {
        "agents": {
            "defaults": {
                "workspace": str(args.workspace),
                "provider": "custom",
                "model": "custom/acceptance-model",
                "maxToolIterations": 1,
                "dream": {"enabled": False},
            }
        },
        "providers": {
            "custom": {
                "apiKey": "acceptance-no-external-call",
                "apiBase": "http://127.0.0.1:9/v1",
            }
        },
        "channels": {
            "websocket": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": args.websocket_port,
                "allowFrom": ["*"],
                "tokenIssueSecret": args.secret,
            }
        },
        "gateway": {
            "host": "127.0.0.1",
            "port": args.gateway_port,
            "heartbeat": {"enabled": False},
        },
        "audit": {
            "mode": "metadata_only",
            "path": str(args.root),
            "indexEnabled": True,
            "warnPlaintextPayloads": False,
        },
    }
    args.config.write_text(json.dumps(config), encoding="utf-8")
    print(json.dumps({"trace_id": TRACE_ID, "revision": revision}))


if __name__ == "__main__":
    main()
