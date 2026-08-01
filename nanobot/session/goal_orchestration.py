"""Durable required-subagent state stored with an active sustained goal."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from nanobot.session.goal_state import GOAL_STATE_KEY, goal_state_raw, parse_goal_state

ORCHESTRATION_SCHEMA_VERSION = 2
DEFAULT_JOIN_DEADLINE_SECONDS = 300
TERMINAL_TASK_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "lost"}
)
MAX_TASK_ERROR_CHARS = 500
TERMINATION_STATES = frozenset({
    "none",
    "cancel_requested",
    "grace_waiting",
    "cooperatively_exited",
    "force_kill_requested",
    "force_killed",
    "termination_failed",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def deadline_remaining_seconds(deadline_at: str | None, *, now: datetime | None = None) -> float | None:
    """Return durable UTC deadline remaining time without granting a new budget."""
    if not deadline_at:
        return None
    try:
        value = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (value - current).total_seconds())


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
                    datetime.now(timezone.utc)
                    + timedelta(seconds=DEFAULT_JOIN_DEADLINE_SECONDS)
                ).isoformat().replace("+00:00", "Z"),
                "cancel_requested_at": None,
                "grace_deadline_at": None,
                "deadline_expired": False,
                "termination_state": "none",
                "termination_evidence": None,
                "ended_at": None,
                "error": None,
                "result": {
                    "available": False,
                    "claimed_at": None,
                    "claim_owner_run_id": None,
                    "delivery_phase": "unclaimed",
                },
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
            termination_state = str(record.get("termination_state") or "none")
            if status in {"cancelled", "timed_out"} and termination_state not in {
                "none", "cooperatively_exited", "force_killed"
            }:
                status_to_store = "lost"
                record["termination_state"] = "termination_failed"
            else:
                status_to_store = status
            record["status"] = status_to_store
            record["ended_at"] = _now()
            record["error"] = (error or "").strip()[:MAX_TASK_ERROR_CHARS] or None
            if status_to_store in TERMINAL_TASK_STATUSES:
                result = record.setdefault("result", {})
                result["available"] = True
                result.setdefault("delivery_phase", "unclaimed")

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
                        record["termination_state"] = "termination_failed"
                        record["deadline_expired"] = deadline_remaining_seconds(record.get("deadline_at")) == 0
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
            selected = {
                task_id: deepcopy(record)
                for task_id, record in orchestration["tasks"].items()
                if isinstance(record, dict)
                and record.get("required") is True
                and record.get("owner_run_id") == owner_run_id
            }
            pending = list(selected)
            while pending:
                replacement = orchestration["tasks"].get(pending.pop())
                replacement_id = (
                    replacement.get("resolved_by_task_id")
                    if isinstance(replacement, dict)
                    else None
                )
                if not isinstance(replacement_id, str) or replacement_id in selected:
                    continue
                replacement_record = orchestration["tasks"].get(replacement_id)
                if isinstance(replacement_record, dict):
                    selected[replacement_id] = deepcopy(replacement_record)
                    pending.append(replacement_id)
            return selected

        return await self._mutate(session_key, select_records)

    async def claim_result(self, session_key: str, task_id: str) -> bool | None:
        """Atomically claim one child result; ``None`` means legacy/background task."""

        def claim(_goal: dict[str, Any], orchestration: dict[str, Any]) -> bool | None:
            record = orchestration["tasks"].get(task_id)
            if not isinstance(record, dict):
                return None
            claims = orchestration.setdefault("result_claims", {})
            existing = claims.get(task_id)
            if existing is not None:
                return False
            claims[task_id] = {
                "claimed_at": _now(),
                "claim_owner_run_id": record.get("owner_run_id"),
                "delivery_phase": "claimed_pending_delivery",
            }
            result = record.setdefault("result", {})
            result.update(claims[task_id])
            return True

        try:
            return await self._mutate(session_key, claim)
        except ValueError:
            return None

    async def mark_termination(
        self,
        session_key: str,
        task_id: str,
        termination_state: str,
        *,
        evidence: dict[str, Any] | None = None,
        grace_seconds: float | None = None,
    ) -> None:
        if termination_state not in TERMINATION_STATES:
            raise ValueError(f"invalid termination state: {termination_state}")

        def update(_goal: dict[str, Any], orchestration: dict[str, Any]) -> None:
            record = orchestration["tasks"].get(task_id)
            if not isinstance(record, dict) or record.get("status") != "running":
                return
            now = _now()
            record["termination_state"] = termination_state
            record["termination_evidence"] = deepcopy(evidence) if evidence else None
            if termination_state == "cancel_requested":
                record["cancel_requested_at"] = now
                if grace_seconds is not None:
                    record["grace_deadline_at"] = (
                        datetime.now(timezone.utc) + timedelta(seconds=max(0.0, grace_seconds))
                    ).isoformat().replace("+00:00", "Z")
            if termination_state in {"cooperatively_exited", "force_killed"}:
                record["ended_at"] = record.get("ended_at") or now
            if termination_state == "termination_failed":
                record["status"] = "lost"
                record["ended_at"] = record.get("ended_at") or now
                record["error"] = "child termination could not be confirmed"

        await self._mutate(session_key, update)

    async def mark_delivery(self, session_key: str, task_id: str, phase: str) -> None:
        if phase not in {"claimed_pending_delivery", "delivered"}:
            raise ValueError("invalid result delivery phase")

        def update(_goal: dict[str, Any], orchestration: dict[str, Any]) -> None:
            record = orchestration["tasks"].get(task_id)
            if not isinstance(record, dict):
                return
            result = record.setdefault("result", {})
            result["delivery_phase"] = phase
            claim = orchestration.setdefault("result_claims", {}).get(task_id)
            if isinstance(claim, dict):
                claim["delivery_phase"] = phase

        await self._mutate(session_key, update)

    async def recover_runtime(self, running_task_ids: set[str] | None = None) -> int:
        """Fail closed for durable running tasks without a provable live executor."""
        active = running_task_ids or set()
        recovered = 0
        for info in self._sessions.list_sessions():
            session_key = info.get("key") if isinstance(info, dict) else None
            if not isinstance(session_key, str) or not session_key:
                continue

            def recover(_goal: dict[str, Any], orchestration: dict[str, Any]) -> int:
                changed = 0
                for task_id, record in orchestration["tasks"].items():
                    if (
                        not isinstance(record, dict)
                        or record.get("status") != "running"
                        or task_id in active
                    ):
                        continue
                    record["status"] = "lost"
                    record["termination_state"] = "termination_failed"
                    executor = record.get("executor")
                    backend = executor.get("backend") if isinstance(executor, dict) else None
                    record["termination_evidence"] = {
                        "backend": str(backend or "asyncio"),
                        "exit_observed": False,
                        "executor_present": False,
                    }
                    record["deadline_expired"] = (
                        deadline_remaining_seconds(record.get("deadline_at")) == 0
                    )
                    record["ended_at"] = _now()
                    record["error"] = "subagent executor is absent after runtime recovery"
                    changed += 1
                return changed

            try:
                recovered += await self._mutate(session_key, recover)
            except ValueError:
                continue
        return recovered
