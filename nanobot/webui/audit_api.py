"""Authenticated HTTP routes for the audit trace workbench."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.audit.graph import GRAPH_BUILDER_VERSION, AuditGraphBuilder
from nanobot.audit.read_service import (
    AuditReadService,
    AuditReadUnavailableError,
    CursorStaleError,
    PayloadLocatorInvalidError,
    PayloadTooLargeError,
    SessionListFilter,
    TraceListFilter,
)
from nanobot.webui.http_utils import (
    case_insensitive_header,
    http_json_response,
    http_response,
    parse_query,
    query_first,
)

_ID = re.compile(r"^[A-Za-z0-9_:.-]{1,256}$")


def _error(status: int, code: str, message: str, *, retryable: bool = False) -> Response:
    return http_json_response(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        status=status,
    )


def _json_with_headers(
    data: dict[str, Any], *, status: int = 200, headers: list[tuple[str, str]] | None = None
) -> Response:
    return http_response(
        json.dumps(data, ensure_ascii=False).encode(),
        status=status,
        content_type="application/json; charset=utf-8",
        extra_headers=headers,
    )


class WebUIAuditRouter:
    def __init__(
        self,
        *,
        read_service: AuditReadService,
        audit_root: Path,
        audit_mode: str,
        check_api_token: Callable[[WsRequest], bool],
        logger: Any,
        resolve_session_title: Callable[[str], str | None] | None = None,
    ) -> None:
        self.read_service = read_service
        self.audit_root = audit_root
        self.audit_mode = audit_mode
        self.check_api_token = check_api_token
        self.logger = logger
        self.graph_builder = AuditGraphBuilder()
        self.resolve_session_title = resolve_session_title

    def _with_audit_mode(self, data: dict[str, Any]) -> dict[str, Any]:
        index = data.get("index")
        if isinstance(index, dict):
            index["audit_mode"] = self.audit_mode
        return data

    async def dispatch(self, request: WsRequest, got: str) -> Response | None:
        if not got.startswith("/api/audit/"):
            return None
        if not self.check_api_token(request):
            return _error(401, "unauthorized", "A valid API bearer token is required.")
        if getattr(request, "method", "GET") != "GET":
            return _error(405, "method_not_allowed", "Audit routes are read-only.")
        if got == "/api/audit/traces":
            return await self._list_traces(request)
        if got == "/api/audit/sessions":
            return await self._list_sessions(request)
        graph_match = re.fullmatch(r"/api/audit/traces/([^/]+)/graph", got)
        if graph_match:
            return await self._graph(request, graph_match.group(1))
        events_match = re.fullmatch(r"/api/audit/traces/([^/]+)/events", got)
        if events_match:
            return await self._events(request, events_match.group(1))
        payload_match = re.fullmatch(r"/api/audit/payloads/([^/]+)", got)
        if payload_match:
            return await self._payload(payload_match.group(1))
        return _error(404, "audit_route_not_found", "Audit route not found.")

    def _validate_id(self, value: str, kind: str) -> Response | None:
        if _ID.fullmatch(value) is None:
            return _error(400, f"invalid_{kind}_id", f"Invalid {kind} ID.")
        return None

    def _unavailable(self, error: AuditReadUnavailableError) -> Response:
        state = error.status.state
        if self.audit_mode == "off":
            code = "audit_off"
        else:
            code = f"audit_index_{state}"
        return _error(503, code, f"Audit index is {state}.", retryable=state in {"building", "stale"})

    async def _list_traces(self, request: WsRequest) -> Response:
        query = parse_query(request.path)
        try:
            filters = TraceListFilter(
                since=query_first(query, "since"),
                until=query_first(query, "until"),
                session_key=query_first(query, "session_key"),
                source_type=query_first(query, "source_type"),
                query=query_first(query, "query"),
                status=query_first(query, "status"),
                model=query_first(query, "model"),
                tool=query_first(query, "tool"),
                anomalies_only=(query_first(query, "anomalies_only") or "false").lower()
                == "true",
                limit=int(query_first(query, "limit") or "50"),
                cursor=query_first(query, "cursor"),
            )
            page = await asyncio.to_thread(self.read_service.list_traces, filters)
        except (ValidationError, ValueError) as error:
            if isinstance(error, CursorStaleError):
                return _error(409, "cursor_stale", "The index revision changed.")
            return _error(400, "invalid_audit_filter", "Audit filter is invalid.")
        except AuditReadUnavailableError as error:
            return self._unavailable(error)
        if self.resolve_session_title is not None:
            for item in page.items:
                if not item.session_key:
                    continue
                try:
                    title = await asyncio.to_thread(
                        self.resolve_session_title, item.session_key
                    )
                except Exception:
                    title = None
                if title:
                    item.title = title
        return http_json_response(self._with_audit_mode(page.model_dump(mode="json")))

    async def _list_sessions(self, request: WsRequest) -> Response:
        query = parse_query(request.path)
        try:
            filters = SessionListFilter(
                query=query_first(query, "query"),
                limit=int(query_first(query, "limit") or "50"),
                cursor=query_first(query, "cursor"),
            )
            page = await asyncio.to_thread(self.read_service.list_sessions, filters)
        except (ValidationError, ValueError) as error:
            if isinstance(error, CursorStaleError):
                return _error(409, "cursor_stale", "The index revision changed.")
            return _error(400, "invalid_audit_filter", "Audit filter is invalid.")
        except AuditReadUnavailableError as error:
            return self._unavailable(error)
        if self.resolve_session_title is not None:
            for item in page.items:
                try:
                    title = await asyncio.to_thread(
                        self.resolve_session_title, item.session_key
                    )
                except Exception:
                    title = None
                if title:
                    item.title = title
        return http_json_response(self._with_audit_mode(page.model_dump(mode="json")))

    async def _graph(self, request: WsRequest, trace_id: str) -> Response:
        invalid = self._validate_id(trace_id, "trace")
        if invalid:
            return invalid
        query = parse_query(request.path)
        level = query_first(query, "level") or "trace"
        run_id = query_first(query, "run_id")
        if level not in {"trace", "trace_full", "run"} or (level == "run" and not run_id):
            return _error(400, "invalid_graph_focus", "Graph level or Run focus is invalid.")
        if run_id and self._validate_id(run_id, "run"):
            return self._validate_id(run_id, "run")  # type: ignore[return-value]
        try:
            detail = await asyncio.to_thread(self.read_service.load_trace_events, trace_id)
            trace_graph = await asyncio.to_thread(
                self.graph_builder.build,
                trace_id=trace_id,
                level=level,
                run_id=run_id,
                events=detail.events,
                active_run_ids=detail.active_run_ids,
                integrity_status=detail.integrity_status,
                integrity_error_codes=detail.integrity_error_codes,
                integrity_warning_codes=detail.integrity_warning_codes,
            )
        except AuditReadUnavailableError as error:
            return self._unavailable(error)
        except KeyError:
            code = "run_not_found" if run_id else "trace_not_found"
            return _error(404, code, "Requested Trace or Run was not found.")
        except (ValueError, ValidationError):
            return _error(500, "audit_graph_contract_error", "Trace graph could not be built.")
        etag_source = (
            f"{trace_id}\0{level}\0{run_id or ''}\0{detail.revision}\0"
            f"{GRAPH_BUILDER_VERSION}\0{self.audit_mode}"
        )
        etag = f'"{hashlib.sha256(etag_source.encode()).hexdigest()}"'
        if case_insensitive_header(request.headers, "If-None-Match") == etag:
            return http_response(b"", status=304, extra_headers=[("ETag", etag)])
        body = trace_graph.model_dump(mode="json", exclude={"event_owners"})
        if self.resolve_session_title is not None and trace_graph.trace.session_key:
            try:
                title = await asyncio.to_thread(
                    self.resolve_session_title, trace_graph.trace.session_key
                )
            except Exception:
                title = None
            if title:
                body["trace"]["title"] = title
        body["index"] = {
            "revision": detail.revision,
            "coverage_complete": self.read_service.status().coverage_complete,
            "lag_ms": self.read_service.status().lag_ms,
            "audit_mode": self.audit_mode,
        }
        return _json_with_headers(body, headers=[("ETag", etag)])

    async def _events(self, request: WsRequest, trace_id: str) -> Response:
        invalid = self._validate_id(trace_id, "trace")
        if invalid:
            return invalid
        query = parse_query(request.path)
        try:
            limit = int(query_first(query, "limit") or "200")
            page = await asyncio.to_thread(
                self.read_service.list_trace_events,
                trace_id,
                cursor=query_first(query, "cursor"),
                limit=limit,
            )
            detail = await asyncio.to_thread(self.read_service.load_trace_events, trace_id)
            trace_graph = await asyncio.to_thread(
                self.graph_builder.build,
                trace_id=trace_id,
                level="trace_full",
                events=detail.events,
                active_run_ids=detail.active_run_ids,
                integrity_status=detail.integrity_status,
            )
        except CursorStaleError:
            return _error(409, "cursor_stale", "The index revision changed.")
        except (ValueError, ValidationError):
            return _error(400, "invalid_event_query", "Event query is invalid.")
        except AuditReadUnavailableError as error:
            return self._unavailable(error)
        except KeyError:
            return _error(404, "trace_not_found", "Trace was not found.")
        event_owners = dict(trace_graph.event_owners)
        items = []
        for event in page.events:
            items.append(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at.isoformat(),
                    "process_instance_id": event.process_instance_id,
                    "durability_epoch": event.durability_epoch,
                    "segment_id": event.segment_id,
                    "segment_sequence": event.segment_sequence,
                    "trace_id": event.trace_id,
                    "turn_id": event.turn_id,
                    "run_id": event.run_id,
                    "model_call_id": event.model_call_id,
                    "attempt_id": event.attempt_id,
                    "tool_call_id": event.tool_call_id,
                    "iteration": event.iteration,
                    "caused_by_event_id": event.caused_by_event_id,
                    "status": getattr(event, "status", None),
                    "elapsed_ms": getattr(event, "elapsed_ms", None),
                    "payload_id": event.payload_id,
                    "semantic_node_id": event_owners.get(event.event_id),
                    "summary": event.event_type.replace("_", " "),
                }
            )
        return http_json_response(
            {
                "items": items,
                "next_cursor": page.next_cursor,
                "total": page.total,
                "index": {"revision": page.revision, "audit_mode": self.audit_mode},
            }
        )

    async def _payload(self, payload_id: str) -> Response:
        invalid = self._validate_id(payload_id, "payload")
        if invalid:
            return invalid
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.read_service.load_payload,
                    payload_id,
                    audit_root=self.audit_root,
                    audit_mode=self.audit_mode,
                ),
                timeout=2,
            )
        except TimeoutError:
            return _error(
                503,
                "audit_payload_lookup_timeout",
                "Payload lookup timed out.",
                retryable=True,
            )
        except KeyError:
            return _error(404, "payload_not_found", "Payload was not found.")
        except PayloadTooLargeError:
            return _error(413, "payload_too_large", "Payload exceeds the bounded read limit.")
        except PayloadLocatorInvalidError:
            return _error(404, "payload_locator_invalid", "Payload locator is invalid.")
        except AuditReadUnavailableError as error:
            return self._unavailable(error)
        return _json_with_headers(
            result.model_dump(mode="json"),
            headers=[("Cache-Control", "no-store"), ("Pragma", "no-cache")],
        )
