import json
from datetime import UTC, datetime

from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.audit.read_service import (
    EventPage,
    IndexedTraceEvents,
    IndexStatus,
    PayloadReadResult,
    SessionListItem,
    SessionListPage,
    TraceListItem,
    TraceListPage,
)
from nanobot.webui.audit_api import WebUIAuditRouter
from tests.audit.test_graph_builder import _retry_trace


class _ReadService:
    def __init__(self) -> None:
        self.index = IndexStatus(state="ready", revision=7, coverage_complete=True)
        self.detail = IndexedTraceEvents(
            trace_id="trace-1",
            revision=7,
            events=_retry_trace(),
            integrity_status="valid",
            integrity_error_codes=[],
            integrity_warning_codes=[],
            active_run_ids=set(),
        )

    def status(self):
        return self.index

    def list_traces(self, _filters):
        return TraceListPage(
            items=[
                TraceListItem(
                    trace_id="trace-1",
                    title="websocket / trace-1",
                    source_types=["websocket"],
                    primary_source_type="websocket",
                    first_seen=datetime(2026, 1, 1, tzinfo=UTC),
                    last_seen=datetime(2026, 1, 1, 0, 0, 9, tzinfo=UTC),
                    display_status="warning",
                    turn_count=1,
                    run_count=1,
                    anomaly_count=1,
                    integrity_status="valid",
                    active=False,
                    session_key="websocket:chat-1",
                    event_count=9,
                )
            ],
            next_cursor=None,
            index=self.index,
        )

    def list_sessions(self, _filters):
        return SessionListPage(
            items=[
                SessionListItem(
                    session_key="websocket:chat-1",
                    title="websocket:chat-1",
                    source_types=["websocket"],
                    first_seen=datetime(2026, 1, 1, tzinfo=UTC),
                    last_seen=datetime(2026, 1, 1, 0, 0, 9, tzinfo=UTC),
                    trace_count=1,
                    active_trace_count=0,
                    warning_count=1,
                    error_count=0,
                    integrity_status="valid",
                    latest_trace_id="trace-1",
                )
            ],
            next_cursor=None,
            index=self.index,
        )

    def load_trace_events(self, trace_id):
        if trace_id != "trace-1":
            raise KeyError(trace_id)
        return self.detail

    def list_trace_events(self, trace_id, *, cursor=None, limit=200):
        del cursor, limit
        if trace_id != "trace-1":
            raise KeyError(trace_id)
        return EventPage(events=self.detail.events, next_cursor=None, revision=7, total=9)

    def load_payload(self, payload_id, **_kwargs):
        if payload_id != "payload-1":
            raise KeyError(payload_id)
        return PayloadReadResult(
            payload_id=payload_id,
            event_id="e1",
            payload_kind="tool_output",
            available=True,
            content={"token": "[REDACTED:CREDENTIAL]", "result": "safe"},
        )


def _request(path: str, *, token: bool = True, etag: str | None = None) -> Request:
    headers = Headers()
    if token:
        headers["Authorization"] = "Bearer valid"
    if etag:
        headers["If-None-Match"] = etag
    return Request(path, headers)


def _router(tmp_path, *, audit_mode="full"):
    return WebUIAuditRouter(
        read_service=_ReadService(),
        audit_root=tmp_path,
        audit_mode=audit_mode,
        check_api_token=lambda request: request.headers.get("Authorization") == "Bearer valid",
        logger=None,
        resolve_session_title=lambda key: "真实会话标题" if key == "websocket:chat-1" else None,
    )


async def test_all_audit_routes_require_bearer_token(tmp_path) -> None:
    router = _router(tmp_path)
    for path in (
        "/api/audit/traces",
        "/api/audit/sessions",
        "/api/audit/traces/trace-1/graph?level=trace",
        "/api/audit/traces/trace-1/events",
        "/api/audit/payloads/payload-1",
    ):
        response = await router.dispatch(_request(path, token=False), path.split("?")[0])
        assert response is not None
        assert response.status_code == 401
        assert json.loads(response.body)["error"]["code"] == "unauthorized"


async def test_session_list_uses_complete_backend_aggregate_and_title_resolver(tmp_path) -> None:
    router = _router(tmp_path)
    response = await router.dispatch(_request("/api/audit/sessions"), "/api/audit/sessions")
    assert response is not None
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["items"][0]["title"] == "真实会话标题"
    assert body["items"][0]["trace_count"] == 1


async def test_audit_capture_mode_is_additive_on_index_responses(tmp_path) -> None:
    router = _router(tmp_path, audit_mode="metadata_only")
    paths = (
        "/api/audit/traces",
        "/api/audit/sessions",
        "/api/audit/traces/trace-1/graph?level=trace_full",
        "/api/audit/traces/trace-1/events",
    )

    for path in paths:
        response = await router.dispatch(_request(path), path.split("?")[0])
        assert response is not None and response.status_code == 200
        body = json.loads(response.body)
        assert body["index"]["audit_mode"] == "metadata_only"
        assert "content" not in body


async def test_graph_has_strong_etag_and_no_payload_content(tmp_path) -> None:
    router = _router(tmp_path)
    path = "/api/audit/traces/trace-1/graph?level=run&run_id=run-1"
    first = await router.dispatch(_request(path), path.split("?")[0])
    assert first is not None
    assert first.status_code == 200
    assert first.headers["ETag"].startswith('"')
    assert b"payload content" not in first.body

    cached = await router.dispatch(
        _request(path, etag=first.headers["ETag"]), path.split("?")[0]
    )
    assert cached is not None
    assert cached.status_code == 304
    assert cached.body == b""


async def test_trace_full_graph_is_additive_and_payload_free(tmp_path) -> None:
    router = _router(tmp_path)
    path = "/api/audit/traces/trace-1/graph?level=trace_full"
    response = await router.dispatch(_request(path), path.split("?")[0])

    assert response is not None
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["level"] == "trace_full"
    assert body["nodes"][0]["lane_kind"] == "main"
    assert body["nodes"][0]["terminal_status"]
    assert body["nodes"][0]["health_status"]
    raw_event = body["nodes"][0]["raw_events"][0]
    assert set(raw_event) == {
        "event_id", "event_type", "occurred_at", "status", "payload_id"
    }
    assert "content" not in body
    assert "resource_key" not in json.dumps(body)
    assert "resource_correction_keys" not in json.dumps(body)
    assert b"payload content" not in response.body


async def test_timeline_has_semantic_ownership_and_payload_is_no_store(tmp_path) -> None:
    router = _router(tmp_path)
    events_path = "/api/audit/traces/trace-1/events"
    events = await router.dispatch(_request(events_path), events_path)
    assert events is not None
    body = json.loads(events.body)
    assert body["items"][0]["semantic_node_id"] is not None
    assert "content" not in body["items"][0]
    assert body["total"] == 9

    payload_path = "/api/audit/payloads/payload-1"
    payload = await router.dispatch(_request(payload_path), payload_path)
    assert payload is not None
    assert payload.headers["Cache-Control"] == "no-store"
    assert b"[REDACTED:CREDENTIAL]" in payload.body
