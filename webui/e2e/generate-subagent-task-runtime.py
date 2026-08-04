"""Build and mutate a deterministic durable-task runtime for real WebUI acceptance."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from nanobot.config.loader import set_config_path
from nanobot.session.manager import SessionManager
from nanobot.session.subagent_tasks import (
    SubagentExecutionPhase,
    SubagentTaskStatus,
    SubagentTaskStore,
    SubagentTerminationState,
)
from nanobot.webui.transcript import append_transcript_object

CHAT_ID = "subagent-task-real-20260803"
SESSION_KEY = f"websocket:{CHAT_ID}"


async def _create_running(
    store: SubagentTaskStore,
    task_id: str,
    label: str,
    *,
    required: bool = False,
) -> None:
    await store.create(
        task_id=task_id,
        owner_session_key=SESSION_KEY,
        owner_run_id="run-main",
        child_run_id=f"run-{task_id}",
        spawn_tool_call_id=f"spawn-{task_id}",
        label=label,
        required=required,
        task_group="real-acceptance",
        budget={
            "max_tokens": 800,
            "max_cost_usd": 0.05,
            "wall_time_seconds": 180,
            "reservation_state": "reserved",
        },
    )
    await store.transition_status(task_id, SubagentTaskStatus.QUEUED)
    await store.transition_status(task_id, SubagentTaskStatus.RUNNING)
    await store.update_runtime(
        task_id,
        phase=SubagentExecutionPhase.AWAITING_TOOLS,
        usage={
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "cost_usd": 0.0042,
        },
    )


async def _initial(workspace: Path, config: Path) -> None:
    sessions = SessionManager(workspace)
    session = sessions.get_or_create(SESSION_KEY)
    session.add_message("user", "Inspect the durable subagent task acceptance state.")
    session.add_message("assistant", "Durable task state is available below.")
    sessions.save(session)

    set_config_path(config)
    append_transcript_object(
        SESSION_KEY,
        {
            "event": "user",
            "chat_id": CHAT_ID,
            "text": "Inspect the durable subagent task acceptance state.",
        },
    )
    append_transcript_object(
        SESSION_KEY,
        {
            "event": "message",
            "chat_id": CHAT_ID,
            "text": "Durable task state is available below.",
        },
    )
    append_transcript_object(SESSION_KEY, {"event": "turn_end", "chat_id": CHAT_ID})


async def _seed_tasks(workspace: Path) -> None:
    store = SubagentTaskStore(workspace)
    await _create_running(store, "task-parallel-a", "Research protocol evidence", required=True)
    await _create_running(store, "task-parallel-b", "Inspect compatibility tests")

    await _create_running(store, "task-pending", "Result pending delivery")
    await store.transition_status("task-pending", SubagentTaskStatus.SUCCEEDED)
    await store.mark_result_ready("task-pending")

    await _create_running(store, "task-delivery-failed", "Delivery failed evidence")
    await store.transition_status("task-delivery-failed", SubagentTaskStatus.SUCCEEDED)
    await store.mark_result_ready("task-delivery-failed")
    await store.claim_result("task-delivery-failed", "run-main")
    await store.mark_delivery_failed("task-delivery-failed")

    await _create_running(store, "task-lost", "Lost executor evidence")
    await store.record_termination(
        "task-lost",
        SubagentTerminationState.TERMINATION_FAILED,
        evidence={"executor_present": False, "exit_observed": False},
    )

    for task_id, label, status in (
        ("task-timeout", "Timed out child", SubagentTaskStatus.TIMED_OUT),
        ("task-cancelled", "Cancelled child", SubagentTaskStatus.CANCELLED),
    ):
        await _create_running(store, task_id, label)
        await store.record_termination(task_id, SubagentTerminationState.CANCEL_REQUESTED)
        await store.record_termination(task_id, SubagentTerminationState.GRACE_WAITING)
        await store.record_termination(task_id, SubagentTerminationState.COOPERATIVELY_EXITED)
        await store.transition_status(task_id, status)


async def _serial(workspace: Path) -> None:
    store = SubagentTaskStore(workspace)
    await store.transition_status("task-parallel-a", SubagentTaskStatus.SUCCEEDED)
    await store.mark_result_ready("task-parallel-a")
    await store.claim_result("task-parallel-a", "run-main-continuation")
    await store.mark_delivered("task-parallel-a")
    await _create_running(
        store,
        "task-serial-c",
        "Verify delivered result then delegate",
        required=True,
    )


async def _reconnect(workspace: Path) -> None:
    await _create_running(
        SubagentTaskStore(workspace),
        "task-reconnect-d",
        "Restore after reconnect",
    )


async def _finish(workspace: Path) -> None:
    store = SubagentTaskStore(workspace)
    for task in store.list_tasks():
        if task.owner_session_key != SESSION_KEY or str(task.status) != "running":
            continue
        await store.update_runtime(task.task_id, phase=SubagentExecutionPhase.FINAL_RESPONSE)
        await store.transition_status(task.task_id, SubagentTaskStatus.SUCCEEDED)
        await store.mark_result_ready(task.task_id)
        await store.claim_result(task.task_id, "run-main-finish")
        await store.mark_delivered(task.task_id)


async def _after_dismiss(workspace: Path) -> None:
    await _create_running(
        SubagentTaskStore(workspace),
        "task-after-dismiss-e",
        "Visible after terminal history is dismissed",
    )


def _write_config(
    config: Path,
    workspace: Path,
    *,
    websocket_port: int,
    gateway_port: int,
    secret: str,
) -> None:
    payload = {
        "agents": {
            "defaults": {
                "workspace": str(workspace),
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
                "port": websocket_port,
                "allowFrom": ["*"],
                "tokenIssueSecret": secret,
            }
        },
        "gateway": {
            "host": "127.0.0.1",
            "port": gateway_port,
            "heartbeat": {"enabled": False},
        },
    }
    config.write_text(json.dumps(payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        choices=("initial", "seed", "serial", "reconnect", "finish", "after-dismiss"),
        required=True,
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--websocket-port", type=int)
    parser.add_argument("--gateway-port", type=int)
    parser.add_argument("--secret")
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)

    if args.action == "initial":
        if not all((args.config, args.websocket_port, args.gateway_port, args.secret)):
            parser.error("initial requires config, websocket-port, gateway-port, and secret")
        _write_config(
            args.config,
            args.workspace,
            websocket_port=args.websocket_port,
            gateway_port=args.gateway_port,
            secret=args.secret,
        )
        asyncio.run(_initial(args.workspace, args.config))
    elif args.action == "seed":
        asyncio.run(_seed_tasks(args.workspace))
    elif args.action == "serial":
        asyncio.run(_serial(args.workspace))
    elif args.action == "reconnect":
        asyncio.run(_reconnect(args.workspace))
    elif args.action == "finish":
        asyncio.run(_finish(args.workspace))
    else:
        asyncio.run(_after_dismiss(args.workspace))

    print(json.dumps({"action": args.action, "chat_id": CHAT_ID, "session_key": SESSION_KEY}))


if __name__ == "__main__":
    main()
