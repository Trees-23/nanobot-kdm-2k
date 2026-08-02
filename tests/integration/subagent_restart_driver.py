"""Separate-process driver for durable subagent restart scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nanobot.session.goal_orchestration import GoalOrchestrationStore
from nanobot.session.goal_state import GOAL_STATE_KEY
from nanobot.session.manager import SessionManager

SESSION_KEY = "test:restart-suite"
TASK_ID = "restart-child"


def _active(sessions: SessionManager) -> None:
    session = sessions.get_or_create(SESSION_KEY)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "restart suite",
    }
    sessions.save(session, fsync=True)


def _record(sessions: SessionManager) -> dict[str, Any] | None:
    session = sessions.get_or_create(SESSION_KEY)
    goal = session.metadata.get(GOAL_STATE_KEY)
    if not isinstance(goal, dict):
        return None
    orchestration = goal.get("orchestration")
    if not isinstance(orchestration, dict):
        return None
    record = orchestration.get("tasks", {}).get(TASK_ID)
    return record if isinstance(record, dict) else None


async def _seed(sessions: SessionManager, mode: str) -> dict[str, Any]:
    _active(sessions)
    store = GoalOrchestrationStore(sessions)
    await store.register(
        SESSION_KEY,
        task_id=TASK_ID,
        label="restart-child",
        group="restart",
        child_run_id="child-run",
        spawn_tool_call_id="spawn-call",
        owner_run_id="owner-run",
    )
    if mode in {"finished", "claimed"}:
        await store.finish(SESSION_KEY, TASK_ID, "succeeded")
    if mode == "claimed":
        await store.claim_result(SESSION_KEY, TASK_ID)
    if mode == "expired":
        session = sessions.get_or_create(SESSION_KEY)
        record = _record(sessions)
        assert record is not None
        record["deadline_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        sessions.save(session, fsync=True)
    record = _record(sessions)
    assert record is not None
    return record


async def _recover(sessions: SessionManager) -> dict[str, Any]:
    store = GoalOrchestrationStore(sessions)
    first = await store.recover_runtime(set())
    second = await store.recover_runtime(set())
    return {"first": first, "second": second, "record": _record(sessions)}


async def _claim(sessions: SessionManager, *, deliver: bool) -> dict[str, Any]:
    store = GoalOrchestrationStore(sessions)
    first = await store.claim_result(SESSION_KEY, TASK_ID)
    second = await store.claim_result(SESSION_KEY, TASK_ID)
    if deliver:
        await store.mark_delivery(SESSION_KEY, TASK_ID, "delivered")
    return {"first": first, "second": second, "record": _record(sessions)}


async def _corrupt_goal(sessions: SessionManager) -> dict[str, Any]:
    session = sessions.get_or_create(SESSION_KEY)
    session.metadata[GOAL_STATE_KEY] = "corrupt-goal-state"
    sessions.save(session, fsync=True)
    before = session.metadata[GOAL_STATE_KEY]
    changed = await GoalOrchestrationStore(sessions).recover_runtime(set())
    after = sessions.get_or_create(SESSION_KEY).metadata[GOAL_STATE_KEY]
    return {"changed": changed, "preserved": before == after, "value": after}


async def _run(root: Path, action: str) -> dict[str, Any]:
    sessions = SessionManager(root)
    if action.startswith("seed-"):
        return await _seed(sessions, action.removeprefix("seed-"))
    if action == "recover":
        return await _recover(sessions)
    if action == "claim":
        return await _claim(sessions, deliver=False)
    if action == "deliver":
        return await _claim(sessions, deliver=True)
    if action == "corrupt-goal":
        return await _corrupt_goal(sessions)
    raise ValueError(f"unknown action: {action}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("action")
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(args.root, args.action))
    print(json.dumps(result, separators=(",", ":")), flush=True)
    if args.hold:
        while True:
            signal.pause()


if __name__ == "__main__":
    main()
