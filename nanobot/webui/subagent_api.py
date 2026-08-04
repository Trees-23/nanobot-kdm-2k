"""Authenticated, read-only projections of durable subagent tasks."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.session.subagent_tasks import (
    SubagentTaskDetailDTO,
    SubagentTaskDTO,
    SubagentTaskStatus,
    SubagentTaskStore,
    SubagentTimelineEventDTO,
)
from nanobot.webui.http_utils import http_json_response, parse_query, query_first

_TASK_ID = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


def _error(status: int, code: str, message: str) -> Response:
    return http_json_response(
        {"error": {"code": code, "message": message, "retryable": False}},
        status=status,
    )


class WebUISubagentRouter:
    def __init__(
        self,
        *,
        task_store: SubagentTaskStore,
        check_api_token: Callable[[WsRequest], bool],
        logger: Any,
    ) -> None:
        self.task_store = task_store
        self.check_api_token = check_api_token
        self.logger = logger

    async def dispatch(self, request: WsRequest, got: str) -> Response | None:
        if not got.startswith("/api/subagents"):
            return None
        if not self.check_api_token(request):
            return _error(401, "unauthorized", "A valid API bearer token is required.")
        if getattr(request, "method", "GET") != "GET":
            return _error(405, "method_not_allowed", "Subagent routes are read-only.")
        if got == "/api/subagents/tasks":
            return await self._list_tasks(request)
        if got == "/api/subagents/snapshot":
            return await self._snapshot(request)
        timeline = re.fullmatch(r"/api/subagents/tasks/([^/]+)/timeline", got)
        if timeline:
            return await self._timeline(timeline.group(1))
        detail = re.fullmatch(r"/api/subagents/tasks/([^/]+)", got)
        if detail:
            return await self._detail(detail.group(1))
        return _error(404, "subagent_route_not_found", "Subagent route not found.")

    async def _list_tasks(self, request: WsRequest) -> Response:
        query = parse_query(request.path)
        session_key = query_first(query, "session_key")
        status = query_first(query, "status")
        try:
            limit = int(query_first(query, "limit") or "100")
        except ValueError:
            return _error(400, "invalid_subagent_filter", "Task limit is invalid.")
        if not 1 <= limit <= 200:
            return _error(400, "invalid_subagent_filter", "Task limit must be between 1 and 200.")
        if status is not None:
            try:
                status = SubagentTaskStatus(status).value
            except ValueError:
                return _error(400, "invalid_subagent_filter", "Task status is invalid.")
        tasks = await asyncio.to_thread(self.task_store.list_tasks)
        filtered = [
            task
            for task in tasks
            if (session_key is None or task.owner_session_key == session_key)
            and (status is None or task.status == status)
        ]
        filtered.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
        return http_json_response({
            "schema_version": 1,
            "items": [
                SubagentTaskDTO.from_task(task).model_dump(mode="json")
                for task in filtered[:limit]
            ],
            "total": len(filtered),
        })

    async def _snapshot(self, request: WsRequest) -> Response:
        session_key = query_first(parse_query(request.path), "session_key")
        if not session_key or len(session_key) > 512:
            return _error(400, "invalid_session_key", "A bounded session_key is required.")
        snapshot = await asyncio.to_thread(self.task_store.snapshot, session_key)
        return http_json_response(snapshot.model_dump(mode="json"))

    async def _detail(self, task_id: str) -> Response:
        if _TASK_ID.fullmatch(task_id) is None:
            return _error(400, "invalid_task_id", "Task ID is invalid.")
        task = await asyncio.to_thread(self.task_store.load, task_id)
        if task is None:
            return _error(404, "subagent_task_not_found", "Subagent task was not found.")
        return http_json_response(SubagentTaskDetailDTO.from_task(task).model_dump(mode="json"))

    async def _timeline(self, task_id: str) -> Response:
        if _TASK_ID.fullmatch(task_id) is None:
            return _error(400, "invalid_task_id", "Task ID is invalid.")
        task = await asyncio.to_thread(self.task_store.load, task_id)
        if task is None:
            return _error(404, "subagent_task_not_found", "Subagent task was not found.")
        return http_json_response({
            "schema_version": 1,
            "task_id": task.task_id,
            "items": [
                SubagentTimelineEventDTO.from_event(event).model_dump(mode="json")
                for event in task.lifecycle_outbox
            ],
        })
