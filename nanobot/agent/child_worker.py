"""Subagent worker entry point for the process-group Child executor."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.child_executor import IPC_SCHEMA_VERSION, MAX_IPC_FRAME_BYTES
from nanobot.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.audit.context import AuditRunContext
from nanobot.audit.runtime import AuditRuntime
from nanobot.bus.events import AUDIT_CONTEXT_META
from nanobot.config.paths import get_audit_dir
from nanobot.config.schema import Config, ModelPresetConfig, ToolsConfig
from nanobot.providers.factory import make_provider
from nanobot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template


def build_child_config_snapshot(config: Config) -> dict[str, Any]:
    """Return the minimum in-memory config needed to rehydrate a Child runtime."""
    return config.model_dump(
        mode="json",
        by_alias=True,
        include={"agents", "providers", "tools", "audit", "model_presets"},
    )


class WorkerProtocol:
    """Versioned line protocol bound to one executor identity."""

    def __init__(self, start: dict[str, Any]) -> None:
        self.start = start

    def send(self, message_type: str, **extra: Any) -> None:
        envelope = {
            "schema_version": IPC_SCHEMA_VERSION,
            "type": message_type,
            "executor_id": self.start["executor_id"],
            "process_instance_id": self.start["process_instance_id"],
            **extra,
        }
        encoded = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode()) + 1 > MAX_IPC_FRAME_BYTES:
            raise ValueError("child worker IPC frame exceeds the 1 MiB limit")
        print(encoded, flush=True)


def _runtime_from_payload(config: Config, raw: dict[str, Any]) -> LLMRuntime:
    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("child runtime model is missing")
    preset_name = raw.get("model_preset")
    base = config.resolve_preset(preset_name)
    generation = raw.get("generation") if isinstance(raw.get("generation"), dict) else {}
    preset = ModelPresetConfig(
        model=model,
        provider=base.provider,
        max_tokens=int(generation.get("max_tokens", base.max_tokens)),
        context_window_tokens=int(raw.get("context_window_tokens", base.context_window_tokens)),
        temperature=float(generation.get("temperature", base.temperature)),
        reasoning_effort=generation.get("reasoning_effort", base.reasoning_effort),
    )
    provider = make_provider(config, preset=preset)
    return LLMRuntime.capture(
        provider,
        model,
        context_window_tokens=preset.context_window_tokens,
        model_preset=preset_name if isinstance(preset_name, str) else None,
    )


def _tools_config(config: Config, *, restrict_to_workspace: bool) -> ToolsConfig:
    return ToolsConfig(
        exec=config.tools.exec,
        web=config.tools.web,
        file=config.tools.file,
        restrict_to_workspace=restrict_to_workspace,
    )


async def _run_child(payload: dict[str, Any], protocol: WorkerProtocol) -> AgentRunResult:
    config = Config.model_validate(payload["config"])
    runtime = _runtime_from_payload(config, payload["runtime"])
    workspace = Path(payload["workspace"]).expanduser().resolve(strict=False)
    access_mode = "restricted" if payload.get("restrict_to_workspace") else "full"
    workspace_scope = build_workspace_scope(workspace, access_mode)
    tools_config = _tools_config(
        config,
        restrict_to_workspace=workspace_scope.restrict_to_workspace,
    )
    exec_sessions = ExecSessionManager()
    tools = ToolRegistry()
    ToolLoader().load(
        ToolContext(
            config=tools_config,
            workspace=str(workspace),
            exec_session_manager=exec_sessions,
            file_state_store=FileStates(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=workspace_scope.restrict_to_workspace,
                workspace=workspace,
            ),
        ),
        tools,
        scope="subagent",
    )
    skills_summary = SkillsLoader(
        workspace,
        disabled_skills=payload.get("disabled_skills") or [],
    ).build_skills_summary()
    messages = [
        {
            "role": "system",
            "content": render_template(
                "agent/subagent_system.md",
                workspace=str(workspace),
                skills_summary=skills_summary or "",
            ),
        },
        {"role": "user", "content": payload["task"]},
    ]
    raw_audit = payload.get("audit_context")
    audit_context = AuditRunContext(**raw_audit) if isinstance(raw_audit, dict) else None
    raw_audit_root = payload.get("audit_root")
    audit_root = (
        Path(raw_audit_root).expanduser().resolve(strict=False)
        if isinstance(raw_audit_root, str) and raw_audit_root
        else get_audit_dir(config.audit.path)
    )
    audit_runtime = AuditRuntime.from_config(config.audit, root=audit_root)
    await audit_runtime.ensure_started()
    request_metadata: dict[str, Any] = {}
    request_metadata["subagent_depth"] = int(payload.get("child_depth") or 0)
    if audit_context is not None:
        request_metadata[AUDIT_CONTEXT_META] = {
            "trace_id": audit_context.trace_id,
            "turn_id": audit_context.turn_id,
            "run_id": audit_context.run_id,
        }
    request_token = bind_request_context(RequestContext(
        channel=payload["origin"]["channel"],
        chat_id=payload["origin"]["chat_id"],
        message_id=payload.get("origin_message_id"),
        session_key=payload["origin"].get("session_key"),
        runtime=runtime,
        metadata=request_metadata,
        workspace=workspace,
    ))
    workspace_token = bind_workspace_scope(workspace_scope)

    async def checkpoint(value: dict[str, Any]) -> None:
        protocol.send(
            "lifecycle",
            state="checkpoint",
            phase=value.get("phase"),
            iteration=value.get("iteration"),
        )

    try:
        return await AgentRunner(audit_emitter=audit_runtime.emitter).run(AgentRunSpec(
            initial_messages=messages,
            tools=tools,
            runtime=runtime,
            max_iterations=int(payload["max_iterations"]),
            max_tool_result_chars=int(payload["max_tool_result_chars"]),
            max_iterations_message="Task completed but no final response was generated.",
            finalize_on_max_iterations=False,
            error_message=None,
            fail_on_tool_error=bool(payload["fail_on_tool_error"]),
            checkpoint_callback=checkpoint,
            session_key=payload["origin"].get("session_key"),
            workspace=workspace,
            llm_timeout_s=payload.get("llm_timeout_s"),
            audit_context=audit_context,
        ))
    finally:
        reset_workspace_scope(workspace_token)
        reset_request_context(request_token)
        await exec_sessions.close_all()
        await audit_runtime.close()


def _result_payload(result: AgentRunResult) -> dict[str, Any]:
    return {
        "final_content": result.final_content,
        "tools_used": result.tools_used,
        "usage": result.usage,
        "stop_reason": result.stop_reason,
        "error": result.error,
        "error_kind": result.error_kind,
        "tool_events": result.tool_events,
        "had_injections": result.had_injections,
    }


async def _read_start(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line or len(line) > MAX_IPC_FRAME_BYTES:
        raise ValueError("invalid child worker start frame")
    start = json.loads(line)
    if not isinstance(start, dict) or start.get("schema_version") != IPC_SCHEMA_VERSION:
        raise ValueError("unsupported child worker IPC schema")
    if start.get("type") != "start" or not isinstance(start.get("payload"), dict):
        raise ValueError("invalid child worker start envelope")
    return start


async def _control_loop(
    reader: asyncio.StreamReader,
    start: dict[str, Any],
    run_task: asyncio.Task[AgentRunResult],
) -> None:
    while line := await reader.readline():
        if len(line) > MAX_IPC_FRAME_BYTES:
            continue
        try:
            command = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            command.get("schema_version") == IPC_SCHEMA_VERSION
            and command.get("executor_id") == start["executor_id"]
            and command.get("process_instance_id") == start["process_instance_id"]
            and command.get("type") == "cancel"
        ):
            run_task.cancel()
            return


async def _async_main() -> int:
    reader = asyncio.StreamReader(limit=MAX_IPC_FRAME_BYTES + 1)
    def protocol_factory() -> asyncio.StreamReaderProtocol:
        return asyncio.StreamReaderProtocol(reader)

    await asyncio.get_running_loop().connect_read_pipe(protocol_factory, sys.stdin.buffer)
    start = await _read_start(reader)
    protocol = WorkerProtocol(start)
    protocol.send("lifecycle", state="started")
    run_task = asyncio.create_task(_run_child(start["payload"], protocol))
    control_task = asyncio.create_task(_control_loop(reader, start, run_task))
    try:
        result = await run_task
    except asyncio.CancelledError:
        protocol.send("result", result={"stop_reason": "cancelled"})
        return 0
    except Exception:
        protocol.send("result", result={
            "stop_reason": "error",
            "error": "child worker execution failed",
            "error_kind": "worker_error",
        })
        return 1
    finally:
        control_task.cancel()
        await asyncio.gather(control_task, return_exceptions=True)
    protocol.send("result", result=_result_payload(result))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
