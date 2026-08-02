"""Generate a tool-recovery trace through the real Runner, Tool, and Audit runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.filesystem import ReadFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.audit.context import AuditRunContext
from nanobot.audit.index import AuditIndexer
from nanobot.audit.read_service import AuditReadService
from nanobot.audit.runtime import AuditRuntime
from nanobot.config.schema import AuditConfig
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.utils.llm_runtime import LLMRuntime

TRACE_ID = "trace-runtime-tool-recovery-rail-20260803"
TURN_ID = "turn-runtime-tool-recovery-rail-20260803"
RUN_ID = "run-runtime-tool-recovery-rail-20260803"
SESSION_KEY = "websocket:runtime-tool-recovery-rail-20260803"


class _ScriptedTool(Tool):
    def __init__(self, name: str, results: list[str]) -> None:
        self._name = name
        self._results = results

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Deterministic audit acceptance tool."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    async def execute(self, **_kwargs: Any) -> str:
        if not self._results:
            raise RuntimeError(f"unexpected {self.name} invocation")
        return self._results.pop(0)


class RecoveryProvider(LLMProvider):
    """Drive recovery, retry, and continuation through the real Runner."""

    def __init__(self) -> None:
        super().__init__()
        self._responses = [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-missing-read",
                    name="read_file",
                    arguments={"path": "recovery-target/missing/config.json"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-recovered-read",
                    name="read_file",
                    arguments={"path": "recovery-target/config.json"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-plugin-failed",
                    name="mcp_fixture",
                    arguments={"operation": "status"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-plugin-retry",
                    name="mcp_fixture",
                    arguments={"operation": "status"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-session-failed",
                    name="write_stdin",
                    arguments={"session_id": "fixture-session", "chars": "input"},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    id="provider-session-continued",
                    name="write_stdin",
                    arguments={"session_id": "fixture-session", "chars": ""},
                )],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="runtime recovery completed"),
        ]

    async def chat(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        if not self._responses:
            raise RuntimeError("recovery provider received an unexpected request")
        return self._responses.pop(0)

    async def chat_stream(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        return await self.chat(*_args, **_kwargs)

    def get_default_model(self) -> str:
        return "runtime-recovery-model"


async def _generate(root: Path, workspace: Path) -> int:
    target = workspace / "recovery-target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text('{"recovered":true}\n', encoding="utf-8")

    audit = AuditRuntime.from_config(
        AuditConfig(
            mode="metadata_only",
            path=str(root),
            fsync_interval_seconds=0.01,
            fsync_record_interval=1,
        ),
        root=root,
    )
    await audit.start()
    provider = RecoveryProvider()
    runtime = LLMRuntime.capture(
        provider,
        provider.get_default_model(),
        context_window_tokens=4096,
    )
    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=workspace, restrict_to_workspace=True))
    tools.register(_ScriptedTool(
        "mcp_fixture",
        [ToolResult.error("Error: fixture plugin unavailable"), "fixture plugin response"],
    ))
    tools.register(_ScriptedTool(
        "write_stdin",
        [
            ToolResult.error("Error: process output not ready"),
            "Process running. session_id: fixture-session",
        ],
    ))
    try:
        result = await AgentRunner(audit_emitter=audit.emitter).run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "Recover the local config read."}],
            tools=tools,
            runtime=runtime,
            max_iterations=7,
            max_tool_result_chars=10_000,
            fail_on_tool_error=False,
            workspace=workspace,
            session_key=SESSION_KEY,
            audit_context=AuditRunContext(TRACE_ID, TURN_ID, RUN_ID),
        ))
        if result.stop_reason != "completed" or result.final_content != "runtime recovery completed":
            raise RuntimeError(f"unexpected Runner result: {result.stop_reason}")
    finally:
        await audit.close()

    update = AuditIndexer(root).update()
    if not update.coverage_complete:
        raise RuntimeError("audit index coverage is incomplete")
    revision = AuditReadService(root / "state" / "audit-index.sqlite").status().revision
    return revision


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
    revision = asyncio.run(_generate(args.root, args.workspace))
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
    print(json.dumps({
        "trace_id": TRACE_ID,
        "session_key": SESSION_KEY,
        "revision": revision,
        "generator": "AgentRunner+ReadFileTool+AuditRuntime",
    }))


if __name__ == "__main__":
    main()
