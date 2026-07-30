from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.audit.context import AuditRunContext
from nanobot.audit.runtime import AuditRuntime
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.command.builtin import cmd_stop
from nanobot.command.router import CommandContext
from nanobot.providers.base import GenerationSettings
from tests.agent.test_loop_audit import RecordingEmitter


async def test_stop_records_targets_before_cancelling(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "model"
    provider.generation = GenerationSettings()
    runtime = AuditRuntime.disabled()
    emitter = RecordingEmitter()
    runtime.emitter = emitter
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        audit_runtime=runtime,
    )
    target = AuditRunContext("trace", "turn", "run")
    loop._active_audit_runs["cli:direct"] = {target.run_id: target}

    async def cancel(_key):
        assert "cancel_requested" in emitter.event_types
        return 1

    loop._cancel_active_tasks = AsyncMock(side_effect=cancel)
    msg = InboundMessage("cli", "user", "direct", "/stop")
    result = await cmd_stop(
        CommandContext(msg=msg, session=None, key="cli:direct", raw="/stop", loop=loop)
    )

    cancelled = next(event for event in emitter.events if event.event_type == "cancel_requested")
    assert cancelled.trace_id == "trace"
    assert cancelled.target_run_ids == ["run"]
    assert emitter.event_types[-2:] == ["turn_response_prepared", "turn_finished"]
    assert result.metadata["_audit_context"]["trace_id"] == "trace"
