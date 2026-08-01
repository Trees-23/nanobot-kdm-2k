"""Durable required-subagent state stored with an active sustained goal."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable

from nanobot.session.goal_state import GOAL_STATE_KEY, goal_state_raw, parse_goal_state

ORCHESTRATION_SCHEMA_VERSION = 2
DEFAULT_JOIN_DEADLINE_SECONDS = 300
TERMINAL_TASK_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "lost"}
)
MAX_TASK_ERROR_CHARS = 500


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def orchestration_snapshot(goal: dict[str, Any]) -> dict[str, Any]:
    value = goal.get("orchestration")
    if not isinstance(value, dict):
        return {"schema_version": ORCHESTRATION_SCHEMA_VERSION, "phase": "running", "groups": {}, "tasks": {}}
    value.setdefault("schema_version", ORCHESTRATION_SCHEMA_VERSION)
    value.setdefault("phase", "running")
    value.setdefault("groups", {})
    value.setdefault("tasks", {})
    return value


def obligation_status(tasks: dict[str, Any], task_id: str) -> tuple[bool, str, list[str]]:
    """Return whether an obligation resolves to success, preserving its evidence chain."""
    chain: list[str] = []
    seen: set[str] = set()
    current = task_id
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        record = tasks.get(current)
        if not isinstance(record, dict):
            return False, "lost", chain
        status = str(record.get("status") or "lost")
        replacement = record.get("resolved_by_task_id")
        if status == "succeeded":
            return True, status, chain
        if isinstance(replacement, str) and replacement:
            current = replacement
            continue
        return False, status, chain
    return False, "lost", chain


def required_gate(goal: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    orchestration = orchestration_snapshot(goal)
    tasks = orchestration["tasks"]
    unresolved: list[dict[str, Any]] = []
    for task_id, record in tasks.items():
        if not isinstance(record, dict) or record.get("required") is not True:
            continue
        # A replacement is itself required, but only the root obligation is reported.
        if any(
            isinstance(other, dict) and other.get("resolved_by_task_id") == task_id
            for other in tasks.values()
        ):
            continue
        satisfied, status, chain = obligation_status(tasks, task_id)
        if not satisfied:
            unresolved.append({"task_id": task_id, "status": status, "chain": chain})
    return not unresolved, unresolved


class GoalOrchestrationStore:
    """Serialize durable orchestration mutations through the Session save boundary."""

    def __init__(self, sessions: Any) -> None:
        self._sessions = sessions
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def _mutate(
        self,
        session_key: str,
        mutation: Callable[[dict[str, Any], dict[str, Any]], Any],
    ) -> Any:
        async with self._lock(session_key):
            session = self._sessions.get_or_create(session_key)
            prior_metadata = deepcopy(session.metadata)
            goal = parse_goal_state(goal_state_raw(session.metadata))
            if not isinstance(goal, dict) or goal.get("status") != "active":
                raise ValueError("required subagents need an active goal in the current session")
            goal = deepcopy(goal)
            orchestration = orchestration_snapshot(goal)
            result = mutation(goal, orchestration)
            goal["orchestration"] = orchestration
            session.metadata[GOAL_STATE_KEY] = goal
            try:
                self._sessions.save(session)
            except BaseException:
                session.metadata.clear()
                session.metadata.update(prior_metadata)
                raise
            return result

    async def register(
        self,
        session_key: str,
        *,
        task_id: str,
        label: str,
        group: str,
        child_run_id: str | None,
        spawn_tool_call_id: str | None,
        owner_run_id: str | None = None,
        replaces_task_id: str | None = None,
    ) -> dict[str, Any]:
        def add(_goal: dict[str, Any], orchestration: dict[str, Any]) -> dict[str, Any]:
            tasks = orchestration["tasks"]
            if task_id in tasks:
                raise ValueError(f"task {task_id} is already registered")
            attempt = 1
            if replaces_task_id:
                old = tasks.get(replaces_task_id)
                if not isinstance(old, dict):
                    raise ValueError("replacement task is not owned by the current goal")
                if old.get("status") not in TERMINAL_TASK_STATUSES - {"succeeded"}:
                    raise ValueError("only a failed, cancelled, timed-out, or lost task can be replaced")
                if old.get("resolved_by_task_id"):
                    raise ValueError("task already has a replacement")
                old["resolved_by_task_id"] = task_id
                attempt = int(old.get("attempt") or 1) + 1
            record = {
                "label": label,
                "status": "running",
                "required": True,
                "group": group,
                "child_run_id": child_run_id,
                "spawn_tool_call_id": spawn_tool_call_id,
                "owner_run_id": owner_run_id,
                "attempt": attempt,
                "resolved_by_task_id": None,
                "started_at": _now(),
                "deadline_at": (
                    datetime.now().astimezone()
                    + timedelta(seconds=DEFAULT_JOIN_DEADLINE_SECONDS)
                ).isoformat(),
                "ended_at": None,
                "error": None,
            }
            tasks[task_id] = record
            group_record = orchestration["groups"].setdefault(group, {"required_task_ids": []})
            group_record.setdefault("required_task_ids", []).append(task_id)
            orchestration["phase"] = "running"
            return deepcopy(record)

        return await self._mutate(session_key, add)

    async def remove_registration(self, session_key: str, task_id: str) -> None:
        def remove(_goal: dict[str, Any], orchestration: dict[str, Any]) -> None:
            record = orchestration["tasks"].pop(task_id, None)
            if not isinstance(record, dict):
                return
            for group in orchestration["groups"].values():
                ids = group.get("required_task_ids", []) if isinstance(group, dict) else []
                if task_id in ids:
                    ids.remove(task_id)
            for other in orchestration["tasks"].values():
                if isinstance(other, dict) and other.get("resolved_by_task_id") == task_id:
                    other["resolved_by_task_id"] = None

        await self._mutate(session_key, remove)

    async def finish(
        self,
        session_key: str,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError(f"invalid terminal task status: {status}")

        def finish_one(_goal: dict[str, Any], orchestration: dict[str, Any]) -> None:
            record = orchestration["tasks"].get(task_id)
            if not isinstance(record, dict) or record.get("status") != "running":
                return
            record["status"] = status
            record["ended_at"] = _now()
            record["error"] = (error or "").strip()[:MAX_TASK_ERROR_CHARS] or None

        await self._mutate(session_key, finish_one)

    async def set_phase(self, session_key: str, phase: str) -> None:
        if phase not in {"running", "waiting_for_children", "ready", "failed"}:
            raise ValueError(f"invalid orchestration phase: {phase}")

        def update(_goal: dict[str, Any], orchestration: dict[str, Any]) -> None:
            orchestration["phase"] = phase

        await self._mutate(session_key, update)

    async def select(
        self,
        session_key: str,
        *,
        task_ids: list[str] | None = None,
        task_group: str | None = None,
        running_task_ids: set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        def select_records(_goal: dict[str, Any], orchestration: dict[str, Any]) -> dict[str, dict[str, Any]]:
            tasks = orchestration["tasks"]
            if running_task_ids is not None:
                for tid, record in tasks.items():
                    if isinstance(record, dict) and record.get("status") == "running" and tid not in running_task_ids:
                        record["status"] = "lost"
                        record["ended_at"] = _now()
                        record["error"] = "subagent process is absent after runtime recovery"
            if task_group is not None:
                group = orchestration["groups"].get(task_group)
                if not isinstance(group, dict):
                    raise ValueError("task group is not owned by the current goal")
                selected = list(group.get("required_task_ids") or [])
            else:
                selected = list(task_ids or [])
            if not selected:
                raise ValueError("no tasks selected")
            unknown = [tid for tid in selected if tid not in tasks]
            if unknown:
                raise ValueError(f"tasks are not owned by the current goal: {', '.join(unknown)}")
            index = 0
            while index < len(selected):
                replacement = tasks[selected[index]].get("resolved_by_task_id")
                if isinstance(replacement, str) and replacement and replacement not in selected:
                    selected.append(replacement)
                index += 1
            return {tid: deepcopy(tasks[tid]) for tid in selected}

        return await self._mutate(session_key, select_records)

    async def select_owner(
        self,
        session_key: str,
        owner_run_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return required obligations created by one Run under the session lock."""

        def select_records(
            _goal: dict[str, Any], orchestration: dict[str, Any]
        ) -> dict[str, dict[str, Any]]:
            return {
                task_id: deepcopy(record)
                for task_id, record in orchestration["tasks"].items()
                if isinstance(record, dict)
                and record.get("required") is True
                and record.get("owner_run_id") == owner_run_id
            }

        return await self._mutate(session_key, select_records)
