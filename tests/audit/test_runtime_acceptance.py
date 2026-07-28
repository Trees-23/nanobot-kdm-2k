from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import InboundMessage
from tests.agent.test_loop_audit import make_loop


@pytest.mark.parametrize(
    ("metadata", "expected_source"),
    [
        ({}, "user"),
        (
            {
                "_cron_trigger": {
                    "job_id": "job",
                    "job_name": "nightly",
                    "run_id": "cron-run",
                }
            },
            "cron",
        ),
        (
            {
                "_local_trigger": {
                    "trigger_id": "trigger",
                    "trigger_name": "hook",
                    "delivery_id": "delivery",
                }
            },
            "local_trigger",
        ),
    ],
)
async def test_user_and_automation_sources_enter_internal_audit_hook(
    tmp_path,
    metadata,
    expected_source,
) -> None:
    loop, emitter = make_loop(tmp_path)
    await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="work",
            metadata=metadata,
        )
    )

    run_started = next(event for event in emitter.events if event.event_type == "run_started")
    run_finished = next(event for event in emitter.events if event.event_type == "run_finished")
    assert run_started.source_type == expected_source
    assert run_started.run_id == run_finished.run_id


async def test_ephemeral_sdk_source_still_has_run_evidence(tmp_path) -> None:
    loop, emitter = make_loop(tmp_path)
    loop._connect_mcp = AsyncMock()

    await loop.process_direct("work", ephemeral=True)

    run_events = [event for event in emitter.events if event.event_type.startswith("run_")]
    assert [event.event_type for event in run_events] == ["run_started", "run_finished"]
    assert all(event.source_type == "sdk" for event in run_events)


async def test_active_goal_reuses_trace_without_reusing_turn_or_run(tmp_path) -> None:
    loop, emitter = make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["goal_state"] = {
        "status": "active",
        "objective": "finish",
        "_audit_goal_id": "goal",
        "_audit_trace_id": "goal-trace",
        "_audit_goal_version": 1,
    }
    loop.sessions.save(session)

    await loop._process_message(
        InboundMessage("cli", "user", "direct", "continue")
    )

    assert emitter.events[0].event_type == "trace_linked"
    assert emitter.events[0].trace_id == "goal-trace"
    assert emitter.events[0].link_reason == "active_goal"
    assert len({event.turn_id for event in emitter.events if event.turn_id}) == 1
    assert len({event.run_id for event in emitter.events if event.run_id}) == 1
