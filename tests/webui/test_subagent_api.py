"""Authenticated, payload-closed subagent task query routes."""

from __future__ import annotations

import json

from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.session.subagent_tasks import SubagentTaskStatus, SubagentTaskStore, TaskSpec
from nanobot.webui.subagent_api import WebUISubagentRouter


def _request(path: str, *, token: bool = True) -> Request:
    headers = Headers()
    if token:
        headers["Authorization"] = "Bearer valid"
    return Request(path, headers)


def _router(store: SubagentTaskStore) -> WebUISubagentRouter:
    return WebUISubagentRouter(
        task_store=store,
        check_api_token=lambda request: request.headers.get("Authorization") == "Bearer valid",
        logger=None,
    )


async def _task(store: SubagentTaskStore) -> None:
    await store.create(
        task_id="task-a",
        owner_session_key="websocket:chat-a",
        trace_id="trace-a",
        turn_id="turn-a",
        owner_run_id="owner-a",
        child_run_id="child-a",
        spawn_tool_call_id="spawn-a",
        task_spec=TaskSpec(objective="safe", context="must-not-leak"),
        budget={"max_tokens": 100, "internal_reservation": "must-not-leak"},
    )
    await store.transition_status("task-a", SubagentTaskStatus.QUEUED)


async def test_subagent_routes_require_bearer_token(tmp_path) -> None:
    router = _router(SubagentTaskStore(tmp_path))
    for path in (
        "/api/subagents/tasks",
        "/api/subagents/snapshot?session_key=websocket:chat-a",
        "/api/subagents/tasks/task-a",
        "/api/subagents/tasks/task-a/timeline",
    ):
        response = await router.dispatch(_request(path, token=False), path.split("?")[0])
        assert response is not None and response.status_code == 401


async def test_list_and_snapshot_are_bounded_payload_closed_projections(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _task(store)
    router = _router(store)

    path = "/api/subagents/tasks?session_key=websocket:chat-a&limit=10"
    response = await router.dispatch(_request(path), "/api/subagents/tasks")
    assert response is not None and response.status_code == 200
    body = json.loads(response.body)
    assert body["total"] == 1
    assert body["items"][0]["task_id"] == "task-a"
    serialized = json.dumps(body)
    assert "must-not-leak" not in serialized
    assert "owner_session_key" not in serialized
    assert "executor" not in serialized

    snapshot_path = "/api/subagents/snapshot?session_key=websocket:chat-a"
    snapshot = await router.dispatch(_request(snapshot_path), "/api/subagents/snapshot")
    assert snapshot is not None
    snapshot_body = json.loads(snapshot.body)
    assert snapshot_body["schema_version"] == 1
    assert snapshot_body["max_revision"] == 2


async def test_detail_and_timeline_expose_ids_without_task_payload(tmp_path) -> None:
    store = SubagentTaskStore(tmp_path)
    await _task(store)
    router = _router(store)

    detail = await router.dispatch(
        _request("/api/subagents/tasks/task-a"),
        "/api/subagents/tasks/task-a",
    )
    assert detail is not None and detail.status_code == 200
    body = json.loads(detail.body)
    assert body["trace_id"] == "trace-a"
    assert body["spawn_tool_call_id"] == "spawn-a"
    assert body["timeline"][-1]["revision"] == 2
    assert "task_spec" not in body
    assert "task_result" not in body
    assert "must-not-leak" not in json.dumps(body)

    timeline = await router.dispatch(
        _request("/api/subagents/tasks/task-a/timeline"),
        "/api/subagents/tasks/task-a/timeline",
    )
    assert timeline is not None
    assert [item["revision"] for item in json.loads(timeline.body)["items"]] == [1, 2]
